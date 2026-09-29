# -*- coding: utf-8 -*-
"""报告图表渲染 —— Windows GDI+ 引擎（零第三方依赖）

适用场景：Windows 环境未安装 matplotlib / Pillow 时，用系统自带 .NET `System.Drawing`
绘制图表并输出 PNG（脚本见 `app/_render_charts.ps1`），保证"解压即用"的演示环境下报告仍有图表嵌入。

优先级：matplotlib → Pillow → GDI+（本模块）→ 无图（仅告警，不报错）。
"""
import json
import os
import subprocess
import sys
import tempfile

from .logging_conf import get_logger
from .paths import OUTPUT_DIR

log = get_logger(__name__)

CHART_DIR = os.path.join(OUTPUT_DIR, "charts")
os.makedirs(CHART_DIR, exist_ok=True)
SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_render_charts.ps1")

_EXE = None


def _powershell():
    global _EXE
    if _EXE is None:
        import shutil
        _EXE = shutil.which("powershell") or shutil.which("pwsh") or ""
    return _EXE


def available():
    """Windows + PowerShell 可用即视为可用"""
    if sys.platform != "win32":
        return False
    if not os.path.exists(SCRIPT):
        return False
    try:
        # 绘图脚本必须为纯 ASCII：Windows PowerShell 5.1 会把无 BOM 的 .ps1 当作 ANSI 读取，
        # 中文注释可能导致解析失败（图表中文一律通过 JSON 规格传入）。
        with open(SCRIPT, encoding="utf-8") as f:
            if any(ord(ch) > 127 for ch in f.read()):
                log.warning("_render_charts.ps1 含非 ASCII 字符，可能解析失败")
    except OSError:
        return False
    return bool(_powershell())


# ---------- 规格构造 ----------
def _specs(data):
    """把报告数据转换为绘图脚本所需的规格列表"""
    out = []

    if data.get("trend"):
        out.append({"key": "trend", "type": "trend", "title": "近 6 个月成本趋势",
                    "ylabel": "总成本(万元)", "ylabel2": "单位成本(元/盒)",
                    "x": [r["月份"][-2:] + "月" for r in data["trend"]],
                    "bars": [round(r["总成本"] / 10000, 2) for r in data["trend"]],
                    "series": [{"name": k, "values": [round(r[k], 2) for r in data["trend"]]}
                               for k in ("单位成本", "直接材料", "直接人工", "制造费用")]})

    if data.get("structure"):
        out.append({"key": "structure", "type": "structure", "title": "单位成本构成",
                    "center": f"{data.get('单位成本') or 0:.2f}", "center_sub": "元/盒",
                    "items": [{"name": k, "value": round(v, 2)} for k, v in data["structure"].items()]})

    if data.get("waterfall"):
        items = data["waterfall"]
        out.append({"key": "waterfall", "type": "waterfall",
                    "title": "单位成本环比变动瀑布（上期 → 本期）", "ylabel": "元/盒",
                    "labels": [it[0] for it in items],
                    "kinds": [it[1] for it in items],
                    "values": [round(it[2], 3) for it in items]})

    if data.get("materials"):
        top = sorted(data["materials"], key=lambda m: -abs(m["变动额_单盒"]))[:8][::-1]
        out.append({"key": "material_impact", "type": "hbar",
                    "title": "主要原材料对单位成本的影响（元/盒）",
                    "labels": [m["药材"] for m in top],
                    "values": [round(m["变动额_单盒"], 3) for m in top]})

    if data.get("prices"):
        series = []
        for mat, arr in data["prices"].items():
            base = arr[0] or 1
            series.append({"name": mat, "values": [round(v / base * 100, 2) for v in arr]})
        out.append({"key": "price_trend", "type": "lines", "title": "关键原材料市场价格走势",
                    "ylabel": "价格指数(1月=100)", "x": [f"{i}月" for i in range(1, 7)],
                    "series": series})

    bench = data.get("benchmark") or {}
    if bench:
        groups = ["单位成本", "直接材料", "直接人工", "制造费用"]
        out.append({"key": "benchmark", "type": "bars", "title": f"两厂成本对标（{bench['产品']}）",
                    "ylabel": "元/盒", "groups": groups,
                    "series": [{"name": "中药一厂", "values": [round(bench["一厂"][g], 2) for g in groups]},
                               {"name": "中药二厂", "values": [round(bench["二厂"][g], 2) for g in groups]}]})

    if data.get("budget"):
        rows = [(lab, v) for lab, v in data["budget"]]
        out.append({"key": "budget_compare", "type": "bars", "title": "单位成本对比（实际 vs 上期/去年同期/预算）",
                    "ylabel": "元/盒", "w": 620, "h": 300, "groups": [r[0] for r in rows],
                    "series": [{"name": "单位成本", "values": [round(r[1] or 0, 2) for r in rows]}]})
    return out


def _decode(raw):
    """PowerShell 输出解码：优先 UTF-8，退回 GBK（Windows 中文环境）"""
    for enc in ("utf-8", "gbk", "mbcs"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", "ignore")


def build_all(data):
    """与其它引擎同签名：返回 {key: png_path}"""
    if not available():
        return {}
    specs = _specs(data)
    if not specs:
        return {}
    try:
        from .charts import _clean_stale
        _clean_stale()
    except Exception:
        pass
    fd, spec_path = tempfile.mkstemp(suffix=".json", prefix="charts_", text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        # ensure_ascii=True：规格文件为纯 ASCII，规避 PowerShell 5.1 的编码坑
        json.dump({"charts": specs}, f, ensure_ascii=True)
    try:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        r = subprocess.run([_powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                            "-File", SCRIPT, "-Spec", spec_path, "-OutDir", CHART_DIR],
                           capture_output=True, timeout=180, creationflags=flags)
        out = _decode(r.stdout or b"").strip()
        if not out:
            err = _decode(r.stderr or b"").strip()[:400]
            log.warning("GDI 绘图脚本无输出：%s", err)
            return {}
        rows = json.loads(out)
        if isinstance(rows, dict):
            rows = [rows]
        result = {row["key"]: row["path"] for row in rows
                  if row.get("ok") and row.get("path") and os.path.exists(row["path"])}
        for row in rows:
            if not row.get("ok"):
                log.warning("GDI 图表 %s 失败：%s", row.get("key"), row.get("error"))
        return result
    except Exception as e:
        log.warning("GDI 渲染异常：%s", e)
        return {}
    finally:
        try:
            os.remove(spec_path)
        except OSError:
            pass
