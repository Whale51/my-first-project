# -*- coding: utf-8 -*-
"""data_service 指标计算测试。

覆盖：
  * row() 单产品 / 全部产品返回字段完整性
  * 全部产品口径必须是"产量加权平均"（单位成本 == 总成本/总产量，且与按产量加权求和一致）
  * _rate() 的边界行为（base=0 / base=None / cur=None → None）
  * kpi_overview() 的环比/同比/预算偏差与手工公式一致，贡献度三项之和 ≈ 100
  * alerts() 覆盖官方埋设的涨价事件（金银花 / 板蓝根）

运行：python -m pytest tests/test_data_service.py -q
      python -m unittest tests.test_data_service -v
"""
import glob
import os
import sys

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.data_service import DataService          # noqa: E402
from app.paths import official_data_dir           # noqa: E402

ds = DataService()
SRC = official_data_dir()
MONTH = "2026-06"
PRODUCT = "六味地黄胶囊"
ALL_FIELDS = {"产量", "直接材料", "直接人工", "制造费用", "单位成本", "总成本"}


def _raw(factory="一厂", year=2026):
    """直接读官方 CSV，作为与 data_service 对拍的独立数据源。"""
    path = os.path.join(SRC, "01_成本明细数据", f"中药{factory}_成本汇总_{year}年1-6月.csv")
    return pd.read_csv(path, encoding="utf-8-sig")


def _close(a, b, tol=1e-6):
    return abs(float(a) - float(b)) <= tol


# ============================================================ row()
def test_row_single_product_fields():
    """单产品口径返回完整字段。"""
    r = ds.row(MONTH, PRODUCT)
    assert r is not None, f"{MONTH} {PRODUCT} 应有数据"
    assert ALL_FIELDS <= set(r), f"字段缺失：{ALL_FIELDS - set(r)}"
    for k in ALL_FIELDS:
        assert isinstance(r[k], float), f"{k} 应为 float，实际 {type(r[k])}"
    assert r["产量"] > 0 and r["总成本"] > 0 and r["单位成本"] > 0

    # 与原始 CSV 逐字段核对
    raw = _raw()
    row = raw[(raw["月份"] == MONTH) & (raw["产品名称"] == PRODUCT)].iloc[0]
    assert _close(r["产量"], row["产量(盒)"])
    assert _close(r["直接材料"], row["直接材料(元/盒)"])
    assert _close(r["直接人工"], row["直接人工(元/盒)"])
    assert _close(r["制造费用"], row["制造费用(元/盒)"])
    assert _close(r["单位成本"], row["单位成本(元/盒)"])
    assert _close(r["总成本"], row["总成本(元)"])


def test_row_all_products_fields_and_weighted_average():
    """全部产品口径：字段完整 + 必须是产量加权平均。"""
    r = ds.row(MONTH, None)
    assert r is not None
    assert ALL_FIELDS <= set(r), f"字段缺失：{ALL_FIELDS - set(r)}"

    raw = _raw()
    q = raw[raw["月份"] == MONTH]
    qty_total = float(q["产量(盒)"].sum())
    assert _close(r["产量"], qty_total), "产量应为各产品产量之和"
    assert _close(r["总成本"], float(q["总成本(元)"].sum())), "总成本应为各产品总成本之和"

    # 核心口径：单位成本 ≈ 总成本 / 总产量
    assert _close(r["单位成本"], r["总成本"] / r["产量"]), "单位成本必须等于 总成本/总产量"

    # 与"按产量加权求和"的手工结果一致
    for col, key in (("直接材料(元/盒)", "直接材料"), ("直接人工(元/盒)", "直接人工"),
                     ("制造费用(元/盒)", "制造费用"), ("单位成本(元/盒)", "单位成本")):
        manual = float((q[col].astype(float) * q["产量(盒)"].astype(float)).sum()) / qty_total
        assert _close(r[key], manual), f"{key} 加权平均不一致：{r[key]} != {manual}"

    # 反例保护：不是"单价简单平均"
    simple_mean = float(q["单位成本(元/盒)"].mean())
    assert not _close(r["单位成本"], simple_mean, 1e-6), "不得退化为简单算术平均"


def test_row_all_products_weighted_average_for_all_months():
    """所有月份的全部产品口径都满足产量加权平均。"""
    for month in ds.months_list():
        r = ds.row(month, None)
        assert r is not None, f"{month} 全部产品口径不应为空"
        assert _close(r["单位成本"], r["总成本"] / r["产量"]), f"{month} 单位成本口径不符"


def test_row_missing_returns_none():
    """不存在的月份/产品组合返回 None（调用方据此降级）。"""
    assert ds.row("2099-01", PRODUCT) is None
    assert ds.row(MONTH, "不存在的产品") is None


# ============================================================ _rate()
def test_rate_normal():
    assert DataService._rate(110, 100) == 10.0
    assert DataService._rate(90, 100) == -10.0
    assert DataService._rate(100, 100) == 0.0


def test_rate_zero_base_returns_none():
    assert DataService._rate(10, 0) is None
    assert DataService._rate(0, 0) is None


def test_rate_none_base_returns_none():
    assert DataService._rate(10, None) is None


def test_rate_none_cur_returns_none():
    assert DataService._rate(None, 10) is None


# ============================================================ kpi_overview()
def test_kpi_overview_rates_match_manual_formula():
    """环比/同比/预算偏差必须与手工按公式计算的结果一致。

    ⚠ 精度说明（已实测确认，非缺陷但需知悉）：`DataService._rate()` 返回
    `round((cur-base)/base*100, 2)`，即**比率只保留 2 位小数**；而
    `kpi_overview()` 的"环比变动率/同比变动率/预算偏差"传入的是**未舍入**的
    单位成本原值。因此本用例分两级断言：

      1) 复核实现（误差 < 1e-9）：KPI 值 == round(手工公式结果, 2)
         —— 证明比率计算逻辑正确；
      2) 口径一致（误差 < 0.01 个百分点）：KPI 值 == 手工公式结果（用未舍入原值）
         —— 容差来自 round 到 0.01 的固有损失（最大 0.005）。
    """
    kpi = ds.kpi_overview(MONTH, PRODUCT)
    prev = ds.row("2026-05", PRODUCT)
    yoy = ds.row("2025-06", PRODUCT, year="2025")
    bud = ds.budget_row(MONTH, PRODUCT)

    # 手工独立重算（全部使用未舍入原值，来源为独立读取的原始 CSV / data_service.row）
    manual = {
        "环比变动率": (kpi["单位成本"] - prev["单位成本"]) / prev["单位成本"] * 100,
        "同比变动率": (kpi["单位成本"] - yoy["单位成本"]) / yoy["单位成本"] * 100,
        "预算偏差": (kpi["单位成本"] - bud["单位成本"]) / bud["单位成本"] * 100,
        "总成本环比": (kpi["总成本"] - prev["总成本"]) / prev["总成本"] * 100,
        "总成本同比": (kpi["总成本"] - yoy["总成本"]) / yoy["总成本"] * 100,
        "总成本预算偏差": (kpi["总成本"] - bud["总成本"]) / bud["总成本"] * 100,
        "产量环比": (kpi["产量"] - prev["产量"]) / prev["产量"] * 100,
        "产量同比": (kpi["产量"] - yoy["产量"]) / yoy["产量"] * 100,
    }
    for key, expect in manual.items():
        assert kpi[key] is not None, f"{key} 不应为 None"
        assert abs(kpi[key] - round(expect, 2)) < 1e-9, \
            f"{key}: {kpi[key]} != round(手工值 {expect}, 2)"
        assert abs(kpi[key] - expect) < 0.01, \
            f"{key}: {kpi[key]} 与手工值 {expect} 偏差超过舍入精度"

    # 要素口径：本月/上月/预算先 round 到 3 位，比率再 round 到 2 位
    for item in kpi["贡献度"]:
        e = item["要素"]
        cv = kpi["_eles"][e]["本月"]
        prev_c = round(item["上月"], 3)
        bud_c = round(kpi["_eles"][e]["预算"], 3)
        assert abs(item["环比"] - round((cv - prev_c) / prev_c * 100, 2)) < 1e-9, f"{e} 环比不符"
        assert abs(kpi["_eles"][e]["预算偏差"] - round((cv - bud_c) / bud_c * 100, 2)) < 1e-9, \
            f"{e} 预算偏差不符"


def test_kpi_overview_contribution_sums_to_100():
    """三要素贡献度之和 ≈ 100（允许 ±0.5 的舍入误差）。"""
    for month, product in [(MONTH, PRODUCT), ("2026-05", "板蓝根颗粒"), ("2026-06", None)]:
        kpi = ds.kpi_overview(month, product)
        total = sum(c["贡献度"] for c in kpi["贡献度"])
        assert abs(total - 100.0) <= 0.5, f"{month}/{product} 贡献度之和={total}（应≈100）"


def test_kpi_overview_contribution_definition():
    """贡献度 = 该要素单盒变动额 / 单位成本变动额 × 100。"""
    kpi = ds.kpi_overview(MONTH, PRODUCT)
    duc = kpi["单位成本"] - kpi["上月单位成本"]
    assert abs(duc) > 1e-9
    for c in kpi["贡献度"]:
        expect = c["变动额"] / duc * 100
        assert abs(c["贡献度"] - expect) <= 0.05 + 1e-6, f"{c['要素']} 贡献度定义不符"


# ============================================================ alerts()
def test_alerts_2026_05_non_empty_and_contains_price_event():
    """2026-05 必须有告警，且覆盖官方埋设的涨价事件（金银花 / 板蓝根）。"""
    alerts = ds.alerts("2026-05")
    assert isinstance(alerts, list) and alerts, "2026-05 应有告警"
    for a in alerts:
        assert {"产品", "要素", "环比", "阈值", "本月", "上月"} <= set(a)
        assert abs(a["环比"]) >= a["阈值"]

    names = " ".join(a["要素"] for a in alerts)
    assert ("金银花" in names) or ("板蓝根" in names), f"未覆盖埋设涨价事件，实际告警：{names}"


def test_alerts_price_base_shift_calculation():
    """带有「较1月基准」标记的告警，其幅度应与行情表 1月→5月 的手工涨幅一致。"""
    price_csv = os.path.join(SRC, "02_行业参考数据", "药材市场价格行情_2026年上半年.csv")
    price = pd.read_csv(price_csv, encoding="utf-8-sig")
    baseline = {r["药材名称"]: (float(r["1月价格"]), float(r["5月价格"])) for _, r in price.iterrows()}

    checked = 0
    for a in ds.alerts("2026-05"):
        if "较1月基准" not in a["要素"]:
            continue
        herb = a["要素"].replace("采购单价（较1月基准）", "")
        assert herb in baseline, f"{herb} 不在行情表中"
        p1, p5 = baseline[herb]
        expect = (p5 - p1) / p1 * 100
        assert abs(a["环比"] - expect) < 0.02, f"{herb} 基准涨幅 {a['环比']} != 手工 {expect}"
        checked += 1
    assert checked > 0, "应至少有一条「较1月基准」告警"


def test_alerts_first_month_empty():
    """首月无上月可比，告警为空列表（不是 None）。"""
    assert ds.alerts("2026-01") == []


# ---------------------------------------------------------------- unittest 兼容
# pytest 直接收集上面的裸函数；unittest 只认 TestCase，子类由同一批函数动态生成。
try:
    from tests.conftest import make_test_case
except ImportError:                                 # pragma: no cover
    from conftest import make_test_case             # type: ignore

TestDataService = make_test_case(globals())


if __name__ == "__main__":                       # pragma: no cover
    import unittest

    unittest.main(verbosity=2)
