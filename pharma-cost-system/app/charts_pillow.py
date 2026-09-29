# -*- coding: utf-8 -*-
"""报告图表渲染 —— Pillow 兜底引擎（无 matplotlib 时使用）

与 `app/charts.py`（matplotlib 引擎）保持 **完全相同的函数签名与取舍**：
    build_all(data) -> {key: png_path}
因此 report_model 只需调用 `charts.build_all()`，引擎可自动降级而不影响报告结构。

实现要点：3 倍超采样绘制 + LANCZOS 缩小，得到平滑线条与清晰中文；
坐标轴、网格、图例、数值标签均为轻量自绘，风格与 matplotlib 版保持一致。
"""
import math
import os

from .logging_conf import get_logger
from .paths import OUTPUT_DIR

log = get_logger(__name__)

CHART_DIR = os.path.join(OUTPUT_DIR, "charts")
os.makedirs(CHART_DIR, exist_ok=True)

PALETTE = ["#2E6FB7", "#E08A2E", "#4C9A6A", "#B4524C", "#7C6BB0", "#5B9BD5", "#C9A227"]
GRID = "#DDDDDD"
AXIS = "#BBBBBB"
TEXT = "#333333"
TITLE = "#1F4E79"
SCALE = 3

_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyh.ttf", r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\Deng.ttf", r"C:\Windows\Fonts\simsun.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]
_FONT_PATH = None


def available():
    try:
        from PIL import Image, ImageDraw, ImageFont  # noqa: F401
        return True
    except Exception:
        return False


def _font_path():
    global _FONT_PATH
    if _FONT_PATH is None:
        _FONT_PATH = next((p for p in _FONT_CANDIDATES if os.path.exists(p)), "")
    return _FONT_PATH


class Canvas:
    """轻量绘图画布（内部 3 倍超采样后缩小，保证线条与文字平滑）"""

    def __init__(self, w, h):
        from PIL import Image, ImageDraw
        self.w, self.h = w, h
        self.img = Image.new("RGB", (w * SCALE, h * SCALE), "white")
        self.d = ImageDraw.Draw(self.img)
        self._fonts = {}

    # ---------- 基础 ----------
    def _font(self, size, bold=False):
        from PIL import ImageFont
        key = (size, bold)
        if key not in self._fonts:
            path = _font_path()
            try:
                self._fonts[key] = ImageFont.truetype(path, size * SCALE) if path \
                    else ImageFont.load_default()
            except Exception:
                self._fonts[key] = ImageFont.load_default()
        return self._fonts[key]

    def text(self, x, y, s, size=9, color=TEXT, anchor="la", bold=False):
        f = self._font(size, bold)
        try:
            self.d.text((x * SCALE, y * SCALE), str(s), font=f, fill=color, anchor=anchor)
        except TypeError:      # 极老版本 Pillow 不支持 anchor 参数
            w = self.text_width(s, size)
            dx = {"l": 0, "m": -w / 2, "r": -w}[anchor[0]]
            dy = {"a": 0, "m": -size / 2, "s": -size}[anchor[1]]
            self.d.text(((x + dx) * SCALE, (y + dy) * SCALE), str(s), font=f, fill=color)

    def text_width(self, s, size=9):
        try:
            return self.d.textlength(str(s), font=self._font(size)) / SCALE
        except Exception:
            return len(str(s)) * size * 0.6

    def line(self, x1, y1, x2, y2, color=AXIS, width=1.0, dash=None):
        if dash:
            self._dashed(x1, y1, x2, y2, color, width, dash)
            return
        self.d.line((x1 * SCALE, y1 * SCALE, x2 * SCALE, y2 * SCALE), fill=color,
                    width=max(1, int(round(width * SCALE))))

    def _dashed(self, x1, y1, x2, y2, color, width, dash):
        total = math.hypot(x2 - x1, y2 - y1)
        if total <= 0:
            return
        n = int(total / dash) + 1
        ux, uy = (x2 - x1) / total, (y2 - y1) / total
        for i in range(n):
            s = i * dash
            e = min(s + dash / 2, total)
            self.line(x1 + ux * s, y1 + uy * s, x1 + ux * e, y1 + uy * e, color, width)

    def rect(self, x0, y0, x1, y1, fill=None, outline=None, width=1.0):
        self.d.rectangle((x0 * SCALE, y0 * SCALE, x1 * SCALE, y1 * SCALE), fill=fill,
                         outline=outline, width=max(1, int(round(width * SCALE))))

    def poly(self, points, color, width=1.6, close=False, fill=None):
        pts = [(x * SCALE, y * SCALE) for x, y in points]
        if fill:
            self.d.polygon(pts, fill=fill)
        if close:
            pts = pts + [pts[0]]
        self.d.line(pts, fill=color, width=max(1, int(round(width * SCALE))), joint="curve")

    def dot(self, x, y, r, color):
        self.d.ellipse((x * SCALE - r * SCALE, y * SCALE - r * SCALE,
                        x * SCALE + r * SCALE, y * SCALE + r * SCALE), fill=color)

    def ring(self, cx, cy, r, thickness, start_deg, end_deg, color):
        box = ((cx - r) * SCALE, (cy - r) * SCALE, (cx + r) * SCALE, (cy + r) * SCALE)
        self.d.arc(box, start_deg, end_deg, fill=color, width=max(1, int(thickness * SCALE)))

    def save(self, name):
        from PIL import Image
        out = self.img.resize((self.w, self.h), Image.LANCZOS)
        path = os.path.join(CHART_DIR, f"{name}.png")
        tmp = f"{path}.tmp"          # 先写临时文件再原子替换，避免读到"半截"PNG
        out.save(tmp, format="PNG")
        os.replace(tmp, path)
        return path


# ---------- 坐标与刻度 ----------
def _ticks(vmax, vmin=0.0, n=4):
    """生成 [vmin, vmax] 区间内的整齐刻度"""
    span = vmax - vmin
    if span <= 0:
        span = abs(vmax) or 1.0
    raw = span / n
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            step = m * mag
            break
    else:
        step = 10 * mag
    lo = math.floor(vmin / step) * step
    hi = math.ceil(vmax / step) * step
    out, v = [], lo
    while v <= hi + step / 1000:
        out.append(round(v, 6))
        v += step
    return out


def _axes(c, box, y_ticks, x_labels, title, ylabel="", rotation=0):
    """绘制坐标轴 + 横向网格，返回 (x0, y0, x1, y1)"""
    x0, y0, x1, y1 = box
    lo, hi = y_ticks[0], y_ticks[-1]
    rng = (hi - lo) or 1.0

    def ypos(v):
        return y1 - (v - lo) / rng * (y1 - y0)

    for v in y_ticks:
        y = ypos(v)
        c.line(x0, y, x1, y, GRID, 0.7)
        c.text(x0 - 5, y, f"{v:,.0f}" if abs(v) >= 100 else f"{v:g}", 8.2, "#666666", anchor="rm")
    c.line(x0, y0, x0, y1, AXIS, 1.0)
    c.line(x0, y1, x1, y1, AXIS, 1.0)
    if title:
        c.text(x0, 12, title, 10.5, TITLE, anchor="lm", bold=True)
    if ylabel:
        c.text(x0, y0 - 13, ylabel, 8.2, "#555555", anchor="lm")
    n = len(x_labels)
    for i, lab in enumerate(x_labels):
        x = x0 + (i + 0.5) * (x1 - x0) / n
        c.text(x, y1 + 7, lab, 8.4, "#444444", anchor="ma")
    return x0, y0, x1, y1, ypos


def _legend(c, items, x, y, ncol=4, gap=96):
    for i, (label, color) in enumerate(items):
        cx = x + (i % ncol) * gap
        cy = y + (i // ncol) * 15
        c.rect(cx, cy - 3, cx + 9, cy + 5, fill=color)
        c.text(cx + 13, cy + 1, label, 8.4, "#444444", anchor="lm")


# ---------- 单图 ----------
def chart_trend(rows, name="chart_trend"):
    W, H = 760, 330
    c = Canvas(W, H)
    box = (58, 52, 700, 292)
    xs = [r["月份"][-2:] + "月" for r in rows]
    totals = [r["总成本"] / 10000 for r in rows]
    ticks = _ticks(max(totals) * 1.15)
    x0, y0, x1, y1, ypos = _axes(c, box, ticks, xs, "近 6 个月成本趋势", "总成本(万元)")
    n = len(rows)
    bw = (x1 - x0) / n * 0.52
    for i, r in enumerate(rows):
        cx = x0 + (i + 0.5) * (x1 - x0) / n
        c.rect(cx - bw / 2, ypos(r["总成本"] / 10000), cx + bw / 2, y1, fill="#E8EEF6")
    series = [("单位成本", "单位成本"), ("直接材料", "直接材料"), ("直接人工", "直接人工"),
              ("制造费用", "制造费用")]
    vals = [r[k] for r in rows for k, _ in series]
    t2 = _ticks(max(vals) * 1.15)
    lo2, hi2 = t2[0], t2[-1]

    def y2(v):
        return y1 - (v - lo2) / ((hi2 - lo2) or 1) * (y1 - y0)

    # 次坐标轴（单位成本）右侧刻度
    c.line(x1, y0, x1, y1, AXIS, 1.0)
    for t in t2:
        c.text(x1 + 5, y2(t), f"{t:g}", 8.2, "#666666", anchor="lm")
    c.text(x1 + 5, y0 - 12, "单位成本(元/盒)", 8.2, "#555555", anchor="rm")

    for i, (k, _lab) in enumerate(series):
        pts = [(x0 + (j + 0.5) * (x1 - x0) / n, y2(r[k])) for j, r in enumerate(rows)]
        c.poly(pts, PALETTE[i], 1.7)
        for px, py in pts:
            c.dot(px, py, 2.0, PALETTE[i])
    _legend(c, [(lab, PALETTE[i]) for i, (_, lab) in enumerate(series)], x0 + 6, 22, 5, 92)
    return c.save(name)


def chart_structure(struct, unit_cost, name="chart_structure"):
    W, H = 430, 330
    c = Canvas(W, H)
    labels = list(struct.keys())
    vals = [struct[k] for k in labels]
    total = sum(vals) or 1.0
    cx, cy, r, th = 130, 175, 92, 34
    angle = 90.0                     # 数学角：从 12 点方向起，逆时针
    for i, v in enumerate(vals):
        sweep = v / total * 360
        start, end = angle - sweep, angle
        # Pillow 的角度自 3 点方向起、顺时针增大，故取负号换算
        c.ring(cx, cy, r, th, -end, -start, PALETTE[i % len(PALETTE)])
        mid = math.radians((start + end) / 2)
        lx, ly = cx + math.cos(mid) * r, cy - math.sin(mid) * r
        c.text(lx, ly, f"{v/total*100:.1f}%", 8.4, "#FFFFFF", anchor="mm")
        angle = start
    c.text(cx, cy - 6, f"{unit_cost:.2f}", 15, TITLE, anchor="mm", bold=True)
    c.text(cx, cy + 14, "元/盒", 8.6, "#777777", anchor="mm")
    for i, (lab, v) in enumerate(zip(labels, vals)):
        ly = 118 + i * 24
        c.rect(268, ly - 5, 280, ly + 4, fill=PALETTE[i % len(PALETTE)])
        c.text(288, ly, f"{lab} {v:.2f}", 9, "#333333", anchor="lm")
    c.text(40, 16, "单位成本构成", 10.5, TITLE, anchor="lm", bold=True)
    return c.save(name)


def chart_waterfall(items, name="chart_waterfall"):
    W, H = 760, 315
    c = Canvas(W, H)
    labels = [it[0] for it in items]
    kinds = [it[1] for it in items]
    vals = [it[2] for it in items]
    running, bars = 0.0, []
    for lab, kind, v in items:
        if kind == "total":
            bars.append((0.0, v, "#5B7DB1", v, True))
            running = v
        else:
            bottom = running if v >= 0 else running + v
            bars.append((bottom, abs(v), "#C0504D" if v >= 0 else "#4C9A6A", v, False))
            running += v
    top = max(b[0] + b[1] for b in bars) * 1.18
    box = (52, 40, 748, 280)
    ticks = _ticks(top)
    x0, y0, x1, y1, ypos = _axes(c, box, ticks, labels, "", "元/盒")
    n = len(bars)
    bw = (x1 - x0) / n * 0.5
    for i, (bottom, height, color, v, is_total) in enumerate(bars):
        cx = x0 + (i + 0.5) * (x1 - x0) / n
        c.rect(cx - bw / 2, ypos(bottom + height), cx + bw / 2, ypos(bottom), fill=color)
        txt = f"{v:.2f}" if is_total else f"{v:+.2f}"
        c.text(cx, ypos(bottom + height) - 5, txt, 8.2, "#333333", anchor="mb")
    c.text(x0, 16, "单位成本环比变动瀑布（上期 → 本期）", 10.5, TITLE, anchor="lm", bold=True)
    return c.save(name)


def chart_materials(mats, name="chart_material_impact"):
    top = sorted(mats, key=lambda m: -abs(m["变动额_单盒"]))[:8][::-1]
    W, H = 760, max(200, 34 * len(top) + 70)
    c = Canvas(W, H)
    box = (128, 34, 738, H - 34)
    vals = [m["变动额_单盒"] for m in top]
    amax = max(abs(v) for v in vals) * 1.35 if vals else 1
    x0, y0, x1, y1 = box
    mid = (x0 + x1) / 2
    step = (y1 - y0) / max(len(top), 1)
    for v in (-amax, -amax / 2, amax / 2, amax):
        x = mid + v / amax * (x1 - x0) / 2
        c.line(x, y0, x, y1, GRID, 0.7)
        c.text(x, y1 + 5, f"{v:+.2f}", 8, "#666666", anchor="ma")
    c.line(mid, y0, mid, y1, "#999999", 0.9)
    for i, m in enumerate(top):
        cy = y0 + (i + 0.5) * step
        v = m["变动额_单盒"]
        x = mid + v / amax * (x1 - x0) / 2
        c.rect(min(mid, x), cy - step * 0.3, max(mid, x), cy + step * 0.3,
               fill="#C0504D" if v >= 0 else "#4C9A6A")
        c.text(x0 - 6, cy, m["药材"], 8.6, "#333333", anchor="rm")
        c.text(x + (6 if v >= 0 else -6), cy, f"{v:+.3f}", 8.2, "#333333",
               anchor="lm" if v >= 0 else "rm")
    c.text(x0 - 84, 14, "主要原材料对单位成本的影响（元/盒）", 10.5, TITLE, anchor="lm", bold=True)
    return c.save(name)


def chart_prices(price_series, name="chart_price_trend"):
    W, H = 760, 315
    c = Canvas(W, H)
    xs = [f"{i}月" for i in range(1, 7)]
    idx = {}
    for mat, series in price_series.items():
        base = series[0] or 1
        idx[mat] = [s / base * 100 for s in series]
    allv = [v for s in idx.values() for v in s]
    box = (58, 46, 748, 280)
    ticks = _ticks(max(allv + [105]) * 1.02)
    x0, y0, x1, y1, ypos = _axes(c, box, ticks, xs, "关键原材料市场价格走势", "价格指数(1月=100)")
    c.line(x0, ypos(100), x1, ypos(100), "#AAAAAA", 0.9, dash=8)
    n = len(xs)
    for i, (mat, series) in enumerate(idx.items()):
        pts = [(x0 + (j + 0.5) * (x1 - x0) / n, ypos(v)) for j, v in enumerate(series)]
        c.poly(pts, PALETTE[i % len(PALETTE)], 1.7)
        for px, py in pts:
            c.dot(px, py, 2.0, PALETTE[i % len(PALETTE)])
    _legend(c, [(m, PALETTE[i % len(PALETTE)]) for i, m in enumerate(idx)], x0 + 6, 22, 4, 132)
    return c.save(name)


def chart_benchmark(bench, name="chart_benchmark"):
    c = Canvas(760, 315)
    groups = ["单位成本", "直接材料", "直接人工", "制造费用"]
    f1 = [bench["一厂"][g] for g in groups]
    f2 = [bench["二厂"][g] for g in groups]
    box = (58, 46, 748, 280)
    ticks = _ticks(max(f1 + f2) * 1.18)
    x0, y0, x1, y1, ypos = _axes(c, box, ticks, groups, f"两厂成本对标（{bench['产品']}）", "元/盒")
    n = len(groups)
    gw = (x1 - x0) / n
    bw = gw * 0.3
    for i in range(n):
        cx = x0 + (i + 0.5) * gw
        for k, (vals, color) in enumerate([(f1, PALETTE[0]), (f2, PALETTE[1])]):
            bx = cx - bw + k * bw
            c.rect(bx, ypos(vals[i]), bx + bw * 0.92, y1, fill=color)
            c.text(bx + bw * 0.46, ypos(vals[i]) - 5, f"{vals[i]:.2f}", 7.8, "#333333", anchor="mb")
    _legend(c, [("中药一厂", PALETTE[0]), ("中药二厂", PALETTE[1])], x0 + 6, 22, 2, 104)
    return c.save(name)


def chart_budget(cmp_rows, name="chart_budget_compare"):
    c = Canvas(560, 300)
    labels = [r[0] for r in cmp_rows]
    vals = [r[1] or 0 for r in cmp_rows]
    box = (58, 40, 548, 264)
    ticks = _ticks(max(vals) * 1.18 if any(vals) else 1)
    x0, y0, x1, y1, ypos = _axes(c, box, ticks, labels, "单位成本对比", "元/盒")
    n = len(labels)
    bw = (x1 - x0) / n * 0.45
    colors = [PALETTE[0], "#9DB8D6", "#B9C9DB", PALETTE[1]]
    for i, v in enumerate(vals):
        cx = x0 + (i + 0.5) * (x1 - x0) / n
        c.rect(cx - bw / 2, ypos(v), cx + bw / 2, y1, fill=colors[i % len(colors)])
        c.text(cx, ypos(v) - 5, f"{v:.2f}", 8.4, "#333333", anchor="mb")
    return c.save(name)


CAPTIONS = {}   # 与 charts.py 共用说明文字，此处不再重复定义


def build_all(data):
    """与 matplotlib 引擎同签名的批量构建入口"""
    if not available():
        return {}
    out = {}
    jobs = [
        ("trend", chart_trend, (data.get("trend") or [],)),
        ("structure", chart_structure, (data.get("structure") or {}, data.get("单位成本") or 0)),
        ("waterfall", chart_waterfall, (data.get("waterfall") or [],)),
        ("material_impact", chart_materials, (data.get("materials") or [],)),
        ("price_trend", chart_prices, (data.get("prices") or {},)),
        ("benchmark", chart_benchmark, (data.get("benchmark") or {},)),
        ("budget_compare", chart_budget, (data.get("budget") or [],)),
    ]
    for key, fn, args in jobs:
        if key == "trend" and not args[0]:
            continue
        if key in ("structure", "waterfall", "benchmark", "budget") and not args[0]:
            continue
        try:
            out[key] = fn(*args, name=key)     # 与其它引擎统一文件名：<key>.png
        except Exception as e:
            log.warning("生成图表 %s 失败：%s", key, e)
    return out
