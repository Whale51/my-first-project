# -*- coding: utf-8 -*-
"""报告数据模型：把"模板结构 + 数据填充 + RAG 知识增强 + 大模型分析文本"组装成一份
与渲染格式无关的统一模型，供 Word（模板占位符渲染）与 PDF（原生排版渲染）共用。

模型结构：
{
  "标题" / "副标题" / "报告类型" / "期间" / "产品" / "编制日期" / "编制单位",
  "values":   {占位符名: 文本}          —— 官方 docx 模板的 101 个占位符取值
  "tables":   {key: {表头, 行, 标题}}   —— 6 张动态表格 + 指标表/告警表/来源表
  "charts":   {key: png 路径}           —— 服务端渲染的图表
  "sections": [{标题, 内容:[段落/表格/图]}] —— PDF 原生排版与目录结构
  "sources":  [RAG 检索来源]
  "engine":   "llm" | "rule"
}
"""
import json
import os
import time

from . import data_service as dsv
from . import period as pd_mod
from . import charts as charts_mod
from . import rpa as rpa_mod
from .benchmark import SYSTEM as BENCH_SYSTEM
from .llm import chat, llm_ready
from .paths import official_data_dir
from .rag import kb
from .rca import SYSTEM as RCA_SYSTEM, rule_based_rca

ds = dsv.ds
SRC = official_data_dir()
CAT_KEY = {"折旧费": "折旧", "动力费(水电气)": "动力", "人工(间接)": "间接人工", "检验费": "检验", "其他": "其他"}
编制单位 = "重庆创灵境数字技术有限公司"

SCENES = ["月度成本分析", "季度成本分析", "专题分析"]
TOPICS = ["综合成本分析", "主要原材料价格波动", "制造费用与折旧", "人工成本与效率", "两厂对标差异"]


# ---------------- 格式化 ----------------
def _f(v, nd=3):
    return "—" if v is None else (f"{v:,.{nd}f}" if isinstance(v, float) else str(v))


def _pct(v, signed=True):
    return "—" if v is None else (f"{v:+.2f}" if signed else f"{v:.2f}")


def _pct_s(v, nd=1):
    """带百分号的格式化（原生 PDF 表格用）"""
    return "—" if v is None else f"{v:+.{nd}f}%"


def _pct_u(v, nd=1):
    return "—" if v is None else f"{v:.{nd}f}%"


def _oh_explain(name, rate):
    if rate is None:
        return "—"
    if abs(rate) < 3:
        return "波动正常"
    if name == "折旧费":
        return "折旧为固定成本，产量变化带来摊薄波动" if rate < 0 else "折旧固定，产量下降导致单位摊薄上升"
    if "动力" in name:
        return "产量上升带动动力消耗增加" if rate > 0 else "产量回落，动力消耗减少"
    if "间接" in name or "人工" in name:
        return "间接人工为固定口径，随产量摊薄波动"
    if "检验" in name:
        return "检验批次调整带来检验费波动"
    return "其他制造费用随生产安排正常波动"


def _industry(product):
    """行业基准中与本产品类别相关的条目"""
    cat = {"银黄口服液": "口服液类", "板蓝根颗粒": "颗粒剂类", "六味地黄胶囊": "胶囊剂类"}.get(product, "")
    try:
        import pandas as pd
        df = pd.read_csv(os.path.join(SRC, "02_行业参考数据/行业成本基准数据_2026.csv"), encoding="utf-8-sig")
        return df[df["产品类别"] == cat].to_dict("records")
    except Exception:
        return []


# ---------------- 分析文本（RAG + 大模型） ----------------
def _rca_text(period, product, kpi, mats, alerts, oh, labor, topic=None, use_llm=True):
    """归因分析文本：大模型 + RAG 知识增强，失败自动降级为规则模板"""
    query = f"{period['label']} {product or ''} {topic or ''} 成本上涨 原因 药材 价格 提取 收率 归因"
    ctx, hits = kb.context(query, product_hint=product, top_k=5)
    payload = json.dumps({"期间": period["label"], "周期口径": f"{period['kind']}（{period['span']}）",
                          "KPI": kpi, "材料明细差异": mats[:8], "波动告警": alerts,
                          "制造费用明细(元/盒)": oh, "人工工时指标": labor,
                          "知识库材料": ctx}, ensure_ascii=False, indent=1)
    text, engine = None, "rule"
    if use_llm and llm_ready():
        text = chat(RCA_SYSTEM, payload)
        if text:
            engine = "llm"
    if not text:
        text = rule_based_rca(period["label"], product, kpi, mats, alerts)
    return text, engine, hits


def _bench_diff_rows(period):
    """周期口径的两厂对标差异表"""
    rows = []
    for p in ds.products_list():
        a = pd_mod.agg_row(period["months"], p, factory="f1")
        b = pd_mod.agg_row(period["months"], p, factory="f2")
        if not a or not b:
            continue
        rows.append({
            "产品": p,
            "一厂单位成本": round(a["单位成本"], 3), "二厂单位成本": round(b["单位成本"], 3),
            "差异额": round(a["单位成本"] - b["单位成本"], 3),
            "差异率%": round((a["单位成本"] - b["单位成本"]) / b["单位成本"] * 100, 2),
            "材料差异率%": round((a["直接材料"] - b["直接材料"]) / b["直接材料"] * 100, 2),
            "人工差异率%": round((a["直接人工"] - b["直接人工"]) / b["直接人工"] * 100, 2),
            "制造费用差异率%": round((a["制造费用"] - b["制造费用"]) / b["制造费用"] * 100, 2),
        })
    rows.sort(key=lambda r: -abs(r["差异率%"]))
    return rows


def _bench(period, product, diff_rows, use_llm=True):
    """对标三步法：找差异 → 拆结构 → 拆原因（大模型 + RAG，失败降级规则）"""
    if not diff_rows:
        return {"差异对比表": [], "结构树": {"产品": product, "单位成本差异率%": 0, "结构拆解": [],
                                       "主因要素": "—"}, "归因分析": "本期无对标数据。",
                "改进建议": [], "sources": [], "engine": "rule"}
    focus = next((r for r in diff_rows if r["产品"] == product), diff_rows[0])
    tree = {"产品": focus["产品"], "单位成本差异率%": focus["差异率%"],
            "结构拆解": [{"要素": "直接材料", "差异率%": focus["材料差异率%"]},
                        {"要素": "直接人工", "差异率%": focus["人工差异率%"]},
                        {"要素": "制造费用", "差异率%": focus["制造费用差异率%"]}]}
    tree["主因要素"] = max(tree["结构拆解"], key=lambda x: abs(x["差异率%"]))["要素"]

    ctx, hits = kb.context(f"对标 二厂 人工效率 设备利用率 自采 {tree['产品']}",
                           product_hint=tree["产品"], top_k=4)
    analysis, engine = None, "rule"
    if use_llm and llm_ready():
        analysis = chat(BENCH_SYSTEM, json.dumps(
            {"期间": period["label"], "差异表": diff_rows, "结构树": tree, "知识库": ctx},
            ensure_ascii=False, indent=1))
        if analysis:
            engine = "llm"
    if not analysis:
        month_label = period["label"]
        lines = [f"第一步·找差异：{month_label}{focus['产品']}一厂单位成本{focus['一厂单位成本']}元/盒，"
                 f"二厂{focus['二厂单位成本']}元/盒，差异{focus['差异额']:+}元/盒（{focus['差异率%']:+}%）。"]
        main = tree["主因要素"]
        rate = [s["差异率%"] for s in tree["结构拆解"] if s["要素"] == main][0]
        lines.append(f"第二步·拆结构：差异主要来自{main}（{rate:+}%）。")
        if main == "直接人工":
            lines.append("第三步·拆原因：二厂人工效率与设备利用率偏低（两厂背景），单位人工成本高于一厂；"
                         "建议二厂优化排班与产线平衡，一厂可输出标准作业程序供其借鉴。")
        elif main == "制造费用":
            lines.append("第三步·拆原因：二厂设备旧但折旧低、维修与动力效率差，单位制造费用偏高；"
                         "建议核查二厂动力单耗与停机时间，评估关键设备更新或租赁方案。")
        elif main == "直接材料":
            lines.append("第三步·拆原因：材料差异受采购规模与药材自采结构影响（二厂部分药材自采有成本优势，"
                         "一厂规模采购价格略低）；建议核对两厂采购价与单耗差异，评估集团统一采购的可行性。")
        else:
            lines.append("第三步·拆原因：综合差异有限，建议持续按月跟踪各要素变化趋势。")
        analysis = "\n".join(lines)
    suggestions = [
        {"任务": f"核查{tree['产品']}主要药材两厂采购价差与自采结构，评估集团统一采购", "责任部门": "采购部", "优先级": "高"},
        {"任务": f"排查二厂{tree['产品']}产线人工效率与排班，输出改善计划", "责任部门": "生产部", "优先级": "高"},
        {"任务": "跟踪两厂动力单耗与设备利用率差异", "责任部门": "设备部", "优先级": "中"},
    ]
    return {"差异对比表": diff_rows, "结构树": tree, "归因分析": analysis,
            "改进建议": suggestions, "sources": hits, "engine": engine}


# ---------------- 专题聚焦 ----------------
def _topic_text(topic, period, product, kpi, mats, oh, labor, bench, ind_rows):
    """按专题生成聚焦段落（规则生成，作为大模型分析之外的固定结论锚点）"""
    p = f"{period['label']}{product}" if product else period["label"]
    out = []
    if topic == "主要原材料价格波动":
        rises = [m for m in mats if m.get("单价变动") is not None and m["单价变动"] > 0]
        rises.sort(key=lambda m: -m["单价变动"])
        if rises:
            top = rises[0]
            out.append(f"本期{top['药材']}行情采购价由{top['单价_上月']}元/kg上行至{top['单价_本月']}元/kg"
                       f"（{top['单价变动']:+.2f}%），单位消耗成本{top['单耗_本月']}元/盒，"
                       f"对单位成本影响{top['变动额_单盒']:+.3f}元/盒，为材料端最显著变量。")
        else:
            out.append("本期主要药材行情整体平稳，未见显著价格上行品种。")
        out.append("结合知识库《药材市场价格行情》《生产工艺路线》等资料：中药材价格受产地天气、采收周期与"
                   "集采节奏影响明显，建议对涨幅居前品种建立“价格—库存”双阈值监控，"
                   "并与供应商约定调价触发条款；对单位消耗成本上升的品种同步排查提取收率与投料配比。")
    elif topic == "制造费用与折旧":
        fixed = [c for c in oh if c["类别"] in ("折旧费", "人工(间接)")]
        var = [c for c in oh if c["类别"] not in ("折旧费", "人工(间接)")]
        tf = sum(c["本月"] for c in fixed if c["本月"] is not None)
        tv = sum(c["本月"] for c in var if c["本月"] is not None)
        out.append(f"本期制造费用中固定性费用（折旧 + 间接人工）合计{tf:.3f}元/盒，"
                   f"变动性费用（动力、检验、其他）合计{tv:.3f}元/盒，固定费用占比"
                   f"{(tf / (tf + tv) * 100 if tf + tv else 0):.1f}%。")
        out.append("知识库《车间设备清单》《GMP法规核心摘要》提示：折旧与间接人工为刚性成本，产量不足时单位摊薄"
                   "显著上升；动力费与产量、设备负荷强相关。建议以产量计划驱动费用预算，产量低于计划时优先安排"
                   "设备检修与验证批次，避免固定费用摊薄恶化。")
    elif topic == "人工成本与效率":
        if labor:
            b = labor.get("上月") or {}
            out.append(f"本期单位人工成本{labor['单位人工成本']}元/盒（上期{b.get('单位人工成本', '—')}元/盒），"
                       f"时薪{labor['时薪']}元/小时，人工效率{labor['效率']}盒/人·天，"
                       f"万盒工时{labor['工时_万盒']}小时。")
            out.append("结合《生产工艺文档》《车间设备清单》：人工成本波动主要来自排班与产线平衡，而非时薪刚性上涨；"
                       "建议按品种标准工时核定用工，推行多能工与柔性排班，并对瓶颈工序做动作分析。")
        else:
            out.append("本期人工工时数据不足，建议补充班组工时台账后重新分析。")
    elif topic == "两厂对标差异":
        focus = next((r for r in bench["差异对比表"] if r["产品"] == product), None) or \
            (bench["差异对比表"][0] if bench["差异对比表"] else None)
        if focus:
            out.append(f"本期对标聚焦{focus['产品']}：一厂单位成本{focus['一厂单位成本']}元/盒 vs 二厂"
                       f"{focus['二厂单位成本']}元/盒，差异{focus['差异额']:+}元/盒（{focus['差异率%']:+}%），"
                       f"主因要素为{bench['结构树']['主因要素']}。")
        out.append("三要素拆解显示，材料差异多与采购结构与自采比例相关，人工与制造费用差异更多来自设备利用率与"
                   "排班效率；建议按“同品种、同口径、同期间”固化对标基线，差异超过阈值自动触发专项整改任务。")
    else:
        out.append(f"本期综合成本分析：{_pct_u(kpi['环比变动率'])}环比、{_pct_u(kpi['同比变动率'])}同比、"
                   f"较预算{_pct_u(kpi['预算偏差'])}。")
        if ind_rows:
            out.append("行业基准对比：" + "；".join(
                f"{r['指标']}{r['本厂水平(中药一厂)']}，{r['对标评价']}" for r in ind_rows[:3]) + "。")
    out.append(f"（专题口径：{period['kind']} · {period['span']}）")
    return "\n".join(out)


# ---------------- 主入口 ----------------
def build(month, product=None, scene="月度成本分析", topic=None, use_llm=True, want_charts=True):
    product = product or ds.products_list()[0]
    scene = scene if scene in SCENES else "月度成本分析"
    topic = topic or TOPICS[0]
    period = pd_mod.build_period(scene, month)

    kpi = pd_mod.agg_kpi(period, product)
    mats = pd_mod.agg_material_diff(period, product)
    oh = pd_mod.agg_oh_unit(period, product)
    labor = pd_mod.agg_labor(period, product)
    alerts = pd_mod.period_alerts(period)
    alerts = [a for a in alerts if a["产品"] == product] if scene != "专题分析" else alerts
    budget = pd_mod.agg_budget_row(period["budget_months"], product)

    rca_text, engine, hits = _rca_text(period, product, kpi, mats, alerts, oh, labor, topic, use_llm)
    diff_rows = _bench_diff_rows(period)
    bench = _bench(period, product, diff_rows, use_llm)

    def ele(name):
        return kpi["_eles"][name]

    ohm = {CAT_KEY[c["类别"]]: c for c in oh}
    title_kind = {"月度成本分析": "月度成本分析报告", "季度成本分析": "季度成本分析报告",
                  "专题分析": "专题分析报告"}[scene]
    title = f"{title_kind}（{period['label']}·{product}）" if scene != "专题分析" else \
        f"专题分析报告（{topic}·{period['label']}·{product}）"

    pos = _industry(product)
    good, bad = [], []
    for r in pos:
        if ("低于" in r["对标评价"] or "处于行业" in r["对标评价"]) and "高于" not in r["对标评价"]:
            good.append(f"{r['指标']}{r['本厂水平(中药一厂)']}，{r['对标评价']}")
        if "高于" in r["对标评价"] or "空间" in r["对标评价"]:
            bad.append(f"{r['指标']}{r['本厂水平(中药一厂)']}，{r['对标评价']}")
    if kpi["同比变动率"] is not None and kpi["同比变动率"] < 0:
        good.append(f"单位成本同比改善{abs(kpi['同比变动率']):.2f}%")
    if alerts:
        bad += [f"{a['要素']}{'较1月基准' if '基准' in a['要素'] else '环比'}变动{a['环比']:+}%"
                for a in alerts[:3]]
    if kpi["预算偏差"] is not None and kpi["预算偏差"] > 5:
        bad.append(f"单位成本较预算偏差{kpi['预算偏差']:+.2f}%，需加强成本管控")
    highlights = "；".join(good[:3]) + "。" if good else "各项成本要素运行平稳。"
    concerns = "；".join(bad[:4]) + "。" if bad else "暂无重点关注事项。"

    suggestions = bench["改进建议"]
    sug_text = "；".join(f"{s['任务']}（{s['责任部门']}，{s['优先级']}）" for s in suggestions[:5])

    # ---------- 模板占位符取值（官方 docx 101 个占位符） ----------
    v = {
        "报告标题": title,
        "报告类型": scene, "分析月份": f"{period['label']}（{period['span']}）",
        "编制日期": time.strftime("%Y-%m-%d"),
        "产品名称": product, "产品规格": ds.spec_of(product),
        "本月产量": _f(kpi["产量"], 0), "上月产量": _f(kpi["上月产量"], 0),
        "去年同月产量": _f(kpi["去年同月产量"], 0), "预算产量": _f(kpi["预算产量"], 0),
        "产量环比": _pct(kpi["产量环比"]), "产量同比": _pct(kpi["产量同比"]),
        "产量预算偏差": _pct(kpi["产量预算偏差"]),
        "单位成本": _f(kpi["单位成本"], 2), "上月单位成本": _f(kpi["上月单位成本"], 2),
        # 模板表 5 第 3 行用的是 {{本月单位成本}}，与上面 {{单位成本}} 是同义占位符，两个键都要给
        "本月单位成本": _f(kpi["单位成本"], 2),
        "去年单位成本": _f(kpi["去年单位成本"], 2),
        "预算单位成本": _f(budget["单位成本"] if budget else None, 2),
        "单位成本环比": _pct(kpi["环比变动率"]), "单位成本同比": _pct(kpi["同比变动率"]),
        "单位成本预算偏差": _pct(kpi["预算偏差"]),
        "本月总成本": _f(kpi["总成本"], 0), "上月总成本": _f(kpi["上月总成本"], 0),
        "去年总成本": _f(kpi["去年总成本"], 0), "预算总成本": _f(kpi["预算总成本"], 0),
        "总成本环比": _pct(kpi["总成本环比"]), "总成本同比": _pct(kpi["总成本同比"]),
        "总成本预算偏差": _pct(kpi["总成本预算偏差"]), "总环比": _pct(kpi["环比变动率"]),
    }
    for key, ele_name in [("材料", "直接材料"), ("人工", "直接人工"), ("制造费用", "制造费用")]:
        e = ele(ele_name)
        tail = "制造费用" if key == "制造费用" else key + "成本"
        v[f"本月{tail}"] = _f(e["本月"])
        v[f"上月{tail}"] = _f(e["上月"])
        v[f"预算{tail}"] = _f(e["预算"])
        v[f"{key}金额"] = _f(e["本月"])
        v[f"{key}占比"] = _f(e["本月"] / kpi["单位成本"] * 100, 1)
        v[f"{key}环比"] = _pct(e["环比"])
        v[f"{key}贡献度"] = _f(e["贡献度"], 1)
        v[f"{key}预算偏差"] = _pct(e["预算偏差"])
        v[f"{key}成本环比"] = _pct(e["环比"])
    if labor:
        b = labor["上月"] or {}
        v.update({"人工单位成本": _f(labor["单位人工成本"]), "上月人工单位成本": _f(b.get("单位人工成本")),
                  "人工环比": _pct(ds._rate(labor["单位人工成本"], b.get("单位人工成本"))),
                  "本月工时": _f(labor["工时_万盒"], 1), "上月工时": _f(b.get("工时_万盒"), 1),
                  "工时环比": _pct(ds._rate(labor["工时_万盒"], b.get("工时_万盒"))),
                  "本月时薪": _f(labor["时薪"], 2), "上月时薪": _f(b.get("时薪"), 2),
                  "时薪环比": _pct(ds._rate(labor["时薪"], b.get("时薪"))),
                  "本月效率": _f(labor["效率"], 1), "上月效率": _f(b.get("效率"), 1),
                  "效率环比": _pct(ds._rate(labor["效率"], b.get("效率")))})
    for c in oh:
        key = CAT_KEY[c["类别"]]
        v[f"本月{key}"] = _f(c["本月"])
        v[f"上月{key}"] = _f(c["上月"])
        v[f"{key}环比"] = _pct(c["环比"])
        v[f"{key}变动说明"] = _oh_explain(c["类别"], c["环比"])
    v["制造费用合计"] = _f(ele("制造费用")["本月"])
    v["上月制造费用合计"] = _f(ele("制造费用")["上月"])
    v["制造费用合计环比"] = _pct(ele("制造费用")["环比"])
    v["材料成本归因分析文本"] = rca_text
    v["成本异常排查分析"] = (
        ("本期触发阈值告警：" + "；".join(
            f"{a['产品']}·{a['要素']}{'较1月基准' if '基准' in a['要素'] else '环比'}变动{a['环比']:+}%"
            f"（{a['上月']}→{a['本月']}）" for a in alerts)) + "。已按流程纳入归因分析并生成整改任务。"
        if alerts else "本期各成本要素波动均在阈值范围内，未触发异常排查流程。")
    v["差异结构拆解分析"] = (
        f"对标产品：{bench['结构树']['产品']}（单位成本差异{bench['结构树']['单位成本差异率%']:+}%）\n" +
        "\n".join(f"{s['要素']}：{s['差异率%']:+}%" for s in bench["结构树"]["结构拆解"]) +
        f"\n主因要素：{bench['结构树']['主因要素']}")
    v["差异归因分析文本"] = bench["归因分析"]
    v["本月亮点"] = highlights
    v["需关注问题"] = concerns
    v["总成本概览评述"] = (
        f"{period['curr_tag']}单位成本{v['单位成本']}元/盒，环比{v['单位成本环比']}%、同比{v['单位成本同比']}%、"
        f"较预算{v['单位成本预算偏差']}%；总成本{v['本月总成本']}元（产量{v['本月产量']}盒，产量环比{v['产量环比']}%），"
        f"总成本环比{v['总成本环比']}%、同比{v['总成本同比']}%。")
    v["要素归因分析"] = f"{rca_text}\n\n对标归因：{bench['归因分析']}"
    v["总结"] = f"亮点：{v['本月亮点']}\n需关注：{v['需关注问题']}"
    v["改进建议"] = sug_text + "。" if sug_text else "暂无补充建议。"
    v["专题聚焦"] = _topic_text(topic, period, product, kpi, mats, oh, labor, bench, pos) if scene == "专题分析" else ""

    # ---------- 动态表格 ----------
    tr = ds.trend(product)
    tables = {}
    tables["trend"] = {"标题": "表 1  近 6 个月成本趋势",
                       "表头": ["月份", "产量(盒)", "单位成本(元/盒)", "直接材料(元/盒)", "直接人工(元/盒)",
                                "制造费用(元/盒)", "总成本(元)"],
                       "行": [[r["月份"], _f(r["产量"], 0), _f(r["单位成本"], 2), _f(r["直接材料"], 2),
                              _f(r["直接人工"], 2), _f(r["制造费用"], 2), _f(r["总成本"], 0)] for r in tr]}
    tables["material"] = {"标题": "表 2  原材料成本明细（单位消耗成本环比）",
                          "表头": ["原材料", f"{period['curr_tag']}(元/盒)", f"{period['prev_tag']}(元/盒)", "环比%",
                                   "对单位成本影响(元/盒)", "总影响(元)"],
                          "行": [[m["药材"], _f(m["单耗_本月"]), _f(m["单耗_上月"]), _pct(m["单耗变动"]),
                                 f"{m['变动额_单盒']:+}", _f(m["变动额"], 0)] for m in mats[:10]]}
    price_rows = []
    mi = int(period["months"][-1].split("-")[1])
    for m in mats:
        p = ds._match_price(m["药材"])
        if not p:
            continue
        try:
            base_p, cur_p = p["1"], p[str(mi)]
        except KeyError:
            continue
        price_rows.append([m["药材"], p.get("单位", "元/kg"),
                           "→".join(_f(p[str(i)], 1) for i in range(1, mi + 1)),
                           _pct(ds._rate(cur_p, base_p)), p.get("趋势", "")])
    tables["price"] = {"标题": "表 3  关键原材料市场价格跟踪",
                       "表头": ["药材", "单位", "1月以来价格走势", "较1月涨幅%", "趋势分析"],
                       "行": price_rows or [["—", "—", "—", "—", "—"]]}
    focus = next((r for r in diff_rows if r["产品"] == product), diff_rows[0] if diff_rows else None)
    bench_rows = []
    if focus:
        bench_rows = [["单位成本(元/盒)", focus["一厂单位成本"], focus["二厂单位成本"], focus["差异额"], focus["差异率%"]],
                      ["直接材料(元/盒)", "—", "—", "—", focus["材料差异率%"]],
                      ["直接人工(元/盒)", "—", "—", "—", focus["人工差异率%"]],
                      ["制造费用(元/盒)", "—", "—", "—", focus["制造费用差异率%"]]]
    tables["benchmark"] = {"标题": "表 4  两厂对标差异（中药一厂 vs 中药二厂）",
                           "表头": ["指标", "中药一厂", "中药二厂", "差异额", "差异率%"], "行": bench_rows}
    tables["suggest"] = {"标题": "表 5  改进建议",
                         "表头": ["序号", "改进建议", "责任部门", "优先级"],
                         "行": [[i + 1, s["任务"], s["责任部门"], s["优先级"]] for i, s in enumerate(suggestions)]}
    tasks = [t for t in rpa_mod.list_tasks() if t.get("产品") in (product, "全部产品")]

    def _task_row(t):
        """兼容 rpa 任务记录的中文字段（责任人/优先级）与历史英文别名（owner/priority）"""
        return [t.get("task_id", "—"), t.get("task_title", "—"),
                t.get("责任人") or t.get("owner") or "—",
                t.get("优先级") or t.get("priority") or "—",
                t.get("status", "—")]

    tables["tasks"] = {"标题": "表 6  整改任务清单（自动生成并推送 RPA）",
                       "表头": ["任务ID", "任务标题", "责任人", "优先级", "状态"],
                       "行": [_task_row(t) for t in tasks[:10]]
                       or [["—", "尚未下发整改任务", "—", "—", "—"]]}
    tables["kpi"] = {"标题": "表 7  核心指标一览",
                     "表头": ["指标", period["curr_tag"], period["prev_tag"], "环比", "去年同期", "同比", "预算值", "预算偏差"],
                     "行": [
                         ["产量(盒)", _f(kpi["产量"], 0), _f(kpi["上月产量"], 0), _pct_s(kpi["产量环比"]),
                          _f(kpi["去年同月产量"], 0), _pct_s(kpi["产量同比"]), _f(kpi["预算产量"], 0),
                          _pct_s(kpi["产量预算偏差"])],
                         ["单位成本(元/盒)", _f(kpi["单位成本"], 2), _f(kpi["上月单位成本"], 2),
                          _pct_s(kpi["环比变动率"]), _f(kpi["去年单位成本"], 2), _pct_s(kpi["同比变动率"]),
                          _f(budget["单位成本"] if budget else None, 2), _pct_s(kpi["预算偏差"])],
                         ["总成本(元)", _f(kpi["总成本"], 0), _f(kpi["上月总成本"], 0), _pct_s(kpi["总成本环比"]),
                          _f(kpi["去年总成本"], 0), _pct_s(kpi["总成本同比"]),
                          _f(kpi["预算总成本"], 0), _pct_s(kpi["总成本预算偏差"])]]}
    tables["structure"] = {"标题": "表 8  成本结构（单位成本口径）",
                           "表头": ["成本要素", "金额(元/盒)", "占比", "环比", "贡献度"],
                           "行": [[e["要素"], _f(e["本月"], 3), _pct_u(e["本月"] / kpi["单位成本"] * 100, 1),
                                  _pct_s(e["环比"]), _pct_u(e["贡献度"], 1)] for e in kpi["贡献度"]]}
    tables["labor"] = {"标题": "表 9  直接人工指标",
                       "表头": ["指标", period["curr_tag"], period["prev_tag"], "环比"],
                       "行": ([[
                           "单位人工成本(元/盒)", _f(labor["单位人工成本"]), _f((labor["上月"] or {}).get("单位人工成本")),
                           _pct_s(ds._rate(labor["单位人工成本"], (labor["上月"] or {}).get("单位人工成本")))],
                           ["人工工时(h/万盒)", _f(labor["工时_万盒"], 1), _f((labor["上月"] or {}).get("工时_万盒"), 1),
                            _pct_s(ds._rate(labor["工时_万盒"], (labor["上月"] or {}).get("工时_万盒")))],
                           ["人均时薪(元/h)", _f(labor["时薪"], 2), _f((labor["上月"] or {}).get("时薪"), 2),
                            _pct_s(ds._rate(labor["时薪"], (labor["上月"] or {}).get("时薪")))],
                           ["人工效率(盒/人·天)", _f(labor["效率"], 1), _f((labor["上月"] or {}).get("效率"), 1),
                            _pct_s(ds._rate(labor["效率"], (labor["上月"] or {}).get("效率")))]]
                              if labor else [["—", "—", "—", "—"]])}
    tables["oh"] = {"标题": "表 10  制造费用五类明细（单位成本口径）",
                    "表头": ["费用类别", f"{period['curr_tag']}(元/盒)", f"{period['prev_tag']}(元/盒)", "环比", "变动说明"],
                    "行": [[c["类别"], _f(c["本月"]), _f(c["上月"]), _pct_s(c["环比"]), _oh_explain(c["类别"], c["环比"])]
                           for c in oh]}
    tables["alerts"] = {"标题": "表 11  阈值告警明细（环比 ±10%）",
                        "表头": ["产品", "要素", "环比", "上期", "本期"],
                        "行": [[a["产品"], a["要素"], _pct_s(a["环比"]), _f(a["上月"], 3), _f(a["本月"], 3)]
                               for a in alerts[:20]] or [["—", "本期无告警", "—", "—", "—"]]}

    sources = []
    seen = set()
    for h in list(hits) + list(bench.get("sources") or []):
        key = (h.get("source"), h.get("doc_id"))
        if key in seen:
            continue
        seen.add(key)
        sources.append(h)
    tables["sources"] = {"标题": "表 12  RAG 检索命中的知识库片段",
                         "表头": ["序号", "来源文档", "相关度", "命中片段（节选）"],
                         "行": [[i + 1, h["source"], h.get("score", ""), (h.get("snippet", "") or "")[:160].replace("\n", " ")]
                                for i, h in enumerate(sources)] or [["—", "本期无检索命中", "—", "—"]]}

    # ---------- 图表 ----------
    # 行情图优先展示"价格真正发生波动"的药材（与报告告警/归因叙述一致），无波动时退回主要材料
    price_series = {}
    _mi = int(period["months"][-1].split("-")[1])
    _cands = []
    for m in mats:
        p = ds._match_price(m["药材"])
        if p and all(str(i) in p for i in range(1, 7)):
            _cands.append((abs(ds._rate(p[str(_mi)], p["1"]) or 0), m["药材"], p))
    _cands.sort(key=lambda x: -x[0])
    for move, name, p in _cands:
        if len(price_series) >= 5 or move < 1.0:
            break
        price_series[name] = [p[str(i)] for i in range(1, 7)]
    if not price_series:
        for m in mats[:4]:
            p = ds._match_price(m["药材"])
            if p and all(str(i) in p for i in range(1, 7)):
                price_series[m["药材"]] = [p[str(i)] for i in range(1, 7)]
    if not price_series:
        for name, p in list(ds.price.items())[:3]:
            price_series[name] = [p[str(i)] for i in range(1, 7)]
    waterfall = [("上月单位成本" if period["kind"] == "月度" else "上期单位成本",
                  "total", kpi["上月单位成本"] or 0)]
    for e in kpi["贡献度"]:
        waterfall.append((e["要素"], "delta", e["变动额"] or 0))
    waterfall.append(("本月单位成本" if period["kind"] == "月度" else "本期单位成本", "total", kpi["单位成本"]))
    charts_data = {
        "trend": tr, "structure": {e["要素"]: e["本月"] for e in kpi["贡献度"]},
        "单位成本": kpi["单位成本"], "waterfall": waterfall, "materials": mats,
        "prices": price_series,
        "benchmark": ({"产品": focus["产品"],
                       "一厂": {"单位成本": focus["一厂单位成本"], "直接材料": 0, "直接人工": 0, "制造费用": 0},
                       "二厂": {"单位成本": focus["二厂单位成本"], "直接材料": 0, "直接人工": 0, "制造费用": 0}}
                      if focus else {}),
        "budget": [["本月实际" if period["kind"] == "月度" else "本期实际", kpi["单位成本"]],
                   ["上月实际" if period["kind"] == "月度" else "上期实际", kpi["上月单位成本"]],
                   ["去年同期", kpi["去年单位成本"]],
                   ["预算值", budget["单位成本"] if budget else None]],
    }
    # 对标图的三要素：用两厂聚合行补齐
    if focus:
        a = pd_mod.agg_row(period["months"], focus["产品"], factory="f1")
        b = pd_mod.agg_row(period["months"], focus["产品"], factory="f2")
        charts_data["benchmark"] = {"产品": focus["产品"],
                                    "一厂": {"单位成本": a["单位成本"], "直接材料": a["直接材料"],
                                             "直接人工": a["直接人工"], "制造费用": a["制造费用"]},
                                    "二厂": {"单位成本": b["单位成本"], "直接材料": b["直接材料"],
                                             "直接人工": b["直接人工"], "制造费用": b["制造费用"]}}
    chart_files = charts_mod.build_all(charts_data) if want_charts else {}

    model = {
        "标题": title, "副标题": f"{scene} · {period['span']} · {product}",
        "报告类型": scene, "期间": f"{period['label']}（{period['span']}）", "产品": product,
        "月份": month, "专题": topic if scene == "专题分析" else "",
        "编制日期": v["编制日期"], "编制单位": 编制单位, "产品规格": ds.spec_of(product),
        "values": v, "tables": tables, "charts": chart_files, "charts_data": charts_data,
        "sources": sources, "engine": engine, "period": period, "kpi": kpi, "alerts": alerts,
        "bench": bench, "mats": mats, "oh": oh, "labor": labor, "budget": budget,
        "highlights": highlights, "concerns": concerns, "rca_text": rca_text,
        "topic_text": v["专题聚焦"],
    }
    model["封面信息"] = [
        ("报告类型", scene), ("分析期间", f"{period['label']}（{period['span']}）"),
        ("目标产品", f"{product}（{ds.spec_of(product)}）"),
        ("统计口径", f"{period['kind']} · 产量加权平均单价口径"),
        ("数据来源", "企业成本明细/预算/工时/行情数据 + RAG 知识库 + 大模型分析"),
        ("分析引擎", "大模型（RAG 增强）" if engine == "llm" else "本地规则引擎（未配置 API Key）"),
        ("编制单位", 编制单位), ("编制日期", v["编制日期"]),
    ]
    model["封面说明"] = [
        "本报告由「基于 RAG 与大模型的制药企业产品成本智能分析报告系统」自动生成。",
        "报告结构来自官方模板，数据取自成本明细与预算数据，分析文本由大模型结合 RAG 检索的行业/工艺知识撰写。",
    ]
    model["章节"] = _build_sections(model)
    return model


# ---------------- PDF 章节结构 ----------------
def _build_sections(model):
    t = model["tables"]
    v = model["values"]
    period = model["period"]
    kpi = model["kpi"]
    secs = []

    secs.append({"标题": "封面与基本信息", "内容": [
        {"类型": "表格", "表头": ["项目", "内容"], "行": [[k, val] for k, val in model["封面信息"]]},
    ]})

    overview = {"标题": "总成本概览", "内容": [
        {"类型": "段落", "文本": v["总成本概览评述"]},
        {"类型": "表格", "标题": t["kpi"]["标题"], "表头": t["kpi"]["表头"], "行": t["kpi"]["行"]},
        {"类型": "表格", "标题": t["structure"]["标题"],
         "表头": t["structure"]["表头"], "行": t["structure"]["行"]},
    ]}
    if model["charts"].get("structure"):
        overview["内容"].append({"类型": "图", "路径": model["charts"]["structure"],
                                 "标题": charts_mod.CAPTIONS["structure"]})
    if model["charts"].get("budget_compare"):
        overview["内容"].append({"类型": "图", "路径": model["charts"]["budget_compare"],
                                 "标题": charts_mod.CAPTIONS["budget_compare"]})
    secs.append(overview)

    if model["专题"]:
        secs.append({"标题": "专题聚焦 · " + model["专题"], "内容": [
            {"类型": "段落", "文本": model["topic_text"]},
        ]})

    material_sec = {"标题": "成本要素明细分析", "内容": [
        {"类型": "段落", "文本": "（一）直接材料"},
        {"类型": "表格", "标题": t["material"]["标题"], "表头": t["material"]["表头"], "行": t["material"]["行"]},
    ]}
    if model["charts"].get("material_impact"):
        material_sec["内容"].append({"类型": "图", "路径": model["charts"]["material_impact"],
                                     "标题": charts_mod.CAPTIONS["material_impact"]})
    material_sec["内容"] += [
        {"类型": "段落", "文本": "（二）人工成本"},
        {"类型": "表格", "标题": t["labor"]["标题"], "表头": t["labor"]["表头"], "行": t["labor"]["行"]},
        {"类型": "段落", "文本": "（三）制造费用"},
        {"类型": "表格", "标题": t["oh"]["标题"], "表头": t["oh"]["表头"], "行": t["oh"]["行"]},
        {"类型": "段落", "文本": "（四）成本要素归因分析（RAG + 大模型生成）"},
        {"类型": "段落", "文本": model["rca_text"]},
    ]
    secs.append(material_sec)

    trend_sec = {"标题": "重点产品专项分析 · " + model["产品"], "内容": [
        {"类型": "段落", "文本": "（一）近 6 个月成本趋势"},
        {"类型": "表格", "标题": t["trend"]["标题"], "表头": t["trend"]["表头"], "行": t["trend"]["行"]},
    ]}
    if model["charts"].get("trend"):
        trend_sec["内容"].append({"类型": "图", "路径": model["charts"]["trend"],
                                  "标题": charts_mod.CAPTIONS["trend"]})
    trend_sec["内容"] += [
        {"类型": "段落", "文本": "（二）成本异常点排查"},
        {"类型": "表格", "标题": t["alerts"]["标题"], "表头": t["alerts"]["表头"], "行": t["alerts"]["行"]},
    ]
    if model["charts"].get("waterfall"):
        trend_sec["内容"].append({"类型": "图", "路径": model["charts"]["waterfall"],
                                  "标题": charts_mod.CAPTIONS["waterfall"]})
    trend_sec["内容"] += [
        {"类型": "段落", "文本": "（三）关键原材料市场价格跟踪"},
        {"类型": "表格", "标题": t["price"]["标题"], "表头": t["price"]["表头"], "行": t["price"]["行"]},
    ]
    if model["charts"].get("price_trend"):
        trend_sec["内容"].append({"类型": "图", "路径": model["charts"]["price_trend"],
                                  "标题": charts_mod.CAPTIONS["price_trend"]})
    secs.append(trend_sec)

    bench_sec = {"标题": "对标分析（与中药二厂）", "内容": [
        {"类型": "段落", "文本": "（一）找差异：对标差异总览"},
        {"类型": "表格", "标题": t["benchmark"]["标题"], "表头": t["benchmark"]["表头"], "行": t["benchmark"]["行"]},
    ]}
    if model["charts"].get("benchmark"):
        bench_sec["内容"].append({"类型": "图", "路径": model["charts"]["benchmark"],
                                  "标题": charts_mod.CAPTIONS["benchmark"]})
    bench_sec["内容"] += [
        {"类型": "段落", "文本": "（二）拆结构：差异结构拆解"},
        {"类型": "段落", "文本": v["差异结构拆解分析"]},
        {"类型": "段落", "文本": "（三）拆原因：差异归因分析"},
        {"类型": "段落", "文本": model["bench"]["归因分析"]},
    ]
    secs.append(bench_sec)

    secs.append({"标题": "总结与建议", "内容": [
        {"类型": "段落", "文本": "（一）本期成本管理亮点"},
        {"类型": "段落", "文本": model["highlights"]},
        {"类型": "段落", "文本": "（二）需关注的问题"},
        {"类型": "段落", "文本": model["concerns"]},
        {"类型": "段落", "文本": "（三）改进建议"},
        {"类型": "表格", "标题": t["suggest"]["标题"], "表头": t["suggest"]["表头"], "行": t["suggest"]["行"]},
        {"类型": "段落", "文本": "（四）整改任务清单（自动生成并推送 RPA）"},
        {"类型": "表格", "标题": t["tasks"]["标题"], "表头": t["tasks"]["表头"], "行": t["tasks"]["行"]},
    ]})

    secs.append({"标题": "附录 A · 知识库引用来源（RAG 检索）", "内容": [
        {"类型": "段落", "文本": f"本期共命中 {len(model['sources'])} 条知识库片段，"
                             f"由 jieba 分词 + BM25（可选向量）混合检索得到，已作为大模型分析的知识依据。"},
        {"类型": "表格", "标题": t["sources"]["标题"], "表头": t["sources"]["表头"], "行": t["sources"]["行"]},
    ]})
    # 统一编号（专题分析会多出一节，故动态生成中文序号）
    cn = "一二三四五六七八九十"
    for i, s in enumerate(secs):
        if s["标题"].startswith("附录"):
            continue
        s["标题"] = f"{cn[i]}、{s['标题']}"
    return secs
