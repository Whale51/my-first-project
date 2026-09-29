# -*- coding: utf-8 -*-
"""分析周期抽象（月度 / 季度）与统一聚合口径。

统一采用 **产量加权平均** 口径：
    单位成本类指标 = Σ(单位成本 × 产量) / Σ产量
    总量类指标     = Σ
月度即"单元素周期"，聚合结果与 `data_service` 的月度口径逐项一致
（已由 `scripts/verify_period.py` 对全部月份/产品自动校验）。

对外提供：
    build_period(kind, month)                 -> 周期描述（月份列表、上期、去年同期、标签）
    agg_kpi(period, product)                  -> 与 data_service.kpi_overview 同构的 KPI 字典
    agg_material_diff / agg_oh_unit / agg_labor
    period_alerts(months)                     -> 周期内告警并集
"""
from . import data_service as dsv

ds = dsv.ds
ELES = dsv.ELES
CN_Q = {1: "第一季度", 2: "第二季度", 3: "第三季度", 4: "第四季度"}


# ---------------- 周期解析 ----------------
def parse_month(month):
    y, m = month.split("-")
    return int(y), int(m)


def quarter_of(year, m):
    return (m - 1) // 3 + 1


def quarter_months(year, q):
    """季度对应的 3 个月份（q 允许 0 → 上一年第 4 季度）"""
    if q < 1:
        year, q = year - 1, 4
    return [f"{year}-{mm:02d}" for mm in range(3 * q - 2, 3 * q + 1)]


def _available(months):
    """过滤出数据集中真实存在的月份（按年份分别校验，2025 年数据用于同比）"""
    out = []
    cache = {}
    for m in months:
        y = int(m[:4])
        if y not in cache:
            df = ds.d.get(f"f1_{y}")
            cache[y] = set(df["月份"].unique().tolist()) if df is not None else set()
        if m in cache[y]:
            out.append(m)
    return out


def build_period(kind="月度成本分析", month=None):
    """根据分析主题与分析月份构造周期描述

    kind 支持 '月度成本分析' / '季度成本分析'（或 '月度' / '季度'）
    """
    month = month or ds.months_list()[-1]
    year, m = parse_month(month)
    is_quarter = "季度" in str(kind) or str(kind) == "季度"
    if is_quarter:
        q = quarter_of(year, m)
        months = _available(quarter_months(year, q))
        prev = _available(quarter_months(year, q - 1))
        yoy = _available(quarter_months(year - 1, q))
        label = f"{year}年{CN_Q[q]}"
        span = f"{months[0]} 至 {months[-1]}" if months else label
        return {"kind": "季度", "kind_label": "季度成本分析", "anchor": month,
                "months": months, "prev_months": prev, "yoy_months": yoy, "budget_months": months,
                "label": label, "span": span, "quarter": q, "year": year,
                "curr_tag": "本期", "prev_tag": "上期"}
    pm = ds.prev_month(month)
    return {"kind": "月度", "kind_label": "月度成本分析", "anchor": month,
            "months": [month], "prev_months": [pm] if pm else [], "yoy_months": [f"{year - 1}-{m:02d}"],
            "budget_months": [month], "label": month, "span": month,
            "quarter": quarter_of(year, m), "year": year,
            "curr_tag": "本月", "prev_tag": "上月"}


# ---------------- 聚合基础 ----------------
def agg_row(months, product=None, factory="f1", year="2026"):
    """跨月份聚合的成本行（产量加权平均口径）"""
    rows = [r for r in (ds.row(mo, product, factory=factory, year=year) for mo in months) if r]
    if not rows:
        return None
    qty = sum(r["产量"] for r in rows)
    if not qty:
        return None
    tot = sum(r["总成本"] for r in rows)
    return {"产量": qty, "总成本": tot, "单位成本": tot / qty,
            "直接材料": sum(r["直接材料"] * r["产量"] for r in rows) / qty,
            "直接人工": sum(r["直接人工"] * r["产量"] for r in rows) / qty,
            "制造费用": sum(r["制造费用"] * r["产量"] for r in rows) / qty}


def agg_budget_row(months, product=None):
    """跨月份聚合的预算行"""
    rows = [r for r in (ds.budget_row(mo, product) for mo in months) if r]
    if not rows:
        return None
    qty = sum(r["产量"] for r in rows)
    if not qty:
        return None
    tot = sum(r["总成本"] for r in rows)
    return {"产量": qty, "总成本": tot, "单位成本": tot / qty,
            "直接材料": sum(r["直接材料"] * r["产量"] for r in rows) / qty,
            "直接人工": sum(r["直接人工"] * r["产量"] for r in rows) / qty,
            "制造费用": sum(r["制造费用"] * r["产量"] for r in rows) / qty}


def agg_kpi(period, product=None):
    """返回与 data_service.kpi_overview 完全同构的 KPI 字典（月度周期结果逐项一致）"""
    cur = agg_row(period["months"], product)
    if cur is None:
        return ds.kpi_overview(period["anchor"], product)
    prev = agg_row(period["prev_months"], product) if period["prev_months"] else None
    yoy = agg_row(period["yoy_months"], product, year=str(int(period["year"]) - 1))
    bud = agg_budget_row(period["budget_months"], product)

    eles = {}
    for e in ELES:
        cv = cur[e]
        pv = prev[e] if prev else None
        bv = bud[e] if bud and bud[e] is not None else None
        dv = (cv - pv) if pv is not None else 0.0
        duc = (cur["单位成本"] - prev["单位成本"]) if prev else 0.0
        eles[e] = {"本月": round(cv, 3), "上月": round(pv, 3) if pv is not None else None,
                   "预算": round(bv, 3) if bv is not None else None,
                   "环比": ds._rate(cv, pv), "预算偏差": ds._rate(cv, bv),
                   "变动额_单盒": round(dv, 3),
                   "贡献度": round(dv / duc * 100, 1) if prev and duc else None}
    return {
        "月份": period["label"], "产品": product or "全部产品",
        "产量": cur["产量"], "上月产量": prev["产量"] if prev else None,
        "预算产量": bud["产量"] if bud else None,
        "总成本": cur["总成本"], "上月总成本": prev["总成本"] if prev else None,
        "预算总成本": bud["总成本"] if bud else None,
        "单位成本": cur["单位成本"], "上月单位成本": prev["单位成本"] if prev else None,
        "去年单位成本": yoy["单位成本"] if yoy else None,
        "去年总成本": yoy["总成本"] if yoy else None, "去年同月产量": yoy["产量"] if yoy else None,
        "总成本环比": ds._rate(cur["总成本"], prev["总成本"] if prev else None),
        "总成本同比": ds._rate(cur["总成本"], yoy["总成本"] if yoy else None),
        "总成本预算偏差": ds._rate(cur["总成本"], bud["总成本"] if bud else None),
        "产量环比": ds._rate(cur["产量"], prev["产量"] if prev else None),
        "产量同比": ds._rate(cur["产量"], yoy["产量"] if yoy else None),
        "产量预算偏差": ds._rate(cur["产量"], bud["产量"] if bud else None),
        "环比变动率": ds._rate(cur["单位成本"], prev["单位成本"] if prev else None),
        "同比变动率": ds._rate(cur["单位成本"], yoy["单位成本"] if yoy else None),
        "预算偏差": ds._rate(cur["单位成本"], bud["单位成本"] if bud else None),
        "贡献度": [{"要素": e, "本月": v["本月"], "上月": v["上月"], "变动额": v["变动额_单盒"],
                    "环比": v["环比"], "贡献度": v["贡献度"]} for e, v in eles.items()],
        "_eles": eles,
        "_period": {"kind": period["kind"], "label": period["label"], "span": period["span"]},
    }


# ---------------- 明细聚合 ----------------
def _material_period(months, product=None):
    """{原材料名: {qty, unit(加权平均 元/盒), total(元)}}"""
    out = {}
    df = ds.d["material"]
    for mo in months:
        q = df[df["月份"] == mo]
        if product:
            q = q[q["产品名称"] == product]
        for _, r in q.iterrows():
            name = r["原材料名称"]
            qty = float(r["产量(盒)"])
            unit = float(r["单位消耗成本(元/盒)"])
            acc = out.setdefault(name, {"qty": 0.0, "wsum": 0.0, "total": 0.0})
            acc["qty"] += qty
            acc["wsum"] += unit * qty
            acc["total"] += float(r["原材料总成本(元)"])
    for v in out.values():
        v["unit"] = round(v["wsum"] / v["qty"], 4) if v["qty"] else 0.0
    return out


def agg_material_diff(period, product=None, min_amount=100.0):
    """原材料单位消耗成本环比（元/盒 口径），结构与 data_service.material_diff 一致"""
    cur = _material_period(period["months"], product)
    base = _material_period(period["prev_months"], product) if period["prev_months"] else {}
    if not base:
        return []
    rows = []
    for name, c in cur.items():
        b = base.get(name)
        if not b:
            continue
        cu, bu = c["unit"], b["unit"]
        dv_unit = round(cu - bu, 3)
        dv_total = round(dv_unit * c["qty"], 2)
        if abs(dv_total) < min_amount:
            continue
        row = {"产品": product or "全部产品", "药材": name,
               "单耗_本月": cu, "单耗_上月": bu,
               "单价_本月": None, "单价_上月": None, "单价变动": None,
               "单耗变动": ds._rate(cu, bu),
               "变动额_单盒": dv_unit, "变动额": dv_total}
        p = ds._match_price(name)
        if p:
            try:
                mi_c = int(period["months"][-1].split("-")[1])
                mi_b = int((period["prev_months"] or period["months"])[-1].split("-")[1])
                if str(mi_c) in p and str(mi_b) in p:
                    row["单价_本月"], row["单价_上月"] = p[str(mi_c)], p[str(mi_b)]
                    row["单价变动"] = ds._rate(p[str(mi_c)], p[str(mi_b)])
            except Exception:
                pass
        rows.append(row)
    rows.sort(key=lambda x: -abs(x["变动额"]))
    return rows


def agg_oh_unit(period, product):
    """单位口径(元/盒)的五类制造费用（周期加权平均），结构同 oh_detail_unit"""
    def _acc(months):
        out = {}
        df = ds.d["oh"]
        for mo in months:
            q = df[(df["月份"] == mo) & (df["产品名称"] == product)]
            for _, r in q.iterrows():
                cat = r["费用类别"]
                qty = float(r["产量(盒)"])
                acc = out.setdefault(cat, {"qty": 0.0, "wsum": 0.0})
                acc["qty"] += qty
                acc["wsum"] += float(r["单位费用(元/盒)"]) * qty
        return {k: (v["wsum"] / v["qty"] if v["qty"] else None) for k, v in out.items()}

    cur, base = _acc(period["months"]), (_acc(period["prev_months"]) if period["prev_months"] else {})
    out = []
    for cat in dsv.OH_CATS:
        # 与既有月度口径保持一致：期间有数据但某类别缺行时本期记 0、上期记 None（保持原样避免回归）
        cv = cur.get(cat, 0.0) if cur else None
        bv = base.get(cat, 0.0) if base else None
        out.append({"类别": cat, "本月": round(cv, 2) if cv is not None else None,
                    "上月": round(bv, 2) if bv else None,
                    "环比": ds._rate(cv, bv)})
    return out


def agg_labor(period, product=None):
    """人工工时派生指标（周期口径），结构同 labor_detail"""
    def _raw(months):
        df = ds.d["labor"]
        rows = []
        for mo in months:
            q = df[df["月份"] == mo]
            if product:
                q = q[q["产品名称"] == product]
            rows.extend(q.to_dict("records"))
        return rows

    def derive(rows):
        if not rows:
            return None
        qty = sum(float(r["产量(盒)"]) for r in rows)
        labor = sum(float(r["直接人工总额(元)"]) for r in rows)
        hours = sum(float(r["总工时(小时)"]) for r in rows)
        people = sum(float(r["生产人数(人)"]) for r in rows) / len(rows)
        days = sum(float(r["工作天数(天)"]) for r in rows)
        if not qty or not hours or not people or not days:
            return None
        return {"产量": qty, "人工总额": labor,
                "单位人工成本": round(labor / qty, 3),
                "工时_万盒": round(hours / qty * 10000, 1),
                "时薪": round(labor / hours, 2),
                "效率": round(qty / (people * days), 1)}

    cur = derive(_raw(period["months"]))
    if not cur:
        return None
    cur["上月"] = derive(_raw(period["prev_months"])) if period["prev_months"] else None
    return cur


def period_alerts(period):
    """周期内告警并集（同产品+同要素取波动最大的一条）"""
    merged = {}
    for mo in period["months"]:
        for a in ds.alerts(mo):
            key = (a["产品"], a["要素"])
            if key not in merged or abs(a["环比"]) > abs(merged[key]["环比"]):
                item = dict(a)
                item["月份"] = mo
                merged[key] = item
    out = list(merged.values())
    out.sort(key=lambda x: -abs(x["环比"]))
    return out


def period_trend(product=None):
    """近 6 个月趋势（图表/表格用）"""
    return ds.trend(product)
