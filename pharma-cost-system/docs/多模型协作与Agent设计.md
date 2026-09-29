# 多模型协作 · Agent 自主决策 · RAG 增强检索 设计说明

> 对应赛题 6.2 加分项与 TODO P1.1 / P2。所有描述均与代码实现一一对应，可在源码中复核。

---

## 一、多模型协作（大模型 + 小模型分工）

### 1.1 分工原则

同一套 OpenAI 兼容接口，按**任务难度 / 调用频次 / 成本敏感度**路由到不同模型：

| 角色 | 环境变量 | 默认回落 | 承担任务 | 为什么这样分 |
|---|---|---|---|---|
| `large` | `LLM_MODEL` | `deepseek-chat` | 报告分析正文、成本要素归因（`app/rca.py`）、对标三步法归因（`app/benchmark.py`） | 需要长上下文 + 多步推理 + 行业语言组织，质量优先 |
| `small` | `LLM_MODEL_SMALL` | 未配置时回落 `LLM_MODEL` | 整改任务结构化（`app/rpa.py` 的 `chat_json`）、Agent 决策说明润色（`app/agent.py`）、要素/类别分类与实体抽取 | 高频、字段化输出、模板化程度高，成本与时延优先 |
| `embed` | `LLM_EMBED_MODEL` | 未配置时用本地哈希向量 | RAG 语义向量（`app/vector_store.py::LLMEmbedder`） | 可选；缺失时降级为本地 TF-IDF 向量，功能不缺失 |

实现位置：`app/llm.py`
* `model_for(role)` —— 角色取模型
* `chat(system, user, role="large"|"small")` —— 统一入口，异常一律返回 `None`
* `info()` —— 返回角色分工表，供 `/api/meta` 与前端横幅展示（评审可实时看到用了哪个模型）

### 1.2 降级链（保证演示不中断）

```
大模型调用（large/small）
   └─ 超时/异常/未配置 → 返回 None → 调用方回落到本地规则引擎（rca.rule_based_rca / rpa._rule_tasks / agent 规则推理）
向量化（LLM_EMBED_MODEL）
   └─ 不可用 → 本地哈希 TF-IDF 向量（字符 1/2/3-gram + IDF，确定可复现）
向量库（ChromaDB → FAISS → numpy）
   └─ 逐级降级，始终可用
```

日志会记录每一次降级（`outputs/logs/app.log`），避免"静默降级"影响评分。

### 1.3 实测参考

* 归因文本：规则引擎平均 531 字 / 金额出现 6.5 次 / 引用 0 处；大模型平均 2260 字 / 金额 34 次 / 书名号引用 4.7 处（同一 6 场景对比，见 `outputs/eval/rca_quality.json`）。
* 结论：**大模型用于"要写给人看"的正文，小模型/规则用于"要稳定结构化"的字段**，两者互补。

---

## 二、Agent 自主决策

### 2.1 决策链（Observe → Reason → Decide → Act → Learn）

实现位置：`app/agent.py`，接口 `POST /api/agent/decide`（只决策）、`POST /api/agent/run`（决策并执行）。

| 阶段 | 做什么 | 代码 |
|---|---|---|
| 观察 Observe | 调 KPI、告警、材料差异、制造费用、人工指标、成本预测，形成证据包 | `observe()` |
| 判断 Reason | 按策略逐条判定，输出「触发依据」表（指标 / 实测 / 阈值 / 判定） | `reason()` |
| 决策 Decide | 落到 4 个动作之一，并给出优先级与建议分析专题 | `decide()` |
| 执行 Act | 真正调用报告生成与 RPA 下发，回填执行日志与产物链接 | `run()` |
| 复盘 Learn | 记录决策快照（最多 50 条），`GET /api/agent/history` 可查 | `_HISTORY` |

### 2.2 决策规则（阈值可配置）

| 条件 | 动作 | 优先级 |
|---|---|---|
| 告警条数 ≥ 2 或出现「高风险」项 | **生成报告 + 下发整改** | high |
| 告警条数 ≥ 1，或单位成本环比 ≥ ±3%、预算偏差 ≥ ±5%、材料单耗变动 ≥ ±5%、预测趋势上升 | **生成报告** | medium |
| 上述均不满足 | **仅更新看板**（不消耗大模型额度） | low |
| 关键指标缺失 | 等待数据 | low |

阈值环境变量：`AGENT_ALERTS_FOR_REPORT` / `AGENT_ALERTS_FOR_ACTION` / `AGENT_UNIT_COST_DELTA` /
`AGENT_BUDGET_DEVIATION` / `AGENT_MATERIAL_DELTA`（`GET /api/agent/policy` 可查看当前生效值）。

**建议专题的自动选择**：比较「材料单价变动」与「单耗变动」的绝对值——前者更大判为「主要原材料价格波动」，
否则结合异常突出项判为「人工成本与效率」或「制造费用与折旧」，否则「综合成本分析」。

### 2.3 实测示例

```
2026-05 / 银黄口服液 → 动作「生成报告」（medium）
触发依据：阈值告警条数=1（≥1 触发报告）→ 需关注；单位成本环比 +2.84%（阈值 ±3%）→ 正常 …
建议专题：主要原材料价格波动
工具调用：kpi_overview → alerts → material_diff → forecast
```

---

## 三、知识图谱增强 RAG（与混合检索的配合）

1. **知识抽取**（`app/knowledge_graph.py`）：词表 + 规则从配方、工艺、设备、行情、异常记录、GMP 文本中抽取
   `产品-配方含-药材`、`提取-前置工序-浓缩`、`工序-使用设备-设备`、`药材-行情趋势-上行`、`异常记录-涉及-药材`、
   `GMP主题-约束-业务域` 等三元组，每条关系带 **证据原句 + 来源文档**，结果确定可复现（不依赖大模型）。
2. **检索增强**（`app/rag.py::KnowledgeBase.search`）：查询先做实体识别，命中实体沿图谱 1 跳扩展，
   相关文段获得额外权重（种子实体 1.0 / 邻居 0.45），并支持 `RAG_HYBRID_ALPHA`、`RAG_FUSION=rrf|weighted` 调参。
3. **可解释**：`GET /api/knowledge/graph?focus=收率` 返回邻居、三元组与证据，前端「知识图谱」页签可视化；
   报告附录也会列出知识库引用来源。
4. **实测**（`outputs/eval/rag_eval.json`，12 条人工标注查询）：

   | 模式 | Hit@1 | Hit@3 | MRR |
   |---|---|---|---|
   | BM25 关键词 | 0.583 | 0.583 | 0.583 |
   | 向量语义（本地哈希 TF-IDF） | 0.583 | 0.750 | 0.639 |
   | 混合（加权归一化） | 0.583 | 0.667 | 0.611 |
   | **混合（RRF 倒数排名融合）** | 0.583 | **0.750** | **0.667** |
   | 混合 RRF + 关闭图谱增强（消融） | 0.500 | 0.583 | 0.542 |

   结论：融合方式选 RRF（默认 `RAG_FUSION=rrf`）；知识图谱增强贡献 **+12.5 个 MRR 百分点**。

---

## 四、可复现命令

```bash
python scripts/verify/verify_rag.py         # 混合检索三模式对比 + alpha 扫描 + 图谱消融 + 多格式接入
python scripts/verify/verify_rca.py         # 归因质量评分卡（规则 vs 大模型）
python scripts/verify/verify_benchmark.py   # 对标准确率独立复算 + 三步法格式校验
python scripts/eval/gen_eval_report.py      # 汇总实测，生成《评测报告》
```
