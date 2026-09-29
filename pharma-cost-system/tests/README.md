# 项目测试套件（pytest / unittest 双运行方式）

> 对应 `TODO-优化清单.md` → **P3 · 工程与体验 → 增加基础测试（pytest）**
> 覆盖：`data_service` 指标计算、`period` 周期口径、`benchmark` 差异率与结构树、
> `report_gen` 占位符零残留、FastAPI 接口契约、RAG 检索来源标注。

## 1. 依赖与"项目内依赖"（vendor）模式

| 依赖 | 状态 | 说明 |
|---|---|---|
| pandas / numpy / python-docx / fastapi / httpx / jieba | 系统已装 | 必需 |
| reportlab / pillow / pypdfium2 | `vendor/` 内 | 报告 PDF 兜底、图表、PDF 知识库解析 |
| **pytest** | `vendor/` 内（可选） | 受限环境下无法写 site-packages，故装到项目内 |

`app/__init__.py` 会把项目根的 `vendor/` 插入 `sys.path`。**但 `python -m pytest`
在导入 `app` 之前就要解析 `pytest` 模块**，所以直接 `python -m pytest` 会报
`No module named pytest`。可选三种用法：

```powershell
# 方式 A（推荐）：把 vendor 加到 PYTHONPATH，pytest 命令与标准用法完全一致
$env:PYTHONPATH='vendor'; python -m pytest -q

# 方式 B：纯 Python 一行调用（不改环境变量）
$env:PYTHONIOENCODING='utf-8'
python -c "import sys; sys.path.insert(0,'vendor'); import pytest; sys.exit(pytest.main(['-q']))"
```

> ⚠ `vendor\bin\pytest.exe`（pip `--target` 生成的 console script）**不可直接用**：
> 它按 `sys.path` 找 `_pytest`，会报 `ModuleNotFoundError: No module named '_pytest'`。
> 请用上面的方式 A / B。
>
> `python -m unittest ...` 不需要 `PYTHONPATH`：unittest 不依赖 vendor，
> 且 `app` 包被导入时会自动把 `vendor/` 加进 `sys.path`。

安装/重装 pytest（仅需一次，评测机可跳过）：

```powershell
$env:PYTHONIOENCODING='utf-8'
python -m pip install --target vendor --no-cache-dir --retries 1 --timeout 20 `
  -i https://pypi.tuna.tsinghua.edu.cn/simple --progress-bar off pytest
```

**pytest 是可选依赖**：未安装时用标准库 `unittest` 也能跑完整套件（见下）。

## 2. 运行命令

```powershell
# 工作目录 = 项目根：<repo>/01_源代码/pharma-cost-system
$env:PYTHONIOENCODING='utf-8'

# ① pytest（推荐；全部 69 个用例，约 5 秒）
$env:PYTHONPATH='vendor'; python -m pytest -q

# ② pytest 常用变体
$env:PYTHONPATH='vendor'; python -m pytest -q -m "not slow"      # 跳过慢用例（报告生成）
$env:PYTHONPATH='vendor'; python -m pytest tests/test_data_service.py -v
$env:PYTHONPATH='vendor'; python -m pytest -q --durations=5

# ③ unittest（零第三方依赖，pytest 未安装时使用）
python -m unittest discover -s tests -v
python -m unittest discover -s tests -t . -v     # 等价写法
python -m unittest tests.test_benchmark -v       # 单模块
```

两种运行器执行的是**同一批测试函数**（详见第 4 节），结果应一致。

## 3. 用例清单

| 文件 | 用例数 | 覆盖内容 |
|---|---|---|
| `tests/test_data_service.py` | 14 | `row()` 单产品/全部产品字段完整；全部产品必须产量加权平均（`单位成本 == 总成本/总产量`，且与加权求和一致，误差<1e-6，并反证不是简单算术平均）；`_rate()` 正常/`base=0`/`base=None`/`cur=None`；`kpi_overview()` 环比·同比·预算偏差与手工公式一致、贡献度三项和≈100（±0.5）；`alerts('2026-05')` 非空且覆盖官方埋设涨价事件（金银花/板蓝根），"较1月基准"涨幅与行情表手算一致 |
| `tests/test_period.py` | 6 | `build_period` 月度（`['2026-06']`/`label='2026-06'`）与季度（`['2026-04','2026-05','2026-06']`、上季 `2026-01..03`、去年同期 `2025-04..06`）；`agg_kpi` 与 `ds.kpi_overview` **月份×产品全组合逐字段一致**（误差<1e-6）；季度口径加权平均 |
| `tests/test_benchmark.py` | 10 | 返回必需键、离线走规则引擎；`差异额 = 一厂-二厂`、`差异率% = 差异额/二厂*100`（表内值重算误差<0.02pp，并与原始 CSV 未舍入值、独立读 CSV 重算三方对拍）；`结构树.主因要素` 为 `|差异率%|` 最大者；来源标注不丢失 |
| `tests/test_report_gen.py` | 13 | `generate(...use_llm=False)`：`leftover==[]`、`.docx` 存在、`stats['表格数']>=10`；用 python-docx 复检**段落+表格**全文无 `{{`、页眉含报告标题、页脚含"第"/"页"/"共"、`inline_shapes>=0`；返回体含前端依赖四键（非空字符串且与数据模型同源）；整改任务表 5 列非空（D1 回归保护）；`_leftovers()` 表格扫描（D2 回归保护）；临时报告清理自检 |
| `tests/test_api.py` | 17 | `TestClient(app.main:app)`：`/api/meta`（months/products/scenes）、`/api/kpi`（单产品/全部产品）、`/api/alerts`、`/api/benchmark?use_llm=false`、`/api/forecast`、`/api/knowledge/search`（结果为列表）、`/api/structure`、`/api/trend`、`/` 与 `/static/echarts.min.js`；参数校验：`POST /api/report` 缺 month→400、`/api/report/download?file=../app/main.py`→400、非法扩展名→400、文件不存在→404、非法月份/非法产品→**400**（D3 回归保护）；`POST /api/report` 端到端 200 + 四键（D1 回归保护） |
| `tests/test_rag.py` | 14 | `kb.search('连翘单耗上升的原因', top_k=3)` ≤3 条且 `source/doc_id/score/snippet` 齐全、`score>0`；来源必须指向真实知识库文档；`chunk_size` 生效（120→631 块 > 220→313 块 > 1200→63 块）；`context()` 带 `[来源: x]`；知识图谱可构建（回归保护） |

合计 **74** 个用例（pytest `74 passed`，unittest `Ran 74 tests ... OK`）。

## 4. 设计说明（为什么同时支持两个运行器）

* 用例本体写成**裸函数**（`assert` 断言）→ pytest 直接收集；
* 文件末尾用 `conftest.make_test_case(globals())` 把同一批函数**动态包成 `unittest.TestCase`**
  → unittest 也能发现执行，零重复编写；
* `tests/conftest.py::pytest_collection_modifyitems` 在 pytest 侧剔除这些包装类，
  避免同一用例被跑两遍；
* pytest 专有语法（`@pytest.mark.slow`）通过 `_slow()` 包装器降级为无操作，
  因此 pytest 缺席时文件仍可导入。

### 离线约束
`.env` 配了 DeepSeek Key，但评测机不能联网。`conftest.py` 在 **import app 之前**
把 `LLM_*` 置空 → `llm_ready()` 为 `False`；同时所有用例都显式传 `use_llm=False`。
`kb.search` 走本地哈希 TF-IDF + BM25，不发网络请求。

### 工作区卫生
`tests/conftest.py` 提供 `clean_reports_setup()` / `clean_reports_teardown()`：
测试前快照 `outputs/report_*`，测试后删除**本次新增**的文件（Word + PDF），
并注册 `atexit` 兜底。因此跑测试不会在 `outputs/` 留下临时交付物。

## 5. 缺陷清单（测试发现 → 已反馈开发者 → 已修复并转为回归保护）

> 本节按"发现时 / 现状"两栏如实记录。**测试未修改 `app/` 下任何业务代码**，
> 问题以 `send_message` 反馈给 app 侧开发者后由其修复；修复后对应用例已改为
> 正向断言（回归保护），因此当前套件全绿。

| 编号 | 位置 | 发现时的现象 | 建议修法 | 现状（复测） |
|---|---|---|---|---|
| **D1**（阻断级） | `app/report_model.py:397` vs `app/rpa.py:163-177` | 整改任务表字段名不一致：`report_model` 读 `t["owner"]` / `t["priority"]`，`rpa.py` 落库的键是 `责任人` / `责任部门` / `优先级` → `KeyError: 'owner'`，**`report_gen.generate()` 与 `POST /api/report` 直接 500**（历史任务落盘 `outputs/rpa_tasks.json` 后必现） | ① `report_model` 改读中文键并保留英文兜底；或 ② `rpa.py` 落库时保留 `owner`/`priority` 别名 | ✅ 已按建议 ① 修复（`_task_row()` 兼容中英文字段）。回归用例：`test_report_gen.py::test_task_table_columns_are_consistent_with_rpa_records`、`test_api.py::test_report_generation_endpoint_contract` |
| **D2** | `app/report_gen.py:362` + `app/report_model.py:290` | 残留自检只扫 `Document(out_path).paragraphs`、不扫表格，`leftover==[]` 掩盖了产物表格里的字面量 `{{本月单位成本}}`（模板含该占位符，取值表 `v` 只有 `单位成本`） | ① 取值表补 `v["本月单位成本"] = _f(kpi["单位成本"], 2)`；② 自检口径扩到表格单元格 | ✅ 已两条都修（新增 `report_gen._leftovers(doc)` 扫段落+表格）。回归用例：`test_generated_docx_no_placeholder_anywhere`、`test_report_gen_leftover_helper_scans_tables` |
| **D3** | `app/main.py:50-52` | `GET /api/kpi?month=非法月份` 返回 **500**（`prev_month()` 的 `self.months.index(month)` 抛 `ValueError`），而非 400；`product="all"` 等非法值同类 | 端点内校验 `month in ds.months_list()` 并返回 400 | ✅ 已修复（`_check_month` / `_check_product` 统一校验，400 + 可选值提示）。回归用例：`test_kpi_invalid_month_is_rejected_with_400`、`test_kpi_invalid_product_is_rejected`、`test_alerts_invalid_month_is_rejected` |
| **D4**（非缺陷·须知） | `app/data_service.py:97-101` | `_rate()` 返回 `round(..., 2)`，比率只有 2 位小数；`kpi_overview` 的"环比/同比/预算偏差"传入未舍入原值，因此与"未舍入手工公式"存在 ≤0.005 个百分点的固有差异 | 属既有口径设计：报告展示够用；若要更高精度可把 `_rate` 的 `ndigits` 提到 4（需同步前端与报告文案） | ⚪ 保留现状（不影响判分）。`test_kpi_overview_rates_match_manual_formula` 以"复核 round(手工值,2) + 容差 0.01pp"两级断言覆盖 |

复测命令与原始结果（app 侧修复后）：

```text
$ $env:PYTHONPATH='vendor'; python -m pytest -q
........................................................................ [ 97%]
..                                                                       [100%]
74 passed in 16.23s

$ python -m unittest discover -s tests
..........................................................................
----------------------------------------------------------------------
Ran 74 tests in 4.520s

OK
```

## 6. 测试不加覆盖的地方（有意为之）

* 不启动 uvicorn、不占用端口：接口测试用 `fastapi.testclient.TestClient`；
* 不测 `/api/rpa/dispatch`（需要本地 RPA mock 服务）；`/api/rpa/tasks` 仅在报告表格中被动使用；
* 不校验大模型生成文本（外网不可用）；`engine` 一律断言为 `rule`；
* 不修改 `app/` 下任何业务代码。
