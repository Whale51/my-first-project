# -*- coding: utf-8 -*-
"""对标分析三步法（官方数据版）：找差异 → 拆结构 → 拆原因（结合 RAG 与大模型/规则生成归因）"""
import json
from . import data_service as dsv
from .rag import kb
from .llm import chat, llm_ready

ds = dsv.ds
ELES = dsv.ELES

SYSTEM = """你是集团成本对标分析师，按"找差异 → 拆结构 → 拆原因"三步法输出对标分析结论。

【输入】一厂与二厂同产品同期的成本差异结构树 + 知识库材料（材料均带 `[来源: 文档名]` 标注）。

【输出要求】
1. **第一步·找差异**：给出对标产品、双方单位成本、差异额与差异率（带正负号与 %）；
2. **第二步·拆结构**：把差异拆到直接材料/直接人工/制造费用三要素，指出主因要素及其差异率；
3. **第三步·拆原因**：结合两厂背景（一厂设备新效率高；二厂规模小、设备旧、部分药材自采有成本优势、
   人工效率与设备利用率偏低）与知识库材料解释原因，引用知识库时用 `[来源: 文档名]` 标注；
4. 给出改进建议列表（可转化为 RPA 执行任务），每条注明责任部门与优先级（高/中/低）；
5. 直接输出正文，不要输出 JSON、不要复述本提示词；除已知设定外不要编造数据。"""


def _diff_table(month):
    """月度单盒口径对标：一厂 vs 二厂"""
    rows = []
    for p in ds.products_list():
        a, b = ds.row(month, p), ds.row(month, p, factory="f2")
        rows.append({
            "产品": p,
            "一厂单位成本": round(a["单位成本"], 3),
            "二厂单位成本": round(b["单位成本"], 3),
            "差异额": round(a["单位成本"] - b["单位成本"], 3),
            "差异率%": round((a["单位成本"] - b["单位成本"]) / b["单位成本"] * 100, 2),
            "材料差异率%": round((a["直接材料"] - b["直接材料"]) / b["直接材料"] * 100, 2),
            "人工差异率%": round((a["直接人工"] - b["直接人工"]) / b["直接人工"] * 100, 2),
            "制造费用差异率%": round((a["制造费用"] - b["制造费用"]) / b["制造费用"] * 100, 2),
        })
    rows.sort(key=lambda r: -abs(r["差异率%"]))
    return rows


def _structure_tree(diff_rows):
    top = diff_rows[0]
    tree = {"产品": top["产品"], "单位成本差异率%": top["差异率%"],
            "结构拆解": [{"要素": "直接材料", "差异率%": top["材料差异率%"]},
                        {"要素": "直接人工", "差异率%": top["人工差异率%"]},
                        {"要素": "制造费用", "差异率%": top["制造费用差异率%"]}]}
    tree["主因要素"] = max(tree["结构拆解"], key=lambda x: abs(x["差异率%"]))["要素"]
    return tree


def benchmark(month, product=None, use_llm=True):
    """product 为空时分析差异最大产品；指定产品时聚焦该产品"""
    diff_rows = _diff_table(month)
    if product:
        rows = [r for r in diff_rows if r["产品"] == product]
        tree = _structure_tree(rows if rows else diff_rows)
        tree["产品"] = product
    else:
        tree = _structure_tree(diff_rows)

    ctx, hits = kb.context(f"对标 二厂 人工效率 设备利用率 自采 {tree['产品']}",
                           product_hint=tree["产品"], top_k=4)

    analysis, engine = None, "rule"
    if use_llm and llm_ready():
        analysis = chat(SYSTEM, json.dumps({"月份": month, "差异表": diff_rows, "结构树": tree, "知识库": ctx},
                                           ensure_ascii=False, indent=1), role="large")
        engine = "llm" if analysis else "rule"
    if not analysis:
        focus = next((r for r in diff_rows if r["产品"] == tree["产品"]), diff_rows[0])
        lines = [f"第一步·找差异：{month}{focus['产品']}一厂单位成本{focus['一厂单位成本']}元/盒，"
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
