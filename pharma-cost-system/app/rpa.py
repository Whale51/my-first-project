# -*- coding: utf-8 -*-
"""RPA 整改任务闭环（对接官方模拟RPA服务 8090）

官方接口：POST /api/rpa/tasks 创建任务（schema 见官方《模拟RPA接口文档.md》）
状态流转：GET /api/rpa/tasks/{task_id}，sent→received→confirmed→in_progress→completed

TODO P2「任务追踪看板：增加时间轴视图」的支撑实现：
* 每个任务维护 `history` 事件流（何时生成、何时推送成功/失败、何时被确认、何时完成的原始时间戳）；
* `timeline()` 输出可直接渲染时间轴/Gantt 的结构（开始时间、最新事件、停留时长、状态跃迁链）；
* 任务表落盘 `outputs/rpa_tasks.json`，服务重启后仍可查看历史闭环记录（演示更连续）。

注意（勿改坏）：官方 mock RPA 对 HTTP keep-alive 兼容不佳，全部请求必须使用 `Connection: close` 短连接；
task_id 撞号时自动加随机后缀重试一次。
"""
import json
import os
import time
import uuid

import httpx

from . import data_service as dsv
from .benchmark import benchmark
from .llm import chat_json, llm_ready
from .logging_conf import get_logger
from .paths import OUTPUT_DIR

ds = dsv.ds
log = get_logger(__name__)

RPA_BASE = os.environ.get("RPA_BASE_URL", "http://127.0.0.1:8090")
STORE = os.path.join(OUTPUT_DIR, "rpa_tasks.json")

TASK_SYSTEM = """你是制药企业成本整改任务调度器。基于归因分析结论，生成结构化整改任务JSON数组，每条包含字段：
task_title（任务标题，具体可执行）、assignee_name（责任人姓名）、assignee_department（责任部门）、
assignee_role（岗位）、priority（high/medium/low）、finding（问题描述，含具体数据）。
输出 {"tasks":[...]}，责任人从以下人员库中选择：采购部-张伟(采购经理)、生产部-李芳(生产主管)、
设备部-王强(设备工程师)、质量部-刘敏(QA主管)、财务部-陈静(成本会计)。"""

# 内存任务表 + 落盘（演示用；生产可换 SQLite/DB）
TASKS = []
_SEQ = 0
_TASKS_FILE = STORE


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _load():
    """从落盘文件恢复历史任务（文件损坏时静默跳过）"""
    global TASKS, _SEQ
    if not os.path.exists(_TASKS_FILE):
        return
    try:
        with open(_TASKS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        TASKS = data.get("tasks", [])
        _SEQ = int(data.get("seq", len(TASKS)))
        log.info("已恢复历史整改任务 %d 条", len(TASKS))
    except Exception as e:
        log.warning("整改任务历史恢复失败：%s", e)


def _save():
    try:
        os.makedirs(os.path.dirname(_TASKS_FILE), exist_ok=True)
        with open(_TASKS_FILE, "w", encoding="utf-8") as f:
            json.dump({"seq": _SEQ, "tasks": TASKS}, f, ensure_ascii=False, indent=1)
    except OSError as e:
        log.warning("整改任务落盘失败：%s", e)


def _event(rec, status, note=""):
    """记录一次状态事件（时间轴用）"""
    rec.setdefault("history", []).append({"time": _now(), "status": status, "note": note})


# ==================== 任务生成 ====================
def _rule_tasks(month, product, rca_text):
    """规则兜底：按归因结论生成 3 条官方 schema 任务"""
    return [
        {"task_title": f"请核查{month}重点药材采购合同调价条款，评估锁价方案",
         "assignee_name": "张伟", "assignee_department": "采购部", "assignee_role": "采购经理",
         "finding": (rca_text or "").split("\n")[0][:120] or "药材采购单价波动超出阈值",
         "priority": "high"},
        {"task_title": f"排查{month}提取车间收率波动原因，形成偏差调查记录",
         "assignee_name": "李芳", "assignee_department": "生产部", "assignee_role": "生产主管",
         "finding": "单位消耗成本存在异常波动", "priority": "high"},
        {"task_title": f"跟踪{month}制造费用单耗，对标行业P50基线",
         "assignee_name": "王强", "assignee_department": "设备部", "assignee_role": "设备工程师",
         "finding": "制造费用单位成本与行业基准存在差异", "priority": "medium"},
    ]


def generate_tasks(month, product, use_llm=True):
    """生成整改任务：大模型（任务字段归一化用 small 角色）→ 规则兜底"""
    bm = benchmark(month, use_llm=False)
    rca_text = bm["归因分析"]
    tasks, engine = None, "rule"
    if use_llm and llm_ready():
        out = chat_json(TASK_SYSTEM, json.dumps({"归因分析": rca_text, "月份": month, "产品": product},
                                                ensure_ascii=False), role="small")
        if out and out.get("tasks"):
            tasks = out["tasks"]
            engine = "llm"
    if not tasks:
        tasks = _rule_tasks(month, product, rca_text)
    return tasks, engine


def _official_payload(t, month, product, seq):
    """转为官方《模拟RPA接口文档》schema"""
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    return {
        "task_id": f"TASK-{month[:4]}-{seq:04d}",
        "task_title": t.get("task_title", "成本整改任务"),
        "assignee": {
            "name": t.get("assignee_name", "张伟"),
            "department": t.get("assignee_department", "采购部"),
            "role": t.get("assignee_role", "采购经理"),
        },
        "source": {
            "analysis_type": "月度成本分析",
            "analysis_month": month,
            "product": product or "全部产品",
            "finding": t.get("finding", ""),
        },
        "priority": t.get("priority", "medium"),
        "deadline": time.strftime("%Y-%m-%d", time.localtime(time.time() + 7 * 86400)),
        "suggestion": t.get("suggestion", t.get("finding", "")),
        "notify_method": "wechat",
        "created_at": now,
    }


# ==================== 调度 ====================
def dispatch(month, product=None, use_llm=True):
    """生成任务并推送到官方模拟 RPA 服务（8090）"""
    global _SEQ
    tasks, engine = generate_tasks(month, product, use_llm)
    results, pushed = [], []
    headers = {"Connection": "close"}       # 官方 mock 服务对 keep-alive 不兼容
    for t in tasks:
        _SEQ += 1
        payload = _official_payload(t, month, product, _SEQ)
        ok, resp = False, {}
        try:
            r = httpx.post(f"{RPA_BASE}/api/rpa/tasks", json=payload, timeout=30, headers=headers)
            body = r.json()
            ok = body.get("code") == 200
            resp = body
            if not ok and "已存在" in str(body.get("message", "") + body.get("detail", "")):
                payload["task_id"] = f"{payload['task_id']}-{uuid.uuid4().hex[:4].upper()}"
                r = httpx.post(f"{RPA_BASE}/api/rpa/tasks", json=payload, timeout=30, headers=headers)
                body = r.json()
                ok = body.get("code") == 200
                resp = body
        except Exception as e:
            resp = {"error": str(e)}
            log.warning("RPA 任务推送失败 %s：%s", payload["task_id"], e)
        status = resp.get("data", {}).get("status", "发送失败" if not ok else "sent")
        rec = {
            "task_id": payload["task_id"],
            "task_title": payload["task_title"],
            "责任人": f"{payload['assignee']['name']}({payload['assignee']['department']})",
            "责任部门": payload["assignee"]["department"],
            "产品": payload["source"]["product"],
            "月份": month,
            "优先级": payload["priority"],
            "截止时间": payload["deadline"],
            "status": status,
            "finding": payload["source"]["finding"],
            "sent_at": payload["created_at"],
            "history": [],
        }
        _event(rec, status, "推送模拟 RPA 服务（HTTP 200，模拟企业微信通知）" if ok
               else f"推送失败：{resp.get('error') or resp.get('message') or '未知原因'}")
        TASKS.append(rec)
        pushed.append(rec)
        results.append({"task_id": rec["task_id"], "title": rec["task_title"], "ok": ok})
        log.info("整改任务已下发：%s %s → %s", rec["task_id"], rec["task_title"][:24], status)
    _save()
    return {"engine": engine, "tasks": pushed, "results": results}


def refresh_status():
    """从官方服务拉取任务最新状态（观测 sent→received→confirmed→completed），并记录状态事件"""
    for t in TASKS:
        try:
            r = httpx.get(f"{RPA_BASE}/api/rpa/tasks/{t['task_id']}", timeout=10,
                          headers={"Connection": "close"})
            body = r.json()
            if body.get("code") == 200:
                new = body["data"].get("status", t["status"])
                t["progress"] = body["data"].get("progress", t.get("progress", ""))
                if new != t["status"]:
                    t["status"] = new
                    _event(t, new, f"状态流转（进度 {t['progress'] or '—'}）")
        except Exception as e:
            log.debug("任务状态刷新失败 %s：%s", t.get("task_id"), e)
    _save()
    return TASKS


def list_tasks(refresh=False):
    return refresh_status() if refresh else TASKS


# ==================== 看板统计与时间轴 ====================
STATUS_ORDER = ["sent", "received", "confirmed", "in_progress", "completed"]
STATUS_CN = {"sent": "已发送", "received": "已接收", "confirmed": "已确认",
             "in_progress": "处理中", "completed": "已完成", "发送失败": "发送失败"}


def stats():
    if not TASKS:
        return {"已生成": 0, "已发送": 0, "已确认": 0, "已完成": 0, "平均闭环时长(分钟)": None}
    done = [t for t in TASKS if t["status"] == "completed"]
    durations = []
    for t in done:
        h = t.get("history") or []
        if len(h) >= 2:
            try:
                t0 = time.mktime(time.strptime(h[0]["time"], "%Y-%m-%d %H:%M:%S"))
                t1 = time.mktime(time.strptime(h[-1]["time"], "%Y-%m-%d %H:%M:%S"))
                durations.append((t1 - t0) / 60.0)
            except Exception:
                pass
    return {
        "已生成": len(TASKS),
        "已发送": sum(1 for t in TASKS if t["status"] in STATUS_ORDER),
        "已确认": sum(1 for t in TASKS if t["status"] in ("confirmed", "in_progress", "completed")),
        "已完成": len(done),
        "发送失败": sum(1 for t in TASKS if t["status"] == "发送失败"),
        "平均闭环时长(分钟)": round(sum(durations) / len(durations), 1) if durations else None,
        "状态分布": {STATUS_CN.get(s, s): sum(1 for t in TASKS if t["status"] == s)
                     for s in set(t["status"] for t in TASKS)},
    }


def timeline(month=None, product=None, limit=20):
    """时间轴视图数据：按任务给出事件流、状态跃迁链与停留时长"""
    rows = [t for t in TASKS
            if (not month or t.get("月份") == month) and (not product or t.get("产品") in (product, "全部产品"))]
    rows = rows[-limit:]
    out = []
    for t in rows:
        hist = t.get("history") or []
        chain = []
        prev = None
        for e in hist:
            dur = None
            if prev:
                try:
                    dur = round((time.mktime(time.strptime(e["time"], "%Y-%m-%d %H:%M:%S"))
                                 - time.mktime(time.strptime(prev["time"], "%Y-%m-%d %H:%M:%S"))) / 60.0, 1)
                except Exception:
                    dur = None
            chain.append({"时间": e["time"], "状态": STATUS_CN.get(e["status"], e["status"]),
                          "状态码": e["status"], "说明": e["note"], "停留分钟": dur})
            prev = e
        out.append({
            "task_id": t["task_id"], "task_title": t["task_title"], "责任人": t["责任人"],
            "责任部门": t.get("责任部门", ""), "产品": t["产品"], "月份": t["月份"],
            "优先级": t["优先级"], "当前状态": STATUS_CN.get(t["status"], t["status"]),
            "状态码": t["status"], "截止时间": t.get("截止时间", ""),
            "起始时间": chain[0]["时间"] if chain else t.get("sent_at", ""),
            "最新事件": chain[-1] if chain else None,
            "事件数": len(chain), "状态链": chain,
            "闭环": t["status"] == "completed",
        })
    return {"时间轴": out, "统计": stats(),
            "状态顺序": [STATUS_CN[s] for s in STATUS_ORDER]}


_load()
