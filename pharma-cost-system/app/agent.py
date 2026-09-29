# -*- coding: utf-8 -*-
"""Agent 自主决策（TODO P2 / 赛题 6.2 加分项）

赛题原文："系统自动判断『需要生成报告还是仅更新看板』"。

实现为**可解释的规则智能体**（规则优先、大模型仅用于把结论写成一段人话），决策链固定为
`观察 → 判断 → 决策 → 执行 → 复盘`，每一步都留痕，便于评审复现：

| 阶段 | 动作 | 产出字段 |
|---|---|---|
| 观察 Observe | 调 `kpi_overview / alerts / material_diff / oh_detail_unit / forecast` 收集证据 | `证据` |
| 判断 Reason | 按阈值策略逐条判定（告警条数、单位成本环比、预算偏差、材料单耗、预测趋势） | `触发依据` |
| 决策 Decide | 落到 4 个动作之一：仅更新看板 / 生成报告 / 生成报告+下发整改 / 等待数据 | `动作`、`优先级` |
| 执行 Act | `run()` 真正调用报告生成与 RPA 下发（可取消） | `执行日志` |
| 复盘 Learn | 记录本次决策快照，供后续对比"决策是否合理" | `决策历史` |

阈值全部可用环境变量覆盖（见 `POLICY`），也可通过 `GET /api/agent/policy` 查看。
"""
import os
import time

from . import data_service as dsv
from . import period as pd_mod
from .llm import chat, llm_ready
from .logging_conf import get_logger

ds = dsv.ds
log = get_logger(__name__)

# 决策策略（阈值可通过环境变量覆盖，便于不同企业口径调整）
POLICY = {
    "告警条数_触发报告": int(os.environ.get("AGENT_ALERTS_FOR_REPORT", "1")),
    "告警条数_触发整改": int(os.environ.get("AGENT_ALERTS_FOR_ACTION", "2")),
    "单位成本环比_触发报告": float(os.environ.get("AGENT_UNIT_COST_DELTA", "3.0")),      # %
    "预算偏差_触发报告": float(os.environ.get("AGENT_BUDGET_DEVIATION", "5.0")),         # %
    "材料单耗变动_触发报告": float(os.environ.get("AGENT_MATERIAL_DELTA", "5.0")),        # %
    "告警阈值": 10.0,                                                                   # 与看板告警一致
}

ACTIONS = {
    "report_action": "生成报告+下发整改",
    "report": "生成报告",
    "board": "仅更新看板",
    "wait": "等待数据",
}
ACTION_DESC = {
    "生成报告+下发整改": "成本波动已超阈值且告警集中，需要出正式报告并推动责任部门整改",
    "生成报告": "存在需要管理层关注的成本变动，出报告留档",
    "仅更新看板": "指标平稳，无需消耗大模型额度与人工阅读成本，仅刷新看板",
    "等待数据": "关键数据缺失，无法判定",
}

_REASON_SYSTEM = """你是成本分析智能体。请把给定的"决策结论 + 触发依据"改写成一段 80 字以内的中文说明，
要求：先给结论（该不该出报告/该不该下发整改），再给最关键的两条数据依据，语气客观、面向财务负责人。
直接输出正文，不要 JSON、不要分点、不要复述提示词。"""

_HISTORY = []      # 决策历史（演示用内存队列，最多保留 50 条）


# ---------------- 观察 ----------------
def observe(month, product=None):
    """收集决策所需证据"""
    t0 = time.time()
    kpi = ds.kpi_overview(month, product)
    alerts = [a for a in ds.alerts(month) if not product or a["产品"] == product]
    mats = ds.material_diff(month, product) or []
    oh = ds.oh_detail_unit(month, product) if product else []
    labor = ds.labor_detail(month, product) if product else None
    tools = ["kpi_overview", "alerts", "material_diff"]
    forecast_trend = None
    if product:
        try:
            from .forecast import forecast as _fc
            fc = _fc(product=product, steps=1)
            if fc.get("items"):
                forecast_trend = fc["items"][0].get("预测趋势")
                tools.append("forecast")
        except Exception as e:      # 预测失败不影响决策
            log.warning("智能体调用预测失败：%s", e)
    if oh:
        tools.append("oh_detail_unit")
    if labor:
        tools.append("labor_detail")
    return {"KPI": kpi, "告警": alerts, "材料差异": mats, "制造费用": oh, "人工": labor,
            "预测趋势": forecast_trend, "工具调用": tools,
            "观察耗时毫秒": int((time.time() - t0) * 1000)}


# ---------------- 判断 ----------------
def reason(ev):
    """逐条策略判定，返回触发依据清单"""
    kpi = ev["KPI"]
    checks = []
    alerts = ev["告警"]

    checks.append({
        "指标": "阈值告警条数", "实测": len(alerts), "阈值": f"≥{POLICY['告警条数_触发报告']} 触发报告 / "
        f"≥{POLICY['告警条数_触发整改']} 触发生成报告+整改",
        "判定": "高风险" if len(alerts) >= POLICY["告警条数_触发整改"] else (
            "需关注" if len(alerts) >= POLICY["告警条数_触发报告"] else "正常")})

    mom = kpi.get("环比变动率")
    checks.append({"指标": "单位成本环比", "实测": f"{mom:+.2f}%" if mom is not None else "—",
                   "阈值": f"±{POLICY['单位成本环比_触发报告']}%",
                   "判定": "需关注" if mom is not None and abs(mom) >= POLICY["单位成本环比_触发报告"] else "正常"})

    bud = kpi.get("预算偏差")
    checks.append({"指标": "预算偏差", "实测": f"{bud:+.2f}%" if bud is not None else "—",
                   "阈值": f"±{POLICY['预算偏差_触发报告']}%",
                   "判定": "需关注" if bud is not None and abs(bud) >= POLICY["预算偏差_触发报告"] else "正常"})

    worst = max(ev["材料差异"], key=lambda m: abs(m.get("单耗变动") or 0), default=None)
    if worst:
        r = worst.get("单耗变动")
        checks.append({"指标": f"材料单耗变动（{worst['药材']}）", "实测": f"{r:+.2f}%" if r is not None else "—",
                       "阈值": f"±{POLICY['材料单耗变动_触发报告']}%",
                       "判定": "需关注" if r is not None and abs(r) >= POLICY["材料单耗变动_触发报告"] else "正常"})

    if ev["预测趋势"]:
        checks.append({"指标": "下期成本预测趋势", "实测": ev["预测趋势"],
                       "阈值": "上升视为风险", "判定": "需关注" if ev["预测趋势"] == "上升" else "正常"})
    return checks


# ---------------- 决策 ----------------
def decide(month, product=None, use_llm=False):
    """给出动作、优先级、触发依据与推理说明"""
    ev = observe(month, product)
    checks = reason(ev)
    kpi = ev["KPI"]
    if not kpi or kpi.get("单位成本") is None:
        return _pack("等待数据", "low", checks, ev, "关键指标缺失，无法判定，等待数据补齐后重试。", month, product)

    n_alerts = len(ev["告警"])
    high = [c for c in checks if c["判定"] == "高风险"]
    attention = [c for c in checks if c["判定"] == "需关注"]

    if n_alerts >= POLICY["告警条数_触发整改"] or high:
        action, prio = ACTIONS["report_action"], "high"
    elif n_alerts >= POLICY["告警条数_触发报告"] or attention:
        action, prio = ACTIONS["report"], "medium"
    else:
        action, prio = ACTIONS["board"], "low"

    topic = "综合成本分析"
    if action != ACTIONS["board"]:
        mats = ev["材料差异"]
        worst = max(mats, key=lambda m: abs(m.get("单耗变动") or 0), default=None)
        if worst and abs(worst.get("单价变动") or 0) > abs(worst.get("单耗变动") or 0):
            topic = "主要原材料价格波动"
        elif any("人工" in c["指标"] for c in attention) or (
                kpi.get("环比变动率") and abs(kpi["环比变动率"]) >= POLICY["单位成本环比_触发报告"]):
            topic = "人工成本与效率"
        elif ev["制造费用"]:
            oh_worst = max(ev["制造费用"], key=lambda c: abs(c.get("环比") or 0))
            if abs(oh_worst.get("环比") or 0) >= POLICY["单位成本环比_触发报告"]:
                topic = "制造费用与折旧"

    reasons = [f"{c['指标']} 实测 {c['实测']}（阈值 {c['阈值']}）→ {c['判定']}" for c in checks]
    rule_text = (f"决策：{action}（优先级 {prio}）。"
                 f"依据：{'; '.join(r for r in reasons if '正常' not in r) or '各项指标均在阈值内'}。"
                 f"建议主题：{topic}。")

    text, engine = rule_text, "rule"
    if use_llm and llm_ready() and action != ACTIONS["wait"]:
        out = chat(_REASON_SYSTEM, f"决策结论：{action}\n触发依据：\n" + "\n".join(reasons), role="small")
        if out:
            text, engine = out.strip(), "llm"

    return _pack(action, prio, checks, ev, text, month, product, topic=topic, engine=engine)


def _pack(action, prio, checks, ev, text, month, product, topic="综合成本分析", engine="rule"):
    kpi = ev["KPI"]
    return {
        "月份": month, "产品": product or "全部产品",
        "动作": action, "动作说明": ACTION_DESC.get(action, ""), "优先级": prio,
        "建议分析主题": "月度成本分析", "建议专题": topic,
        "触发依据": checks, "推理": text, "推理引擎": engine,
        "关键指标": {"单位成本": kpi.get("单位成本"), "环比变动率": kpi.get("环比变动率"),
                     "同比变动率": kpi.get("同比变动率"), "预算偏差": kpi.get("预算偏差"),
                     "告警条数": len(ev["告警"])},
        "工具调用": ev["工具调用"], "观察耗时毫秒": ev["观察耗时毫秒"],
        "决策时间": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


# ---------------- 执行 ----------------
def run(month, product=None, use_llm=True, do_report=None, do_dispatch=None):
    """按决策执行：生成报告 / 下发整改任务；返回执行日志

    do_report / do_dispatch 为 None 时按决策动作自动决定，可显式覆盖（便于演示单步动作）。
    """
    decision = decide(month, product, use_llm=False)
    action = decision["动作"]
    want_report = (action in (ACTIONS["report"], ACTIONS["report_action"])) if do_report is None else bool(do_report)
    want_dispatch = (action == ACTIONS["report_action"]) if do_dispatch is None else bool(do_dispatch)

    logs = []
    t0 = time.time()
    result = {"决策": decision, "报告": None, "整改": None}
    if want_report:
        try:
            from . import report_gen
            res = report_gen.generate(month, product, scene="月度成本分析",
                                      topic=decision["建议专题"], use_llm=use_llm)
            result["报告"] = {"file": res["file"], "file_pdf": res["file_pdf"],
                              "download": res["download"], "download_pdf": res["download_pdf"],
                              "pdf_engine": res["pdf_engine"], "图表数": res["stats"]["图表数"],
                              "占位符残留": len(res["leftover"])}
            logs.append(f"已生成报告（{res['stats']['图表数']} 张图表，"
                        f"PDF 引擎 {res['pdf_engine'] or '无'}，占位符残留 {len(res['leftover'])} 处）")
        except Exception as e:
            logs.append(f"报告生成失败：{e}")
            log.exception("智能体执行报告生成失败")
    else:
        logs.append("按决策跳过报告生成（指标平稳，仅更新看板 → 节省大模型调用与人工阅读成本）")

    if want_dispatch:
        try:
            from . import rpa as rpa_mod
            res = rpa_mod.dispatch(month, product, use_llm=use_llm)
            ok = sum(1 for r in res["results"] if r["ok"])
            result["整改"] = {"任务数": len(res["tasks"]), "成功": ok,
                              "任务": [{"task_id": t["task_id"], "title": t["task_title"],
                                        "责任人": t["责任人"], "优先级": t["优先级"]} for t in res["tasks"]]}
            logs.append(f"已下发 {len(res['tasks'])} 条整改任务到模拟 RPA（成功 {ok} 条）")
        except Exception as e:
            logs.append(f"整改任务下发失败：{e}")
            log.exception("智能体执行整改下发失败")
    elif action == ACTIONS["report_action"]:
        logs.append("整改任务下发被显式取消")

    result["执行日志"] = logs
    result["耗时毫秒"] = int((time.time() - t0) * 1000)
    _HISTORY.append({"时间": decision["决策时间"], "月份": month, "产品": decision["产品"],
                     "动作": action, "优先级": decision["优先级"], "执行": logs})
    del _HISTORY[:-50]
    log.info("智能体决策：%s（%s）→ %s", action, decision["优先级"], "；".join(logs))
    return result


def policy():
    return {"策略阈值": POLICY, "动作定义": ACTION_DESC,
            "说明": "阈值可用环境变量覆盖（AGENT_ALERTS_FOR_REPORT / AGENT_ALERTS_FOR_ACTION / "
                    "AGENT_UNIT_COST_DELTA / AGENT_BUDGET_DEVIATION / AGENT_MATERIAL_DELTA）",
            "决策链": ["观察 Observe", "判断 Reason", "决策 Decide", "执行 Act", "复盘 Learn"]}


def history(limit=20):
    return list(reversed(_HISTORY[-limit:]))
