# 基于 RAG 与大模型的制药企业产品成本智能分析报告系统

2026 年第二届重庆市 AI 大模型创新应用大赛 · 企业出题赛道（重庆创灵境出题）参赛作品原型。

> **接手须知**：本项目已完整跑通赛题四大模块，可直接运行演示。
> 需要你做的两件事：① 配置大模型 API Key（见下方"第 2 步"）；② 按 `TODO-优化清单.md` 迭代优化。

---

## 一、快速开始（Windows）

**双击 `run.bat` 即可**。脚本会自动完成：安装依赖 → 启动官方模拟 RPA 服务(8090) → 启动本系统(8000)。

启动后在浏览器输入 **http://127.0.0.1:8000** 访问。

> ⚠️ 请不要双击 `static/index.html` 直接打开文件。`file://` 协议下浏览器会拦截所有 `/api` 请求，页面将没有任何数据。

### macOS / Linux

```bash
python3 -m pip install -r requirements.txt
python3 -c "from app.paths import rpa_server_file; import subprocess,sys; subprocess.run([sys.executable, rpa_server_file()])" &
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# 或直接执行 ./run.sh
```

### 依赖装不上 / PyPI 太慢怎么办

报告模块的图表与 PDF **都有零依赖兜底**（Windows GDI+ 图表、Word/LibreOffice 转 PDF），
所以即使 `matplotlib`/`pillow`/`reportlab` 装不上，功能也不会缺失。若想装齐，可用：

```bash
# 方式一：换国内镜像
python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 方式二：机器不允许写 site-packages 时，把依赖解包到项目内 vendor/（启动时自动加载）
python scripts/fetch_vendor.py            # 逐个镜像重试，把 wheel 解包到 vendor/
```

### Docker

```bash
docker build -t pharma-cost .
docker run -p 8000:8000 pharma-cost
```

---

## 二、配置大模型 API（重要）

系统默认使用**本地规则引擎**生成归因分析文本（离线也能完整演示，页面顶部会显示"本地规则模式"）。

赛题要求"归因分析文本由**大模型**生成"，因此建议配置一个大模型。**无需修改任何代码**：

1. 复制 `.env.example` 为 `.env`（与 `app/` 同级目录）
2. 填入任意一家 OpenAI 兼容接口的 Key：

```ini
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_API_KEY=sk-你的Key
LLM_MODEL=qwen-plus
```

3. 重启服务，页面顶部横幅会变为"大模型模式"

**免费额度获取渠道**（任选其一）：

| 平台 | 注册地址 | 说明 |
|---|---|---|
| 阿里云百炼（通义千问） | bailian.aliyun.com | 新用户送 100 万 tokens，推荐 |
| 硅基流动 SiliconFlow | siliconflow.cn | Qwen2.5-7B 等小模型完全免费 |
| DeepSeek | platform.deepseek.com | 价格极低，但基本为付费 |

> 配置后若接口调用失败，系统会**自动降级**回本地规则引擎，不会中断演示。

---

## 三、官方模拟数据放置位置

程序会自动查找官方数据目录，**推荐放在项目内**：

```
pharma-cost-system/
└── data_official/
    └── 创灵境_考题模拟数据/      ← 官方数据包解压到这里
        ├── 01_成本明细数据/
        ├── 02_行业参考数据/
        ├── 03_制药知识文档/
        ├── 04_报告模板/
        └── 05_RPA接口文档/
```

也支持：与项目同级的 `官方模拟数据/创灵境_考题模拟数据/`，或用环境变量 `PHARMA_DATA_DIR` 指定绝对路径（见 `app/paths.py`）。

---

## 四、功能模块（对应赛题四大模块）

| 模块 | 实现位置 | 说明 |
|---|---|---|
| 模块一：智能成本分析报告生成 | `app/report_gen.py` + `app/report_model.py` + `app/pdf_export.py` | 解析官方模板 `data/template_official.docx` 的占位符，按**分析主题（月度/季度/专题）+ 分析月份 + 目标产品**自动生成完整报告；数据填充 + 服务端图表渲染 + RAG 知识增强 + 大模型撰写；**Word / PDF 双格式导出**（页眉页脚、表格样式、图表嵌入） |
| 模块二：单品成本看板与归因 | `app/data_service.py` + `static/index.html` | 环比/同比/预算偏差/贡献度计算；人工工时派生指标；ECharts 趋势图、瀑布图、构成环形图、热力图；±10% 波动阈值告警 |
| 模块三：对标分析三步法 | `app/benchmark.py` | 找差异（一厂 vs 二厂）→ 拆结构（要素下钻）→ 拆原因（RAG + 大模型归因 + 行业 P25/P50/P75 基准） |
| 模块四：RPA 整改任务闭环 | `app/rpa.py` | 按官方 schema 生成任务 → POST `http://localhost:8090/api/rpa/tasks` → 模拟微信通知 → 轮询状态流转；**状态事件流 + 时间轴视图 + 落盘持久化** |
| 加分项：知识图谱增强 RAG | `app/knowledge_graph.py` + `app/rag.py` | 从配方/工艺/设备/行情/GMP/异常记录抽取实体关系，检索时 1 跳扩展加权；每条关系带证据句与来源文档，前端可视化 |
| 加分项：多模型协作 | `app/llm.py` | `LLM_MODEL`（大模型：报告正文/归因）+ `LLM_MODEL_SMALL`（小模型：任务结构化/分类/抽取），角色化路由与降级 |
| 加分项：Agent 自主决策 | `app/agent.py` | 观察 → 判断 → 决策 → 执行 → 复盘；自动判断"生成报告 / 生成报告+下发整改 / 仅更新看板"，阈值可配置 |
| 加分项：成本预测 | `app/forecast.py` | 线性回归 / 移动平均 / Holt 线性趋势 / ARIMA(1,1,0) **四模型回测对比 + 自动选优**，输出 MAPE 与模型对比表 |

### 报告双格式导出

一次生成、双格式交付，Word 与 PDF 内容同源（同一份报告数据模型），排版均为专业版式：

| 能力 | 实现 |
|---|---|
| 分析主题 | 月度成本分析 / 季度成本分析 / 专题分析（专题可选：综合成本分析、主要原材料价格波动、制造费用与折旧、人工成本与效率、两厂对标差异） |
| 统一口径 | `app/period.py` 提供"产量加权平均"周期聚合，月度与既有口径逐项一致（`scripts/verify/verify_period.py` 自动对拍） |
| 模板结构 | 官方模板 101 个 `{{占位符}}` + 7 张动态表格（趋势/材料/行情/对标/建议/整改任务/知识库来源）+ 页眉页脚与图表嵌入 |
| 图表嵌入 | `app/charts.py` 统一渲染 7 张 PNG（趋势/构成/瀑布/材料影响/行情指数/两厂对标/预算对比）；引擎自动降级：**matplotlib → Pillow → Windows GDI+（零依赖）** |
| Word | 填充模板 → 插入动态表格 → 嵌入图表 → 设置页眉（报告标题/报告类型·期间）与页脚（编制单位 + 第 X 页 / 共 Y 页域 + 保密标识） |
| PDF | 三级后端自动降级：**LibreOffice**（版式与 Word 一致）→ **reportlab 原生排版**（约 1 秒出稿、纯 Python、离线可用，自带页眉页脚/表格样式/图表）→ **Microsoft Word**（有 `pywin32` 用 COM，没有则用系统自带 PowerShell COM） |
| 产物体检 | 每个 PDF 后端产出后自动校验（`%PDF` 头、体积、页数），空壳产物会被拒绝并继续尝试下一个后端 |
| 引擎选择 | 环境变量 `PDF_ENGINE=auto\|libreoffice\|native\|word`；接口返回 `pdf_engine` 与 `pdf_note`，页面直接显示 |

> **弱网 / 离线也能演示**：不装 `matplotlib`、`pillow`、`reportlab` 时，图表自动走 Windows GDI+，
> PDF 自动走本机 Word（Windows 自带 PowerShell 调用 COM，无需 `pywin32`）或 LibreOffice，功能不缺失。

### RAG 检索（BM25 + 向量 + 知识图谱）

* 知识库支持 **TXT / Markdown / Word / PDF** 直接入库（PDF 抽取经 NFKC 规范化，修复"康熙部首"噪声字符）；
* 切分策略可调：`RAG_CHUNK_SIZE`（默认 220）/ `RAG_CHUNK_OVERLAP`（40）/ `RAG_MIN_CHUNK`（60）；
* 混合检索 = jieba+BM25（关键词） + 向量相似度（**LLM embeddings → 本地哈希 TF-IDF** 两级降级），
  融合方式 `RAG_FUSION=rrf|weighted`（默认 RRF），向量权重 `RAG_HYBRID_ALPHA` 可调；
* 向量库后端：**ChromaDB → FAISS → numpy** 自动选择；检索结果保留来源文档标注与召回路径（`bm25/vector/hybrid` + 图谱加权）；
* 实测（12 条标注查询，`outputs/eval/rag_eval.json`）：BM25 MRR 0.583 → 混合(RRF) **0.667**；关闭图谱增强降至 0.542。

### 工程与可观测

* **统一日志**：`app/logging_conf.py`，控制台 + `outputs/logs/app.log`（2MB×5 滚动），记录 RAG 命中、LLM 降级、PDF 引擎选择等关键链路，杜绝静默降级；
* **入参校验**：所有接口对非法月份/产品/主题/专题/步数/文件名返回 `400 + {error}`（不再 500）；
* **API 文档**：`/docs`（Swagger）与 `/redoc` 按 8 个标签分组，含 summary 与请求示例；离线速查见 `docs/API速查.md`；
* **自动化测试**：`tests/` 下 pytest 用例（口径计算 / 报告零残留 / 接口契约 / 参数校验 / RAG）；
* **复核脚本**：`scripts/verify/` 6 个脚本产出可复现证据（见 `scripts/README.md`）。

---

## 五、目录结构

```
pharma-cost-system/
├── run.bat / run.sh            # 一键启动脚本
├── .env.example                # 配置模板（复制为 .env）
├── requirements.txt / requirements-optional.txt / Dockerfile
├── TODO-优化清单.md             # ★ 交接优化任务清单（P0~P3 完成状态）
├── CHANGELOG-报告双格式导出.md   # 报告双格式导出改动说明
├── app/                        # 后端（FastAPI）
│   ├── main.py                 # 路由入口（8 个标签分组、统一入参校验、访问日志）
│   ├── paths.py                # 路径/配置解析（.env 加载、官方数据定位）
│   ├── logging_conf.py         # ★ 统一日志（控制台 + outputs/logs/app.log 滚动）
│   ├── data_service.py         # 官方CSV指标计算（含2025同比、预算、工时）
│   ├── period.py               # ★ 月度/季度周期聚合（产量加权平均口径）
│   ├── textutil.py             # ★ 文本规范化（PDF 部首噪声 NFKC）+ 中文切词
│   ├── vector_store.py         # ★ 向量检索（LLM/本地嵌入 × ChromaDB/FAISS/numpy）
│   ├── rag.py                  # ★ 混合检索（BM25 + 向量 + 知识图谱增强，多格式入库）
│   ├── knowledge_graph.py      # ★ 知识图谱增强 RAG（实体关系抽取 + 证据链）
│   ├── llm.py                  # ★ OpenAI兼容适配器（大/小模型角色分工 + 降级）
│   ├── rca.py                  # 归因分析（结构化 Prompt + 引用解析 + 规则兜底）
│   ├── benchmark.py            # 对标三步法
│   ├── forecast.py             # ★ 成本预测（四模型回测对比 + 自动选优）
│   ├── agent.py                # ★ Agent 自主决策（观察→判断→决策→执行→复盘）
│   ├── report_model.py         # ★ 报告数据模型（占位符/表格/章节/RAG来源）
│   ├── charts.py               # ★ 图表渲染统一入口（引擎自动选择）
│   ├── charts_pillow.py        # ★ 图表引擎：Pillow 自绘（原子写盘）
│   ├── charts_gdi.py + _render_charts.ps1  # ★ 图表引擎：Windows GDI+（零依赖兜底）
│   ├── report_gen.py           # ★ 官模板 Word 渲染 + 双格式导出调度（残留自检含表格）
│   ├── pdf_export.py           # ★ PDF 三级后端（LibreOffice/reportlab/Word）+ 产物体检
│   └── rpa.py                  # 对接官方模拟RPA服务（8090）+ 时间轴 + 落盘持久化
├── static/
│   ├── index.html              # 前端 8 页签（ECharts，本地 echarts.min.js）
│   └── echarts.min.js
├── tests/                      # ★ pytest/unittest 测试套件
├── docs/                       # ★ 方案文档（横向扩展 / 多模型与Agent / API速查）
├── data/
│   ├── knowledge_official/     # 官方PDF/CSV提取的知识库文本
│   ├── template_official.docx  # 官方报告模板
│   └── knowledge/              # 自制补充知识文档
├── data_official/              # ← 官方模拟数据（自行放入）
├── scripts/                    # ★ 按用途分类：verify / eval / tools / data（见 scripts/README.md）
└── outputs/                    # 报告成品、图表、评测证据（eval/）、日志（logs/）
```

---

## 六、演示脚本（对应评测要求的 3 个场景）

官方模拟数据埋设的异常事件（2026-05）：金银花采购单价较 1 月 +10.4%（银黄口服液）、板蓝根 +14.3%（板蓝根颗粒）。

1. **看板与告警**：选 2026-05 → 告警面板命中上述两个官方设计异常；银黄口服液单位成本 11.21 元/盒，环比 +2.84%、同比 +4.67%、预算偏差 +5.75%
2. **报告生成（双格式）**：选主题/月份/产品 → 生成 → 页面同时给出 **Word / PDF 两个下载按钮**，并展示 7 张内嵌图表与 RAG 引用来源；Word 报告含 16 张表格、页眉页脚与图表嵌入，占位符零残留
   - 月度成本分析：2026-06 + 六味地黄胶囊
   - 季度成本分析：2026-05（自动聚合 2026 年第二季度 4–6 月，环比对比 Q1）
   - 专题分析：2026-06 + 板蓝根颗粒 + 主要原材料价格波动
3. **对标三步法**：2026-06 → 一厂低于二厂 6.44%，主因人工差异 -11.11%
4. **整改闭环**：一键下发 3 条任务到官方 mock RPA（8090），约 30 秒后点"刷新任务状态"观测 sent→received 流转

### 自动化测试

```bash
# pytest（推荐）：vendor 需先进 PYTHONPATH（pytest 装在项目内 vendor/）
$env:PYTHONPATH='vendor'; python -m pytest -q          # PowerShell
PYTHONPATH=vendor python -m pytest -q                   # bash
# 不想改环境变量的等价写法
python -c "import sys; sys.path.insert(0,'vendor'); import pytest; sys.exit(pytest.main(['-q']))"
# unittest（pytest 属可选依赖，无需 PYTHONPATH）
python -m unittest discover -s tests -v
```

> ⚠️ `vendor\bin\pytest.exe` 不可用（缺 `_pytest` 包），请用上面的 `python -m pytest` 形式。
> 实测：**74 个用例全绿**（pytest 16.6s / unittest 4.5s），覆盖口径计算、周期对拍、对标差异率、
> 报告占位符零残留（段落+表格）、接口契约与参数校验、RAG 来源标注；细节见 `tests/README.md`。

### 自检脚本（按用途分类，详见 `scripts/README.md`）

```bash
# 回归与口径（改代码后必跑）
python scripts/verify/verify_period.py        # 周期聚合 vs 既有月度口径逐项对拍
python scripts/verify/verify_report.py        # 3 主题 × Word/PDF 导出、占位符残留、页眉页脚
python scripts/verify/verify_api.py           # 接口全链路 + 非法参数拦截

# 评测证据（生成评测报告前跑，结果落 outputs/eval/）
python scripts/verify/verify_rag.py           # BM25/向量/混合(RRF) 检索对比 + 图谱消融 + 多格式接入
python scripts/verify/verify_benchmark.py     # 对标差异独立复算（≤1%）+ 三步法格式正确率
python scripts/verify/verify_rca.py           # 归因质量评分卡（规则 vs 大模型）

# 交付物与文档
python scripts/eval/gen_eval_report.py        # 汇总实测 → 生成 02_参赛文档/评测报告.docx
python scripts/tools/dump_api_doc.py          # 导出 docs/API速查.md + openapi.json
python scripts/tools/report_summary.py        # 报告交付物清单（页数/表格/图表/页眉页脚）
python scripts/tools/check_template_placeholders.py   # 模板占位符覆盖率自检
python scripts/tools/check_frontend.py        # 前端 JS 语法 / DOM id / ECharts 容器自检

# 受限环境（可选）
python scripts/tools/fetch_vendor.py          # 依赖装不上时把 wheel 解包到 vendor/
python scripts/tools/preview_pdf.py outputs/report_xxx.pdf -n 3   # PDF 转 PNG 便于人工检查排版
```

实测结果（Windows + Python 3.12，报告写在 `outputs/`）：

| 主题 | Word | PDF（引擎） | 表格 | 内嵌图表 | 占位符残留 |
|---|---|---|---|---|---|
| 月度成本分析 2026-06 六味地黄胶囊 | 167 KB | 475 KB（native） | 16 | 7 | 0 |
| 季度成本分析 2026Q2 六味地黄胶囊 | 168 KB | 476 KB（native） | 16 | 7 | 0 |
| 专题分析·主要原材料价格波动 2026-05 银黄口服液 | 159 KB | 468 KB（native） | 16 | 7 | 0 |

其他关键实测指标：

| 评测项 | 结果 | 证据 |
|---|---|---|
| 对标差异计算最大绝对误差 | **0.000000**（126 条对拍，红线 ≤1%） | `outputs/eval/benchmark_accuracy.json` |
| 三步法输出格式正确率 | **100%（90/90）** | 同上 |
| RAG 混合检索 MRR | BM25 0.583 → 混合(RRF) **0.667**；图谱增强 +12.5 个百分点 | `outputs/eval/rag_eval.json` |
| 归因质量（规则 vs 大模型） | 6 项检查均全中；信息密度 531 字/6.5 次 vs 2260 字/34 次引用 | `outputs/eval/rca_quality.json` |
| RPA 触发成功率 | **100%**（3/3） | `outputs/eval/eval_summary.json` |
| 成本预测最优 MAPE | 1.56%~1.75%（四模型回测对比） | 同上 |
| 自动化测试 | **74 用例全绿** | `tests/` |

### 报告相关接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/report` | 生成报告；入参 `month`/`product`/`scene`/`topic`/`use_llm`/`charts`/`pdf`；返回 `download`(Word)、`download_pdf`、`pdf_engine`、`charts`、`sources`、`leftover` 等 |
| GET | `/api/report/options` | 可选分析主题、专题方向、月份、产品，以及 PDF 后端可用性 |
| GET | `/api/report/download?file=xxx.docx` | 下载 Word / PDF（`fmt=pdf` 可强制切换格式） |
| GET | `/api/report/chart?file=xxx.png` | 报告内嵌图表原图 |
| GET | `/api/report/list` | 已生成报告归档（Word/PDF 成对列出） |

---

## 七、已知适配点（不要改动）

- 官方 mock RPA 服务对 HTTP keep-alive 连接复用兼容不佳（第 2 个请求起返回 404），本系统统一使用 `Connection: close` 短连接
- 任务 `task_id` 撞号（服务端未重启时重复下发）会自动加随机后缀重试
- 前端禁止用 `file://` 直接打开，必须通过 `http://127.0.0.1:8000` 访问

---

## 八、说明

- 换用真实数据时，仅需替换 `data_official/` 下 CSV 与 `data/knowledge_official/` 文本，无需改代码
- 知识产权归参赛团队所有；出题方享有同等条件下优先受让权
