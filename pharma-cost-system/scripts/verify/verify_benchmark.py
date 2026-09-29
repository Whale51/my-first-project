# -*- coding: utf-8 -*-
"""对标分析（三步法）独立复算自验脚本 —— 赛题红线：差异计算准确率误差 ≤ 1%

设计原则（重要）：
    本脚本 **不调用** 被测模块的任何计算函数，只把 `app.benchmark` 当作"黑盒输出"来对拍。
    所有期望值均由 pandas 直接读取官方模拟 CSV（`data_official/创灵境_考题模拟数据/01_成本明细数据/`，
    encoding='utf-8-sig'）后独立复算：
        差异额  = 一厂单位成本 − 二厂单位成本
        差异率% = 差异额 / 二厂单位成本 × 100
        三要素差异率同理（直接材料 / 直接人工 / 制造费用）。

复算/对拍口径说明（避免"看似有误差"的误判）：
    1. 官方 CSV 的"单位成本(元/盒)"已是单盒口径，且与三项要素之和一致（脚本会校验），
       因此不需要再用"总成本 ÷ 产量"反算，直接取列值即为复算基准。
    2. 被测实现先对输入做 round(x, 3)（因为 CSV 原始值只保留 2 位小数，此步理论无损），
       再对输出的"差异额"round(..., 3)、对百分比 round(..., 2)。本脚本按同一输出精度对拍，
       并把"未四舍五入的原始误差"一并记录，以便区分**显示取整误差**与**真实计算误差**。

输出证据：outputs/eval/benchmark_accuracy.json
退出码：  0 = 全部通过；1 = 任一超限（或脚本自身异常）

用法：
    python scripts/verify/verify_benchmark.py
    python scripts/verify/verify_benchmark.py --month 2026-05
    python scripts/verify/verify_benchmark.py --strict-pct 0.01 --skip-format
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
import unicodedata

# ---- 脚本位于二级目录，项目根为三级上级目录 ----
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import pandas as pd  # noqa: E402

SCRIPT_VERSION = "verify_benchmark.py v1.0 (2026-09, 独立复算版：pandas 直读官方 CSV，不调用被测计算函数)"
SPEC_REDLINE = 1.0        # 赛题红线：差异计算准确率误差 ≤ 1%
SELF_STRICT = 0.01        # 自定更严阈值：≤ 0.01%
TOL_UNITCOST = 0.001      # 单位成本/差异额（元/盒）容忍度，等于实现输出精度
ELEMENTS = ["直接材料", "直接人工", "制造费用"]

OFFICIAL_CSV = {
    "f1": "01_成本明细数据/中药一厂_成本汇总_2026年1-6月.csv",
    "f2": "01_成本明细数据/中药二厂_成本汇总_2026年1-6月.csv",
}

# 对拍字段：输出字段名 -> (要素名或 None 表示单位成本, 是否为百分比)
COMPARE_FIELDS = [
    ("一厂单位成本", None, False),
    ("二厂单位成本", None, False),
    ("差异额", None, False),
    ("差异率%", "单位成本", True),
    ("材料差异率%", "直接材料", True),
    ("人工差异率%", "直接人工", True),
    ("制造费用差异率%", "制造费用", True),
]


# ======================================================================
# 0. 小工具
# ======================================================================
def disp_width(s: str) -> int:
    """中日韩全角字符按 2 列宽计算，保证控制台表格对齐"""
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


def print_table(headers, rows, aligns=None, title=None):
    """打印可读的等宽表格（自动按显示宽度对齐）"""
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


def r3(x: float) -> float:
    return round(float(x), 3)


def r2(x: float) -> float:
    return round(float(x), 2)


def impl_fingerprint() -> dict:
    """记录被测实现文件指纹：并行开发时用于说明"本次验证对应哪个版本" """
    import hashlib
    out = {}
    for rel in ["app/benchmark.py", "app/data_service.py", "app/llm.py", "app/rag.py"]:
        path = os.path.join(ROOT, rel.replace("/", os.sep))
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as f:
            data = f.read()
        out[rel] = {"sha256": hashlib.sha256(data).hexdigest()[:16], "字节": len(data),
                    "修改时间": time.strftime("%Y-%m-%d %H:%M:%S",
                                          time.localtime(os.path.getmtime(path)))}
    return out


# ======================================================================
# 1. 独立复算：pandas 直读官方 CSV
# ======================================================================
class OfficialCSVReader:
    """官方成本汇总 CSV 独立读取器（不经过 app.data_service）"""

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.raw = {}
        self.source_files = {}
        for key, rel in OFFICIAL_CSV.items():
            path = os.path.join(data_dir, rel)
            if not os.path.isfile(path):
                raise FileNotFoundError(f"缺少官方 CSV：{path}")
            df = pd.read_csv(path, encoding="utf-8-sig")
            need = ["月份", "产品名称", "产量(盒)", "直接材料(元/盒)", "直接人工(元/盒)",
                    "制造费用(元/盒)", "单位成本(元/盒)", "总成本(元)"]
            miss = [c for c in need if c not in df.columns]
            if miss:
                raise ValueError(f"{rel} 缺少列：{miss}")
            self.raw[key] = df
            self.source_files[key] = rel

        m1 = set(self.raw["f1"]["月份"].astype(str))
        m2 = set(self.raw["f2"]["月份"].astype(str))
        self.months = sorted(m1 & m2)
        p1 = set(self.raw["f1"]["产品名称"].astype(str))
        p2 = set(self.raw["f2"]["产品名称"].astype(str))
        self.products = sorted(p1 & p2)
        self.only_f1 = sorted(p1 - p2)
        self.only_f2 = sorted(p2 - p1)

    def cell(self, factory: str, month: str, product: str) -> dict | None:
        df = self.raw[factory]
        q = df[(df["月份"].astype(str) == month) & (df["产品名称"].astype(str) == product)]
        if not len(q):
            return None
        r = q.iloc[0]
        return {
            "产量": float(r["产量(盒)"]),
            "直接材料": float(r["直接材料(元/盒)"]),
            "直接人工": float(r["直接人工(元/盒)"]),
            "制造费用": float(r["制造费用(元/盒)"]),
            "单位成本": float(r["单位成本(元/盒)"]),
            "总成本": float(r["总成本(元)"]),
        }

    def audit_internal_consistency(self) -> list:
        """数据层自检：三项要素之和 = 单位成本；单位成本 × 产量 = 总成本（容差 0.01）"""
        issues = []
        for key, df in self.raw.items():
            for _, r in df.iterrows():
                unit = float(r["单位成本(元/盒)"])
                parts = sum(float(r[c]) for c in ["直接材料(元/盒)", "直接人工(元/盒)", "制造费用(元/盒)"])
                if abs(parts - unit) > 0.01:
                    issues.append({"文件": self.source_files[key], "月份": str(r["月份"]),
                                   "产品": str(r["产品名称"]), "问题": "三要素之和不等于单位成本",
                                   "三要素之和": round(parts, 3), "单位成本": round(unit, 3)})
                qty, tot = float(r["产量(盒)"]), float(r["总成本(元)"])
                if abs(unit * qty - tot) > 0.01:
                    issues.append({"文件": self.source_files[key], "月份": str(r["月份"]),
                                   "产品": str(r["产品名称"]), "问题": "单位成本×产量不等于总成本",
                                   "计算值": round(unit * qty, 2), "CSV值": round(tot, 2)})
        return issues

    def recompute(self, month: str) -> dict:
        """独立复算一个月的全部产品对标指标（严格按赛题公式）"""
        out = {}
        for p in self.products:
            a = self.cell("f1", month, p)
            b = self.cell("f2", month, p)
            if not a or not b:
                continue
            rec = {
                "产品": p,
                # 期望值按实现输出精度取整后再对拍
                "一厂单位成本": r3(a["单位成本"]),
                "二厂单位成本": r3(b["单位成本"]),
                "差异额": r3(a["单位成本"] - b["单位成本"]),
                "差异率%": r2((a["单位成本"] - b["单位成本"]) / b["单位成本"] * 100),
                # 未取整原始值（用于诊断"显示取整误差"）
                "raw": {
                    "一厂单位成本": a["单位成本"], "二厂单位成本": b["单位成本"],
                    "差异额": a["单位成本"] - b["单位成本"],
                    "差异率%": (a["单位成本"] - b["单位成本"]) / b["单位成本"] * 100,
                },
            }
            for ele, field in zip(ELEMENTS, ["材料差异率%", "人工差异率%", "制造费用差异率%"]):
                rec[field] = r2((a[ele] - b[ele]) / b[ele] * 100)
                rec["raw"][field] = (a[ele] - b[ele]) / b[ele] * 100
            out[p] = rec
        return out


# ======================================================================
# 2. 黑盒对拍
# ======================================================================
def compare_month(month: str, expected: dict, actual_rows: list) -> dict:
    """把被测实现一个月的结果与独立复算结果逐字段对拍"""
    actual = {r.get("产品"): r for r in actual_rows}
    details, month_max = [], 0.0
    for product, exp in expected.items():
        act = actual.get(product)
        if act is None:
            details.append({"月份": month, "产品": product, "字段": "*", "状态": "缺失",
                            "复算值": None, "实现值": None, "误差": None})
            month_max = max(month_max, 999.0)
            continue
        for field, ele, is_pct in COMPARE_FIELDS:
            if field not in act:
                details.append({"月份": month, "产品": product, "字段": field, "状态": "字段缺失",
                                "复算值": exp[field], "实现值": None, "误差": None})
                month_max = max(month_max, 999.0)
                continue
            exp_val = exp[field]
            act_val = act[field]
            impl_prec = 2 if is_pct else 3
            err = abs(round(float(act_val), impl_prec) - round(float(exp_val), impl_prec))
            raw_err = abs(float(exp["raw"][field]) - round(float(exp_val), impl_prec))
            month_max = max(month_max, err)
            details.append({
                "月份": month, "产品": product, "字段": field, "要素": ele or "单位成本",
                "状态": "通过" if err <= max(TOL_UNITCOST, 0.0) else "超限",
                "复算值": exp_val, "实现值": act_val, "误差": round(err, 6),
                "未取整复算值": round(float(exp["raw"][field]), 6),
                "参考显示误差": round(raw_err, 6),
            })
    extra = sorted(set(actual) - set(expected))
    for product in extra:
        details.append({"月份": month, "产品": product, "字段": "*", "状态": "多余产品",
                        "复算值": None, "实现值": None, "误差": None})
        month_max = max(month_max, 999.0)
    return {"月份": month, "最大绝对误差": round(month_max, 6), "明细": details}


# ======================================================================
# 3. 三步法输出格式校验
# ======================================================================
def check_format(result: dict) -> dict:
    """校验 结构树 / 归因分析（规则引擎路径）/ 改进建议 的输出格式，统计格式正确率"""
    checks = []

    def add(name, ok, detail=""):
        checks.append({"检查项": name, "通过": bool(ok), "说明": detail})

    tree = result.get("结构树") or {}
    add("结构树·含产品", isinstance(tree, dict) and bool(str(tree.get("产品", "")).strip()),
        f"产品={tree.get('产品')!r}")
    rate = tree.get("单位成本差异率%") if isinstance(tree, dict) else None
    add("结构树·含单位成本差异率%", isinstance(rate, (int, float)),
        f"单位成本差异率%={rate!r}")

    struct = tree.get("结构拆解") if isinstance(tree, dict) else None
    ok_struct = isinstance(struct, list) and len(struct) == 3
    eles = []
    if ok_struct:
        for item in struct:
            if not (isinstance(item, dict) and "要素" in item and "差异率%" in item
                    and isinstance(item["差异率%"], (int, float))):
                ok_struct = False
                break
            eles.append(item["要素"])
        ok_struct = ok_struct and sorted(eles) == sorted(ELEMENTS)
    add("结构树·结构拆解含 3 要素(直接材料/直接人工/制造费用)", ok_struct, "要素=" + "/".join(map(str, eles)))

    main = tree.get("主因要素") if isinstance(tree, dict) else None
    main_ok = isinstance(main, str) and main in ELEMENTS
    if main_ok and ok_struct:
        try:
            main_ok = main == max(struct, key=lambda x: abs(x["差异率%"]))["要素"]
        except Exception:  # noqa: BLE001
            main_ok = False
    add("结构树·主因要素∈{三要素}且为|差异率%|最大项", main_ok, f"主因要素={main!r}")

    text = result.get("归因分析") or ""
    for step, label in [("第一步", "找差异"), ("第二步", "拆结构"), ("第三步", "拆原因")]:
        add(f"归因分析·含“{step}”({label})，规则引擎三步法", step in text,
            f"长度={len(text)}")
    order_ok = all(k in text for k in ["第一步", "第二步", "第三步"]) and \
        text.index("第一步") < text.index("第二步") < text.index("第三步")
    add("归因分析·三步顺序正确", order_ok, "第一步<第二步<第三步")

    sugg = result.get("改进建议")
    ok_list = isinstance(sugg, list) and len(sugg) >= 1
    add("改进建议·为非空列表", ok_list, f"条数={len(sugg) if isinstance(sugg, list) else 'N/A'}")
    if isinstance(sugg, list):
        for i, s in enumerate(sugg, 1):
            has = isinstance(s, dict) and all(k in s and str(s[k]).strip() for k in ["任务", "责任部门", "优先级"])
            add(f"改进建议#{i}·含 任务/责任部门/优先级", has,
                f"任务={str(s.get('任务'))[:18] if isinstance(s, dict) else s!r}…")
            pri = s.get("优先级") if isinstance(s, dict) else None
            add(f"改进建议#{i}·优先级∈{{高,中,低}}", pri in {"高", "中", "低"}, f"优先级={pri!r}")
    else:
        add("改进建议#1·含 任务/责任部门/优先级", False, "改进建议不是列表")

    passed = sum(1 for c in checks if c["通过"])
    return {"检查项": checks, "通过项": passed, "检查项数": len(checks),
            "格式正确率%": round(passed / len(checks) * 100, 2) if checks else 0.0}


# ======================================================================
# 4. 主流程
# ======================================================================
def evaluate(data_dir: str, months: list, products_filter: list | None,
             strict_pct: float, check_fmt: bool, out_path: str) -> int:
    from app.benchmark import benchmark  # 被测实现（仅黑盒调用）
    from app import paths

    reader = OfficialCSVReader(data_dir)
    print("=" * 100)
    print("对标分析差异计算准确率 · 独立复算自验（pandas 直读官方 CSV，不调用被测计算函数）")
    print("=" * 100)
    print(f"项目根      ：{ROOT}")
    print(f"官方数据目录：{data_dir}")
    print(f"被测模块    ：app.benchmark.benchmark(month, use_llm=False)")
    print(f"按月数据源  ：{reader.source_files['f1']}  &  {reader.source_files['f2']}")
    print(f"覆盖范围    ：{len(months)} 个月 × {len(reader.products)} 个产品 "
          f"= {len(months) * len(reader.products)} 组对标")
    print(f"赛题红线    ：最大绝对误差 ≤ {SPEC_REDLINE}%；自定严阈值 ≤ {strict_pct}%")

    data_issues = reader.audit_internal_consistency()
    print("\n【数据层自检】官方 CSV 三要素之和=单位成本、单位成本×产量=总成本")
    if data_issues:
        for it in data_issues[:10]:
            print(f"  ⚠ {it}")
    else:
        print("  ✅ 全部行通过（容差 0.01 元）")
    if reader.only_f1 or reader.only_f2:
        print(f"  ⚠ 产品集合不一致：仅一厂={reader.only_f1}，仅二厂={reader.only_f2}")

    # ---------- 逐月对拍 ----------
    month_results, all_details = [], []
    for month in months:
        expected = reader.recompute(month)
        if not expected:
            print(f"  ⚠ {month} 无共同产品，跳过")
            continue
        res = benchmark(month, use_llm=False)
        rows = res.get("差异对比表") or []
        cmp_res = compare_month(month, expected, rows)
        month_results.append(cmp_res)
        all_details.extend(cmp_res["明细"])

    if not month_results:
        print("❌ 没有任何可对拍的月份")
        return 1

    max_err = max(m["最大绝对误差"] for m in month_results)
    # 按字段聚合最大误差
    field_max = {f: 0.0 for f, _, _ in COMPARE_FIELDS}
    for d in all_details:
        if d.get("误差") is not None and d["字段"] in field_max:
            field_max[d["字段"]] = max(field_max[d["字段"]], d["误差"])
    far = [d for d in all_details if d.get("误差") is not None and d["误差"] > strict_pct]
    abnormal = [d for d in all_details if d.get("状态") in {"缺失", "字段缺失", "多余产品", "超限"}]

    # ---------- 汇总表 1：逐月 ----------
    print("\n【表1】逐月最大绝对误差")
    print_table(
        ["月份", "对拍字段数", "最大绝对误差(百分点/元)", f"≤{SPEC_REDLINE}%", f"≤{strict_pct}%"],
        [[m["月份"], len(m["明细"]), f"{m['最大绝对误差']:.6f}",
          "✅" if m["最大绝对误差"] <= SPEC_REDLINE else "❌",
          "✅" if m["最大绝对误差"] <= strict_pct else "❌"] for m in month_results],
        ["left", "right", "right", "center", "center"])

    # ---------- 汇总表 2：逐字段 ----------
    print("\n【表2】逐字段最大绝对误差（全部月份 × 全部产品）")
    print_table(
        ["输出字段", "最大绝对误差", f"≤{strict_pct}%"],
        [[f, f"{field_max[f]:.6f}", "✅" if field_max[f] <= strict_pct else "❌"]
         for f, _, _ in COMPARE_FIELDS],
        ["left", "right", "center"])

    # ---------- 样例明细（每字段最大误差那条）----------
    print("\n【表3】每字段最大误差明细（复算值 vs 实现值）")
    sample_rows = []
    for f, _, _ in COMPARE_FIELDS:
        cand = [d for d in all_details if d["字段"] == f and d.get("误差") is not None]
        if not cand:
            continue
        d = max(cand, key=lambda x: x["误差"])
        sample_rows.append([d["月份"], d["产品"], d["字段"], f"{d['复算值']}", f"{d['实现值']}",
                            f"{d['误差']:.6f}"])
    print_table(["月份", "产品", "字段", "复算值(独立)", "实现值(app)", "绝对误差"],
                sample_rows, ["left", "left", "left", "right", "right", "right"])

    # ---------- 格式校验 ----------
    fmt = None
    if check_fmt:
        print("\n【表4】三步法输出格式校验（结构与规则引擎路径）")
        fmt_checks, fmt_details = [], []
        for month in months:
            res = benchmark(month, use_llm=False)
            f = check_format(res)
            fmt_details.append({"月份": month, **f})
            fmt_checks.extend(f["检查项"])
        passed = sum(1 for c in fmt_checks if c["通过"])
        total = len(fmt_checks)
        fmt = {"检查项": fmt_checks, "通过项": passed, "检查项数": total,
               "格式正确率%": round(passed / total * 100, 2) if total else 0.0,
               "逐月": fmt_details}
        # 按检查项名聚合（去掉 #序号 的影响）
        agg = {}
        for c in fmt_checks:
            key = c["检查项"]
            agg.setdefault(key, [0, 0])
            agg[key][1] += 1
            agg[key][0] += 1 if c["通过"] else 0
        print_table(["检查项", "通过/总数", "结论"],
                    [[k, f"{v[0]}/{v[1]}", "✅" if v[0] == v[1] else "❌"]
                     for k, v in agg.items()],
                    ["left", "center", "center"])
        print(f"  格式正确率 = {passed}/{total} = {fmt['格式正确率%']}%")

    # ---------- 判定 ----------
    redline_ok = max_err <= SPEC_REDLINE
    strict_ok = max_err <= strict_pct
    fmt_ok = (fmt is None) or (fmt["格式正确率%"] == 100.0)
    print("\n【判定】")
    print(f"  最大绝对误差（全部 {len(all_details)} 条对拍）：{max_err:.6f}")
    print(f"  赛题红线误差 ≤ {SPEC_REDLINE}% ：{'✅ 满足' if redline_ok else '❌ 不满足'}")
    print(f"  自定严阈值 ≤ {strict_pct}% ：{'✅ 满足' if strict_ok else '❌ 不满足'}")
    if fmt is not None:
        print(f"  三步法格式正确率：{fmt['格式正确率%']}% "
              f"（{fmt['通过项']}/{fmt['检查项数']}）")
    if abnormal:
        print(f"  ⚠ 异常对拍记录 {len(abnormal)} 条：")
        for a in abnormal[:10]:
            print(f"     - {a}")
    if far and strict_ok is False:
        print(f"  ⚠ 超过严阈值 {strict_pct}% 的记录 {len(far)} 条（前 10 条）：")
        for d in far[:10]:
            print(f"     - {d['月份']} {d['产品']} {d['字段']}：复算{d['复算值']} vs 实现{d['实现值']}"
                  f"（误差{d['误差']:.6f}）")

    # ---------- 落盘 ----------
    evidence = {
        "脚本": SCRIPT_VERSION,
        "生成时间": time.strftime("%Y-%m-%d %H:%M:%S"),
        "项目根": ROOT,
        "官方数据目录": data_dir,
        "数据源文件": reader.source_files,
        "被测实现": "app/benchmark.py :: benchmark(month, product=None, use_llm=True)（本次 use_llm=False）",
        "被测实现指纹": impl_fingerprint(),
        "复算口径": {
            "公式": "差异额 = 一厂 - 二厂；差异率% = 差异额 / 二厂 × 100",
            "精度": "差异额/单位成本保留 3 位小数；差异率保留 2 位小数（与实现输出一致）",
            "读取": "pandas.read_csv(encoding='utf-8-sig')，CSV 的“单位成本(元/盒)”为单盒口径基准",
            "说明": "未取整的原始误差另存于每条明细的“未取整复算值/参考显示误差”，用于区分显示取整误差与真实计算误差",
        },
        "覆盖": {"月份": months, "产品": reader.products,
                 "对拍条数": len(all_details), "对拍字段": [f for f, _, _ in COMPARE_FIELDS]},
        "阈值": {"赛题红线%": SPEC_REDLINE, "自定严阈值%": strict_pct,
                 "单位成本/差异额容忍度": TOL_UNITCOST},
        "数据层自检": {"问题数": len(data_issues), "问题": data_issues},
        "结果": {
            "最大绝对误差": round(max_err, 6),
            "逐月最大误差": [{"月份": m["月份"], "最大绝对误差": m["最大绝对误差"]} for m in month_results],
            "逐字段最大误差": {k: round(v, 6) for k, v in field_max.items()},
            "赛题红线满足": redline_ok,
            "自定严阈值满足": strict_ok,
            "超严阈值记录数": len(far),
            "异常记录": abnormal,
        },
        "三步法格式校验": fmt,
        "逐条对拍明细": all_details,
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(evidence, f, ensure_ascii=False, indent=1)
    print(f"\n📄 证据已落盘：{out_path}")

    passed = redline_ok and strict_ok and fmt_ok and not abnormal
    print("\n" + ("✅ 自验通过" if passed else "❌ 自验未通过（详见上方判定与证据 JSON）"))
    return 0 if passed else 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description="对标分析差异计算准确率独立复算自验（赛题红线：误差 ≤ 1%）")
    ap.add_argument("--month", action="append", default=None,
                    help="只校验指定月份，可重复，如 --month 2026-01 --month 2026-05；默认 2026-01..06 全部")
    ap.add_argument("--product", action="append", default=None,
                    help="只校验指定产品（可重复），仅影响报告标注，对拍仍按整表逐字段")
    ap.add_argument("--strict-pct", type=float, default=SELF_STRICT,
                    help=f"自定严阈值，默认 {SELF_STRICT}")
    ap.add_argument("--skip-format", action="store_true", help="跳过三步法输出格式校验")
    ap.add_argument("--out", default=os.path.join(ROOT, "outputs", "eval", "benchmark_accuracy.json"),
                    help="证据 JSON 输出路径")
    args = ap.parse_args()

    if args.strict_pct > SPEC_REDLINE:
        print(f"⚠ 自定严阈值 {args.strict_pct} 大于赛题红线 {SPEC_REDLINE}，已按红线收敛")
        args.strict_pct = SPEC_REDLINE

    from app.paths import official_data_dir
    data_dir = official_data_dir()

    reader_probe = OfficialCSVReader(data_dir)
    months = args.month or reader_probe.months
    bad = [m for m in months if m not in reader_probe.months]
    if bad:
        print(f"❌ 月份不在官方数据范围内：{bad}；可选：{reader_probe.months}")
        return 1
    months = sorted(months)

    t0 = time.time()
    try:
        code = evaluate(data_dir, months, args.product, args.strict_pct,
                        not args.skip_format, args.out)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        print("\n❌ 脚本自身异常，非零退出")
        return 1
    print(f"⏱ 用时 {time.time() - t0:.1f}s")
    return code


if __name__ == "__main__":
    sys.exit(main())
