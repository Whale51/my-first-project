# -*- coding: utf-8 -*-
"""数据服务（官方模拟数据版）：成本汇总(2025+2026)/预算/原材料/制造费用/人工工时/药材行情"""
import os
import pandas as pd

from .paths import official_data_dir

SRC = official_data_dir()

F = {
    "f1_2026": "01_成本明细数据/中药一厂_成本汇总_2026年1-6月.csv",
    "f1_2025": "01_成本明细数据/中药一厂_成本汇总_2025年1-6月.csv",
    "f2_2026": "01_成本明细数据/中药二厂_成本汇总_2026年1-6月.csv",
    "f2_2025": "01_成本明细数据/中药二厂_成本汇总_2025年1-6月.csv",
    "material": "01_成本明细数据/中药一厂_原材料消耗明细_2026年1-6月.csv",
    "oh": "01_成本明细数据/中药一厂_制造费用明细_2026年1-6月.csv",
    "labor": "01_成本明细数据/中药一厂_人工工时明细_2026年1-6月.csv",
    "budget": "01_成本明细数据/中药一厂_预算数据_2026年.csv",
    "price": "02_行业参考数据/药材市场价格行情_2026年上半年.csv",
}
ELES = ["直接材料", "直接人工", "制造费用"]
OH_CATS = {"折旧费": "折旧", "动力费(水电气)": "动力", "人工(间接)": "间接人工", "检验费": "检验", "其他": "其他"}


def _load(rel):
    return pd.read_csv(os.path.join(SRC, F[rel]), encoding="utf-8-sig")


class DataService:
    def __init__(self):
        self.d = {k: _load(k) for k in F}
        self.months = sorted(self.d["f1_2026"]["月份"].unique().tolist())
        self.products = sorted(self.d["f1_2026"]["产品名称"].unique().tolist())
        self.spec = {p: g["产品规格"].iloc[0] for p, g in self.d["f1_2026"].groupby("产品名称")}
        # 行情表转置为 {药材: {"1".."6": 价格, ...}}
        self.price = {}
        for _, r in self.d["price"].iterrows():
            self.price[r["药材名称"]] = {
                "规格": r["规格等级"], "单位": r["单位"], "来源": r["价格来源"], "趋势": r["趋势分析"],
                **{str(i): float(r[f"{i}月价格"]) for i in range(1, 7)},
            }

    # ---------- 基础 ----------
    def _find(self, df, month, product):
        q = df[df["月份"] == month]
        if product:
            q = q[q["产品名称"] == product]
        return q

    def row(self, month, product=None, factory="f1", year="2026"):
        q = self._find(self.d[f"{factory}_{year}"], month, product)
        if not len(q):
            return None
        if product:
            r = q.iloc[0]
            return {"产量": float(r["产量(盒)"]), "直接材料": float(r["直接材料(元/盒)"]),
                    "直接人工": float(r["直接人工(元/盒)"]), "制造费用": float(r["制造费用(元/盒)"]),
                    "单位成本": float(r["单位成本(元/盒)"]), "总成本": float(r["总成本(元)"])}
        # 全部产品：按产量加权平均（CSV 中材料/人工/制造费用均为"元/盒"单价口径）
        q = q.copy()
        qty = q["产量(盒)"].astype(float)
        tot_q = float(qty.sum())
        if tot_q == 0:
            return None
        tc = float(q["总成本(元)"].sum())
        return {"产量": tot_q,
                "直接材料": float((q["直接材料(元/盒)"].astype(float) * qty).sum()) / tot_q,
                "直接人工": float((q["直接人工(元/盒)"].astype(float) * qty).sum()) / tot_q,
                "制造费用": float((q["制造费用(元/盒)"].astype(float) * qty).sum()) / tot_q,
                "单位成本": tc / tot_q, "总成本": tc}

    def budget_row(self, month, product=None):
        q = self._find(self.d["budget"], month, product)
        if not len(q):
            return None
        if product:
            r = q.iloc[0]
            return {"产量": float(r["预算产量(盒)"]), "直接材料": float(r["预算直接材料(元/盒)"]),
                    "直接人工": float(r["预算直接人工(元/盒)"]), "制造费用": float(r["预算制造费用(元/盒)"]),
                    "单位成本": float(r["预算单位成本(元/盒)"]), "总成本": float(r["预算总成本(元)"])}
        qb = q.copy()
        qty = qb["预算产量(盒)"].astype(float)
        tot_q = float(qty.sum())
        if tot_q == 0:
            return None
        return {"产量": tot_q,
                "直接材料": float((qb["预算直接材料(元/盒)"].astype(float) * qty).sum()) / tot_q,
                "直接人工": float((qb["预算直接人工(元/盒)"].astype(float) * qty).sum()) / tot_q,
                "制造费用": float((qb["预算制造费用(元/盒)"].astype(float) * qty).sum()) / tot_q,
                "单位成本": float(qb["预算总成本(元)"].sum()) / tot_q,
                "总成本": float(qb["预算总成本(元)"].sum())}

    def prev_month(self, month):
        i = self.months.index(month)
        return self.months[i - 1] if i > 0 else None

    @staticmethod
    def _rate(cur, base):
        if base in (None, 0) or cur is None:
            return None
        return round((cur - base) / base * 100, 2)

    # ---------- KPI ----------
    def kpi_overview(self, month, product=None):
        cur = self.row(month, product)
        pm = self.prev_month(month)
        prev = self.row(pm, product) if pm else None
        ym = month.replace("2026", "2025")
        yoy = self.row(ym, product, year="2025")
        bud = self.budget_row(month, product)

        eles = {}
        for e in ELES:
            cv = cur[e]
            pv = prev[e] if prev else None
            bv = bud[e] if bud and bud[e] is not None else None
            dv = (cv - pv) if pv is not None else 0.0
            duc = (cur["单位成本"] - prev["单位成本"]) if prev else 0.0
            eles[e] = {"本月": round(cv, 3), "上月": round(pv, 3) if pv is not None else None,
                       "预算": round(bv, 3) if bv is not None else None,
                       "环比": self._rate(cv, pv), "预算偏差": self._rate(cv, bv),
                       "变动额_单盒": round(dv, 3),
                       "贡献度": round(dv / duc * 100, 1) if prev and duc else None}
        return {
            "月份": month, "产品": product or "全部产品",
            "产量": cur["产量"], "上月产量": prev["产量"] if prev else None,
            "预算产量": bud["产量"] if bud else None,
            "总成本": cur["总成本"], "上月总成本": prev["总成本"] if prev else None,
            "预算总成本": bud["总成本"] if bud else None,
            "单位成本": cur["单位成本"], "上月单位成本": prev["单位成本"] if prev else None,
            "去年单位成本": yoy["单位成本"] if yoy else None,
            "去年总成本": yoy["总成本"] if yoy else None, "去年同月产量": yoy["产量"] if yoy else None,
            "总成本环比": self._rate(cur["总成本"], prev["总成本"] if prev else None),
            "总成本同比": self._rate(cur["总成本"], yoy["总成本"] if yoy else None),
            "总成本预算偏差": self._rate(cur["总成本"], bud["总成本"] if bud else None),
            "产量环比": self._rate(cur["产量"], prev["产量"] if prev else None),
            "产量同比": self._rate(cur["产量"], yoy["产量"] if yoy else None),
            "产量预算偏差": self._rate(cur["产量"], bud["产量"] if bud else None),
            "环比变动率": self._rate(cur["单位成本"], prev["单位成本"] if prev else None),
            "同比变动率": self._rate(cur["单位成本"], yoy["单位成本"] if yoy else None),
            "预算偏差": self._rate(cur["单位成本"], bud["单位成本"] if bud else None),
            "贡献度": [{"要素": e, "本月": v["本月"], "上月": v["上月"], "变动额": v["变动额_单盒"],
                       "环比": v["环比"], "贡献度": v["贡献度"]} for e, v in eles.items()],
            "_eles": eles,
        }

    # ---------- 原材料 ----------
    def material_rows(self, month, product=None):
        q = self.d["material"]
        q = q[q["月份"] == month]
        if product:
            q = q[q["产品名称"] == product]
        return q

    def material_diff(self, month, product=None):
        """原材料单位消耗成本环比（元/盒口径），并关联行情价格环比"""
        pm = self.prev_month(month)
        if not pm:
            return []
        cur, base = self.material_rows(month, product), self.material_rows(pm, product)
        rows = []
        for _, r in cur.iterrows():
            b = base[base["原材料名称"] == r["原材料名称"]]
            if not len(b):
                continue
            b = b.iloc[0]
            cu, bu = float(r["单位消耗成本(元/盒)"]), float(b["单位消耗成本(元/盒)"])
            dv_unit = round(cu - bu, 3)
            dv_total = round(dv_unit * float(r["产量(盒)"]), 2)
            if abs(dv_total) < 100:
                continue
            row = {"产品": r["产品名称"], "药材": r["原材料名称"],
                   "单耗_本月": cu, "单耗_上月": bu,
                   "单价_本月": None, "单价_上月": None, "单价变动": None,
                   "单耗变动": self._rate(cu, bu),
                   "变动额_单盒": dv_unit, "变动额": dv_total}
            # 行情价格（原材料名与药材名可能不完全一致，做包含匹配）
            p = self._match_price(r["原材料名称"])
            if p:
                mi = int(pm.split("-")[1])
                row["单价_本月"], row["单价_上月"] = p[str(mi + 1)], p[str(mi)]
                row["单价变动"] = self._rate(p[str(mi + 1)], p[str(mi)])
            rows.append(row)
        rows.sort(key=lambda x: -abs(x["变动额"]))
        return rows

    def _match_price(self, name):
        if name in self.price:
            return self.price[name]
        for k, v in self.price.items():
            if name in k or k in name:
                return v
        return None

    # ---------- 制造费用 ----------
    def oh_detail(self, month, product=None):
        """返回(五类费用明细, 合计本月, 合计上月)，单位：总额(元)"""
        pm = self.prev_month(month)
        df = self.d["oh"]
        cur = df[df["月份"] == month]
        base = df[df["月份"] == pm] if pm else pd.DataFrame()
        if product:
            cur = cur[cur["产品名称"] == product]
            base = base[base["产品名称"] == product] if pm else base
        out = []
        for cat in OH_CATS:
            cv = float(cur[cur["费用类别"] == cat]["费用总额(元)"].sum()) if len(cur) else None
            bv = float(base[base["费用类别"] == cat]["费用总额(元)"].sum()) if len(base) else None
            out.append({"类别": cat, "本月": round(cv, 2), "上月": round(bv, 2) if bv else None,
                        "环比": self._rate(cv, bv)})
        total_c = sum(r["本月"] for r in out)
        total_b = sum(r["上月"] for r in out if r["上月"] is not None) if pm else None
        return out, total_c, total_b

    def oh_detail_unit(self, month, product):
        """单位口径(元/盒)的五类费用"""
        pm = self.prev_month(month)
        df = self.d["oh"]
        cur = df[(df["月份"] == month) & (df["产品名称"] == product)]
        base = df[(df["月份"] == pm) & (df["产品名称"] == product)] if pm else pd.DataFrame()
        out = []
        for cat in OH_CATS:
            cv = float(cur[cur["费用类别"] == cat]["单位费用(元/盒)"].sum()) if len(cur) else None
            bv = float(base[base["费用类别"] == cat]["单位费用(元/盒)"].sum()) if len(base) else None
            out.append({"类别": cat, "本月": round(cv, 3), "上月": round(bv, 3) if bv else None,
                        "环比": self._rate(cv, bv)})
        return out

    # ---------- 人工工时 ----------
    def labor_detail(self, month, product=None):
        pm = self.prev_month(month)
        df = self.d["labor"]
        cur = self._find(df, month, product)
        base = self._find(df, pm, product) if pm else pd.DataFrame()
        if not len(cur):
            return None

        def derive(r):
            out_put = float(r["产量(盒)"])
            labor = float(r["直接人工总额(元)"])
            hours = float(r["总工时(小时)"])
            return {"产量": out_put, "人工总额": labor,
                    "单位人工成本": round(labor / out_put, 3),
                    "工时_万盒": round(hours / out_put * 10000, 1),
                    "时薪": round(labor / hours, 2),
                    "效率": round(out_put / (float(r["生产人数(人)"]) * float(r["工作天数(天)"])), 1)}
        c = derive(cur.iloc[0])
        c["上月"] = derive(base.iloc[0]) if len(base) else None
        return c

    # ---------- 告警 ----------
    def alerts(self, month, threshold=10.0):
        base_m = self.prev_month(month)
        if not base_m:
            return []
        out = []
        for p in self.products:
            cur, base = self.row(month, p), self.row(base_m, p)
            for e in ELES:
                r = self._rate(cur[e], base[e])
                if r is not None and abs(r) >= threshold:
                    out.append({"产品": p, "要素": e + "（单位成本口径）", "环比": r, "阈值": threshold,
                                "本月": cur[e], "上月": base[e]})
            r = self._rate(cur["单位成本"], base["单位成本"])
            if r is not None and abs(r) >= threshold:
                out.append({"产品": p, "要素": "单位成本", "环比": r, "阈值": threshold,
                            "本月": cur["单位成本"], "上月": base["单位成本"]})
            for m in self.material_diff(month, p):
                if m["单价变动"] is not None and abs(m["单价变动"]) >= threshold:
                    out.append({"产品": p, "要素": f"{m['药材']}采购单价", "环比": m["单价变动"],
                                "阈值": threshold, "本月": m["单价_本月"], "上月": m["单价_上月"]})
                if m["单耗变动"] is not None and abs(m["单耗变动"]) >= threshold:
                    out.append({"产品": p, "要素": f"{m['药材']}单位消耗成本", "环比": m["单耗变动"],
                                "阈值": threshold, "本月": m["单耗_本月"], "上月": m["单耗_上月"]})
                # 较年初(1月)行情基准涨幅 ≥ 阈值也告警（对应官方设计的涨价事件）
                pr = self._match_price(m["药材"])
                if pr and "1" in pr:
                    base_p, cur_p = pr["1"], pr[str(int(month.split("-")[1]))]
                    r0 = self._rate(cur_p, base_p)
                    if r0 is not None and abs(r0) >= threshold:
                        out.append({"产品": p, "要素": f"{m['药材']}采购单价（较1月基准）", "环比": r0,
                                    "阈值": threshold, "本月": cur_p, "上月": base_p})
            for c in self.oh_detail_unit(month, p):
                if c["环比"] is not None and abs(c["环比"]) >= threshold:
                    out.append({"产品": p, "要素": c["类别"], "环比": c["环比"], "阈值": threshold,
                                "本月": c["本月"], "上月": c["上月"]})
        return out

    # ---------- 图表 ----------
    def trend(self, product=None):
        rows = []
        for m in self.months:
            r = self.row(m, product)
            rows.append({"月份": m, "总成本": round(r["总成本"], 2), "单位成本": round(r["单位成本"], 3),
                         "直接材料": round(r["直接材料"], 3), "直接人工": round(r["直接人工"], 3),
                         "制造费用": round(r["制造费用"], 3), "产量": r["产量"]})
        return rows

    def structure(self, month, product=None):
        r = self.row(month, product)
        return {e: round(r[e], 3) for e in ELES}

    def heatmap(self):
        out = []
        for m in self.months:
            for p in self.products:
                k = self.kpi_overview(m, p)
                for c in k["贡献度"]:
                    out.append({"月份": m, "产品": p, "要素": c["要素"],
                                "变动额": c["变动额"], "环比": c["环比"]})
        return out

    def months_list(self):
        return self.months

    def products_list(self):
        return self.products

    def spec_of(self, product):
        return self.spec.get(product, "")


ds = DataService()
