# -*- coding: utf-8 -*-
"""向量检索与嵌入（可插拔后端）——支撑"BM25 + 向量"真·混合检索

赛题 5.1.2 要求检索策略支持「语义检索（向量相似度）+ 关键词检索（BM25）混合」，
技术栈表列出 ChromaDB / Milvus / FAISS。本模块把"嵌入提供方"与"向量库后端"做成两级可插拔，
任何一级缺失都能自动降级，保证离线环境同样可用：

嵌入提供方（embedder）
  1. **llm**  ：OpenAI 兼容 embeddings 接口（配置 `LLM_EMBED_MODEL` + `LLM_API_KEY`），语义质量最好
  2. **local**：本地轻量向量化 —— 字符 1/2/3-gram 哈希 + TF-IDF（`zlib.crc32` 稳定哈希、signed hashing
                降低碰撞噪声），零依赖、离线可用、结果确定（不随 PYTHONHASHSEED 变化）

向量库后端（index backend）
  1. **chromadb**：已安装则优先（支持持久化与元数据过滤）
  2. **faiss**   ：已安装则用 `IndexFlatIP`（向量已 L2 归一化 → 内积即余弦）
  3. **numpy**   ：内置兜底（矩阵点积），始终可用

对外接口：
    embedder_name() / backend_name() / info()
    Embedder.fit(corpus) -> None          # 仅 local 需要；llm 为 no-op
    Embedder.transform(texts) -> np.ndarray(float32, n×d, L2 归一化)
    VectorIndex(chunks).search(query_vec, top_k) -> [(chunk_index, score)]
"""
import math
import os
import zlib

import numpy as np
from .logging_conf import get_logger

log = get_logger(__name__)

# 本地哈希向量维度（512 维在 10^3 量级语料上足够，且碰撞噪声可控）
LOCAL_DIM = int(os.environ.get("RAG_LOCAL_DIM", "512"))


# ==================== 嵌入提供方 ====================
class LocalTfidfEmbedder:
    """本地轻量向量化：字符 n-gram 哈希 + TF-IDF（离线、确定、无依赖）

    适用中文短文本的语义近似检索（对"药材名/工序名/指标名"等实体命中尤其敏感），
    与 BM25 形成互补：BM25 强于词面精确匹配，本向量强于近义/跨词表述。
    """

    name = "local"

    def __init__(self, dim=LOCAL_DIM):
        self.dim = dim
        self.idf = None
        self.n_docs = 0

    # ---- 特征 ----
    @staticmethod
    def _grams(text):
        chars = [c for c in str(text) if not c.isspace()]
        grams = {}
        for n in (1, 2, 3):
            for i in range(len(chars) - n + 1):
                g = "".join(chars[i:i + n])
                grams[g] = grams.get(g, 0) + 1
        return grams

    @staticmethod
    def _bucket(g):
        h = zlib.crc32(g.encode("utf-8"))          # 稳定哈希：不受 PYTHONHASHSEED 影响
        return h % LOCAL_DIM, (1.0 if (h >> 16) & 1 else -1.0)

    def _tf_vector(self, text):
        v = np.zeros(self.dim, dtype=np.float64)
        for g, c in self._grams(text).items():
            idx, sign = self._bucket(g)
            v[idx] += sign * (1.0 + math.log(c))
        return v

    def fit(self, corpus):
        """统计文档频率以得到 IDF 权重"""
        self.n_docs = max(len(corpus), 1)
        df = np.zeros(self.dim, dtype=np.float64)
        for text in corpus:
            v = self._tf_vector(text)
            df += (v != 0).astype(np.float64)
        self.idf = np.log((self.n_docs + 1) / (df + 1)) + 1.0
        return self

    def transform(self, texts):
        if self.idf is None:
            self.fit(list(texts))
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            v = self._tf_vector(text) * self.idf
            n = float(np.linalg.norm(v))
            out[i] = (v / n) if n else v
        return out

    def info(self):
        return {"name": self.name, "dim": self.dim, "说明": "字符1/2/3-gram哈希 + TF-IDF（离线本地向量化）"}


class LLMEmbedder:
    """OpenAI 兼容 embeddings（配置 LLM_EMBED_MODEL 后自动启用）"""

    name = "llm"

    def __init__(self, model=None):
        self.model = model or os.environ.get("LLM_EMBED_MODEL", "text-embedding-v3")
        self._dim = None

    def fit(self, corpus):
        return self

    def transform(self, texts):
        from .llm import get_embeddings_batch
        vecs = get_embeddings_batch(list(texts))
        if not vecs:
            raise RuntimeError("embeddings 接口无返回")
        arr = np.asarray(vecs, dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self._dim = arr.shape[1]
        return arr / norms

    def info(self):
        return {"name": self.name, "model": self.model, "dim": self._dim,
                "说明": "OpenAI 兼容 embeddings 语义向量"}


def build_embedder(prefer=None):
    """按需选择嵌入提供方：llm（配置齐全且可用）→ local（永远可用）"""
    prefer = (prefer or os.environ.get("RAG_EMBEDDER", "auto") or "auto").lower()
    if prefer in ("llm", "auto"):
        try:
            from .llm import embed_ready
            if embed_ready():
                return LLMEmbedder()
            if prefer == "llm":
                log.warning("指定 llm 嵌入但未配置 LLM_EMBED_MODEL/API Key，降级为本地向量")
        except Exception as e:
            log.warning("llm 嵌入不可用（%s），降级为本地向量", e)
    return LocalTfidfEmbedder()


# ==================== 向量库后端 ====================
def backend_name():
    """返回实际可用的向量库后端名"""
    try:
        import chromadb  # noqa: F401
        return "chromadb"
    except Exception:
        pass
    try:
        import faiss  # noqa: F401
        return "faiss"
    except Exception:
        return "numpy"


def _normalize_rows(mat):
    mat = np.asarray(mat, dtype=np.float32)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


class VectorIndex:
    """向量检索索引：按环境自动选择 chromadb / faiss / numpy 后端"""

    def __init__(self, vectors=None, backend=None, collection="pharma_kb"):
        self.backend = (backend or backend_name()).lower()
        self.collection_name = collection
        self.vectors = None
        self._faiss_index = None
        self._chroma = None
        self._chroma_ids = []
        if vectors is not None:
            self.build(vectors)

    # ---- 构建 ----
    def build(self, vectors):
        mat = _normalize_rows(vectors)
        self.vectors = mat
        if self.backend == "faiss":
            try:
                import faiss
                self._faiss_index = faiss.IndexFlatIP(mat.shape[1])
                self._faiss_index.add(mat)
                return self
            except Exception as e:
                log.warning("faiss 初始化失败（%s），改用 numpy", e)
                self.backend = "numpy"
        if self.backend == "chromadb":
            try:
                import chromadb
                client = chromadb.EphemeralClient()
                self._chroma = client.get_or_create_collection(self.collection_name,
                                                               metadata={"hnsw:space": "cosine"})
                ids = [f"c{i}" for i in range(mat.shape[0])]
                self._chroma_ids = ids
                self._chroma.add(ids=ids, embeddings=mat.tolist())
                return self
            except Exception as e:
                log.warning("chromadb 初始化失败（%s），改用 numpy", e)
                self.backend = "numpy"
        self.backend = "numpy"
        return self

    # ---- 检索 ----
    def search(self, query_vec, top_k=5):
        """返回 [(索引, 余弦相似度)]，按相似度降序"""
        if self.vectors is None or len(self.vectors) == 0:
            return []
        q = np.asarray(query_vec, dtype=np.float32).reshape(1, -1)
        q = q / (np.linalg.norm(q) or 1.0)
        k = int(min(top_k, len(self.vectors)))
        if self.backend == "faiss" and self._faiss_index is not None:
            sims, idx = self._faiss_index.search(q, k)
            return [(int(i), float(s)) for s, i in zip(sims[0], idx[0]) if i >= 0]
        if self.backend == "chromadb" and self._chroma is not None:
            res = self._chroma.query(query_embeddings=q.tolist(), n_results=k)
            ids = (res.get("ids") or [[]])[0]
            dists = (res.get("distances") or [[]])[0]
            out = []
            for cid, dist in zip(ids, dists):
                try:
                    i = self._chroma_ids.index(cid)
                except ValueError:
                    continue
                out.append((i, float(1.0 - dist)))     # cosine 距离 → 相似度
            return out
        sims = (self.vectors @ q.reshape(-1)).astype(float)
        order = np.argsort(-sims)[:k]
        return [(int(i), float(sims[i])) for i in order]

    def info(self):
        return {"backend": self.backend,
                "vectors": 0 if self.vectors is None else int(self.vectors.shape[0]),
                "dim": 0 if self.vectors is None else int(self.vectors.shape[1])}


def info(embedder=None, index=None):
    """汇总向量检索环境信息（用于 /api/meta 与前端展示）"""
    out = {"embedder": embedder.info() if embedder is not None else None,
           "backend": backend_name()}
    if index is not None:
        out["index"] = index.info()
    return out
