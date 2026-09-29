# -*- coding: utf-8 -*-
"""PDF 预览小工具：把 PDF 页面渲染成 PNG，便于人工检查排版（可选依赖 pypdfium2）

    python scripts/preview_pdf.py outputs/report_xxx.pdf            # 前 3 页
    python scripts/preview_pdf.py outputs/report_xxx.pdf -n 10      # 前 10 页
    python scripts/preview_pdf.py outputs/report_xxx.pdf -s 3       # 2 倍缩放（默认 2）

未安装 pypdfium2 时提示安装方式（不会影响系统其他功能）：
    python -m pip install -r requirements.txt          # 走 requirements（含 pypdfium2 以外依赖）
    python -m pip install pypdfium2 -i https://pypi.tuna.tsinghua.edu.cn/simple
    或：python scripts/fetch_vendor.py pypdfium2       # 解包到项目内 vendor/
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
_VENDOR = os.path.join(ROOT, "vendor")      # 支持项目内依赖（scripts/fetch_vendor.py）
if os.path.isdir(_VENDOR):
    sys.path.insert(0, _VENDOR)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("-n", "--pages", type=int, default=3, help="渲染前 N 页")
    ap.add_argument("-s", "--scale", type=float, default=2.0, help="缩放倍数（2 ≈ 144dpi）")
    ap.add_argument("-o", "--outdir", default=None, help="输出目录（默认 <pdf名>_preview/）")
    args = ap.parse_args()

    try:
        import pypdfium2 as pdfium
    except Exception:
        print("未安装 pypdfium2。安装方式：\n"
              "  python -m pip install pypdfium2 -i https://pypi.tuna.tsinghua.edu.cn/simple\n"
              "  或 python scripts/fetch_vendor.py pypdfium2")
        return 1

    pdf_path = os.path.abspath(args.pdf)
    if not os.path.exists(pdf_path):
        print(f"文件不存在：{pdf_path}")
        return 1
    outdir = args.outdir or os.path.join(os.path.dirname(pdf_path),
                                         os.path.splitext(os.path.basename(pdf_path))[0] + "_preview")
    os.makedirs(outdir, exist_ok=True)

    doc = pdfium.PdfDocument(pdf_path)
    n = min(args.pages, len(doc))
    print(f"共 {len(doc)} 页，渲染前 {n} 页到 {outdir}")
    for i in range(n):
        img = doc[i].render(scale=args.scale).to_pil()
        out = os.path.join(outdir, f"page{i + 1}.png")
        img.save(out)
        print(" ", out, img.size)
    doc.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
