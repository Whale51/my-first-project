# -*- coding: utf-8 -*-
"""pytest / unittest 共用的测试夹具与路径准备。

设计要点
--------
1. **双运行方式兼容**：本仓库的测试既可用 `python -m pytest -q`，也可用
   `python -m unittest discover -s tests -v`（pytest 为可选依赖）。
   所有 pytest 专有写法（fixture / parametrize / mark）都在 `HAS_PYTEST`
   为假时退化为普通函数与装饰器，保证 unittest 也能跑。
2. **离线优先**：`.env` 里配置了 DeepSeek API Key，但评测机外网不可用。
   这里在**导入 app 之前**清掉 LLM_* 环境变量，杜绝任何测试意外走网络。
3. **不污染工作区**：`clean_reports` 夹具在测试前后比对 `outputs/report_*`，
   自动删除测试新生成的报告文件（Word / PDF）。
"""
import os
import sys

# ---------------------------------------------------------------- 路径准备
# 项目根目录（tests/ 的上一级）加入 sys.path，保证 `from app import ...` 可用。
# 注意：必须在 import app 之前完成，因为 app 内部有模块级单例（ds / kb）。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ---------------------------------------------------------------- 强制离线
# app/paths.py 的 load_dotenv() 只在环境变量"不存在"时才写入，
# 因此这里提前把 LLM_* 置为空字符串即可阻止 .env 生效 → llm_ready() 为 False。
for _k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "LLM_MODEL_SMALL", "LLM_EMBED_MODEL"):
    os.environ[_k] = ""
os.environ.setdefault("PDF_ENGINE", "native")   # 报告测试里不依赖 LibreOffice / Word

# ---------------------------------------------------------------- pytest 可选
try:                                     # pragma: no cover - 取决于环境
    import pytest
    HAS_PYTEST = True
except Exception:                        # pragma: no cover
    pytest = None
    HAS_PYTEST = False


def pytest_configure(config):            # pragma: no cover - 仅 pytest 调用
    """注册 slow 标记的兜底（pytest.ini 缺失时也不报错）。"""
    config.addinivalue_line("markers", "slow: 较慢的测试（报告生成/Word 渲染）")


def pytest_collection_modifyitems(items):   # pragma: no cover - 仅 pytest 调用
    """跳过 make_test_case() 生成的 TestCase 包装类，避免同一用例被跑两遍。

    本仓库的测试是"裸函数 + 动态 TestCase 包装"双形态（裸函数给 pytest、
    TestCase 给 unittest）。pytest 会同时收集两者，因此这里在 pytest 侧剔除
    由 make_test_case 生成的包装类（以 `__make_test_case__` 标记识别）。
    """
    keep = []
    for item in items:
        cls = getattr(getattr(item, "parent", None), "cls", None)
        if cls is not None and getattr(cls, "__make_test_case__", False):
            continue
        keep.append(item)
    items[:] = keep


# ---------------------------------------------------------------- 公共工具
OUTPUT_DIR = os.path.join(_ROOT, "outputs")


def list_reports():
    """当前 outputs/ 下已存在的 report_* 文件集合（绝对路径）。"""
    if not os.path.isdir(OUTPUT_DIR):
        return set()
    return {os.path.join(OUTPUT_DIR, n) for n in os.listdir(OUTPUT_DIR)
            if n.startswith("report_")}


def _cleanup_new_reports(before):
    """删除测试期间新增的报告文件，返回被删除的路径列表。"""
    removed = []
    for p in sorted(list_reports() - before):
        try:
            os.remove(p)
            removed.append(p)
        except OSError:
            pass
    return removed


_BASELINE = None          # 进程启动时的报告快照（兜底清理用，避免误删既有交付物）


def clean_reports_setup():
    global _BASELINE
    if _BASELINE is None:
        _BASELINE = list_reports()
    return list_reports()


def clean_reports_teardown(before):
    return _cleanup_new_reports(_BASELINE or set() if before is None else before)


def _install_atexit_cleanup():
    """进程退出兜底：即使用例中途崩溃，也只清理"本次运行新增"的报告文件。"""
    import atexit

    clean_reports_setup()                    # 固定基线快照
    atexit.register(lambda: _cleanup_new_reports(_BASELINE or set()))


_install_atexit_cleanup()


if HAS_PYTEST:                            # pragma: no cover - 仅 pytest 分支

    @pytest.fixture(scope="session")
    def project_root():
        return _ROOT

    @pytest.fixture(autouse=True)
    def _clean_generated_reports():
        """逐用例兜底：删除本用例在 outputs/ 新增的 report_* 文件。

        覆盖那些"顺带生成报告"的用例（如 `POST /api/report` 的接口测试），
        保证测试不会污染 outputs/。测试模块自己的清理逻辑（test_report_gen）
        仍在模块结束时执行，两者互不冲突。
        """
        before = clean_reports_setup()
        yield
        _cleanup_new_reports(before)

    @pytest.fixture
    def clean_reports():
        """自动清理本测试新生成的 outputs/report_* 文件（Word 与 PDF 一并清理）。"""
        before = list_reports()
        yield before
        _cleanup_new_reports(before)

    @pytest.fixture(scope="session")
    def ds():
        from app.data_service import ds as _ds
        return _ds

    @pytest.fixture(scope="session")
    def kb():
        from app.rag import kb as _kb
        return _kb


# ---------------------------------------------------------------- unittest 兼容
def _unwrap(fn):
    """取装饰器包装前的原始函数（pytest 的 mark 会把函数包成 MarkDecorator）。"""
    seen = 0
    while not callable(fn) and hasattr(fn, "mark") and seen < 5:
        try:
            fn = fn.mark.args[0]
        except Exception:
            break
        seen += 1
    return fn


def make_test_case(module_globals, name=None):
    """把模块里的 `test_*` 函数收进一个 unittest.TestCase 子类。

    pytest 直接收集裸函数，unittest 只认 TestCase 子类；本函数让**同一批测试函数**
    在两个运行器下都执行，无需重复编写用例。

    用法（放在测试模块末尾）::

        from conftest import make_test_case       # 或 from tests.conftest import ...
        TestXxx = make_test_case(globals())

    返回的类名默认由模块名生成（test_data_service → TestDataService），
    同时模块内的 `test_*` 函数仍会被 pytest 收集，因此 pytest 下不会重复执行。
    """
    import unittest

    mod_name = module_globals.get("__name__", "tests")
    short = mod_name.rsplit(".", 1)[-1]
    cls_name = name or ("Test" + "".join(p.capitalize() for p in short.split("_") if p != "test")
                        or "Tests")

    names = [k for k, v in list(module_globals.items())
             if k.startswith("test_")
             and _unwrap(v) is not None
             and (callable(v) or hasattr(v, "mark"))
             and getattr(_unwrap(v), "__module__", None) == mod_name]
    names.sort()

    members = {"__make_test_case__": True}
    for fn_name in names:
        fn = _unwrap(module_globals[fn_name])
        doc = fn.__doc__ or ""
        members[fn_name] = (lambda self, _fn=fn: _fn())
        members[fn_name].__doc__ = doc.strip().split("\n")[0]
        members[fn_name].__name__ = fn_name

    return type(cls_name, (unittest.TestCase,), members)
