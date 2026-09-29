# -*- coding: utf-8 -*-
"""报告双格式导出自验：3 个分析主题 × Word/PDF，检查占位符残留、图表嵌入、页眉页脚。

    python scripts/verify_report.py            # 全场景
    python scripts/verify_report.py 月度成本分析 2026-06 六味地黄胶囊
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app import report_gen                       # noqa: E402
from app.paths import OUTPUT_DIR                 # noqa: E402

CASES = [
    ("月度成本分析", "2026-06", "六味地黄胶囊"),
    ("季度成本分析", "2026-05", "六味地黄胶囊"),
    ("专题分析", "2026-06", "板蓝根颗粒"),
]


def check_docx(path):
    from docx import Document
    doc = Document(path)
    leftover = [p.text for p in doc.paragraphs if "{{" in p.text]
    imgs = len(doc.inline_shapes)
    hdr = " | ".join(p.text for s in doc.sections for p in s.header.paragraphs if p.text.strip())
    ftr = " | ".join(p.text for s in doc.sections for p in s.footer.paragraphs if p.text.strip())
    return {"表格": len(doc.tables), "内嵌图片": imgs, "占位符残留": len(leftover),
            "页眉": hdr[:80], "页脚": ftr[:80]}


def main():
    cases = [tuple(sys.argv[1:4])] if len(sys.argv) >= 4 else CASES
    ok = True
    for scene, month, product in cases:
        print("=" * 78)
        print(f"场景：{scene} / {month} / {product}")
        res = report_gen.generate(month, product, scene=scene, use_llm=False)
        print(f"  报告标题：{res['stats']['报告标题']}")
        print(f"  Word    ：{res['file']}")
        print(f"  PDF     ：{res['file_pdf']}（引擎={res['pdf_engine']}，{res['pdf_note']}）")
        print(f"  图表 {res['stats']['图表数']} 张，知识库引用 {res['stats']['知识库引用']} 条，"
              f"模板占位符残留 {len(res['leftover'])} 处")
        info = check_docx(os.path.join(OUTPUT_DIR, res["file"]))
        print(f"  Word 自检：{info}")
        if res["file_pdf"]:
            size = os.path.getsize(os.path.join(OUTPUT_DIR, res["file_pdf"]))
            print(f"  PDF 自检：{round(size/1024)} KB")
        if info["占位符残留"]:
            ok = False
            print("  ❌ 存在未替换的模板占位符：", res["leftover"][:5])
    print("=" * 78)
    print("✅ 全部场景导出成功" if ok else "❌ 存在占位符残留")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
