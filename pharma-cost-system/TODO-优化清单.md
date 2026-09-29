# TODO - 优化任务清单

> 交接时项目状态：赛题四大模块均已跑通并实测通过，可直接运行演示。
> 下列任务按优先级排序，**P0 为必须完成**（否则影响交付/演示），P1 影响评分，P2 为加分冲刺。

---

## P0 · 必须完成（交付前）

### 0.1 配置大模型 API Key
- [ ] 按 `README.md` 第二节配置 `.env`，使页面横幅从"本地规则模式"变为"大模型模式"
- [ ] 验证三处大模型生成链路均走真实模型：**报告分析文本**、**归因分析**、**RPA 整改任务**
- [ ] 若调用失败会自动降级回规则引擎，请检查控制台确认没有静默降级

### 0.2 补充 PDF 导出（**已完成 ✅**）
赛题 5.1.3 原文："支持 **PDF 和 Word 双格式导出**，格式需保持专业排版（页眉页脚、表格样式、图表嵌入）"。
- [x] 三级可插拔 PDF 后端（`app/pdf_export.py`）：**LibreOffice**（`soffice --headless --convert-to pdf`，版式与 Word 一致）
      → **reportlab 原生排版**（纯 Python，约 1 秒出稿，离线可用，自带页眉页脚、专业表格样式、图表嵌入）
      → **Microsoft Word**（有 `pywin32` 用 COM；没有则自动用系统自带 PowerShell COM，超时清理 + 自动重试）
- [x] 环境变量 `PDF_ENGINE=auto|libreoffice|word|native` 可指定后端；接口返回 `pdf_engine` / `pdf_note`，页面直接展示
- [x] `/api/report` 返回体新增 `file_pdf` / `download_pdf` / `pdf_ok` / `pdf_engine`，前端新增 **PDF 下载按钮**
- [x] Word 页眉（报告标题 + 报告类型·期间）与页脚（编制单位 + 「第 X 页 / 共 Y 页」域 + 保密标识）已重构并实测；
      原生 PDF 的页眉页脚与页码在画布中绘制（"第 X 页 / 共 Y 页"两遍绘制）
- [x] 依赖：`requirements.txt` 增加 `reportlab`；高保真可选依赖 `requirements-optional.txt`（`pywin32`）

### 0.3 报告内嵌图表（**已完成 ✅**）
赛题要求报告"图表嵌入"。
- [x] `app/charts.py` 统一渲染入口，7 张高清 PNG：近 6 月趋势（柱+折线双轴）、单位成本构成（环形）、
      环比变动瀑布、主要材料影响（横向条）、关键药材行情指数、两厂对标（分组柱）、实际 vs 上期/去年同期/预算；
      引擎自动降级 **matplotlib → Pillow → Windows GDI+（零第三方依赖）**，缺引擎时仅告警不报错
- [x] 中文字体自动探测（微软雅黑/黑体/宋体/Noto CJK/PingFang），保证不出现方块字；缺 matplotlib 时自动降级为"无图不报错"
- [x] 图表按章节插入 Word（表格/段落占位符之后，含图注）与原生 PDF（对应章节内嵌并等比缩放）
- [x] 前端报告面板提供图表原图预览（`/api/report/chart?file=xxx.png`）

### 0.3b 报告主题扩展与模板渲染修复（本次一并完成 ✅）
- [x] 分析主题支持 **月度成本分析 / 季度成本分析 / 专题分析**（`app/period.py` + `app/report_model.py`）
- [x] 季度报告自动聚合当季 3 个月（产量加权平均口径），环比对比上一季度、同比对比去年同期，措辞自动切换"本期/上期"
- [x] 专题分析支持 5 个方向：综合成本分析、主要原材料价格波动、制造费用与折旧、人工成本与效率、两厂对标差异
- [x] **修复原模板渲染缺陷**：不在取值表中的 `{{占位符}}`（动态表格锚点）此前被清空，导致 6 张动态表格从未插入；
      现已保留锚点并正确插入 → Word 报告表格数 9 → 16（含附录表）
- [x] 报告自动追加「附录 · 知识库引用来源（RAG 检索命中）」表，强化"知识增强"证据链
- [x] 自检脚本：`scripts/verify_period.py`（周期口径与既有月度口径逐项对拍，防回归）、
      `scripts/verify_report.py`（3 主题 × Word/PDF 双格式导出检查）

### 0.4 代码托管与提交物
- [ ] 推送到 **GitHub / Gitee 公开仓库**（赛题硬性要求），补 `.gitignore`（排除 `.env`、`outputs/`、`__pycache__/`）
- [ ] 核对 5 项必交物是否齐备：源代码、技术方案文档、评测报告、演示视频(≤5min)、演示PPT
- [ ] 报名截止 **9.18**，作品提交截止 **9.24**（以官方通知为准）

---

## P1 · 影响评分（**全部完成 ✅**）

### 1.1 RAG 升级为真·混合检索 ✅
- [x] 新增 `app/vector_store.py`：嵌入与向量库**两级可插拔**——
      嵌入 `LLM_EMBED_MODEL`（OpenAI 兼容语义向量）→ 本地哈希 TF-IDF 向量（字符 1/2/3-gram + IDF，离线确定可用）；
      向量库 **ChromaDB → FAISS → numpy** 自动降级（赛题技术栈列出的 ChromaDB/FAISS 均已接入）
- [x] `app/rag.py` 重写为真·混合检索：jieba+BM25 与向量相似度两路归一化后融合，
      支持 `RAG_FUSION=rrf|weighted`（默认 **RRF 倒数排名融合**）与 `RAG_HYBRID_ALPHA` 调权
- [x] **来源标注保留并增强**：每条结果含 `source/doc_id/snippet/score` 及召回路径 `bm25/vector/hybrid`、
      `graph`（图谱加权），前端检索面板逐条展示
- [x] **切分策略可调**：`RAG_CHUNK_SIZE`(220) / `RAG_CHUNK_OVERLAP`(40) / `RAG_MIN_CHUNK`(60)，
      先按 `【小节】`语义切块再滑窗重叠；`GET /api/knowledge/stats` 返回当前参数
- [x] **多格式入库**：`.txt/.md/.docx/.pdf` 直接加载（PDF 走 pypdfium2 并做 NFKC 规范化，修复"康熙部首"噪声），
      带磁盘缓存（`data/cache/knowledge/`）
- [x] 实测证据：`scripts/verify/verify_rag.py` → BM25 MRR 0.583 / 向量 0.639 / 混合(加权) 0.611 /
      **混合(RRF) 0.667**；关闭图谱增强降到 0.542（图谱增强 +12.5 个百分点）

### 1.2 归因文本质量 ✅
- [x] `app/rca.py` Prompt 重写：强制「要素 + 金额 + 幅度 + 贡献度 + `[来源: 文档名]` 依据 + 编号建议 + 责任部门」，
      明确禁止"仅描述现象"的写法
- [x] `app/benchmark.py` SYSTEM 同步升级为三步法结构化输出 + 知识库引用要求
- [x] 新增 `cited_sources()`：从生成文本解析实际引用的知识库文档，随接口返回并在报告附录体现
- [x] 规则引擎兜底也补上 `[来源: ...]` 引用（原来只写现象与建议）
- [x] 实测对比（`scripts/verify/verify_rca.py`，6 场景）：两者 6 项硬性检查全部命中（评分卡满分饱和）；
      信息密度差异显著——大模型 2260 字 / 金额 34 次 / 书名号引用 4.7 处，规则引擎 531 字 / 6.5 次 / 0 处

### 1.3 对标分析准确率自验 ✅
- [x] 新增 `scripts/verify/verify_benchmark.py`：pandas **直读官方 CSV 独立复算**（不复用被测函数），
      与系统输出逐字段对拍 6 个月 × 3 产品 = 126 条明细
- [x] 实测：**最大绝对误差 0.000000**，满足赛题红线 ≤1% 与自定严阈值 ≤0.01%；
      三步法输出格式正确率 **100%（90/90）**；证据含被测实现 SHA-256 指纹 → `outputs/eval/benchmark_accuracy.json`

### 1.4 评测报告补全 ✅
- [x] 新增 `scripts/eval/gen_eval_report.py`：现场跑 3 个场景（月度/季度/专题）+ 启动官方模拟 RPA 下发并轮询
      + 三步法校验 + 预测 + Agent 决策，汇总 `outputs/eval/*.json` 证据，自动生成 Word《评测报告》
- [x] 报告内容含：执行摘要、评测概述、评测环境、功能评测（报告结构完整度 / 对标准确率 / RPA 触发成功率）、
      RAG 与知识图谱评测、归因质量对比、加分项评测、工程化与兼容性、结论与待改进
- [x] 三场景实测（启用大模型）：表格 16 张 / 图表 7 张 / 占位符残留 0 处 / PDF 引擎 native，单场景 14~15 秒

---

## P2 · 加分冲刺（**全部完成 ✅**）

- [x] **知识图谱增强 RAG**：新增 `app/knowledge_graph.py`，词表+规则从配方/工艺/设备/行情/异常记录/GMP 抽取
      实体-关系三元组（实测 80 节点 / 105 关系 / 9 类实体），每条关系带**证据原句 + 来源文档**；
      检索时实体 1 跳扩展加权（`rag.attach_graph`），前端新增「知识图谱」页签可视化（ECharts force 布局 + 点击看证据）
- [x] **多模型协作**：`app/llm.py` 角色化路由——`LLM_MODEL`（大模型：报告正文/归因/对标）+
      `LLM_MODEL_SMALL`（小模型：整改任务结构化/Agent 说明/分类抽取），`/api/meta` 返回分工表；
      设计说明见 `docs/多模型协作与Agent设计.md`
- [x] **Agent 自主决策**：新增 `app/agent.py`，决策链 观察→判断→决策→执行→复盘，
      动作四选一（生成报告+下发整改 / 生成报告 / 仅更新看板 / 等待数据），阈值可配置（`/api/agent/policy`），
      支持只决策与决策后执行（`/api/agent/decide`、`/api/agent/run`），决策历史可复盘；前端新增「智能体决策」页签
- [x] **成本预测升级**：`app/forecast.py` 重写为**四模型回测对比 + 自动选优**
      （线性回归 / 移动平均 / Holt 线性趋势 / ARIMA(1,1,0)，statsmodels 可选），输出 MAPE、权重与模型对比表；
      实测最优 MAPE 1.56%~1.75%（六味/板蓝根/银黄），前端新增「成本预测」页签展示趋势图与模型对比
- [x] **任务追踪时间轴**：`app/rpa.py` 为每个任务记录状态事件流（生成/推送/流转），
      新增 `GET /api/rpa/timeline` 与前端时间轴视图（事件、停留时长、状态链、闭环标记），任务落盘 `outputs/rpa_tasks.json`
- [x] **横向扩展说明**：新增 `docs/横向扩展方案-从制药到机械汽配.md`（框架复用度评估表 10 模块 / 行业差异对照 /
      知识库迁移清单 / 指标与模型改造点 / 三阶段路线图 / 风险边界），并在 `docs/README.md` 建索引

---

## P3 · 工程与体验（**全部完成 ✅**）

- [x] 基础测试（`tests/`，pytest 或 `unittest discover` 均可跑）：`data_service` 指标计算（含产量加权平均口径）、
      `period` 季度口径对拍、`benchmark` 差异率、`report_gen` 占位符零残留（段落+表格）、`api` 契约与参数校验、`rag` 来源标注
- [x] 统一日志：新增 `app/logging_conf.py`（控制台 + `outputs/logs/app.log`，2MB×5 滚动），
      `app/` 下全部 `print` 已替换为 `log.info/warning`，关键链路（RAG 命中、LLM 降级、PDF 引擎选择、报告残留）均有日志
- [x] 前端体验：全局加载遮罩 + toast 错误/成功提示 + `api()` 统一错误处理 + 按钮禁用态；
      响应式适配（≤1080px / ≤640px 断点）、表格横向滚动；报告面板显示引擎与残留统计、图表预览、归档下载
- [x] 入参校验：`app/main.py` 统一 `_check_month/_check_product` + 主题/专题白名单 + steps 范围 + 文件名扩展名白名单，
      非法入参返回 **400 + `{error: 可选值}`**（不再 500）
- [x] API 文档：`/docs`、`/redoc` 按 8 个标签分组并补 summary 与请求示例；新增
      `scripts/tools/dump_api_doc.py` 自动导出 `docs/API速查.md` 与 `outputs/eval/openapi.json`（26 个接口）


---

## 已经做好、请勿改坏的地方

| 位置 | 说明 |
|---|---|
| `data_service.row()` 全部产品口径 | 按**产量加权平均**计算单价，不能用 sum(单价)/总产量（会≈0） |
| `app/rpa.py` 短连接 | 官方 mock RPA 对 keep-alive 不兼容，必须 `Connection: close` |
| 任务 task_id 重试 | 服务端未重启时撞号，已自动加随机后缀 |
| 前端访问方式 | 必须 `http://127.0.0.1:8000`，不能 `file://` 直开（CORS 拦截 /api） |
| 前端 ECharts | 用本地 `static/echarts.min.js`，**不要改回 CDN**（国内网络不稳定） |
| 瀑布图 | 用 ECharts `custom series` 实现，改成堆叠柱会出现悬空错位 |
| `report_gen` 前端字段 | 后端必须保留 `总成本概览评述`/`要素归因分析`/`总结`/`改进建议` 四个键，前端面板依赖它们 |
