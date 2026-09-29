# -*- coding: utf-8 -*-
"""period（分析周期抽象）测试。

覆盖：
  * build_period('月度成本分析', '2026-06') → months=['2026-06']、label='2026-06'
  * build_period('季度成本分析', '2026-05') → 当季 3 个月 / 上季 3 个月 / 去年同期 3 个月
  * agg_kpi(月度周期, p) 与 data_service.kpi_overview(month, p) 逐字段一致
    （月份 × 产品全组合，关键字段误差 < 1e-6）

运行：python -m pytest tests/test_period.py -q
      python -m unittest tests.test_period -v
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app import period as pdm                       # noqa: E402
from app.data_service import ds                     # noqa: E402

# agg_kpi 额外带 _period 元信息，与 data_service.kpi_overview 对比时忽略
_EXTRA_KEYS = {"_period"}

# 允许有浮点尾差的字段
_FLOAT_KEYS = {"产量", "总成本", "单位成本", "上月产量", "预算产量", "上月总成本",
               "预算总成本", "上月单位成本", "去年单位成本", "去年总成本", "去年同月产量"}


# ============================================================ build_period
def test_build_period_monthly():
    p = pdm.build_period("月度成本分析", "2026-06")
    assert p["months"] == ["2026-06"], p["months"]
    assert p["label"] == "2026-06", p["label"]
    assert p["kind"] == "月度"
    assert p["prev_months"] == ["2026-05"]
    assert p["yoy_months"] == ["2025-06"]
    assert p["budget_months"] == ["2026-06"]
    assert p["span"] == "2026-06"
    assert p["year"] == 2026 and p["quarter"] == 2


def test_build_period_quarterly_2026_05():
    """2026-05 属于第二季度 → 当季 4/5/6 月，上季 1/2/3 月，去年同期 4/5/6 月。"""
    p = pdm.build_period("季度成本分析", "2026-05")
    assert p["months"] == ["2026-04", "2026-05", "2026-06"], p["months"]
    assert p["prev_months"] == ["2026-01", "2026-02", "2026-03"], p["prev_months"]
    assert p["yoy_months"] == ["2025-04", "2025-05", "2025-06"], p["yoy_months"]
    assert p["kind"] == "季度"
    assert p["quarter"] == 2 and p["year"] == 2026
    assert p["label"] == "2026年第二季度", p["label"]
    assert p["span"] == "2026-04 至 2026-06"


def test_build_period_quarterly_first_quarter_prev_is_last_year_q4():
    """第一季度：上一季度应落到上一年第四季度（数据不存在时被 _available 过滤为空）。"""
    p = pdm.build_period("季度成本分析", "2026-02")
    assert p["months"] == ["2026-01", "2026-02", "2026-03"]
    assert p["quarter"] == 1
    # 2025Q4 不在数据集中（只有 1-6 月），应按"可用月份"过滤
    assert p["prev_months"] == [], p["prev_months"]


# ============================================================ agg_kpi 一致性
def _assert_kpi_equal(a, b, ctx=""):
    """逐字段比对两个 KPI 字典。"""
    assert set(a) - _EXTRA_KEYS == set(b), f"{ctx} 键不一致：{set(a) ^ set(b)}"
    for key in b:
        va, vb = a[key], b[key]
        if isinstance(vb, (int, float)) and not isinstance(vb, bool):
            if key in _FLOAT_KEYS or isinstance(vb, float):
                assert abs(float(va) - float(vb)) < 1e-6, f"{ctx} {key}: {va} != {vb}"
            else:
                assert va == vb, f"{ctx} {key}: {va} != {vb}"
        elif isinstance(vb, list):
            assert len(va) == len(vb), f"{ctx} {key} 长度不一致"
            for i, (x, y) in enumerate(zip(va, vb)):
                assert set(x) == set(y), f"{ctx} {key}[{i}] 键不一致"
                for kk in y:
                    if isinstance(y[kk], float):
                        assert abs(x[kk] - y[kk]) < 1e-6, f"{ctx} {key}[{i}].{kk}: {x[kk]} != {y[kk]}"
                    else:
                        assert x[kk] == y[kk], f"{ctx} {key}[{i}].{kk}: {x[kk]} != {y[kk]}"
        else:
            assert va == vb, f"{ctx} {key}: {va} != {vb}"


def test_agg_kpi_monthly_matches_kpi_overview_all_combinations():
    """月份 × 产品全组合：月度周期聚合结果与 data_service 月度口径逐字段一致。"""
    combos = [(m, p) for m in ds.months_list() for p in ds.products_list()]
    combos.append((ds.months_list()[-1], None))          # 全部产品口径
    for month, product in combos:
        p = pdm.build_period("月度成本分析", month)
        agg = pdm.agg_kpi(p, product)
        direct = ds.kpi_overview(month, product)
        _assert_kpi_equal(agg, direct, f"{month}/{product}")


def test_agg_kpi_quarterly_uses_weighted_average():
    """季度聚合口径校验：单位成本 == 总成本/总产量（产量加权平均）。"""
    p = pdm.build_period("季度成本分析", "2026-06")
    for product in ds.products_list() + [None]:
        kpi = pdm.agg_kpi(p, product)
        assert kpi["月份"] == "2026年第二季度"
        assert abs(kpi["单位成本"] - kpi["总成本"] / kpi["产量"]) < 1e-6, product
        # 产量应为当季三个月之和
        manual_qty = sum(ds.row(m, product)["产量"] for m in p["months"])
        assert abs(kpi["产量"] - manual_qty) < 1e-6, product


def test_agg_row_weighted_average():
    """agg_row 的要素单价必须是产量加权平均。"""
    months = ["2026-04", "2026-05", "2026-06"]
    product = "六味地黄胶囊"
    agg = pdm.agg_row(months, product)
    rows = [ds.row(m, product) for m in months]
    qty = sum(r["产量"] for r in rows)
    for key in ("直接材料", "直接人工", "制造费用"):
        manual = sum(r[key] * r["产量"] for r in rows) / qty
        assert abs(agg[key] - manual) < 1e-6, f"{key}: {agg[key]} != {manual}"
    assert abs(agg["单位成本"] - agg["总成本"] / agg["产量"]) < 1e-9


# ---------------------------------------------------------------- unittest 兼容
try:
    from tests.conftest import make_test_case
except ImportError:                                 # pragma: no cover
    from conftest import make_test_case             # type: ignore

TestPeriod = make_test_case(globals())


if __name__ == "__main__":                       # pragma: no cover
    import unittest

    unittest.main(verbosity=2)
