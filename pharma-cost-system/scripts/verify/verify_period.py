# -*- coding: utf-8 -*-
"""口径自验脚本：验证「周期聚合（period.py）」与「月度既有口径（data_service.py）」逐项一致。

月度报告统一走 agg_kpi/agg_* 聚合路径，必须与原有 kpi_overview/material_diff/oh_detail_unit/
labor_detail 完全对齐，否则会引入回归。本脚本对全部月份 × 全部产品自动对拍，并打印误差。

    python scripts/verify_period.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app import data_service as dsv          # noqa: E402
from app import period as pd_mod             # noqa: E402

ds = dsv.ds
EPS = 1e-6
fails = []


def chk(tag, a, b, eps=1e-6):
    if a is None and b is None:
        return
    if a is None or b is None:
        fails.append(f"{tag}: 一方为空 {a!r} vs {b!r}")
        return
    if abs(float(a) - float(b)) > eps:
        fails.append(f"{tag}: {a} != {b}")


print("=" * 74)
print("① 月度 KPI：agg_kpi(单月周期)  vs  kpi_overview")
for m in ds.months_list():
    for p in ds.products_list():
        per = pd_mod.build_period("月度成本分析", m)
        a = pd_mod.agg_kpi(per, p)
        b = ds.kpi_overview(m, p)
        for k in ("产量", "总成本", "单位成本", "上月产量", "上月总成本", "上月单位成本",
                  "去年单位成本", "去年总成本", "去年同月产量", "预算产量", "预算总成本",
                  "总成本环比", "总成本同比", "总成本预算偏差", "产量环比", "产量同比",
                  "产量预算偏差", "环比变动率", "同比变动率", "预算偏差"):
            chk(f"[{m}/{p}] KPI.{k}", a[k], b[k], 1e-4)
        for e in dsv.ELES:
            for k in ("本月", "上月", "预算", "环比", "预算偏差", "变动额_单盒", "贡献度"):
                chk(f"[{m}/{p}] {e}.{k}", a["_eles"][e][k], b["_eles"][e][k], 1e-4)
print("   KPI 对拍完成")

print("② 原材料差异：agg_material_diff  vs  material_diff")
for m in ds.months_list():
    for p in ds.products_list():
        per = pd_mod.build_period("月度成本分析", m)
        a = {r["药材"]: r for r in pd_mod.agg_material_diff(per, p)}
        b = {r["药材"]: r for r in ds.material_diff(m, p)}
        if set(a) != set(b):
            fails.append(f"[{m}/{p}] 材料集合不一致 {set(a) ^ set(b)}")
        for name in set(a) & set(b):
            for k in ("单耗_本月", "单耗_上月", "单耗变动", "变动额_单盒", "变动额", "单价_本月", "单价_上月", "单价变动"):
                chk(f"[{m}/{p}] {name}.{k}", a[name][k], b[name][k], 1e-4)
print("   材料差异对拍完成")

print("③ 制造费用：agg_oh_unit  vs  oh_detail_unit")
for m in ds.months_list():
    for p in ds.products_list():
        per = pd_mod.build_period("月度成本分析", m)
        a = {r["类别"]: r for r in pd_mod.agg_oh_unit(per, p)}
        b = {r["类别"]: r for r in ds.oh_detail_unit(m, p)}
        for cat in b:
            for k in ("本月", "上月", "环比"):
                chk(f"[{m}/{p}] {cat}.{k}", a[cat][k], b[cat][k], 1e-4)
print("   制造费用对拍完成")

print("④ 人工工时：agg_labor  vs  labor_detail")
for m in ds.months_list():
    for p in ds.products_list():
        per = pd_mod.build_period("月度成本分析", m)
        a = pd_mod.agg_labor(per, p)
        b = ds.labor_detail(m, p)
        if (a is None) != (b is None):
            fails.append(f"[{m}/{p}] labor 空值不一致")
            continue
        if a:
            for k in ("单位人工成本", "工时_万盒", "时薪", "效率"):
                chk(f"[{m}/{p}] labor.{k}", a[k], b[k], 1e-3)
            if a.get("上月") and b.get("上月"):
                for k in ("单位人工成本", "工时_万盒", "时薪", "效率"):
                    chk(f"[{m}/{p}] labor.prev.{k}", a["上月"][k], b["上月"][k], 1e-3)
print("   人工工时对拍完成")

print("⑤ 季度聚合基本校验（Q2 2026 = 4/5/6 月）")
per = pd_mod.build_period("季度成本分析", "2026-05")
kpi_q = pd_mod.agg_kpi(per, ds.products_list()[0])
manual_qty = sum(ds.row(m, ds.products_list()[0])["产量"] for m in ("2026-04", "2026-05", "2026-06"))
chk("季度产量", kpi_q["产量"], manual_qty, 1e-6)
print(f"   期间={per['label']} 月份={per['months']} 上期={per['prev_months']} 去年同期={per['yoy_months']}")
print(f"   季度产量={kpi_q['产量']:,.0f} 单位成本={kpi_q['单位成本']:.4f} 环比={kpi_q['环比变动率']}")

print("=" * 74)
if fails:
    print(f"❌ 发现 {len(fails)} 处口径不一致：")
    for f in fails[:40]:
        print("   -", f)
    sys.exit(1)
print("✅ 全部口径一致：月度聚合路径与既有 data_service 月度口径逐项相等")
