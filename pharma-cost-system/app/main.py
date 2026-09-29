# -*- coding: utf-8 -*-
"""FastAPI 主入口

覆盖赛题四大模块 + 加分项与工程能力：
* 模块一 智能报告生成：`/api/report*`（模板解析 + 数据填充 + RAG 增强 + 大模型文本，Word/PDF 双格式）
* 模块二 成本看板与归因：`/api/kpi /api/trend /api/structure /api/heatmap /api/alerts /api/material_diff /api/rca`
* 模块三 对标分析三步法：`/api/benchmark`
* 模块四 RPA 整改闭环：`/api/rpa/dispatch /api/rpa/tasks /api/rpa/timeline`
* 加分项：知识图谱增强 RAG（`/api/knowledge/*`）、Agent 自主决策（`/api/agent/*`）、成本预测（`/api/forecast`）

工程约定：所有接口对入参做显式校验（非法月份/产品/场景返回 400 而不是 500），
统一日志见 `app/logging_conf.py`，交互式 API 文档见 `/docs`（Swagger）与 `/redoc`。
"""
import os
import time

from fastapi import Body, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import agent as agent_mod
from . import benchmark, rpa as rpa_mod
from . import data_service as dsv
from . import report_gen
from .llm import info as llm_info
from .llm import llm_ready
from .logging_conf import get_logger, setup
from .rag import kb

ds = dsv.ds
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
log = get_logger(__name__)
setup()

TAGS = [
    {"name": "系统", "description": "元信息与引擎状态"},
    {"name": "成本看板", "description": "KPI / 趋势 / 结构 / 告警 / 材料差异（模块二）"},
    {"name": "归因分析", "description": "RAG + 大模型归因（模块二）"},
    {"name": "知识库", "description": "混合检索、知识库统计、知识图谱（模块一/加分项）"},
    {"name": "报告", "description": "模板解析 + 数据填充 + RAG 增强，Word/PDF 双格式导出（模块一）"},
    {"name": "对标分析", "description": "三步法：找差异 → 拆结构 → 拆原因（模块三）"},
    {"name": "整改闭环", "description": "RPA 任务下发、状态追踪与时间轴（模块四）"},
    {"name": "智能体与预测", "description": "Agent 自主决策、成本预测（加分项）"},
]

app = FastAPI(
    title="制药企业产品成本智能分析报告系统",
    description="基于 RAG 与大模型的成本智能分析系统：看板 → 归因 → 报告 → 对标 → 整改闭环，"
                "支持知识图谱增强检索、Agent 自主决策与成本预测。",
    version="1.0.0",
    openapi_tags=TAGS,
)


# ---------------- 工具 ----------------
def charts_available():
    try:
        from . import charts
        return charts.available()
    except Exception:
        return False


def charts_engine():
    try:
        from . import charts
        return charts.engine()
    except Exception:
        return None


def _bad(msg, code=400):
    log.warning("参数校验失败：%s", msg)
    return JSONResponse({"error": msg}, status_code=code)


def _check_month(month):
    if not month:
        return "缺少必填参数 month（示例：2026-06）"
    if month not in ds.months_list():
        return f"非法月份 {month}，可选：{'、'.join(ds.months_list())}"
    return None


def _check_product(product):
    if product and product not in ds.products_list():
        return f"非法产品 {product}，可选：{'、'.join(ds.products_list())}（留空表示全部产品）"
    return None


@app.middleware("http")
async def _access_log(request: Request, call_next):
    t0 = time.time()
    resp = await call_next(request)
    if not request.url.path.startswith("/static"):
        log.info("%s %s → %s（%.0f ms）", request.method, request.url.path,
                 resp.status_code, (time.time() - t0) * 1000)
    return resp


# ---------------- 系统 ----------------
@app.get("/api/meta", tags=["系统"], summary="元信息：月份/产品/引擎与能力开关")
def meta():
    from . import pdf_export
    from . import report_model as rm
    st = kb.stats()
    return {
        "months": ds.months_list(), "products": ds.products_list(),
        "llm_ready": llm_ready(), "llm": llm_info(),
        "scenes": rm.SCENES, "topics": rm.TOPICS,
        "pdf_backends": pdf_export.available_backends(),
        "charts_engine": charts_engine(), "charts_ready": charts_available(),
        "rag": {"文档数": st["文档数"], "知识块数": st["知识块数"], "检索模式": st["检索模式"],
                "向量后端": st["向量后端"], "嵌入器": (st["嵌入器"] or {}).get("name"),
                "切分参数": st["切分参数"], "知识图谱": st["知识图谱"]},
        "agent": {"策略阈值": agent_mod.POLICY, "动作": list(agent_mod.ACTION_DESC.values())},
    }


@app.get("/api/health", tags=["系统"], summary="健康检查")
def health():
    return {"status": "ok", "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "months": len(ds.months_list()), "products": len(ds.products_list()),
            "kb_chunks": kb.stats()["知识块数"]}


# ---------------- 模块二：看板与归因 ----------------
@app.get("/api/kpi", tags=["成本看板"], summary="核心指标（环比/同比/预算偏差/贡献度）")
def kpi(month: str, product: str = None):
    err = _check_month(month) or _check_product(product)
    if err:
        return _bad(err)
    return ds.kpi_overview(month, product)


@app.get("/api/alerts", tags=["成本看板"], summary="阈值告警（环比 ±10%）")
def alerts(month: str):
    err = _check_month(month)
    if err:
        return _bad(err)
    return {"alerts": ds.alerts(month)}


@app.get("/api/trend", tags=["成本看板"], summary="近 6 个月成本趋势")
def trend(product: str = None):
    err = _check_product(product)
    if err:
        return _bad(err)
    return ds.trend(product)


@app.get("/api/structure", tags=["成本看板"], summary="本期成本结构（单位成本口径）")
def structure(month: str, product: str = None):
    err = _check_month(month) or _check_product(product)
    if err:
        return _bad(err)
    return ds.structure(month, product)


@app.get("/api/heatmap", tags=["成本看板"], summary="产品×月份×要素 环比变动矩阵")
def heatmap():
    out = []
    for m in ds.months_list():
        for p in ds.products_list():
            k = ds.kpi_overview(m, p)
            for c in k["贡献度"]:
                out.append({"月份": m, "产品": p, "要素": c["要素"], "变动额": c["变动额"], "环比": c["环比"]})
    return out


@app.get("/api/material_diff", tags=["成本看板"], summary="原材料单位消耗成本环比明细")
def material_diff(month: str, product: str = None):
    err = _check_month(month) or _check_product(product)
    if err:
        return _bad(err)
    rows = ds.material_diff(month, product)
    if product:
        rows = [r for r in rows if r["产品"] == product]
    return rows


@app.post("/api/rca", tags=["归因分析"], summary="生成归因分析文本（RAG + 大模型，含引用来源）")
def rca_endpoint(data: dict = Body(..., examples=[{"month": "2026-06", "product": "六味地黄胶囊",
                                                   "use_llm": True}])):
    from .rca import rca as run_rca
    month, product = data.get("month"), data.get("product")
    err = _check_month(month) or _check_product(product)
    if err:
        return _bad(err)
    return run_rca(month, product, use_llm=data.get("use_llm", True))


# ---------------- 知识库（含知识图谱） ----------------
@app.get("/api/knowledge/search", tags=["知识库"], summary="混合检索（BM25 + 向量 [+ 图谱增强]）")
def knowledge_search(q: str, product: str = None, top_k: int = 5, mode: str = None, alpha: float = None):
    err = _check_product(product)
    if err:
        return _bad(err)
    if not q or not q.strip():
        return _bad("缺少查询参数 q")
    if not (1 <= top_k <= 30):
        return _bad("top_k 需在 1~30 之间")
    if mode and mode not in ("bm25", "vector", "hybrid", "auto"):
        return _bad("mode 需为 bm25 / vector / hybrid / auto")
    return kb.search(q, top_k=top_k, product_hint=product, mode=mode, alpha=alpha)


@app.get("/api/knowledge/stats", tags=["知识库"], summary="知识库统计（文档/知识块/切分/向量/图谱）")
def knowledge_stats():
    return {"统计": kb.stats(), "文档清单": kb.documents()}


@app.get("/api/knowledge/graph", tags=["知识库"], summary="知识图谱数据（ECharts + 三元组）")
def knowledge_graph(focus: str = None, limit: int = 90, triples: int = 0):
    if kb.graph is None:
        return {"节点": [], "关系": [], "统计": {}, "提示": "知识图谱未启用"}
    data = kb.graph.to_echarts(focus=focus, limit=max(10, min(limit, 300)))
    out = {"echarts": data, "统计": kb.graph.stats()}
    if triples:
        out["三元组"] = kb.graph.triples(limit=max(1, min(triples, 200)))
    if focus:
        out["邻居"] = {k: [{"关系": r["关系"], "证据": r["证据"]} for r in v]
                       for k, v in kb.graph.neighbors(focus, hops=1).items()}
    return out


# ---------------- 模块一：报告 ----------------
@app.post("/api/report", tags=["报告"], summary="生成报告并按需导出 Word / PDF")
def report(data: dict = Body(..., examples=[{"month": "2026-06", "product": "六味地黄胶囊",
                                            "scene": "月度成本分析", "topic": "综合成本分析"}])):
    month, product = data.get("month"), data.get("product")
    scene, topic = data.get("scene", "月度成本分析"), data.get("topic")
    from . import report_model as rm
    err = _check_month(month) or _check_product(product)
    if err:
        return _bad(err)
    if scene not in rm.SCENES:
        return _bad(f"非法分析主题 {scene}，可选：{'、'.join(rm.SCENES)}")
    if topic and topic not in rm.TOPICS:
        return _bad(f"非法专题 {topic}，可选：{'、'.join(rm.TOPICS)}")
    try:
        return report_gen.generate(month, product, scene, topic=topic,
                                   use_llm=data.get("use_llm", True),
                                   want_charts=data.get("charts", True),
                                   pdf=data.get("pdf", True))
    except Exception as e:
        log.exception("报告生成失败")
        return _bad(f"报告生成失败：{e}", 500)


@app.get("/api/report/options", tags=["报告"], summary="可选主题/专题/月份/产品与引擎可用性")
def report_options():
    from . import pdf_export
    from . import report_model as rm
    return {"scenes": rm.SCENES, "topics": rm.TOPICS,
            "months": ds.months_list(), "products": ds.products_list(),
            "pdf_backends": pdf_export.available_backends(),
            "charts_engine": charts_engine()}


_SAFE_EXT = {".docx", ".pdf", ".png"}


@app.get("/api/report/download", tags=["报告"], summary="下载报告（Word/PDF）")
def report_download(file: str, fmt: str = None):
    name = os.path.basename(file or "")
    if fmt and fmt.lower() in ("pdf", "docx"):
        name = os.path.splitext(name)[0] + "." + fmt.lower()
    if not name or os.path.splitext(name)[1].lower() not in _SAFE_EXT:
        return _bad("非法文件名（仅允许 docx/pdf/png）")
    path = os.path.join(report_gen.OUT_DIR, name)
    if not os.path.exists(path):
        return JSONResponse({"error": "file not found", "file": name}, status_code=404)
    media = {"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
             "pdf": "application/pdf", "png": "image/png"}[os.path.splitext(name)[1].lower()[1:]]
    return FileResponse(path, filename=name, media_type=media)


@app.get("/api/report/chart", tags=["报告"], summary="报告内嵌图表原图")
def report_chart(file: str):
    name = os.path.basename(file or "")
    if os.path.splitext(name)[1].lower() != ".png":
        return _bad("非法文件名（仅允许 png）")
    path = os.path.join(report_gen.CHART_DIR, name)
    if not os.path.exists(path):
        return JSONResponse({"error": "file not found"}, status_code=404)
    return FileResponse(path, filename=name, media_type="image/png")


@app.get("/api/report/list", tags=["报告"], summary="报告归档（Word/PDF 成对）")
def report_list():
    import glob
    out = {}
    for p in sorted(glob.glob(os.path.join(report_gen.OUT_DIR, "report_*.*")), reverse=True):
        base, ext = os.path.splitext(os.path.basename(p))
        if ext.lower() not in (".docx", ".pdf"):
            continue
        item = out.setdefault(base, {"base": base, "docx": None, "pdf": None,
                                     "size_kb": 0, "mtime": os.path.getmtime(p)})
        item[ext.lower()[1:]] = os.path.basename(p)
        item["size_kb"] += round(os.path.getsize(p) / 1024)
        item["mtime"] = max(item["mtime"], os.path.getmtime(p))
    rows = sorted(out.values(), key=lambda x: -x["mtime"])
    for r in rows:
        r["mtime"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["mtime"]))
    return {"reports": rows[:20]}


# ---------------- 模块三：对标 ----------------
@app.get("/api/benchmark", tags=["对标分析"], summary="对标三步法（找差异→拆结构→拆原因）")
def bench(month: str, product: str = None, use_llm: bool = True):
    err = _check_month(month) or _check_product(product)
    if err:
        return _bad(err)
    return benchmark.benchmark(month, product, use_llm=use_llm)


# ---------------- 加分项：预测 / 整改闭环 / Agent ----------------
@app.get("/api/forecast", tags=["智能体与预测"], summary="成本预测（四模型回测对比 + 自动选优）")
def forecast_endpoint(product: str = None, steps: int = 3):
    err = _check_product(product)
    if err:
        return _bad(err)
    if not (1 <= steps <= 12):
        return _bad("steps 需在 1~12 之间")
    from .forecast import forecast
    return forecast(product=product, steps=steps)


@app.post("/api/rpa/dispatch", tags=["整改闭环"], summary="生成整改任务并推送模拟 RPA")
def rpa_dispatch(data: dict = Body(..., examples=[{"month": "2026-06", "product": "六味地黄胶囊"}])):
    month, product = data.get("month"), data.get("product")
    err = _check_month(month) or _check_product(product)
    if err:
        return _bad(err)
    return rpa_mod.dispatch(month, product, use_llm=data.get("use_llm", True))


@app.get("/api/rpa/tasks", tags=["整改闭环"], summary="整改任务列表与统计")
def rpa_tasks(refresh: bool = False):
    return {"tasks": rpa_mod.list_tasks(refresh=refresh), "stats": rpa_mod.stats()}


@app.get("/api/rpa/timeline", tags=["整改闭环"], summary="整改任务时间轴视图（状态流转事件流）")
def rpa_timeline(month: str = None, product: str = None, limit: int = 20):
    if month:
        err = _check_month(month)
        if err:
            return _bad(err)
    err = _check_product(product)
    if err:
        return _bad(err)
    return rpa_mod.timeline(month=month, product=product, limit=max(1, min(limit, 100)))


@app.post("/api/agent/decide", tags=["智能体与预测"], summary="Agent 自主决策（生成报告 / 仅更新看板）")
def agent_decide(data: dict = Body(default={}, examples=[{"month": "2026-06", "product": "六味地黄胶囊"}])):
    month, product = (data or {}).get("month"), (data or {}).get("product")
    err = _check_month(month) or _check_product(product)
    if err:
        return _bad(err)
    return agent_mod.decide(month, product, use_llm=(data or {}).get("use_llm", False))


@app.post("/api/agent/run", tags=["智能体与预测"], summary="执行 Agent 决策（可生成报告、下发整改）")
def agent_run(data: dict = Body(..., examples=[{"month": "2026-06", "product": "六味地黄胶囊"}])):
    month, product = data.get("month"), data.get("product")
    err = _check_month(month) or _check_product(product)
    if err:
        return _bad(err)
    return agent_mod.run(month, product, use_llm=data.get("use_llm", True),
                         do_report=data.get("do_report"), do_dispatch=data.get("do_dispatch"))


@app.get("/api/agent/policy", tags=["智能体与预测"], summary="Agent 决策策略与阈值")
def agent_policy():
    return agent_mod.policy()


@app.get("/api/agent/history", tags=["智能体与预测"], summary="Agent 决策历史")
def agent_history(limit: int = 20):
    return {"history": agent_mod.history(limit=max(1, min(limit, 50)))}


# ---------------- 静态前端 ----------------
app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(BASE, "static", "index.html"))
