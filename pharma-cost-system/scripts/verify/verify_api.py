# -*- coding: utf-8 -*-
"""接口自验：用 FastAPI TestClient 走通"元数据 → 生成报告 → 下载 Word/PDF → 报告归档"全链路。

    python scripts/verify_api.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi.testclient import TestClient      # noqa: E402

from app.main import app                       # noqa: E402

client = TestClient(app)
fails = []


def show(tag, ok, extra=""):
    print(f"  {'✅' if ok else '❌'} {tag} {extra}")
    if not ok:
        fails.append(tag)


def main():
    print("=" * 74)
    r = client.get("/api/meta")
    meta = r.json()
    show("GET /api/meta", r.status_code == 200 and "scenes" in meta,
         f"｜主题={meta.get('scenes')}｜PDF后端={meta.get('pdf_backends')}｜图表={meta.get('charts_ready')}")

    r = client.get("/api/report/options")
    show("GET /api/report/options", r.status_code == 200, f"｜专题={r.json().get('topics')}")

    for scene, month, product, topic in [
        ("月度成本分析", "2026-06", "六味地黄胶囊", None),
        ("季度成本分析", "2026-05", "六味地黄胶囊", None),
        ("专题分析", "2026-06", "板蓝根颗粒", "主要原材料价格波动"),
    ]:
        body = {"month": month, "product": product, "scene": scene, "use_llm": False}
        if topic:
            body["topic"] = topic
        r = client.post("/api/report", json=body)
        ok = r.status_code == 200
        res = r.json() if ok else {}
        show(f"POST /api/report [{scene}/{month}/{product}]", ok and "download" in res,
             f"｜标题={res.get('stats', {}).get('报告标题')}｜PDF={res.get('pdf_engine')}"
             f"｜图表={res.get('stats', {}).get('图表数')}｜残留={len(res.get('leftover', []))}")
        if not ok:
            continue
        # 下载 Word
        rd = client.get(res["download"])
        show(f"  ↳ 下载 Word", rd.status_code == 200 and len(rd.content) > 20000,
             f"｜{round(len(rd.content)/1024)} KB")
        # 下载 PDF
        if res.get("download_pdf"):
            rp = client.get(res["download_pdf"])
            show(f"  ↳ 下载 PDF", rp.status_code == 200 and rp.content[:4] == b"%PDF",
                 f"｜引擎={res.get('pdf_engine')}｜{round(len(rp.content)/1024)} KB")
        else:
            show("  ↳ 下载 PDF", False, f"｜{res.get('pdf_note')}")
        # 图表原图
        for key, url in list(res.get("charts", {}).items())[:2]:
            rc = client.get(url)
            show(f"  ↳ 图表 {key}", rc.status_code == 200 and rc.content[:4] == b"\x89PNG",
                 f"｜{round(len(rc.content)/1024)} KB")

    r = client.get("/api/report/list")
    show("GET /api/report/list", r.status_code == 200 and len(r.json().get("reports", [])) >= 3,
         f"｜归档 {len(r.json().get('reports', []))} 份")

    # 非法参数防护
    r = client.get("/api/report/download?file=../app/main.py")
    show("非法文件名拦截", r.status_code == 400)
    r = client.post("/api/report", json={"product": "六味地黄胶囊"})
    show("缺少 month 参数拦截", r.status_code == 400)

    print("=" * 74)
    print("✅ 接口全链路通过" if not fails else f"❌ 失败项：{fails}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
