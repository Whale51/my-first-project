# -*- coding: utf-8 -*-
"""交付物汇总（本次改动验收用）：列出 outputs/ 下报告的结构信息。

    python scripts/report_summary.py
"""
import glob
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.paths import OUTPUT_DIR      # noqa: E402


def main():
    rows = sorted(glob.glob(os.path.join(OUTPUT_DIR, "report_*")))
    if not rows:
        print("outputs/ 下暂无报告，请先在页面生成或运行 scripts/verify_report.py")
        return 0
    print(f"{'文件':60s} {'大小':>8s}  结构")
    print("-" * 96)
    for f in rows:
        name = os.path.basename(f)
        kb = round(os.path.getsize(f) / 1024)
        if f.lower().endswith(".pdf"):
            b = io.open(f, "rb").read()
            pages = len(re.findall(rb"/Type\s*/Page[^s]", b))
            imgs = len(re.findall(rb"/Subtype\s*/Image", b))
            print(f"{name[:60]:60s} {kb:>6} KB  页数={pages} 图表={imgs}")
        elif f.lower().endswith(".docx"):
            from docx import Document
            d = Document(f)
            hdr = next((p.text for s in d.sections for p in s.header.paragraphs if p.text.strip()), "")
            ftr = next((p.text for s in d.sections for p in s.footer.paragraphs if p.text.strip()), "")
            print(f"{name[:60]:60s} {kb:>6} KB  表格={len(d.tables)} 图表={len(d.inline_shapes)}")
            print(f"{'':60s}          页眉：{hdr[:46]}")
            print(f"{'':60s}          页脚：{ftr[:46]}")
    charts = sorted(glob.glob(os.path.join(OUTPUT_DIR, "charts", "*.png")))
    print(f"\n内嵌图表原图（outputs/charts/，共 {len(charts)} 张）：" +
          "、".join(os.path.basename(c) for c in charts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
