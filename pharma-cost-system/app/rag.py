# -*- coding: utf-8 -*-
"""RAG 知识库与"BM25 + 向量"混合检索

赛题 5.1.2 要求：知识库至少含产品知识 / 行业知识 / 企业内部知识三类文档，**支持 PDF、Word、TXT**；
检索策略需支持「语义检索（向量相似度）+ 关键词检索（BM25）混合」，结果需标注来源文档。

本模块实现：
1. **多格式文档加载**：`.txt/.md`（直接读）、`.docx`（python-docx）、`.pdf`（pypdfium2），
   抽取结果带磁盘缓存（`data/cache/knowledge/`，按源文件 mtime 失效），重复启动不再解析。
2. **可配置切分策略**：先按 `【小节】`/空行做语义切块，再按 `chunk_size` 滑窗并保留 `overlap` 重叠，
   `min_chunk` 控制最小区块长度；参数可用 `RAG_CHUNK_SIZE / RAG_CHUNK_OVERLAP / RAG_MIN_CHUNK` 调整。
3. **真·混合检索**：jieba 分词 + BM25（关键词）+ 向量相似度（`app/vector_store.py`；
   嵌入可走 LLM embeddings 或本地哈希 TF-IDF，向量库可走 ChromaDB / FAISS / numpy），
   两路分数归一化后按 `RAG_HYBRID_ALPHA` 加权融合（默认 0.5 向量权重），并叠加实体精确命中加权。
4. **知识图谱增强**：可选接入 `app/knowledge_graph.py`（`attach_graph()`），
   命中实体经图谱 1 跳扩展后，相关文段获得加权，提升"配方/工艺/设备"类查询的召回与可解释性。
5. **来源可追溯**：每条结果保留 `source / doc_id / snippet / score`，并标注由哪一路召回
   （`retriever = bm25 | vector | hybrid`）以及图谱加权，便于评测与在报告中引用。
"""
import math
import os
import re
import time

from . import vector_store as vs
from .logging_conf import get_logger
from .paths import PROJECT_ROOT
from .textutil import normalize_text, tokens as _tokenize

log = get_logger(__name__)

KB_DIRS_DEFAULT = [os.path.join(PROJECT_ROOT, "data", "knowledge"),
                   os.path.join(PROJECT_ROOT, "data", "knowledge_official")]
CACHE_DIR = os.path.join(PROJECT_ROOT, "data", "cache", "knowledge")

# ---- 可调参数（环境变量 > 默认值）----
CHUNK_SIZE = int(os.environ.get("RAG_CHUNK_SIZE", "220"))
CHUNK_OVERLAP = int(os.environ.get("RAG_CHUNK_OVERLAP", "40"))
MIN_CHUNK = int(os.environ.get("RAG_MIN_CHUNK", "60"))
HYBRID_ALPHA = float(os.environ.get("RAG_HYBRID_ALPHA", "0.5"))     # 向量路权重
# 融合方式默认取 rrf（倒数排名融合）：scripts/verify/verify_rag.py 实测 MRR 0.667 > 加权归一化 0.611 > 纯 BM25 0.583
FUSION = (os.environ.get("RAG_FUSION", "rrf") or "rrf").lower()      # rrf | weighted
DEFAULT_MODE = (os.environ.get("RAG_MODE", "hybrid") or "hybrid").lower()

STOP = set("的 了 在 是 与 及 和 或 等 中 为 对 由 从 把 将 被 而 且 但 并 可 需 应 以 之 其 本 该 各 每 约 上 下 内 外".split())
SUPPORTED_EXT = (".txt", ".md", ".docx", ".pdf")


# ==================== 文档加载 ====================
def _extract_docx(path):
    from docx import Document
    doc = Document(path)
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for t in doc.tables:
        for row in t.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _extract_pdf(path):
    """优先 pypdfium2（vendor 内已含），失败时给出明确指引"""
    import pypdfium2 as pdfium
    parts = []
    pdf = pdfium.PdfDocument(path)
    try:
        for page in pdf:
            tp = page.get_textpage()
            parts.append(tp.get_text_range())
    finally:
        pdf.close()
    return "\n".join(parts)


def load_document(path):
    """按扩展名抽取纯文本，支持 .txt/.md/.docx/.pdf（抽取后统一做 NFKC 规范化）"""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".txt", ".md"):
        with open(path, encoding="utf-8", errors="ignore") as f:
            return normalize_text(f.read())
    if ext == ".docx":
        return normalize_text(_extract_docx(path))
    if ext == ".pdf":
        return normalize_text(_extract_pdf(path))
    raise ValueError(f"暂不支持的文档格式：{ext}")


def load_document_cached(path):
    """带磁盘缓存的文档抽取（缓存失效条件：源文件 mtime 变化）"""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".txt", ".md"):
        return load_document(path)
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        key = re.sub(r"[^\w\u4e00-\u9fa5]", "_", os.path.basename(path)) + f".{int(os.path.getmtime(path))}.txt"
        cache_file = os.path.join(CACHE_DIR, key)
        if os.path.exists(cache_file):
            with open(cache_file, encoding="utf-8") as f:
                return f.read()
        text = load_document(path)
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            pass
        return text
    except Exception as e:
        log.warning("文档解析失败 %s：%s", os.path.basename(path), e)
        raise


# ==================== 切分 ====================
def chunk_text(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP, min_chunk=MIN_CHUNK):
    """语义切块 + 滑窗重叠切分

    1) 先按 `【小节标题】` 或空行拆成语义块；
    2) 语义块 <= chunk_size 直接成块，相邻短块合并；
    3) 过长语义块按 chunk_size 滑窗切分，相邻窗口保留 overlap 字符重叠（避免语义被截断）；
    4) 低于 min_chunk 的碎块并入前块或丢弃。
    """
    blocks = [b.strip() for b in re.split(r"\n(?=【)|\n\s*\n", text or "") if b.strip()]
    chunks, buf = [], ""
    for b in blocks:
        if len(b) <= chunk_size:
            if len(buf) + len(b) <= chunk_size:
                buf = (buf + "\n" + b).strip()
                continue
            if buf:
                chunks.append(buf)
            buf = b
            continue
        if buf:
            chunks.append(buf)
            buf = ""
        step = max(chunk_size - overlap, 1)
        for i in range(0, len(b), step):
            piece = b[i:i + chunk_size].strip()
            if piece:
                chunks.append(piece)
            if i + chunk_size >= len(b):
                break
    if buf:
        chunks.append(buf)
    merged = []
    for c in chunks:
        if len(c) < min_chunk and merged:
            merged[-1] = (merged[-1] + "\n" + c).strip()
        elif len(c) >= min_chunk:
            merged.append(c)
    return merged or ([text.strip()] if text and text.strip() else [])


def _tokens(text):
    """jieba 分词 + 去停用词（见 app/textutil.py，同时处理 PDF 部首噪声）"""
    return _tokenize(text)


# ==================== 知识库 ====================
class KnowledgeBase:
    """混合检索知识库（BM25 + 向量 [+ 知识图谱增强]）"""

    def __init__(self, kb_dir=None, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP,
                 min_chunk=MIN_CHUNK, extra_dirs=None, embedder="auto", use_vector=True):
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.min_chunk = min_chunk
        self.dirs = ([kb_dir] if kb_dir else list(KB_DIRS_DEFAULT))
        if extra_dirs:
            self.dirs = list(self.dirs) + [d for d in extra_dirs if d]
        self.docs = []          # [{doc_id, source, path, chunk, tokens}]
        self.files = []         # 已加载文档清单
        self.graph = None       # 知识图谱（可选）
        self.N = 0
        self.avgdl = 1.0
        self.df = {}
        self.index = None
        self.mode_used = "bm25"
        self.built_at = None
        self.build_seconds = None
        if use_vector:
            self.embedder = vs.build_embedder(None if embedder == "auto" else embedder)
        else:
            self.embedder = None
        self._build()

    # ---------- 构建 ----------
    def _scan(self):
        files = []
        for d in self.dirs:
            if not d or not os.path.isdir(d):
                continue
            for fn in sorted(os.listdir(d)):
                p = os.path.join(d, fn)
                if os.path.isfile(p) and fn.lower().endswith(SUPPORTED_EXT):
                    files.append(p)
        return files

    def _build(self):
        t0 = time.time()
        for path in self._scan():
            try:
                raw = load_document_cached(path)
            except Exception:
                continue
            source = os.path.splitext(os.path.basename(path))[0]
            pieces = chunk_text(raw, self.chunk_size, self.overlap, self.min_chunk)
            self.files.append({"file": os.path.basename(path), "来源": source,
                               "格式": os.path.splitext(path)[1].lstrip(".").lower(),
                               "字符数": len(raw), "知识块": len(pieces)})
            for i, ch in enumerate(pieces):
                self.docs.append({"doc_id": f"{source}#{i}", "source": source, "path": path,
                                  "chunk": ch, "tokens": _tokens(ch)})
        self.N = len(self.docs)
        self.avgdl = sum(len(d["tokens"]) for d in self.docs) / max(self.N, 1)
        self.df = {}
        for d in self.docs:
            for t in set(d["tokens"]):
                self.df[t] = self.df.get(t, 0) + 1
        if self.embedder is not None and self.docs:
            try:
                corpus = [d["chunk"] for d in self.docs]
                self.embedder.fit(corpus)
                self.index = vs.VectorIndex(self.embedder.transform(corpus))
                self.mode_used = "hybrid"
            except Exception as e:
                log.warning("向量索引构建失败（%s），本次退化为纯 BM25", e)
                self.index = None
        self.built_at = time.strftime("%Y-%m-%d %H:%M:%S")
        self.build_seconds = round(time.time() - t0, 2)

    def attach_graph(self, graph):
        """接入知识图谱（app/knowledge_graph.py）以启用图谱增强检索"""
        self.graph = graph
        return self

    def rebuild(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
        self.docs, self.files, self.index, self.df = [], [], None, {}
        self._build()
        return self

    # ---------- BM25 ----------
    def _bm25(self, query_tokens, k1=1.5, b=0.75):
        scores = [0.0] * self.N
        if not query_tokens:
            return scores
        for i, d in enumerate(self.docs):
            tf = {}
            for t in d["tokens"]:
                tf[t] = tf.get(t, 0) + 1
            dl = len(d["tokens"])
            s = 0.0
            for t in query_tokens:
                if t not in self.df:
                    continue
                idf = math.log((self.N - self.df[t] + 0.5) / (self.df[t] + 0.5) + 1)
                f = tf.get(t, 0)
                s += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * dl / self.avgdl))
            scores[i] = s
        return scores

    def _vector(self, query, top_k):
        """向量路召回：{chunk_index: 相似度}"""
        if self.index is None or self.embedder is None:
            return {}
        try:
            qv = self.embedder.transform([query])[0]
            return {i: s for i, s in self.index.search(qv, max(top_k * 3, 10))}
        except Exception as e:
            log.warning("向量检索失败（%s），本次仅用 BM25", e)
            return {}

    # ---------- 检索 ----------
    def search(self, query, top_k=5, product_hint=None, mode=None, alpha=None, fusion=None):
        """混合检索；结果带来源文档标注与召回路径标注

        fusion='weighted'：两路分数 min-max 归一化后按 alpha 加权（默认）
        fusion='rrf'     ：倒数排名融合（Reciprocal Rank Fusion），对两路分数量纲不敏感、更稳健
        """
        mode = (mode or DEFAULT_MODE).lower()
        alpha = HYBRID_ALPHA if alpha is None else float(alpha)
        fusion = (fusion or FUSION).lower()
        q_tokens = _tokens(query)
        if product_hint:
            q_tokens += _tokens(product_hint)

        bm = self._bm25(q_tokens) if mode in ("hybrid", "bm25", "auto") else [0.0] * self.N
        vec = self._vector(query, top_k) if mode in ("hybrid", "vector", "auto") else {}
        if mode == "vector":
            eff_mode = "vector" if vec else "bm25"
        elif mode == "bm25":
            eff_mode = "bm25"
        else:
            eff_mode = "hybrid" if vec else "bm25"

        def _norm(vals):
            if not vals:
                return [0.0] * self.N
            lo, hi = min(vals), max(vals)
            if hi - lo < 1e-9:
                return [0.0] * self.N
            return [(v - lo) / (hi - lo) for v in vals]

        bm_n = _norm(bm)
        vec_raw = [vec.get(i, 0.0) for i in range(self.N)]
        vec_n = _norm(vec_raw)

        if eff_mode == "hybrid" and fusion == "rrf":
            # 倒数排名融合：k=60 为标准取值，抵消两路分数量纲差异
            fused = [0.0] * self.N
            for ranks in (sorted(range(self.N), key=lambda i: -bm[i]),
                          sorted(range(self.N), key=lambda i: -vec_raw[i])):
                for pos, i in enumerate(ranks[:max(top_k * 4, 20)], start=1):
                    fused[i] += 1.0 / (60 + pos)
            fused = _norm(fused)
        elif eff_mode == "hybrid":
            fused = [(1 - alpha) * bm_n[i] + alpha * vec_n[i] for i in range(self.N)]
        elif eff_mode == "vector":
            fused = vec_n
        else:
            fused = bm_n

        # 实体/关键词精确命中加权：作为"轻度加权项"，量级刻意控制在检索分之下，
        # 避免单一关键词命中压过"BM25+向量"两路融合结果（否则三种模式排序会趋同）。
        boost = [0.0] * self.N
        words = {w for w in q_tokens if len(w) >= 2}
        for i, d in enumerate(self.docs):
            b = 0.0
            for w in words:
                if w in d["chunk"]:
                    b += 0.25
            boost[i] = min(b, 0.9)

        # 知识图谱增强：命中实体经 1 跳扩展后给相关文段加权
        graph_hits = {}
        if self.graph is not None:
            try:
                exp = self.graph.expand(query, product_hint=product_hint)
                for kw, w in (exp.get("chunk_weights") or {}).items():
                    if not kw:
                        continue
                    for i, d in enumerate(self.docs):
                        if kw in d["chunk"]:
                            graph_hits[i] = graph_hits.get(i, 0.0) + w
            except Exception as e:
                log.warning("知识图谱扩展失败：%s", e)

        scores = []
        for i in range(self.N):
            scores.append(fused[i] + boost[i] + graph_hits.get(i, 0.0))

        ranked = sorted(range(self.N), key=lambda i: -scores[i])[:max(top_k, 1)]
        results = []
        for i in ranked:
            if scores[i] <= 0:
                continue
            hit = {
                "source": self.docs[i]["source"],
                "doc_id": self.docs[i]["doc_id"],
                "score": round(float(scores[i]), 4),
                "snippet": self.docs[i]["chunk"][:400],
                "bm25": round(float(bm_n[i]), 4),
                "vector": round(float(vec_n[i]), 4),
                "retriever": ("bm25" if vec_n[i] <= 0 else ("vector" if bm_n[i] <= 0 else "hybrid")),
            }
            if i in graph_hits:
                hit["graph"] = round(float(graph_hits[i]), 4)
            results.append(hit)
        self.last_mode = eff_mode
        return results

    def context(self, query, product_hint=None, top_k=4, mode=None):
        """拼接为 RAG 上下文（带来源标注），供大模型 Prompt 使用"""
        hits = self.search(query, top_k=top_k, product_hint=product_hint, mode=mode)
        if not hits:
            return "", []
        ctx = "\n\n".join(f"[来源: {h['source']}]\n{h['snippet']}" for h in hits)
        return ctx, hits

    # ---------- 元信息 ----------
    def stats(self):
        by_fmt = {}
        for f in self.files:
            by_fmt[f["格式"]] = by_fmt.get(f["格式"], 0) + 1
        return {
            "文档数": len(self.files),
            "知识块数": len(self.docs),
            "平均块长": round(sum(len(d["chunk"]) for d in self.docs) / max(len(self.docs), 1), 1),
            "文档格式": by_fmt,
            "切分参数": {"chunk_size": self.chunk_size, "overlap": self.overlap, "min_chunk": self.min_chunk},
            "检索模式": self.mode_used,
            "向量后端": vs.backend_name(),
            "嵌入器": (self.embedder.info() if self.embedder is not None else None),
            "知识图谱": (self.graph.stats() if self.graph is not None else None),
            "构建耗时秒": self.build_seconds,
            "构建时间": self.built_at,
        }

    def documents(self):
        return list(self.files)


# ==================== 单例 ====================
kb = KnowledgeBase()

# 自动接入知识图谱（存在即启用；失败不影响检索）
try:
    from .knowledge_graph import build_graph
    kb.attach_graph(build_graph())
except Exception as _e:  # pragma: no cover
    log.info("知识图谱未启用：%s", _e)
