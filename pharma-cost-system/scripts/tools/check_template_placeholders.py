# -*- coding: utf-8 -*-
"""模板占位符覆盖自检（TODO P3 回归防护）

检查官方模板 `data/template_official.docx` 中的全部 `{{占位符}}` 是否都能被
`app/report_model.py` 的取值表覆盖（段落 + 表格单元格都要扫），并检查生成产物中是否残留字面量。

    python scripts/tools/check_template_placeholders.py            # 只检查模板覆盖率
    python scripts/tools/check_template_placeholders.py --report   # 顺带生成一份报告并检查产物残留
"""
import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
_VENDOR = os.path.join(ROOT, "vendor")
if os.path.isdir(_VENDOR):
    sys.path.insert(0, _VENDOR)

from docx import Document        # noqa: E402

PAT = re.compile(r"\{\{(\w+)\}\}")
TPL = os.path.join(ROOT, "data", "template_official.docx")
# 这些占位符是"动态表格/图表插入锚点"，由 report_gen 在渲染时替换为真实表格，不需要取值表覆盖
ANCHORS = {"近6个月成本趋势表格", "原材料成本明细表格", "原材料价格跟踪表格", "对标差异表格",
           "改进建议表格", "整改任务表格", "图表"}


def template_placeholders(path=TPL):
    doc = Document(path)
    found = {}

    def scan(text, where):
        for m in PAT.finditer(text or ""):
            found.setdefault(m.group(1), []).append(where)

    for i, p in enumerate(doc.paragraphs):
        scan(p.text, f"段落{i}")
    for ti, t in enumerate(doc.tables):
        for ri, row in enumerate(t.rows):
            for ci, cell in enumerate(row.cells):
                scan(cell.text, f"表{ti}行{ri}列{ci}")
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="额外生成报告并检查产物残留")
    args = ap.parse_args()

    ph = template_placeholders()
    print(f"模板占位符：{len(ph)} 个（{TPL}）")

    from app import report_model as rm
    model = rm.build("2026-06", rm.ds.products_list()[0], scene="月度成本分析", use_llm=False,
                     want_charts=False)
    values = model["values"]
    missing = {k: v for k, v in ph.items() if k not in values and k not in ANCHORS}
    unused = [k for k in values if k not in ph]
    print(f"取值表键：{len(values)} 个；锚点占位符：{sorted(set(ph) & ANCHORS)}")
    if missing:
        print(f"❌ 模板中 {len(missing)} 个占位符取值表未覆盖（会以字面量残留在成稿里）：")
        for k, where in sorted(missing.items()):
            print(f"   - {k}  (出现于 {where[0]}{' 等' if len(where) > 1 else ''})")
    else:
        print("✅ 模板全部占位符均有取值（除动态表格锚点）")
    if unused:
        print(f"ℹ️  取值表中有 {len(unused)} 个键模板未直接使用（供 PDF 章节/前端复用）：{unused[:12]}")

    if args.report:
        from app import report_gen
        res = report_gen.generate("2026-06", rm.ds.products_list()[0], use_llm=False, want_charts=False)
        path = os.path.join(report_gen.OUT_DIR, res["file"])
        doc = Document(path)
        leftovers = [p.text for p in doc.paragraphs if "{{" in p.text]
        for t in doc.tables:
            for row in t.rows:
                for cell in row.cells:
                    if "{{" in cell.text:
                        leftovers.append(f"[表格] {cell.text}")
        print(f"成稿残留检查：{len(leftovers)} 处" + (f" → {leftovers[:5]}" if leftovers else "（零残留 ✅）"))
        os.remove(path)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
