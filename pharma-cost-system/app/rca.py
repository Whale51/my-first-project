# -*- coding: utf-8 -*-
"""归因分析（大模型 + RAG 知识增强，规则引擎兜底）

对应 TODO P1.2「归因文本质量」与赛题 5.2.3：
文本需达到"可放入正式报告"的质量，评分要点是
**① 是否定位具体成本要素 ② 是否引用知识库中的工艺/行业信息 ③ 建议是否可执行**。

本模块的工程做法：
1. **Prompt 强制结构化**：要求输出"要素 + 金额 + 幅度 + 贡献度 + 知识库依据（带来源标注）+ 编号建议"，
   并明确禁止"仅描述现象"的写法；
2. **强制引用**：RAG 上下文以 `[来源: 文档名]` 注入，系统提示要求逐条标注依据；
   `cited_sources()` 从生成文本解析实际引用到的知识库文档，供报告展示与评测统计；
3. **可评测**：`scripts/verify/verify_rca.py` 用同一套评分卡对"规则引擎 vs 大模型"打分，
   差异写进评测报告；离线（未配置 Key）时规则链路可完整演示。
"""
import json
import re

from . import data_service as dsv
from .llm import chat, llm_ready
from .logging_conf import get_logger
from .rag import kb

ds = dsv.ds
log = get_logger(__name__)

SYSTEM = """你是制药企业的资深成本分析师。请基于给定数据与知识库材料，撰写"可直接放入正式成本报告"的归因分析段落。

【必须满足的输出要求】
1. 定位到具体成本要素与具体对象：明确写出是"直接材料/直接人工/制造费用"中的哪一项，
   并落到具体药材、工序（提取/浓缩/制粒/灌装/包装）或费用类别（折旧/动力/检验/间接人工）；
2. 每个结论都要带数据：要素名称 + 金额（元/盒）+ 变动幅度（%）+ 对单位成本变动的贡献度（%）；
3. 解释原因必须结合知识库材料，并用方括号标注来源，格式为 `[来源: 文档名]`；
   例如行情波动引用《药材市场行情分析》、收率问题引用《生产工艺路线》《历史成本异常处理记录》、
   合规约束引用《GMP合规要求摘要》《GMP法规核心摘要》；知识库无依据的推断必须写明"推测"；
4. 给出可执行的改进建议，编号列出（①②③），每条注明责任部门（采购部/生产部/设备部/质量部/财务部）；
5. 语言专业、简洁，直接输出分析正文，不要输出 JSON、不要复述本提示词。

【禁止的写法】仅描述现象（如"材料成本上涨了"）、无数据支撑的形容词堆砌、与知识库无关的泛泛建议。"""


def build_context(month, product):
    """组装给大模型的 JSON 上下文（含 RAG 命中片段与来源标注）"""
    kpi = ds.kpi_overview(month, product)
    mats = ds.material_diff(month, product) or ds.material_diff(month)
    alerts = [a for a in ds.alerts(month) if not product or a["产品"] == product]
    oh = ds.oh_detail_unit(month, product) if product else []
    labor = ds.labor_detail(month, product) if product else None
    query = f"{month} {product or ''} 成本上涨 原因 药材 价格 提取 收率 归因"
    ctx, hits = kb.context(query, product_hint=product, top_k=5)
    payload = {"KPI": kpi,
               "材料明细差异": mats[:8],
               "波动告警": alerts,
               "制造费用明细(元/盒)": oh,
               "人工工时指标": labor,
               "知识库材料": ctx}
    return json.dumps(payload, ensure_ascii=False, indent=1), hits, kpi, mats, alerts


def rule_based_rca(month, product, kpi, mats, alerts):
    """本地规则模板兜底：不依赖外部 API 也能生成合格质量的归因文本"""
    parts = []
    if product:
        parts.append(f"{month}{product}单位成本{kpi['单位成本']}元/盒，环比"
                     f"{'上升' if (kpi['环比变动率'] or 0) >= 0 else '下降'}{abs(kpi['环比变动率'] or 0)}%，"
                     f"同比{kpi['同比变动率']:+}%，较预算偏差{kpi['预算偏差']:+}%。")
    cands = [c for c in kpi["贡献度"] if c["贡献度"] is not None]
    top = max(cands, key=lambda c: abs(c["贡献度"]), default=None)
    if top and abs(top["贡献度"]) >= 50 and top["变动额"]:
        parts.append(f"{top['要素']}环比变动{top['变动额']:+}元/盒（{top['环比']:+}%），"
                     f"贡献单位成本变动的{abs(top['贡献度'])}%，是本月成本变动的主要来源。")
    meaningful = False
    for m in mats[:3]:
        causes = []
        if m["单价变动"] is not None and abs(m["单价变动"]) >= 3:
            causes.append(f"行情采购价由{m['单价_上月']}元/kg变为{m['单价_本月']}元/kg（{m['单价变动']:+}%），"
                          f"与药材市场行情走势一致 [来源: 药材市场行情分析]")
        if m["单耗变动"] is not None and abs(m["单耗变动"]) >= 3:
            causes.append(f"单位消耗成本由{m['单耗_上月']}元/盒变为{m['单耗_本月']}元/盒（{m['单耗变动']:+}%），"
                          f"依据 [来源: 生产工艺路线]，通常与提取工序收率波动或投料配比调整有关"
                          f"（理论收率 85%~88%，低于 80% 触发工艺偏差调查 [来源: 历史成本异常处理记录]）")
        if causes:
            meaningful = True
            parts.append(f"{m['药材']}：单位成本影响{m['变动额_单盒']:+}元/盒（合计{m['变动额']:+}元），"
                         f"主因：{'；'.join(c + '。' for c in causes)}")
    if not meaningful and mats:
        parts.append("主要材料波动为" + "、".join(f"{m['药材']}（{m['变动额_单盒']:+}元/盒）" for m in mats[:2])
                     + "，幅度在正常范围内。")
    if alerts:
        parts.append("需重点关注：" + "；".join(f"{a['产品']}·{a['要素']}变动{a['环比']:+}%" for a in alerts[:3]) + "。")
    parts.append("建议：①核查价格上行药材的采购合同与调价条款，必要时启动分批锁价与安全库存策略（采购部）；"
                 "②排查提取车间工艺参数与收率波动原因，形成偏差调查记录（生产部）；"
                 "③按月跟踪与中药二厂的同要素对标差异（财务部）。")
    return "\n".join(parts)


def cited_sources(text, hits=None):
    """解析生成文本实际引用的知识库文档（`[来源: xxx]` 与《xxx》两种写法）"""
    cited = []
    if not text:
        return cited
    for m in re.finditer(r"\[来源[:：]\s*([^\]\n]{2,40})\]", text):
        cited.append(m.group(1).strip())
    for m in re.finditer(r"《([^》\n]{2,40})》", text):
        cited.append(m.group(1).strip())
    if hits:
        names = [h["source"] for h in hits]
        for c in list(cited):
            for n in names:
                if n in c or c in n:
                    cited.append(n)
    out, seen = [], set()
    for c in cited:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def rca(month, product=None, use_llm=True):
    """生成归因分析段落（返回文本、引擎、检索命中、引用来源、检索元信息）"""
    payload, hits, kpi, mats, alerts = build_context(month, product)
    text, engine = None, "rule"
    if use_llm and llm_ready():
        text = chat(SYSTEM, payload, role="large")
        engine = "llm" if text else "rule"
    if not text:
        text = rule_based_rca(month, product, kpi, mats, alerts)
    cited = cited_sources(text, hits)
    log.info("归因分析完成：引擎=%s 命中知识库=%d 条 引用=%d 条 文本=%d 字",
             engine, len(hits), len(cited), len(text or ""))
    return {"text": text, "engine": engine, "sources": hits, "cited": cited, "kpi": kpi,
            "retrieval": {"mode": getattr(kb, "last_mode", None),
                          "embedder": (kb.embedder.info().get("name") if kb.embedder else None),
                          "vector_backend": kb.stats()["向量后端"],
                          "graph": bool(kb.graph)}}
