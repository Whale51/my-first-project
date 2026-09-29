# -*- coding: utf-8 -*-
"""benchmark（两厂对标三步法）测试。

覆盖：
  * benchmark('2026-06', use_llm=False) 返回必需键，且不依赖外网（engine 应为 rule）
  * 差异对比表每行满足 差异额 = 一厂 - 二厂、差异率% = 差异额 / 二厂 × 100
    （重算误差 < 0.02 个百分点）
  * 结构树.主因要素 = 结构拆解中 |差异率%| 最大者

运行：python -m pytest tests/test_benchmark.py -q
      python -m unittest tests.test_benchmark -v
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app import benchmark as bm                     # noqa: E402
from app.data_service import ds                     # noqa: E402

MONTH = "2026-06"
REQUIRED_KEYS = {"差异对比表", "结构树", "归因分析", "改进建议", "sources", "engine"}

# 缓存一次（benchmark 内部有 RAG 检索，重复调用没有必要）
_RESULT = None


def result():
    global _RESULT
    if _RESULT is None:
        _RESULT = bm.benchmark(MONTH, use_llm=False)
    return _RESULT


# ============================================================ 返回结构
def test_benchmark_required_keys_present():
    r = result()
    assert REQUIRED_KEYS <= set(r), f"缺少键：{REQUIRED_KEYS - set(r)}"
    assert r["差异对比表"], "差异对比表不应为空"
    assert isinstance(r["改进建议"], list) and r["改进建议"]
    assert isinstance(r["归因分析"], str) and r["归因分析"].strip()


def test_benchmark_offline_uses_rule_engine():
    """use_llm=False 时不得走大模型（评测机外网不可用）。"""
    r = result()
    assert r["engine"] == "rule", f"应降级为规则引擎，实际 engine={r['engine']}"
    assert "第一步" in r["归因分析"], "规则引擎输出应包含三步法叙述"


def test_benchmark_covers_all_products():
    rows = result()["差异对比表"]
    assert {r["产品"] for r in rows} == set(ds.products_list())
    # 差异按 |差异率%| 降序
    rates = [abs(r["差异率%"]) for r in rows]
    assert rates == sorted(rates, reverse=True)


# ============================================================ 差异率计算
def test_diff_table_row_formula_from_table_values():
    """差异额 = 一厂 - 二厂；差异率% = 差异额 / 二厂 × 100（用表内值重算，误差<0.02pp）。"""
    for row in result()["差异对比表"]:
        expect_diff = row["一厂单位成本"] - row["二厂单位成本"]
        assert abs(row["差异额"] - expect_diff) < 1e-9, \
            f"{row['产品']} 差异额 {row['差异额']} != 一厂-二厂 {expect_diff}"

        expect_rate = row["差异额"] / row["二厂单位成本"] * 100
        assert abs(row["差异率%"] - expect_rate) < 0.02, \
            f"{row['产品']} 差异率 {row['差异率%']} != 重算 {expect_rate}"


def test_diff_table_row_formula_from_raw_data():
    """与原始 CSV 的未舍入值逐行核对（单位成本与三要素差异率）。"""
    for row in result()["差异对比表"]:
        p = row["产品"]
        a, b = ds.row(MONTH, p), ds.row(MONTH, p, factory="f2")
        assert a is not None and b is not None

        assert abs(row["差异额"] - (a["单位成本"] - b["单位成本"])) <= 5e-4, p
        expect = (a["单位成本"] - b["单位成本"]) / b["单位成本"] * 100
        assert abs(row["差异率%"] - expect) < 0.01, f"{p}: {row['差异率%']} != {expect}"

        for key, ele in (("材料差异率%", "直接材料"), ("人工差异率%", "直接人工"),
                         ("制造费用差异率%", "制造费用")):
            expect = (a[ele] - b[ele]) / b[ele] * 100
            assert abs(row[key] - expect) < 0.01, f"{p} {key}: {row[key]} != {expect}"


def test_diff_rate_independent_recomputation():
    """独立对拍：直接用 CSV 重算差异率，断言 benchmark 结果完全一致（≤0.01pp）。"""
    import pandas as pd
    from app.paths import official_data_dir

    src = os.path.join(official_data_dir(), "01_成本明细数据")
    f1 = pd.read_csv(os.path.join(src, "中药一厂_成本汇总_2026年1-6月.csv"), encoding="utf-8-sig")
    f2 = pd.read_csv(os.path.join(src, "中药二厂_成本汇总_2026年1-6月.csv"), encoding="utf-8-sig")
    f1 = f1[f1["月份"] == MONTH].set_index("产品名称")
    f2 = f2[f2["月份"] == MONTH].set_index("产品名称")

    for row in result()["差异对比表"]:
        p = row["产品"]
        c1, c2 = float(f1.loc[p, "单位成本(元/盒)"]), float(f2.loc[p, "单位成本(元/盒)"])
        expect = round((c1 - c2) / c2 * 100, 2)
        assert abs(row["差异率%"] - expect) < 1e-9, f"{p}: {row['差异率%']} != {expect}"


# ============================================================ 结构树
def test_structure_tree_main_factor_is_max_abs_rate():
    tree = result()["结构树"]
    assert tree["结构拆解"], "结构拆解不应为空"
    top = max(tree["结构拆解"], key=lambda x: abs(x["差异率%"]))
    assert tree["主因要素"] == top["要素"], \
        f"主因要素 {tree['主因要素']} 应为 |差异率%| 最大者 {top['要素']}"


def test_structure_tree_product_is_worst_diff_product():
    """未指定产品时，结构树应聚焦差异率绝对值最大的产品。"""
    rows = result()["差异对比表"]
    tree = result()["结构树"]
    assert tree["产品"] == rows[0]["产品"]
    assert abs(tree["单位成本差异率%"] - rows[0]["差异率%"]) < 1e-9


def test_structure_tree_with_explicit_product():
    """指定产品时结构树聚焦该产品（专项对标场景）。"""
    r = bm.benchmark(MONTH, product="六味地黄胶囊", use_llm=False)
    tree = r["结构树"]
    assert tree["产品"] == "六味地黄胶囊"
    top = max(tree["结构拆解"], key=lambda x: abs(x["差异率%"]))
    assert tree["主因要素"] == top["要素"]
    row = next(x for x in r["差异对比表"] if x["产品"] == "六味地黄胶囊")
    assert abs(tree["单位成本差异率%"] - row["差异率%"]) < 1e-9
    for item in tree["结构拆解"]:
        key = {"直接材料": "材料差异率%", "直接人工": "人工差异率%", "制造费用": "制造费用差异率%"}[item["要素"]]
        assert abs(item["差异率%"] - row[key]) < 1e-9


# ============================================================ RAG 来源
def test_benchmark_sources_carry_provenance():
    """对标结论必须带知识库来源标注（来源文档 + 相关度）。"""
    r = result()
    assert r["sources"], "sources 不应为空"
    for h in r["sources"]:
        assert {"source", "doc_id", "score", "snippet"} <= set(h)
        assert h["source"] and h["doc_id"]


# ---------------------------------------------------------------- unittest 兼容
try:
    from tests.conftest import make_test_case
except ImportError:                                 # pragma: no cover
    from conftest import make_test_case             # type: ignore

TestBenchmark = make_test_case(globals())


if __name__ == "__main__":                       # pragma: no cover
    import unittest

    unittest.main(verbosity=2)
