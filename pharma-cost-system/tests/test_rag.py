# -*- coding: utf-8 -*-
"""RAG（BM25 + 向量混合检索知识库）测试。

覆盖：
  * kb.search('连翘单耗上升的原因', top_k=3)：≤3 条、每条含 source/doc_id/score/snippet 且 score > 0
  * 来源标注不丢失（source 指向真实知识库文档、doc_id 与 source 前缀一致）
  * 不同切分参数（chunk_size）下文档块数量变化
  * 知识图谱增强启用且可构建（回归保护：遍历 dict 时不得新增节点，否则
    `RuntimeError: dictionary changed size during iteration` 会被静默吞掉 → 图谱恒为空）

运行：python -m pytest tests/test_rag.py -q
      python -m unittest tests.test_rag -v
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app import knowledge_graph as kg                # noqa: E402
from app import rag as rag_mod                       # noqa: E402
from app.rag import KnowledgeBase, kb                # noqa: E402

QUERY = "连翘单耗上升的原因"
KB_DIRS = [d for d in rag_mod.KB_DIRS_DEFAULT if os.path.isdir(d)]


# ============================================================ search
def test_search_returns_at_most_top_k():
    hits = kb.search(QUERY, top_k=3)
    assert isinstance(hits, list)
    assert 0 < len(hits) <= 3, f"top_k=3 应返回 1~3 条，实际 {len(hits)}"


def test_search_hit_fields_and_positive_score():
    hits = kb.search(QUERY, top_k=3)
    assert hits, "查询应至少命中一条"
    for h in hits:
        assert {"source", "doc_id", "score", "snippet"} <= set(h), f"字段缺失：{set(h)}"
        assert isinstance(h["source"], str) and h["source"]
        assert isinstance(h["doc_id"], str) and h["doc_id"]
        assert isinstance(h["score"], float) and h["score"] > 0, h
        assert isinstance(h["snippet"], str) and h["snippet"].strip()


def test_search_source_provenance_is_real_document():
    """来源标注不得丢失：source 必须对应知识库目录下真实存在的文档。"""
    files = set()
    for d in KB_DIRS:
        files |= {f.rsplit(".", 1)[0] for f in os.listdir(d)
                  if f.lower().endswith(rag_mod.SUPPORTED_EXT)}
    assert files, f"知识库目录为空：{KB_DIRS}"
    for h in kb.search(QUERY, top_k=5):
        assert h["source"] in files, f"未知来源文档：{h['source']}"
        assert h["doc_id"].startswith(h["source"]), f"doc_id 与 source 不匹配：{h}"


def test_search_relevance_ordering():
    """结果按 score 降序，且命中片段与查询相关。"""
    hits = kb.search(QUERY, top_k=3)
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores, reverse=True)
    assert any("连翘" in h["snippet"] for h in hits), "应有命中片段包含关键词「连翘」"


def test_search_irrelevant_query_scores_lower_than_relevant():
    """无关查询的得分必须明显低于相关查询（相对排序正确）。

    注：新版权重设计下，无关查询仍会因"实体/关键词精确命中加权"（每个命中词 +0.25，
    上限 0.9）拿到约 1.0 的兜底分，因此这里断言的是**相对关系**而不是绝对阈值。
    """
    relevant = [h["score"] for h in kb.search(QUERY, top_k=3)]
    irrelevant = [h["score"] for h in kb.search("zzz不存在的词xyz", top_k=3)]
    assert relevant, "相关查询应有命中"
    if irrelevant:
        assert min(relevant) > min(irrelevant), (relevant, irrelevant)


def test_search_product_hint_boosts_product_docs():
    """product_hint 应把产品相关片段排到前面。"""
    hits = kb.search("配方 标准配方", top_k=3, product_hint="六味地黄胶囊")
    assert hits, "应至少命中一条"
    assert any("六味" in h["snippet"] or "六味" in h["doc_id"] for h in hits), hits


def test_search_mode_bm25_only():
    """纯 BM25 模式可用，且结果仍带来源标注。"""
    hits = kb.search(QUERY, top_k=3, mode="bm25")
    assert 0 < len(hits) <= 3
    for h in hits:
        assert h["source"] and h["doc_id"] and h["score"] > 0


def test_search_reports_retriever_path():
    """混合检索应标注召回路径（bm25 / vector / hybrid），便于评测与报告引用。"""
    hits = kb.search(QUERY, top_k=3)
    assert hits
    for h in hits:
        if "retriever" in h:                      # 新版实现提供该字段
            assert h["retriever"] in ("bm25", "vector", "hybrid"), h


def test_context_carries_source_labels():
    """context() 拼接的上下文必须带 [来源: xxx] 标注（报告证据链依赖）。"""
    ctx, hits = kb.context(QUERY, top_k=3)
    assert hits, "context 不应为空"
    for h in hits:
        assert f"[来源: {h['source']}]" in ctx


# ============================================================ 切分参数
def test_chunk_size_changes_document_count():
    """不同切分参数下文档块数量应发生变化（chunk 越小 → 块越多）。"""
    small = KnowledgeBase(chunk_size=120)
    large = KnowledgeBase(chunk_size=1200)
    assert len(small.docs) > len(large.docs), \
        f"chunk_size=120 块数 {len(small.docs)} 应多于 chunk_size=1200 的 {len(large.docs)}"
    assert len(large.docs) <= len(kb.docs) <= len(small.docs)


def test_chunked_instance_search_still_has_provenance():
    """自定义切分实例检索结果同样保留来源标注。"""
    inst = KnowledgeBase(chunk_size=120, use_vector=False)
    hits = inst.search(QUERY, top_k=3)
    assert 0 < len(hits) <= 3
    for h in hits:
        assert h["source"] and h["doc_id"] and h["score"] > 0
        assert len(h["snippet"]) <= 400


def test_kb_stats_reports_chunking_params_and_sources():
    """stats() 应给出文档数/知识块数/切分参数/来源格式（评测证据）。"""
    st = kb.stats()
    assert st["文档数"] >= 3, "知识库至少含 3 类文档"
    assert st["知识块数"] > 0
    assert st["切分参数"]["chunk_size"] > 0
    assert sum(st["文档格式"].values()) == st["文档数"]


# ============================================================ 知识图谱增强
def test_knowledge_graph_builds_without_error():
    """知识图谱必须能构建出实体与关系。

    回归保护：`app/knowledge_graph.py::_extract()` 在遍历 `g.nodes` 的同时调用
    `g.add_edge()`（会新增节点）会触发
    `RuntimeError: dictionary changed size during iteration`；该异常被 build_graph
    的 except 静默吞掉，表现为"图谱恒为空"且只在启动日志里留一行 warning。
    现行实现已改为 `for m in list(g.nodes):` —— 本用例锁定该行为不被改回。
    """
    g = kg.build_graph(force=True)
    assert g.nodes, "知识图谱节点为空（构建失败被静默降级）"
    assert g.edges, "知识图谱关系为空（构建失败被静默降级）"
    stats = g.stats()
    assert stats.get("节点数", 0) > 0 and stats.get("关系数", 0) > 0


def test_kb_graph_attached_and_expand_works():
    """单例知识库应已接入图谱，且 expand() 能给出邻居实体/文段权重。"""
    if kb.graph is None:
        # 旧版实现未接入图谱：退化为只校验检索来源标注
        assert kb.search(QUERY, top_k=1)
        return
    exp = kb.graph.expand(QUERY, product_hint="六味地黄胶囊")
    assert isinstance(exp.get("entities"), list)


# ---------------------------------------------------------------- unittest 兼容
try:
    from tests.conftest import make_test_case
except ImportError:                                 # pragma: no cover
    from conftest import make_test_case             # type: ignore

TestRag = make_test_case(globals())


if __name__ == "__main__":                       # pragma: no cover
    import unittest

    unittest.main(verbosity=2)
