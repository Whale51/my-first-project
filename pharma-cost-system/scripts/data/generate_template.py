# -*- coding: utf-8 -*-
"""生成 Word 月度成本分析报告模板（含 {{占位符}} 与表格占位段）"""
import os
from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "data", "template")
os.makedirs(OUT, exist_ok=True)

doc = Document()
style = doc.styles["Normal"]
style.font.name = "宋体"
style.font.size = Pt(12)

def head(text, level=1):
    doc.add_heading(text, level=level)

def para(text, center=False):
    p = doc.add_paragraph(text)
    if center:
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    return p

# ① 封面与基本信息
para("{{报告标题}}", center=True)
para("产品：{{产品名称}}    分析月份：{{分析月份}}", center=True)
para("编制单位：{{编制单位}}    编制日期：{{编制日期}}", center=True)

# ② 总成本概览
head("一、总成本概览")
para("本月总成本 {{本月总成本}} 元，环比 {{环比变动率}}，同比 {{同比变动率}}，较预算偏差 {{预算偏差}}。{{总成本概览评述}}")
para("{{总成本概览表}}")

# ③ 成本要素明细分析
head("二、成本要素明细分析")
para("直接材料 {{材料成本}} 元（环比 {{环比变动率_材料}}）；直接人工 {{人工成本}} 元（环比 {{环比变动率_人工}}）；制造费用 {{制造费用}} 元（环比 {{环比变动率_制造费用}}）。{{要素归因分析}}")
para("{{要素明细表}}")

# ④ 重点产品专项分析
head("三、重点产品专项分析")
para("重点分析产品：{{产品名称}}。本月单位成本 {{产品单位成本}} 元/盒，环比 {{产品环比}}，较预算偏差 {{产品预算偏差}}。{{产品专项分析}}")
para("{{产品归因分析}}")
para("{{产品要素表}}")

# ⑤ 总结与建议
head("四、总结与建议")
para("{{总结}}")
para("{{改进建议}}")

doc.save(os.path.join(OUT, "report_template.docx"))
print("template saved:", os.path.join(OUT, "report_template.docx"))
