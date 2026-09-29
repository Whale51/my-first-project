# -*- coding: utf-8 -*-
"""FastAPI 接口测试（fastapi.testclient，无需启动 uvicorn / 不占用端口）。

覆盖：
  * GET /api/meta 200 且含 months/products/scenes
  * GET /api/kpi?month=2026-06&product=六味地黄胶囊 200
  * GET /api/alerts?month=2026-05 200
  * GET /api/benchmark?month=2026-06&use_llm=false 200
  * GET /api/forecast?product=六味地黄胶囊 200
  * GET /api/knowledge/search?q=连翘单耗&top_k=3 200 且结果是列表
  * 参数校验：POST /api/report 缺 month → 400；GET /api/report/download?file=../app/main.py → 400
  * 非法月份：期望 400；若实现返回 500 则断言"非 2xx"并在断言消息中标注为已知缺陷
    （任务要求：不在 app/ 下改业务代码）

运行：python -m pytest tests/test_api.py -q
      python -m unittest tests.test_api -v
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from fastapi.testclient import TestClient            # noqa: E402

from app.main import app                             # noqa: E402

try:                                                # 兼容两种 unittest 加载方式
    from tests.conftest import (clean_reports_setup, clean_reports_teardown)
except ImportError:                                 # pragma: no cover
    from conftest import (clean_reports_setup, clean_reports_teardown)   # type: ignore

client = TestClient(app)

MONTH = "2026-06"
PRODUCT = "六味地黄胶囊"

_BEFORE = None


def setUpModule():
    """报告生成用例会在 outputs/ 落盘，模块结束统一清理。"""
    global _BEFORE
    _BEFORE = clean_reports_setup()


def tearDownModule():
    clean_reports_teardown(_BEFORE)


# ============================================================ 基础接口
def test_meta_returns_months_products_scenes():
    r = client.get("/api/meta")
    assert r.status_code == 200, r.text
    data = r.json()
    for key in ("months", "products", "scenes"):
        assert key in data, f"/api/meta 缺少 {key}"
        assert isinstance(data[key], list) and data[key], f"{key} 不应为空列表"
    assert MONTH in data["months"]
    assert PRODUCT in data["products"]
    assert "月度成本分析" in data["scenes"]


def test_kpi_ok():
    r = client.get("/api/kpi", params={"month": MONTH, "product": PRODUCT})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["月份"] == MONTH
    assert data["产品"] == PRODUCT
    assert isinstance(data["贡献度"], list) and len(data["贡献度"]) == 3
    assert data["单位成本"] > 0


def test_kpi_all_products_ok():
    """不传 product 时返回全部产品口径（产量加权平均）。"""
    r = client.get("/api/kpi", params={"month": MONTH})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["产品"] == "全部产品"
    assert abs(data["单位成本"] - data["总成本"] / data["产量"]) < 1e-6


def test_alerts_ok():
    r = client.get("/api/alerts", params={"month": "2026-05"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert "alerts" in data and isinstance(data["alerts"], list)
    assert data["alerts"], "2026-05 应有告警"
    names = " ".join(a["要素"] for a in data["alerts"])
    assert ("金银花" in names) or ("板蓝根" in names)


def test_benchmark_ok():
    r = client.get("/api/benchmark", params={"month": MONTH, "use_llm": "false"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert {"差异对比表", "结构树", "归因分析", "改进建议"} <= set(data)
    assert data["engine"] == "rule", "use_llm=false 必须走规则引擎"


def test_forecast_ok():
    r = client.get("/api/forecast", params={"product": PRODUCT})
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data.get("months"), list) and data["months"]
    assert isinstance(data.get("items"), list) and data["items"]
    item = data["items"][0]
    assert item["产品"] == PRODUCT
    assert isinstance(item["预测单位成本"], list) and item["预测单位成本"]


def test_knowledge_search_ok():
    r = client.get("/api/knowledge/search", params={"q": "连翘单耗", "top_k": 3})
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data, list), f"结果应为列表，实际 {type(data)}"
    assert 0 < len(data) <= 3, f"top_k=3 应返回 1~3 条，实际 {len(data)}"
    for hit in data:
        assert {"source", "doc_id", "score", "snippet"} <= set(hit)
        assert hit["score"] > 0


# ============================================================ 参数校验
def test_report_missing_month_returns_400():
    r = client.post("/api/report", json={"product": PRODUCT})
    assert r.status_code == 400, f"缺 month 应返回 400，实际 {r.status_code}: {r.text[:200]}"
    assert "month" in r.json().get("error", "")


def test_report_download_path_traversal_returns_400():
    r = client.get("/api/report/download", params={"file": "../app/main.py"})
    assert r.status_code == 400, f"路径穿越应返回 400，实际 {r.status_code}: {r.text[:200]}"


def test_report_download_bad_extension_returns_400():
    r = client.get("/api/report/download", params={"file": "app/main.py"})
    assert r.status_code == 400


def test_report_download_missing_file_returns_404():
    r = client.get("/api/report/download", params={"file": "report_不存在.docx"})
    assert r.status_code == 404, f"存在的文件名但文件缺失应 404，实际 {r.status_code}"


def test_kpi_invalid_month_is_rejected_with_400():
    """非法月份必须返回 400（客户端错误），而不是 500。

    历史缺陷 D3（已修复，本用例锁定）：旧版 `app/main.py::kpi()` 没有入参校验，
    `data_service.prev_month()` 的 `self.months.index(month)` 抛 ValueError →
    FastAPI 返回 500。现 `app/main.py` 的 `_check_month` 返回 400 + 可选值提示。
    """
    r = client.get("/api/kpi", params={"month": "非法月份"})
    assert r.status_code == 400, f"非法月份应返回 400，实际 {r.status_code}: {r.text[:200]}"
    assert "error" in r.json()


def test_kpi_invalid_product_is_rejected():
    """非法产品名同样应被校验拦截（旧版会 500）。"""
    r = client.get("/api/kpi", params={"month": MONTH, "product": "不存在的产品"})
    assert r.status_code == 400, f"非法产品应返回 400，实际 {r.status_code}: {r.text[:200]}"


def test_alerts_invalid_month_is_rejected():
    r = client.get("/api/alerts", params={"month": "2026-13"})
    assert r.status_code == 400, f"非法月份应返回 400，实际 {r.status_code}"


def test_report_generation_endpoint_contract():
    """POST /api/report 端到端：200 且返回前端依赖的四个 values 键。

    对应历史缺陷 D1（整改任务字段名不一致 → `KeyError: 'owner'` → 500），
    该用例是端到端的集成回归保护。
    """
    r = client.post("/api/report", json={"month": MONTH, "product": PRODUCT,
                                        "scene": "月度成本分析", "use_llm": False,
                                        "pdf": False})
    assert r.status_code == 200, f"报告生成应 200，实际 {r.status_code}: {r.text[:300]}"
    data = r.json()
    assert data["leftover"] == [], data["leftover"][:3]
    assert data["stats"]["表格数"] >= 10
    for key in ("总成本概览评述", "要素归因分析", "总结", "改进建议"):
        assert isinstance(data["values"].get(key), str) and data["values"][key].strip(), key


def test_structure_and_trend_endpoints():
    r = client.get("/api/structure", params={"month": MONTH, "product": PRODUCT})
    assert r.status_code == 200
    assert set(r.json()) == {"直接材料", "直接人工", "制造费用"}

    r = client.get("/api/trend", params={"product": PRODUCT})
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 6 and {"月份", "总成本", "单位成本"} <= set(rows[0])


def test_index_and_static_frontend_served():
    """/ 与 /static 由 FastAPI 挂载（前端面板依赖）。"""
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")

    r = client.get("/static/echarts.min.js")
    assert r.status_code == 200, "本地 ECharts 必须存在（禁止改回 CDN）"


# ---------------------------------------------------------------- unittest 兼容
try:
    from tests.conftest import make_test_case
except ImportError:                                 # pragma: no cover
    from conftest import make_test_case             # type: ignore

TestApi = make_test_case(globals())


if __name__ == "__main__":                       # pragma: no cover
    import unittest

    unittest.main(verbosity=2)
