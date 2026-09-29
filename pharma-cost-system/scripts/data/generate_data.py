# -*- coding: utf-8 -*-
"""生成赛题模拟数据：成本明细、对标工厂数据、药材价格、预算"""
import csv, os, random
random.seed(42)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data")
os.makedirs(DATA, exist_ok=True)

MONTHS = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"]
PRODUCTS = ["益气口服液", "清热颗粒", "安神胶囊"]

# 材料单耗(kg/盒) 与采购价(元/kg) 基准
MATERIALS = {
    "益气口服液": [("当归", 0.35, 12.5), ("黄芪", 0.28, 9.8), ("党参", 0.22, 15.2)],
    "清热颗粒": [("金银花", 0.30, 28.0), ("连翘", 0.25, 19.5), ("板蓝根", 0.20, 11.0)],
    "安神胶囊": [("酸枣仁", 0.18, 45.0), ("茯苓", 0.24, 13.5), ("远志", 0.15, 22.0)],
}
# 价格扰动: 6月当归+12%（触发告警），金银花4月起+18%，其余小幅波动
PRICE_SHOCK_F1 = {
    ("当归", "2026-06"): 1.18, ("当归", "2026-05"): 1.04,
    ("金银花", "2026-04"): 1.25, ("金银花", "2026-05"): 1.25, ("金银花", "2026-06"): 1.25,
}
# 单耗扰动: 清热颗粒提取收率下降 -> 连翘单耗5-6月上升
USAGE_SHOCK_F1 = {("连翘", "2026-05"): 1.12, ("连翘", "2026-06"): 1.12}

OUTPUT_BASE = {
    "益气口服液": 42000, "清热颗粒": 36000, "安神胶囊": 28000,
}
LABOR_UNIT = {"益气口服液": 1.35, "清热颗粒": 1.10, "安神胶囊": 1.60}   # 元/盒
OH_UNIT = {"益气口服液": 1.05, "清热颗粒": 0.92, "安神胶囊": 1.30}      # 元/盒
BUDGET_UNIT = {"益气口服液": 13.2, "清热颗粒": 15.8, "安神胶囊": 16.4}   # 预算单位成本


def gen_factory(tag, price_factor, usage_factor, seed_offset=0):
    rows_detail, rows_material, rows_budget = [], [], []
    for m_i, month in enumerate(MONTHS):
        seasonal = 1.0 + 0.01 * ((m_i * 7 + seed_offset) % 5 - 2) / 2.0
        for p in PRODUCTS:
            output = int(OUTPUT_BASE[p] * (0.92 + 0.03 * ((m_i + seed_offset) % 4)))
            mat_cost = 0.0
            for (mat, unit_use, price) in MATERIALS[p]:
                use = unit_use * usage_factor * seasonal
                pr = price * price_factor * (1 + 0.004 * (((m_i * 3 + seed_offset) % 3) - 1))
                key = (mat, month)
                pr *= PRICE_SHOCK_F1.get(key, 1.0) if tag == "f1" else 1.0
                use *= USAGE_SHOCK_F1.get(key, 1.0) if tag == "f1" else 1.0
                mat_cost += output * use * pr
                rows_material.append([month, p, mat, round(use, 4), round(pr, 2)])
            labor = output * LABOR_UNIT[p] * (1 + 0.006 * ((m_i + seed_offset) % 3))
            oh = output * OH_UNIT[p] * (1 + 0.008 * ((m_i * 2 + seed_offset) % 3))
            unit = (mat_cost + labor + oh) / output
            rows_detail.append([month, p, output, round(mat_cost, 2), round(labor, 2),
                                round(oh, 2), round(unit, 3)])
            rows_budget.append([month, p, BUDGET_UNIT[p] * (1 + 0.01 * (m_i % 3))])
    return rows_detail, rows_material, rows_budget


def write(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


h_detail = ["月份", "产品", "产量_盒", "直接材料_元", "直接人工_元", "制造费用_元", "单位成本_元每盒"]
h_mat = ["月份", "产品", "药材", "单耗_kg每盒", "采购价_元每kg"]
h_budget = ["月份", "产品", "预算单位成本_元每盒"]

d1, m1, b1 = gen_factory("f1", 1.0, 1.0)
d2, m2, b2 = gen_factory("f2", 0.94, 0.97, seed_offset=1)  # 二厂整体略优

write(os.path.join(DATA, "cost_detail_factory1.csv"), h_detail, d1)
write(os.path.join(DATA, "cost_detail_factory2.csv"), h_detail, d2)
write(os.path.join(DATA, "material_usage_factory1.csv"), h_mat, m1)
write(os.path.join(DATA, "material_usage_factory2.csv"), h_mat, m2)
write(os.path.join(DATA, "budget_factory1.csv"), h_budget, b1)

print("data files written to", DATA)
