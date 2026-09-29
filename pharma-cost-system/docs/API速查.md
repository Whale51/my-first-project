# API 速查（由 `scripts/tools/dump_api_doc.py` 自动生成，请勿手改）

服务：制药企业产品成本智能分析报告系统 v1.0.0

在线交互文档：运行服务后访问 **/docs**（Swagger UI）或 **/redoc**；
OpenAPI 规范原文：`outputs/eval/openapi.json`。

## 系统

元信息与引擎状态

| 方法 | 路径 | 说明 | 关键入参 |
|---|---|---|---|
| GET | `/api/health` | 健康检查 | —；— |
| GET | `/api/meta` | 元信息：月份/产品/引擎与能力开关 | —；— |

## 成本看板

KPI / 趋势 / 结构 / 告警 / 材料差异（模块二）

| 方法 | 路径 | 说明 | 关键入参 |
|---|---|---|---|
| GET | `/api/alerts` | 阈值告警（环比 ±10%） | `month`(query*)；— |
| GET | `/api/heatmap` | 产品×月份×要素 环比变动矩阵 | —；— |
| GET | `/api/kpi` | 核心指标（环比/同比/预算偏差/贡献度） | `month`(query*)、`product`(query)；— |
| GET | `/api/material_diff` | 原材料单位消耗成本环比明细 | `month`(query*)、`product`(query)；— |
| GET | `/api/structure` | 本期成本结构（单位成本口径） | `month`(query*)、`product`(query)；— |
| GET | `/api/trend` | 近 6 个月成本趋势 | `product`(query)；— |

## 归因分析

RAG + 大模型归因（模块二）

| 方法 | 路径 | 说明 | 关键入参 |
|---|---|---|---|
| POST | `/api/rca` | 生成归因分析文本（RAG + 大模型，含引用来源） | —；请求体 JSON |

## 知识库

混合检索、知识库统计、知识图谱（模块一/加分项）

| 方法 | 路径 | 说明 | 关键入参 |
|---|---|---|---|
| GET | `/api/knowledge/graph` | 知识图谱数据（ECharts + 三元组） | `focus`(query)、`limit`(query)、`triples`(query)；— |
| GET | `/api/knowledge/search` | 混合检索（BM25 + 向量 [+ 图谱增强]） | `q`(query*)、`product`(query)、`top_k`(query)、`mode`(query)、`alpha`(query)；— |
| GET | `/api/knowledge/stats` | 知识库统计（文档/知识块/切分/向量/图谱） | —；— |

## 报告

模板解析 + 数据填充 + RAG 增强，Word/PDF 双格式导出（模块一）

| 方法 | 路径 | 说明 | 关键入参 |
|---|---|---|---|
| POST | `/api/report` | 生成报告并按需导出 Word / PDF | —；请求体 JSON |
| GET | `/api/report/chart` | 报告内嵌图表原图 | `file`(query*)；— |
| GET | `/api/report/download` | 下载报告（Word/PDF） | `file`(query*)、`fmt`(query)；— |
| GET | `/api/report/list` | 报告归档（Word/PDF 成对） | —；— |
| GET | `/api/report/options` | 可选主题/专题/月份/产品与引擎可用性 | —；— |

## 对标分析

三步法：找差异 → 拆结构 → 拆原因（模块三）

| 方法 | 路径 | 说明 | 关键入参 |
|---|---|---|---|
| GET | `/api/benchmark` | 对标三步法（找差异→拆结构→拆原因） | `month`(query*)、`product`(query)、`use_llm`(query)；— |

## 整改闭环

RPA 任务下发、状态追踪与时间轴（模块四）

| 方法 | 路径 | 说明 | 关键入参 |
|---|---|---|---|
| POST | `/api/rpa/dispatch` | 生成整改任务并推送模拟 RPA | —；请求体 JSON |
| GET | `/api/rpa/tasks` | 整改任务列表与统计 | `refresh`(query)；— |
| GET | `/api/rpa/timeline` | 整改任务时间轴视图（状态流转事件流） | `month`(query)、`product`(query)、`limit`(query)；— |

## 智能体与预测

Agent 自主决策、成本预测（加分项）

| 方法 | 路径 | 说明 | 关键入参 |
|---|---|---|---|
| POST | `/api/agent/decide` | Agent 自主决策（生成报告 / 仅更新看板） | —；请求体 JSON |
| GET | `/api/agent/history` | Agent 决策历史 | `limit`(query)；— |
| GET | `/api/agent/policy` | Agent 决策策略与阈值 | —；— |
| POST | `/api/agent/run` | 执行 Agent 决策（可生成报告、下发整改） | —；请求体 JSON |
| GET | `/api/forecast` | 成本预测（四模型回测对比 + 自动选优） | `product`(query)、`steps`(query)；— |

---

说明：带 `*` 的参数为必填；所有接口对非法入参返回 **400 + `{error: 提示}`**，
文件下载类接口仅允许 `docx/pdf/png` 白名单扩展名。
