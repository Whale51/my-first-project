# 本次改动说明 · 报告 PDF / Word 双格式导出

> 对应交接清单 P0-0.2「补 PDF 导出」与 P0-0.3「报告内嵌图表」。
> 目标：按用户选择的**分析主题（月度成本分析 / 季度成本分析 / 专题分析）+ 分析月份 + 目标产品**
> 自动生成完整报告，并支持 **PDF 与 Word 双格式导出**，两个格式均保持专业排版
> （页眉页脚、表格样式、图表嵌入），报告内容融合「模板结构 + 数据填充 + RAG 行业/工艺知识增强 + 大模型分析文本」。

---

## 一、新增文件

| 文件 | 作用 |
|---|---|
| `app/period.py` | 分析周期抽象与统一聚合口径。月度/季度同一套"产量加权平均"计算，年度同比数据自动定位（2025 年 1–6 月）。`build_period()` → 周期描述；`agg_kpi / agg_material_diff / agg_oh_unit / agg_labor / period_alerts` → 周期指标 |
| `app/charts.py` | 图表渲染统一入口（引擎自动选择）。7 张图：近 6 月趋势（柱+折线双轴）、单位成本构成（环形）、环比变动瀑布、主要材料影响、关键药材行情指数、两厂对标、实际 vs 上期/去年同期/预算 |
| `app/charts_pillow.py` | 图表引擎 2：Pillow 自绘（无 matplotlib 时使用），3 倍超采样保证线条与中文清晰 |
| `app/charts_gdi.py` + `app/_render_charts.ps1` | 图表引擎 3：Windows GDI+（.NET `System.Drawing`）零第三方依赖出图，**保证未装任何 Python 绘图库时报告仍有图表嵌入** |
| `app/report_model.py` | **报告数据模型**：把模板占位符取值、6 张动态表格 + 6 张指标/明细表、RAG 知识引用、大模型分析文本、图表数据组装成与渲染格式无关的统一模型；同时生成 PDF 原生排版的章节树（含「附录 · 知识库引用来源」） |
| `app/pdf_export.py` | PDF 导出三级后端 + reportlab 原生排版引擎（封面、页眉页脚、"第 X 页 / 共 Y 页"两遍绘制、专业表格样式、图表等比嵌入） |
| `scripts/verify_period.py` | 口径自验：周期聚合路径与既有月度口径**逐项对拍**（全部月份 × 全部产品），保证不引入回归 |
| `scripts/verify_report.py` | 报告自验：3 个主题 × Word/PDF 导出，检查占位符残留、页眉页脚、图表数量 |
| `scripts/verify_api.py` | 接口自验：`/api/meta` → `/api/report` → 下载 Word/PDF/图表 → 归档列表 → 非法参数拦截 |
| `scripts/fetch_vendor.py` | 受限网络/离线部署：按镜像顺序重试下载 wheel 并解包到项目内 `vendor/`（`app/__init__.py` 自动加载），适用于不能写 site-packages 的机器 |
| `scripts/pdf_to_png.ps1` | 辅助工具：用系统自带 `Windows.Data.Pdf` 把 PDF 页面渲染成 PNG，便于人工检查排版 |
| `requirements-optional.txt` | 可选增强依赖（`pywin32`：调用本机 Word 做高保真 PDF 导出） |
| `.gitignore` | 排除 `.env`、`outputs/`、`vendor/`、`__pycache__/` 等 |

## 二、修改文件

| 文件 | 改动 |
|---|---|
| `app/report_gen.py` | 重写为"模型 → Word 渲染 → PDF 导出"编排：填充占位符 → 插入 6 张动态表格 → 按章节嵌入图表（带图注）→ 设置页眉页脚 → 季度措辞替换 → 追加 RAG 来源附录；返回 Word/PDF 双下载地址与 PDF 引擎信息 |
| `app/main.py` | `/api/report` 支持 `scene`/`topic`/`charts`/`pdf` 入参并返回 `download_pdf`、`pdf_engine`、`charts`、`sources`、`leftover` 等；新增 `/api/report/options`、`/api/report/chart`、`/api/report/list`；下载接口做扩展名白名单与 `fmt` 切换；`/api/meta` 返回主题/专题/PDF 后端可用性 |
| `static/index.html` | 报告面板：分析主题（3 种）+ 专题方向（5 种）选择、生成结果展示（成稿结构统计、占位符残留、PDF 引擎）、**Word / PDF 双下载按钮**、7 张图表预览、报告归档列表 |
| `requirements.txt` | 增加 `matplotlib`、`pillow`（图表嵌入）与 `reportlab`（PDF 原生排版兜底） |
| `app/llm.py` | 增加显式超时/重试（`LLM_TIMEOUT`、`LLM_MAX_RETRIES`），接口异常时快速降级回本地规则引擎，避免报告生成被网络拖住 |
| `run.bat` / `run.sh` | 首步安装依赖后，顺带安装 `requirements-optional.txt`（失败不影响运行） |
| `README.md` / `TODO-优化清单.md` | 补充双格式导出说明、接口文档、自检脚本与完成状态 |

## 三、PDF 与图表的"多级降级"设计

```
PDF_ENGINE=auto（默认顺序，任一成功即返回，并对产物做体检：%PDF 头 / 体积 / 页数）
  ① libreoffice  soffice --headless --convert-to pdf     版式与 Word 100% 一致（跨平台；服务器推荐）
  ② native       reportlab 原生排版                       约 1 秒出稿、不依赖 Office，演示与离线首选
  ③ word         Microsoft Word COM                       Windows + Office 环境，所见即所得
                   ├─ 有 pywin32：win32com 直连
                   └─ 无 pywin32：自动改用系统自带 PowerShell COM（超时清理 + 自动重试 1 次）

图表引擎（app/charts.py 自动选择）
  ① matplotlib   质量最佳（首选，requirements.txt 已含）
  ② Pillow       轻量自绘（3 倍超采样，中文清晰）
  ③ Windows GDI+  系统自带 .NET System.Drawing，零第三方依赖（app/_render_charts.ps1）
  ④ 都没有       仅告警、不报错，报告其余部分照常生成
```

* 接口返回 `pdf_engine` 与 `stats.图表引擎`，前端直接展示本次实际使用的引擎；
* 想要"与 Word 完全一致"的 PDF：`PDF_ENGINE=word`（或 `libreoffice`）；想要"秒级出稿"：`PDF_ENGINE=native`；
* `native` 后端与 Word 报告**同源**（同一份 `report_model`），因此两者章节、表格、图表、数据完全一致；
* 依赖装不上时的两条后路（已在受限网络环境实测）：
  * `python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`
  * `python scripts/fetch_vendor.py`：逐个镜像重试，把 wheel 直接解包到项目内 `vendor/`，
    项目启动时 `app/__init__.py` 自动加入 `sys.path`（适用于不能写 site-packages 的机器）。

## 四、报告内容如何"融合"

| 要求 | 实现 |
|---|---|
| 模板结构 | 官方 `data/template_official.docx` 的 `{{占位符}}` + 固定章节（封面/总成本概览/要素明细/专项分析/对标/总结） |
| 数据填充 | `data_service` + `period` 的产量加权平均口径；6 张动态表格（趋势、材料明细、行情跟踪、对标差异、改进建议、整改任务） |
| RAG 增强 | `app/rag.py`（jieba + BM25，可升级向量）命中片段进入大模型 Prompt，并在报告「附录 · 知识库引用来源」列出出处与相关度 |
| 大模型文本 | 归因分析段落与对标归因由大模型撰写（`rca.SYSTEM` / `benchmark.SYSTEM`），失败自动降级为规则模板 |
| 图表嵌入 | 7 张服务端渲染 PNG 按章节嵌入 Word 与 PDF，带图注编号 |
| 页眉页脚 | Word：页眉=报告标题 + 报告类型·期间；页脚=编制单位 + 「第 X 页 / 共 Y 页」域 + 保密标识。PDF 同结构 |

## 五、验证方式

```bash
python scripts/verify_period.py     # ✅ 全口径一致（月度聚合路径 = 既有月度口径）
python scripts/verify_report.py     # ✅ 3 主题导出成功，占位符残留 0 处
python scripts/verify_api.py        # ✅ 接口全链路通过
```

页面操作：**http://127.0.0.1:8000 → 报告生成 → 选主题/月份/产品 → 生成报告 → 下载 Word / 下载 PDF**。

## 六、顺带修复

* **模板占位符锚点被清空**：原 `_fill_paragraph` 会把不在取值表中的 `{{占位符}}` 替换为空字符串，
  导致 6 张动态表格的插入锚点（`{{近6个月成本趋势表格}}` 等）全部失效、表格从未插入（Word 报告只有 9 张模板表）。
  现已保留未知占位符并正确插入 → Word 报告表格数 9 → 16（含附录表）。
* **季度报告措辞**：模板表头写死"上月/去年同月"，季度报告自动替换为"上期/去年同期"。
* **文件名规范化**：季度报告文件名形如 `report_2026Q2_六味地黄胶囊_20260929-201357.docx`。
