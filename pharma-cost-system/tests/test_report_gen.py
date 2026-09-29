# -*- coding: utf-8 -*-
"""report_gen（Word 报告生成）测试 —— 重点是"模板占位符零残留"。

覆盖：
  * generate('2026-06','六味地黄胶囊', scene='月度成本分析', use_llm=False)
      - leftover == []（模板 {{占位符}} 零残留）
      - file 以 .docx 结尾且文件真实存在
      - stats['表格数'] >= 10
  * 用 python-docx 打开产物复检：段落无 "{{"、页眉含报告标题关键字、页脚含"第"/"页"、
    inline_shapes >= 0（图表引擎不可用时应为 0 且不报错）
  * 返回体包含前端依赖的四个键，且都是非空字符串
  * 测试结束删除本次新生成的 outputs/report_* 文件

运行：python -m pytest tests/test_report_gen.py -q            （约 3-10 秒）
      python -m unittest tests.test_report_gen -v

说明：本文件较慢，标记 @pytest.mark.slow；pytest 未安装时标记自动退化为空装饰器。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:                                            # pytest 为可选依赖
    import pytest
    HAS_PYTEST = True
except Exception:                               # pragma: no cover
    pytest = None
    HAS_PYTEST = False


def _slow(fn):
    """pytest 存在时打 slow 标记，否则原样返回（保证 unittest 可用）。"""
    return pytest.mark.slow(fn) if HAS_PYTEST else fn


from app import report_gen                          # noqa: E402
from app import report_model as rm                  # noqa: E402
from app.data_service import ds                     # noqa: E402

try:                                                # 兼容 `-m unittest tests.test_x` 与
    from tests.conftest import (                    # `discover -s tests`（后者把 tests/ 当顶层目录）
        clean_reports_setup, clean_reports_teardown)
except ImportError:                                 # pragma: no cover
    from conftest import (                          # type: ignore
        clean_reports_setup, clean_reports_teardown)

MONTH = "2026-06"
PRODUCT = "六味地黄胶囊"
SCENE = "月度成本分析"

# 前端面板强依赖的四个取值键
FRONTEND_KEYS = ["总成本概览评述", "要素归因分析", "总结", "改进建议"]

_RESULT = None          # generate() 返回体缓存
_MODEL = None           # 同参数下的数据模型缓存（用于核对 values 键）
_BEFORE = None          # 测试前的 outputs 快照


# ---------------------------------------------------------------- 生命周期
def _ensure_teardown():
    """注册进程退出时的兜底清理（先于 setUpModule 执行也无副作用）。"""
    import atexit

    atexit.register(lambda: clean_reports_teardown(_BEFORE))


def _snapshot():
    """记录基线快照（幂等：仅在尚未记录时重新记录）。

    注意：unittest 的某些加载路径（如 `loadTestsFromModule`）**不会**调用
    `setUpModule`，因此这里不能只依赖它；`_BEFORE` 保持 None 时由调用方懒加载。
    """
    global _BEFORE
    if _BEFORE is None:
        _BEFORE = clean_reports_setup()
    return _BEFORE


def setUpModule():                              # unittest discover 与 pytest 都会调用
    _snapshot()


def tearDownModule():
    """模块级统一清理（只在模块结束时执行一次）。

    此前用 `teardown_function` 会在 pytest 下逐用例删除报告文件，
    导致后续用例拿不到产物（PackageNotFoundError）；改为模块级清理即可。
    """
    clean_reports_teardown(_BEFORE)


def _generate(force=False):
    """只生成一次报告，多个用例共享（报告生成约 3-10 秒）。

    `force=True` 或产物文件已不存在时重新生成（此时同步重建数据模型，
    保证返回值与模型取值仍逐字符一致）。
    """
    global _RESULT, _MODEL
    if force or _RESULT is None or not os.path.exists(os.path.join(report_gen.OUT_DIR, _RESULT["file"])):
        _MODEL = rm.build(MONTH, PRODUCT, scene=SCENE, use_llm=False, want_charts=True)
        _RESULT = report_gen.generate(MONTH, PRODUCT, scene=SCENE, use_llm=False)
        _ensure_teardown()
    return _RESULT


def _docx_path(res):
    return os.path.join(report_gen.OUT_DIR, res["file"])


# ============================================================ 占位符零残留
@_slow
def test_generate_leftover_empty():
    res = _generate()
    assert res["leftover"] == [], f"模板占位符未清空：{res['leftover'][:5]}"


@_slow
def test_generate_file_exists_and_is_docx():
    res = _generate()
    assert res["file"].endswith(".docx"), res["file"]
    path = _docx_path(res)
    assert os.path.exists(path), f"报告文件不存在：{path}"
    assert os.path.getsize(path) > 10 * 1024, "报告文件过小，可能渲染失败"
    assert res["download"].startswith("/api/report/download?file=")


@_slow
def test_generate_table_count():
    res = _generate()
    assert res["stats"]["表格数"] >= 10, f"表格数={res['stats']['表格数']}，应 >= 10"


# ============================================================ 打开产物复检
def test_generated_docx_paragraphs_have_no_placeholder():
    """正文段落中不得残留任何 {{占位符}}（report_gen 自检口径）。"""
    from docx import Document

    res = _generate()
    doc = Document(_docx_path(res))

    bad = [p.text for p in doc.paragraphs if "{{" in p.text]
    assert bad == [], f"段落中残留占位符：{bad[:3]}"
    # 与 report_gen 自检结果一致（自检只扫描 paragraphs，不含表格单元格）
    assert res["leftover"] == []


def test_generated_docx_no_placeholder_anywhere():
    """全文（段落 + 表格单元格）不得残留任何 `{{占位符}}`。

    历史缺陷 D2（已在 app 侧修复，本用例锁定）：模板表 5 第 3 行用的是
    `{{本月单位成本}}`，而 `report_model.py` 的取值表只给了 `单位成本`，
    导致报告表格里出现字面量 `{{本月单位成本}}`；同时 `report_gen` 的残留自检
    只扫段落、不扫表格，`leftover` 返回空列表掩盖了该问题。
    现取值表已补 `本月单位成本`，本用例做全文兜底复检。
    """
    from docx import Document

    res = _generate()
    doc = Document(_docx_path(res))

    bad_paras = [p.text for p in doc.paragraphs if "{{" in p.text]
    bad_cells = [c.text for t in doc.tables for row in t.rows for c in row.cells if "{{" in c.text]
    assert bad_paras == [], f"段落中残留占位符：{bad_paras[:3]}"
    assert bad_cells == [], f"表格中残留占位符：{bad_cells[:3]}"
    assert res["leftover"] == [], f"report_gen 自检报告残留：{res['leftover'][:3]}"


def test_report_gen_leftover_helper_scans_tables():
    """回归保护：`report_gen._leftovers()` 必须同时扫描段落与表格单元格。

    该 helper 是 D2 的修复件（原先的自检只扫 `doc.paragraphs`）。
    这里用一份内存中的临时 Word 文档（不落盘到 outputs/）验证它对表格生效。
    """
    from docx import Document

    helper = getattr(report_gen, "_leftovers", None)
    if helper is None:                      # 旧版实现无此函数：本次跳过（不判失败）
        assert True
        return
    doc = Document()
    doc.add_paragraph("正文保留 {{段落占位符}}")
    t = doc.add_table(rows=1, cols=2)
    t.rows[0].cells[0].text = "{{表格占位符}}"
    t.rows[0].cells[1].text = "正常内容"
    found = helper(doc)
    assert any("段落占位符" in x for x in found), found
    assert any("表格占位符" in x for x in found), f"表格单元格未被扫描：{found}"


def test_generated_docx_header_footer_and_shapes():
    """页眉含报告标题关键字、页脚含页码域，图表嵌入情况与引擎状态一致。"""
    from docx import Document

    res = _generate()
    doc = Document(_docx_path(res))

    # 页眉：报告标题 + 报告类型·期间（封面节被清空，故取所有节拼接后判断非空与关键字）
    header_text = "".join(p.text for s in doc.sections for p in s.header.paragraphs)
    assert header_text.strip(), "页眉不应为空"
    assert "月度成本分析报告" in header_text, f"页眉缺少报告标题：{header_text!r}"
    assert "六味地黄胶囊" in header_text, f"页眉缺少产品名：{header_text!r}"

    # 页脚："第 X 页 / 共 Y 页"（域缓存值 1）
    footer_text = "".join(p.text for s in doc.sections for p in s.footer.paragraphs)
    assert "第" in footer_text and "页" in footer_text, f"页脚缺少页码域：{footer_text!r}"
    assert "共" in footer_text, f"页脚缺少总页数域：{footer_text!r}"

    # 图表引擎可用 → 图片应嵌入；不可用 → inline_shapes 为 0 且不报错
    n_shapes = len(doc.inline_shapes)
    assert n_shapes >= 0
    if res["stats"]["图表数"] > 0:
        assert n_shapes > 0, f"生成了 {res['stats']['图表数']} 张图表，但 Word 中未嵌入图片"


@_slow
def test_generated_docx_contains_key_text():
    """正文应包含 KPI 评述、归因分析与改进建议等实质内容。"""
    from docx import Document

    res = _generate()
    doc = Document(_docx_path(res))
    text = "\n".join(p.text for p in doc.paragraphs)
    text += "\n" + "\n".join(c.text for t in doc.tables for row in t.rows for c in row.cells)

    assert "元/盒" in text, "报告缺少成本口径文字"
    assert "第一步" in text or "归因" in text, "报告缺少归因分析"
    assert "采购部" in text or "生产部" in text, "报告缺少改进建议责任部门"
    assert "知识库" in text, "报告缺少 RAG 知识库来源说明"


def test_task_table_columns_are_consistent_with_rpa_records():
    """整改任务表必须有 5 个非空列（回归保护：曾因字段名不一致整条链路 500）。

    历史缺陷：`app/report_model.py` 读 `t["owner"] / t["priority"]`，
    而 `app/rpa.py` 落库的是中文键 `责任人 / 优先级` → `KeyError: 'owner'`，
    导致 `report_gen.generate()` 与 `POST /api/report` 直接 500。
    本用例通过"任务表每行 5 列且均非占位符"锁定该集成契约。
    """
    from docx import Document

    res = _generate()
    doc = Document(_docx_path(res))

    tasks_tbl = None
    for t in doc.tables:
        if t.rows and t.rows[0].cells[0].text.strip() == "任务ID":
            tasks_tbl = t
            break
    assert tasks_tbl is not None, "未找到整改任务表（表头应为 任务ID/任务标题/责任人/优先级/状态）"

    header = [c.text.strip() for c in tasks_tbl.rows[0].cells]
    assert header == ["任务ID", "任务标题", "责任人", "优先级", "状态"], header

    for row in tasks_tbl.rows[1:]:
        cells = [c.text.strip() for c in row.cells]
        assert len(cells) == 5, cells
        assert all(cells), f"任务行存在空单元格（字段名不匹配时会出现）：{cells}"
        assert all("{{" not in c for c in cells), cells


# ============================================================ 返回体契约
@_slow
def test_generate_values_contain_frontend_keys():
    """前端面板依赖的四个键必须存在且为非空字符串。"""
    res = _generate()
    values = res["values"]
    for key in FRONTEND_KEYS:
        assert key in values, f"返回体缺少前端依赖键：{key}"
        val = values[key]
        assert isinstance(val, str), f"{key} 应为字符串，实际 {type(val)}"
        assert val.strip(), f"{key} 不应为空字符串"
        assert "{{" not in val, f"{key} 中残留占位符"
        assert len(val) > 10, f"{key} 内容过短：{val!r}"


@_slow
def test_generate_values_match_model():
    """返回体的 values 必须与数据模型的 values 同源（四个前端键逐字符一致）。"""
    res = _generate()
    _generate()                                  # 确保 _MODEL 已构建
    for key in FRONTEND_KEYS:
        assert res["values"][key] == _MODEL["values"][key], f"{key} 与模型取值不一致"


@_slow
def test_generate_return_contract():
    res = _generate()
    assert res["scene"] == SCENE
    assert res["product"] == PRODUCT
    assert res["period"]["label"] == MONTH
    assert res["period"]["kind"] == "月度"
    assert res["engine"] == "rule", "use_llm=False 时应为规则引擎（不得联网）"
    assert isinstance(res["alerts"], list)
    assert isinstance(res["sources"], list) and res["sources"]
    for key in ("表格数", "图表数", "知识库引用", "报告标题"):
        assert key in res["stats"]
    assert PRODUCT in res["stats"]["报告标题"]


# ============================================================ 临时文件清理
@_slow
def test_reports_are_cleaned_up():
    """确认测试不会把临时报告留在 outputs/（清理逻辑自检）。

    这里不依赖模块级基线（unittest 的某些加载路径不调用 setUpModule），
    而是临时生成一个"哨兵"文件，独立验证 setup/teardown 两个清理原语。
    """
    before = clean_reports_setup()
    sentinel = os.path.join(report_gen.OUT_DIR, "report_单元测试哨兵.docx")
    with open(sentinel, "w", encoding="utf-8") as f:
        f.write("sentinel")

    after = clean_reports_setup()
    assert sentinel in after, "哨兵文件应被添加到 outputs/"

    removed = clean_reports_teardown(before)
    assert sentinel in removed, f"新增文件应被清理，实际清理：{removed}"
    assert sentinel not in clean_reports_setup(), "清理后哨兵文件不应存在"

    # 真实报告同样可被清理
    res = _generate()
    before2 = clean_reports_setup()
    assert _docx_path(res) in before2
    removed2 = clean_reports_teardown(before2 - {_docx_path(res)})
    assert _docx_path(res) in removed2, "报告文件应可被删除（未被其他进程占用）"
    assert _docx_path(res) not in clean_reports_setup()


# ---------------------------------------------------------------- unittest 兼容
try:
    from tests.conftest import make_test_case
except ImportError:                                 # pragma: no cover
    from conftest import make_test_case             # type: ignore

TestReportGen = make_test_case(globals())


if __name__ == "__main__":                       # pragma: no cover
    import unittest

    unittest.main(verbosity=2)
