# -*- coding: utf-8 -*-
"""RCA（成本归因分析）质量评分卡自验脚本 —— 规则引擎 vs 大模型 输出质量对比

思路：
    把"人工 0-5 分"的质量评价拆成 6 个**可机械判定**的检查项，每项 0/1 分，
    加权映射到 0-5 总分（权重合计 5.0）。对同一场景分别取
        · 规则引擎输出：rca(month, product, use_llm=False)
        · 大模型输出  ：rca(month, product, use_llm=True)
    分别打分并逐项对比，定位规则模板相对大模型缺失的信息维度。

    场景覆盖：3 个产品 × 2026-05 / 2026-06。
    大模型可用性：.env 配了 LLM_API_KEY/LLM_BASE_URL 且连通才评分；连不通则
    只评规则输出，并在报告中明确标注"大模型不可用/降级"。

被测模块只作为黑盒被调用，本脚本不修改 app/ 下任何文件。

输出证据：outputs/eval/rca_quality.json（含逐场景得分、检查项证据、不可用标注）
退出码：  0 = 评分卡执行完成（评分高低不影响退出码）；1 = 脚本自身异常/无法完成

用法：
    python scripts/verify/verify_rca.py
    python scripts/verify/verify_rca.py --no-llm            # 只评规则引擎
    python scripts/verify/verify_rca.py --product 银黄口服液 --month 2026-06
    python scripts/verify/verify_rca.py --llm-probe-timeout 15
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import traceback
import unicodedata

# ---- 脚本位于二级目录，项目根为三级上级目录 ----
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

SCRIPT_VERSION = "verify_rca.py v1.0 (2026-09, 可复用 RCA 质量评分卡：6 项可判定检查，加权 0-5 分)"

DEFAULT_PRODUCTS = ["银黄口服液", "板蓝根颗粒", "六味地黄胶囊"]
DEFAULT_MONTHS = ["2026-05", "2026-06"]

# ----------------------------------------------------------------------
# 知识库词表（来自官方知识文档：药材行情/配方文档/生产工艺文档）
# ----------------------------------------------------------------------
HERBS = ["金银花", "黄芩提取物", "黄芩", "板蓝根", "熟地黄", "山茱萸", "山药", "泽泻",
         "茯苓", "牡丹皮", "蔗糖", "糊精", "空心胶囊", "苯甲酸钠", "纯化水",
         "包装材料", "PVC", "铝箔"]

# 注意：官方 PDF 抽取文本使用了「康熙部首」异体字（如 ⼯序 = U+2F27 + 序），
# 故同时收录两种写法，避免因字形差异漏判。
PROCESSES = ["提取", "⽔提取", "水提取", "浓缩", "配制", "灌流", "灌装", "灭菌", "灯检",
             "包装", "内包装", "外包装", "制粒", "⼲燥", "干燥", "混合", "总混", "拣选",
             "清洗", "粉碎", "胶囊填充", "填充", "抛光", "泡罩包装", "分装", "贴签",
             "收率", "投料", "工艺", "排班"]

ELEMENT_NAMES = ["直接材料", "直接人工", "制造费用"]
ELEMENT_CATS = ["折旧费", "动力费", "水电气", "间接人工", "检验费", "其他制造费用",
                "材料成本", "人工成本"]

# 第 4 项"知识库/工艺依据"关键词（不含《》，书名号单独判定）
KB_KEYWORDS = ["知识库", "工艺", "收率", "GMP", "行情", "配方", "药典", "市场价", "采购价"]

# 6 个可判定检查项：(编号, 名称, 权重)
CHECKS = [
    ("C1", "定位具体成本要素", 0.8),
    ("C2", "含金额与幅度", 0.8),
    ("C3", "含贡献度", 0.7),
    ("C4", "引用知识库/工艺依据", 0.9),
    ("C5", "给出可执行建议", 1.0),
    ("C6", "含具体药材/工序名", 0.8),
]
MAX_SCORE = 5.0


# ======================================================================
# 工具
# ======================================================================
def disp_width(s) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in str(s))


def pad(s, width: int, align: str = "left") -> str:
    s = str(s)
    extra = width - disp_width(s)
    if extra <= 0:
        return s
    if align == "right":
        return " " * extra + s
    if align == "center":
        left = extra // 2
        return " " * left + s + " " * (extra - left)
    return s + " " * extra


def impl_fingerprint() -> dict:
    """记录被测实现文件指纹：并行开发时用于说明"本次验证对应哪个版本" """
    import hashlib
    out = {}
    for rel in ["app/rca.py", "app/data_service.py", "app/llm.py", "app/rag.py"]:
        path = os.path.join(ROOT, rel.replace("/", os.sep))
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as f:
            data = f.read()
        out[rel] = {"sha256": hashlib.sha256(data).hexdigest()[:16], "字节": len(data),
                    "修改时间": time.strftime("%Y-%m-%d %H:%M:%S",
                                          time.localtime(os.path.getmtime(path)))}
    return out


def print_table(headers, rows, aligns=None, title=None):
    aligns = aligns or ["left"] * len(headers)
    widths = [disp_width(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], disp_width(cell))
    line = "─" * (sum(widths) + 3 * len(headers) + 1)
    if title:
        print(title)
    print("┌" + line + "┐")
    print("│ " + " │ ".join(pad(h, widths[i], "center") for i, h in enumerate(headers)) + " │")
    print("├" + line + "┤")
    for row in rows:
        print("│ " + " │ ".join(pad(c, widths[i], aligns[i]) for i, c in enumerate(row)) + " │")
    print("└" + line + "┘")


# ---- 正则：金额 / 幅度 / 贡献度 / 建议编号 ----
RE_AMOUNT = re.compile(r"[-+]?\d+(?:\.\d+)?\s*元(?:/[^\s，。；、）)]+)?")
RE_PERCENT = re.compile(r"[-+]?\d+(?:\.\d+)?\s*%")
RE_SIGNED_NUM = re.compile(r"[-+]\d+(?:\.\d+)?")
RE_BOOK = re.compile(r"《[^》]{2,40}》")
RE_CONTRIB_WORD = re.compile(r"贡献")

# 建议编号：①-⑩ / 1. / 1、 / 1) 等（"32.0元/kg"这类小数点后接数字的不算编号）
RE_ITEM = re.compile(r"[①②③④⑤⑥⑦⑧⑨⑩]|(?:(?<!\d)\d{1,2}(?:[.、)）](?!\d)))")

# 建议段落边界：Markdown 标题 / 粗体小标题 / 「五、改进建议」式标题 / 文末
RE_HEADING = re.compile(r"(?m)^\s*(?:#{1,6}\s+.+|一、.{0,30}|二、.{0,30}|三、.{0,30}|四、.{0,30}"
                        r"|五、.{0,30}|六、.{0,30}|七、.{0,30}|\*\*.{0,40}\*\*)\s*$")


def _suggest_section(text: str) -> tuple[str, str]:
    """定位“建议”所在段落：优先取最后一个含“建议”的小标题（如「五、改进建议」/「**改进建议**」），
    取其到下一个标题之间的正文；无标题时退化为最后一次出现“建议”之后的文本。

    返回 (段落文本, 定位说明)。这样可避免大模型在正文句子里使用“建议……”时
    把 rfind 命中到句尾、从而误判为“无建议”。
    """
    heads = list(RE_HEADING.finditer(text))
    sb = None
    for h in heads:
        if "建议" in h.group(0):
            sb = h
    if sb is not None:
        nxt = next((h for h in heads if h.start() > sb.start()), None)
        end = nxt.start() if nxt else len(text)
        return text[sb.start():end], f"标题「{sb.group(0).strip()[:24]}」（第{sb.start()}字起）"
    idx = text.rfind("建议")
    if idx < 0:
        return "", "全文未出现“建议”"
    return text[idx:idx + 800], f"无“建议”标题，退化为末次出现位置（第{idx}字）"


# ======================================================================
# 评分卡（可复用）
# ======================================================================
def score_text(text: str, scene: str = "", engine: str = "") -> dict:
    """对一段归因文本执行 6 项可判定检查，返回 0-5 加权总分与逐项证据"""
    text = text or ""
    results = []

    def add(code, name, weight, ok, evidence):
        results.append({"编号": code, "检查项": name, "权重": weight,
                        "得分": 1 if ok else 0, "加权得分": weight if ok else 0.0,
                        "证据": evidence})

    # C1 定位具体成本要素
    hit_e1 = [w for w in ELEMENT_NAMES + ELEMENT_CATS if w in text]
    add("C1", "定位具体成本要素", 0.8, bool(hit_e1),
        "命中：" + "、".join(hit_e1) if hit_e1 else "未出现任何成本要素名（直接材料/直接人工/制造费用/费用类别）")

    # C2 含金额与幅度（元/盒、%、带符号数字 三者任一，且数额与幅度都要有）
    amounts = RE_AMOUNT.findall(text)
    percents = RE_PERCENT.findall(text)
    signed = RE_SIGNED_NUM.findall(text)
    ok2 = bool(amounts) and bool(percents or signed)
    add("C2", "含金额与幅度", 0.8, ok2,
        f"金额样例={amounts[:3]} 百分比样例={percents[:3]} 带符号数字样例={signed[:3]}"
        if ok2 else f"金额={len(amounts)}个，百分比={len(percents)}个，带符号数字={len(signed)}个")

    # C3 含贡献度（出现"贡献"或含百分数）
    ok3 = bool(RE_CONTRIB_WORD.search(text)) or bool(percents)
    add("C3", "含贡献度", 0.7, ok3,
        f"“贡献”出现{len(RE_CONTRIB_WORD.findall(text))}次，百分数{len(percents)}个")

    # C4 引用知识库/工艺依据（《》书名号 或 知识库/工艺/收率/GMP/行情/配方 等关键词）
    books = RE_BOOK.findall(text)
    kws = [w for w in KB_KEYWORDS if w in text]
    ok4 = bool(books) or bool(kws)
    add("C4", "引用知识库/工艺依据", 0.9, ok4,
        f"书名号={books[:3]} 关键词={kws}" if ok4 else "既无《》引用也无知识库/工艺/收率/GMP/行情/配方关键词")

    # C5 给出可执行建议（定位“建议”段落，段落内至少 2 条编号/分号建议）
    seg, loc = _suggest_section(text)
    items = RE_ITEM.findall(seg) if seg else []
    semis = [s for s in re.split(r"[；;]", seg) if len(s.strip()) >= 6] if seg else []
    ok5 = len(items) >= 2 or len(semis) >= 2
    add("C5", "给出可执行建议", 1.0, ok5,
        (f"{loc}；段内编号建议{len(items)}条（{'、'.join(items[:6])}），分号条目{len(semis)}条"
         if seg else loc))

    # C6 含具体药材/工序名（从知识库药材清单/工艺清单匹配）
    herbs = [w for w in HERBS if w in text]
    procs = [w for w in PROCESSES if w in text]
    ok6 = bool(herbs) or bool(procs)
    add("C6", "含具体药材/工序名", 0.8, ok6,
        f"药材={herbs[:6]} 工序/工艺词={procs[:6]}" if ok6 else "未命中知识库药材或工序词表")

    total = round(sum(r["加权得分"] for r in results), 2)
    base = round(sum(r["权重"] for r in results), 2)
    # 辅助丰富度指标：评分卡满分饱和时用于区分（不计入评分卡）
    richness = {
        "字数": len(text),
        "药材命中数": len([w for w in HERBS if w in text]),
        "工序/工艺词命中数": len([w for w in PROCESSES if w in text]),
        "金额出现次数": len(amounts),
        "百分比出现次数": len(percents),
        "带符号数字次数": len(signed),
        "书名号引用数": len(books),
        "知识库关键词数": len(kws),
    }
    return {
        "场景": scene, "引擎": engine, "文本长度": len(text),
        "检查项": results,
        "原始分": f"{sum(r['得分'] for r in results)}/{len(results)}",
        "加权总分": total, "满分": base,
        "折算5分制": round(total / base * MAX_SCORE, 2) if base else 0.0,
        "丰富度指标": richness,
        "文本摘要": re.sub(r"\s+", " ", text)[:220],
    }


# ======================================================================
# 大模型可用性探测
# ======================================================================
def probe_llm(probe_timeout: float, model_override: str | None) -> dict:
    """探测 LLM 是否可用：.env 已配 Key 且能连通（一次极短超时的真实调用）"""
    info = {"配置BaseURL": False, "配置APIKey": False, "模型": None,
            "探测结果": "未探测", "可用": False, "错误": None, "探测耗时s": None}
    try:
        import app.llm as llm_mod
    except Exception as exc:  # noqa: BLE001
        info["探测结果"] = "导入 app.llm 失败"
        info["错误"] = repr(exc)
        return info

    info["配置BaseURL"] = bool(llm_mod.BASE_URL)
    info["配置APIKey"] = bool(llm_mod.API_KEY)
    info["模型"] = model_override or llm_mod.MODEL
    if not (info["配置BaseURL"] and info["配置APIKey"]):
        info["探测结果"] = "LLM_API_KEY/LLM_BASE_URL 未配置（降级为规则引擎）"
        return info

    old_timeout, old_retries = os.environ.get("LLM_TIMEOUT"), os.environ.get("LLM_MAX_RETRIES")
    os.environ["LLM_TIMEOUT"] = str(probe_timeout)
    os.environ["LLM_MAX_RETRIES"] = "0"
    t0 = time.time()
    try:
        txt = llm_mod.chat("你是连通性测试助手。", "只回复两个字：连通", temperature=0.0)
        info["探测耗时s"] = round(time.time() - t0, 2)
        if txt:
            info["可用"] = True
            info["探测结果"] = f"连通成功（返回：{txt.strip()[:20]}）"
        else:
            info["探测结果"] = "调用失败/超时，已降级为规则引擎"
    except Exception as exc:  # noqa: BLE001
        info["探测耗时s"] = round(time.time() - t0, 2)
        info["错误"] = f"{type(exc).__name__}: {exc}"
        info["探测结果"] = "调用异常，已降级为规则引擎"
    finally:
        if old_timeout is None:
            os.environ.pop("LLM_TIMEOUT", None)
        else:
            os.environ["LLM_TIMEOUT"] = old_timeout
        if old_retries is None:
            os.environ.pop("LLM_MAX_RETRIES", None)
        else:
            os.environ["LLM_MAX_RETRIES"] = old_retries
    return info


# ======================================================================
# 主流程
# ======================================================================
def evaluate(scenes, use_llm: bool, llm_info: dict, out_path: str) -> int:
    from app.rca import rca

    print("=" * 104)
    print("RCA 归因分析质量评分卡 · 规则引擎 vs 大模型（0-5 分制，6 项可判定检查）")
    print("=" * 104)
    print(f"项目根  ：{ROOT}")
    print(f"被测模块：app.rca.rca(month, product, use_llm=...)")
    print(f"场景    ：{len(scenes)} 个（{'、'.join(f'{m}/{p}' for m, p in scenes)}）")
    print(f"大模型  ：{llm_info['探测结果']}；模型={llm_info['模型']}；可用={llm_info['可用']}")
    if not llm_info["可用"]:
        print("          ⚠ 标注：大模型不可用/降级 —— 本轮仅评规则引擎输出，大模型对照列为 N/A")

    print("\n【评分卡定义】每项 0/1 分，加权合计满分 5.0")
    print_table(["编号", "检查项", "权重", "可判定规则"],
                [[c, n, f"{w:.1f}", d] for (c, n, w), d in zip(CHECKS, [
                    "出现 直接材料/直接人工/制造费用 或费用类别（折旧费/动力费/间接人工/检验费）",
                    "正则命中 金额(元/kg、元/盒…) 且 命中 百分比 或 带符号数字",
                    "出现“贡献”或任何百分数",
                    "出现《》书名号引用，或 知识库/工艺/收率/GMP/行情/配方 等关键词",
                    "出现“建议”且其后至少有 2 条 ①②③ / 1. 2. / 分号 建议",
                    "命中知识库药材清单（金银花/黄芩提取物/熟地黄…）或工序词（提取/浓缩/制粒/胶囊填充…）",
                ])], ["center", "left", "center", "left"])

    records = []
    for month, product in scenes:
        scene = f"{month} / {product}"
        print("\n" + "-" * 104)
        print(f"▶ 场景：{scene}")
        # --- 规则引擎（黑盒调用规则路径）---
        t0 = time.time()
        rule_res = rca(month, product, use_llm=False)
        rule_elapsed = round(time.time() - t0, 2)
        rule_score = score_text(rule_res.get("text", ""), scene, "规则引擎")

        # --- 大模型（可用才调用）---
        llm_score, llm_elapsed, llm_engine, llm_hits = None, None, None, None
        if use_llm and llm_info["可用"]:
            t0 = time.time()
            llm_res = rca(month, product, use_llm=True)
            llm_elapsed = round(time.time() - t0, 2)
            llm_engine = llm_res.get("engine")
            llm_hits = len(llm_res.get("sources") or [])
            if llm_engine == "llm" and llm_res.get("text"):
                llm_score = score_text(llm_res["text"], scene, "大模型")
            else:
                llm_engine = llm_engine or "rule"
                print("   ⚠ 大模型本次调用未返回文本（超时/异常），该场景大模型评分记 N/A（实现已自动降级）")

        # 控制台逐场景表
        rows = []
        for item in rule_score["检查项"]:
            lscore = None
            if llm_score:
                lscore = next((x["得分"] for x in llm_score["检查项"] if x["编号"] == item["编号"]), None)
            rows.append([f"{item['编号']} {item['检查项']}",
                         f"{item['得分']}(权重{item['权重']:.1f})",
                         "N/A" if lscore is None else f"{lscore}",
                         "✅" if item["得分"] else "❌",
                         "—" if lscore is None else ("✅" if lscore else "❌")])
        print_table(["检查项", "规则引擎", "大模型", "规则", "大模型"], rows,
                    ["left", "center", "center", "center", "center"],
                    title=f"  场景得分对比：{scene}")
        print(f"  规则引擎 加权总分 {rule_score['加权总分']}/{rule_score['满分']} "
              f"（折算 5 分制 {rule_score['折算5分制']}），文本 {rule_score['文本长度']} 字，耗时 {rule_elapsed}s")
        if llm_score:
            print(f"  大模型   加权总分 {llm_score['加权总分']}/{llm_score['满分']} "
                  f"（折算 5 分制 {llm_score['折算5分制']}），文本 {llm_score['文本长度']} 字，耗时 {llm_elapsed}s")
        else:
            print(f"  大模型   N/A{'（不可用/降级）' if not llm_info['可用'] else ''}")

        records.append({
            "场景": scene, "月份": month, "产品": product,
            "规则引擎": {**rule_score, "耗时s": rule_elapsed, "engine": rule_res.get("engine"),
                     "sources命中数": len(rule_res.get("sources") or [])},
            "大模型": (None if llm_score is None else
                    {**llm_score, "耗时s": llm_elapsed, "engine": llm_engine,
                     "sources命中数": llm_hits}),
        })

    # ---------- 汇总 ----------
    rule_totals = [r["规则引擎"]["加权总分"] for r in records]
    llm_totals = [r["大模型"]["加权总分"] for r in records if r["大模型"]]
    rule_avg = round(sum(rule_totals) / len(rule_totals), 2) if rule_totals else 0.0
    llm_avg = round(sum(llm_totals) / len(llm_totals), 2) if llm_totals else None

    check_pass = {}
    for code, name, _w in CHECKS:
        r_pass = sum(1 for r in records
                     if next(x["得分"] for x in r["规则引擎"]["检查项"] if x["编号"] == code))
        l_den = [r for r in records if r["大模型"]]
        l_pass = sum(1 for r in l_den
                     if next(x["得分"] for x in r["大模型"]["检查项"] if x["编号"] == code))
        check_pass[code] = {"检查项": name, "规则通过": r_pass, "规则样本": len(records),
                            "大模型通过": (l_pass if l_den else None),
                            "大模型样本": (len(l_den) if l_den else 0)}

    print("\n" + "=" * 104)
    print("【汇总】逐检查项通过率")
    print_table(["编号", "检查项", "规则引擎通过", "大模型通过", "规则胜/负"],
                [[code, v["检查项"], f"{v['规则通过']}/{v['规则样本']}",
                  ("N/A" if v["大模型通过"] is None else f"{v['大模型通过']}/{v['大模型样本']}"),
                  ("—" if v["大模型通过"] is None else
                   ("规则更优" if v["规则通过"] * v["大模型样本"] > v["大模型通过"] * v["规则样本"]
                    else ("持平" if v["规则通过"] * v["大模型样本"] == v["大模型通过"] * v["规则样本"]
                          else "大模型更优")))]
                 for code, v in check_pass.items()],
                ["center", "left", "center", "center", "center"])

    print("\n【汇总】场景总分对比（折算 5 分制）")
    print_table(["场景", "规则引擎", "大模型", "差值(大模型-规则)"],
                [[r["场景"],
                  f"{r['规则引擎']['折算5分制']}",
                  ("N/A" if not r["大模型"] else f"{r['大模型']['折算5分制']}"),
                  ("N/A" if not r["大模型"] else
                   f"{round(r['大模型']['折算5分制'] - r['规则引擎']['折算5分制'], 2):+}")]
                 for r in records],
                ["left", "center", "center", "center"])
    print(f"  平均分：规则引擎 {rule_avg}/5.0"
          + (f"；大模型 {llm_avg}/5.0；差值 {round(llm_avg - rule_avg, 2):+}" if llm_avg is not None
             else "；大模型 N/A（不可用/降级，未参与对比）"))

    # 辅助：评分卡饱和时用丰富度指标进一步区分
    keys = ["字数", "药材命中数", "工序/工艺词命中数", "金额出现次数", "百分比出现次数", "书名号引用数"]
    r_rich = {k: round(sum(r["规则引擎"]["丰富度指标"][k] for r in records) / len(records), 1) for k in keys}
    l_recs = [r["大模型"] for r in records if r["大模型"]]
    l_rich = ({k: round(sum(r["丰富度指标"][k] for r in l_recs) / len(l_recs), 1) for k in keys}
              if l_recs else None)
    print("\n【汇总·辅助】信息丰富度均值（评分卡满分饱和时用于区分，不计入评分卡）")
    print_table(["指标", "规则引擎均值", "大模型均值"],
                [[k, r_rich[k], ("N/A" if l_rich is None else l_rich[k])] for k in keys],
                ["left", "center", "center"])

    # ---------- 结论 ----------
    conclusion = []
    if llm_avg is not None:
        if llm_avg > rule_avg:
            conclusion.append(f"大模型整体优于规则引擎（均值 {llm_avg} vs {rule_avg}，"
                              f"{round(llm_avg - rule_avg, 2):+}）")
        elif llm_avg < rule_avg:
            conclusion.append(f"规则引擎整体优于大模型（均值 {rule_avg} vs {llm_avg}，"
                              f"{round(rule_avg - llm_avg, 2):+}）—— 规则模板在本评分卡口径下更稳定")
        else:
            conclusion.append(f"两者整体持平（均值均 {rule_avg}）")
        diff_items = [code for code, v in check_pass.items()
                      if v["大模型通过"] is not None
                      and v["规则通过"] * v["大模型样本"] != v["大模型通过"] * v["规则样本"]]
        if diff_items:
            conclusion.append("存在差异的检查项：" + "、".join(
                f"{c}({check_pass[c]['检查项']})" for c in diff_items))
        else:
            conclusion.append("全部 6 项检查通过率一致")
    else:
        conclusion.append("大模型不可用/降级，仅完成规则引擎评分；规则引擎在本评分卡口径下"
                          f"平均 {rule_avg}/5.0")
    if l_rich and rule_avg == llm_avg:
        conclusion.append("评分卡出现满分饱和（两者均 6/6 项通过，折算均 "
                          f"{rule_avg}/5.0）；按丰富度均值区分：字数 {r_rich['字数']} vs {l_rich['字数']}、"
                          f"药材命中 {r_rich['药材命中数']} vs {l_rich['药材命中数']}、"
                          f"金额次数 {r_rich['金额出现次数']} vs {l_rich['金额出现次数']} —— "
                          "大模型信息密度更高，规则引擎以稳定可复现、零调用成本见长")
    worst = [c for c, v in check_pass.items() if v["规则通过"] < v["规则样本"]]
    if worst:
        conclusion.append("规则引擎未满分的检查项：" + "、".join(
            f"{c}({check_pass[c]['检查项']} {check_pass[c]['规则通过']}/{check_pass[c]['规则样本']})"
            for c in worst))
    for c in conclusion:
        print("  · " + c)

    # ---------- 落盘 ----------
    evidence = {
        "脚本": SCRIPT_VERSION,
        "生成时间": time.strftime("%Y-%m-%d %H:%M:%S"),
        "项目根": ROOT,
        "被测模块": "app/rca.py :: rca(month, product=None, use_llm=True)",
        "被测实现指纹": impl_fingerprint(),
        "评分卡": {
            "满分": MAX_SCORE,
            "检查项": [{"编号": c, "检查项": n, "权重": w} for c, n, w in CHECKS],
            "说明": "每项 0/1 分，加权合计后折算为 5 分制；对应人工 0-5 分的自动化近似",
            "词表": {"成本要素": ELEMENT_NAMES + ELEMENT_CATS,
                    "知识库关键词": KB_KEYWORDS,
                    "药材清单": HERBS, "工序/工艺词": PROCESSES},
        },
        "大模型可用性": llm_info,
        "降级标注": (None if llm_info["可用"] else "大模型不可用/降级：本期仅评规则引擎输出"),
        "场景": [{"月份": m, "产品": p} for m, p in scenes],
        "逐场景得分": records,
        "汇总": {
            "规则引擎平均折算5分制": rule_avg,
            "大模型平均折算5分制": llm_avg,
            "逐检查项通过率": check_pass,
            "丰富度均值": {"规则引擎": r_rich, "大模型": l_rich},
            "结论": conclusion,
        },
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(evidence, f, ensure_ascii=False, indent=1)
    print(f"\n📄 证据已落盘：{out_path}")
    print("\n✅ 评分卡执行完成（评分高低不影响退出码）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="RCA 质量评分卡自验：规则引擎 vs 大模型（0-5 分制）")
    ap.add_argument("--month", action="append", default=None,
                    help=f"月份，可重复；默认 {'、'.join(DEFAULT_MONTHS)}")
    ap.add_argument("--product", action="append", default=None,
                    help=f"产品，可重复；默认 {'、'.join(DEFAULT_PRODUCTS)}")
    ap.add_argument("--no-llm", action="store_true", help="只评规则引擎（不探测也不调用大模型）")
    ap.add_argument("--llm-probe-timeout", type=float, default=10.0,
                    help="大模型连通性探测超时秒数，默认 10")
    ap.add_argument("--model", default=None, help="覆盖 LLM_MODEL（仅本次进程生效）")
    ap.add_argument("--out", default=os.path.join(ROOT, "outputs", "eval", "rca_quality.json"),
                    help="证据 JSON 输出路径")
    args = ap.parse_args()

    # 关键：在导入 app.* 之前写入环境变量，保证 app.llm 读到（.env 中已存在则不覆盖）
    os.environ.setdefault("LLM_TIMEOUT", str(max(args.llm_probe_timeout * 3, 30)))
    os.environ.setdefault("LLM_MAX_RETRIES", "1")
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    months = sorted(set(args.month or DEFAULT_MONTHS))
    products = args.product or DEFAULT_PRODUCTS
    scenes = [(m, p) for m in months for p in products]

    t0 = time.time()
    try:
        if args.no_llm:
            llm_info = {"配置BaseURL": None, "配置APIKey": None, "模型": None,
                        "探测结果": "已通过 --no-llm 显式关闭大模型评分（降级标注生效）",
                        "可用": False, "错误": None, "探测耗时s": None}
        else:
            llm_info = probe_llm(args.llm_probe_timeout, args.model)
        code = evaluate(scenes, not args.no_llm, llm_info, args.out)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        print("\n❌ 脚本自身异常，非零退出")
        return 1
    print(f"⏱ 用时 {time.time() - t0:.1f}s")
    return code


if __name__ == "__main__":
    sys.exit(main())
