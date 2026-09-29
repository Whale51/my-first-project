# -*- coding: utf-8 -*-
"""报告图表服务端渲染（matplotlib → PNG）

赛题要求报告需"图表嵌入"。本模块把看板同源的 ECharts 图表在服务端用 matplotlib
重绘为高分辨率 PNG，供 Word 文档（python-docx 插图）与 PDF（reportlab 插图）共用。

设计要点：
* 中文字体自动探测（Windows: 微软雅黑/黑体/宋体；Linux: Noto CJK；macOS: PingFang/Hiragino）；
* matplotlib 未安装时自动降级：返回空字典，报告仍可生成（仅缺图）；
* 所有图表风格统一（配色、网格、去顶右边框），保证嵌入报告后排版专业。
"""
import os

from .logging_conf import get_logger
from .paths import OUTPUT_DIR

log = get_logger(__name__)

CHART_DIR = os.path.join(OUTPUT_DIR, "charts")
os.makedirs(CHART_DIR, exist_ok=True)

PALETTE = ["#2E6FB7", "#E08A2E", "#4C9A6A", "#B4524C", "#7C6BB0", "#5B9BD5", "#C9A227"]
GRID = {"color": "#DDDDDD", "linewidth": 0.6, "alpha": 0.8}
FONT_FILE = None
_FONT_READY = False

_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyh.ttf", r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\Deng.ttf", r"C:\Windows\Fonts\simsun.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
]


def available():
    """是否有可用的图表引擎（matplotlib 优先，Pillow 兜底）"""
    return engine() is not None


def engine():
    """返回当前可用的图表引擎名：'matplotlib' | 'pillow' | 'gdi' | None"""
    try:
        import matplotlib  # noqa: F401
        return "matplotlib"
    except Exception:
        pass
    try:
        from . import charts_pillow
        if charts_pillow.available():
            return "pillow"
    except Exception:
        pass
    try:
        from . import charts_gdi
        if charts_gdi.available():
            return "gdi"
    except Exception:
        pass
    return None


def _setup_font():
    """注册中文字体，避免图表出现方块字"""
    global FONT_FILE, _FONT_READY
    if _FONT_READY:
        return
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import font_manager as fm
    from matplotlib import rcParams

    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                fm.fontManager.addfont(path)
                FONT_FILE = path
                break
            except Exception:
                continue
    families = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "WenQuanYi Zen Hei",
                "PingFang SC", "Hiragino Sans GB", "SimSun", "DejaVu Sans"]
    if FONT_FILE:
        try:
            families.insert(0, fm.FontProperties(fname=FONT_FILE).get_name())
        except Exception:
            pass
    rcParams["font.sans-serif"] = families
    rcParams["font.family"] = "sans-serif"
    rcParams["axes.unicode_minus"] = False
    rcParams["figure.autolayout"] = False
    rcParams["savefig.facecolor"] = "white"
    _FONT_READY = True


def _new(w=7.2, h=3.3):
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(w, h), dpi=170)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#BBBBBB")
    ax.tick_params(labelsize=8.5, colors="#444444")
    ax.set_title("")
    return fig, ax


def _save(fig, name):
    path = os.path.join(CHART_DIR, f"{name}.png")
    tmp = f"{path}.tmp"          # 先写临时文件再原子替换，避免读到"半截"PNG
    fig.tight_layout(pad=0.6)
    fig.savefig(tmp, bbox_inches="tight", facecolor="white")
    import matplotlib.pyplot as plt
    plt.close(fig)
    os.replace(tmp, path)
    return path


def _fig_title(ax, text):
    ax.set_title(text, fontsize=10.5, color="#1F4E79", pad=8, loc="left")


# ---------------- 单图 ----------------
def chart_trend(rows, name="chart_trend"):
    """近 6 个月：单位成本构成折线 + 总成本柱（双轴）"""
    fig, ax = _new(7.2, 3.4)
    xs = [r["月份"][-2:] + "月" for r in rows]
    ax.bar(xs, [r["总成本"] / 10000 for r in rows], color="#E8EEF6", width=0.55,
           label="总成本(万元)", zorder=1)
    ax.set_ylabel("总成本（万元）", fontsize=8.5, color="#5B7DB1")
    ax.grid(axis="y", **GRID)
    ax.set_axisbelow(True)
    ax2 = ax.twinx()
    for i, (key, label) in enumerate([("单位成本", "单位成本"), ("直接材料", "直接材料"),
                                      ("直接人工", "直接人工"), ("制造费用", "制造费用")]):
        ax2.plot(xs, [r[key] for r in rows], marker="o", markersize=3.4, linewidth=1.7,
                 color=PALETTE[i], label=label, zorder=3)
    ax2.set_ylabel("单位成本（元/盒）", fontsize=8.5, color="#444444")
    ax2.tick_params(labelsize=8.5, colors="#444444")
    for side in ("top",):
        ax2.spines[side].set_visible(False)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=8, frameon=False, ncol=5, loc="upper center",
              bbox_to_anchor=(0.5, 1.14))
    _fig_title(ax, "")
    ax.set_title("近 6 个月成本趋势", fontsize=10.5, color="#1F4E79", pad=22, loc="left")
    return _save(fig, name)


def chart_structure(struct, unit_cost, name="chart_structure"):
    """本月单位成本构成（环形图）"""
    fig, ax = _new(4.0, 3.3)
    labels = list(struct.keys())
    vals = [struct[k] for k in labels]
    wedges, _, autotexts = ax.pie(vals, labels=None, autopct="%1.1f%%", startangle=100,
                                  colors=PALETTE[:len(vals)], pctdistance=0.78,
                                  wedgeprops={"width": 0.42, "edgecolor": "white", "linewidth": 1.2},
                                  textprops={"fontsize": 8.5, "color": "#333333"})
    ax.text(0, 0.08, f"{unit_cost:.2f}", ha="center", va="center", fontsize=13,
            color="#1F4E79", fontweight="bold")
    ax.text(0, -0.22, "元/盒", ha="center", va="center", fontsize=8.5, color="#777777")
    ax.legend(wedges, [f"{k} {v:.2f}" for k, v in zip(labels, vals)], fontsize=8.2,
              frameon=False, loc="center left", bbox_to_anchor=(0.92, 0.5))
    ax.set_title("单位成本构成", fontsize=10.5, color="#1F4E79", pad=6)
    return _save(fig, name)


def chart_waterfall(items, name="chart_waterfall"):
    """要素变动瀑布图：上月单位成本 → 各要素贡献 → 本月单位成本"""
    fig, ax = _new(7.2, 3.2)
    labels = [it[0] for it in items]
    kinds = [it[1] for it in items]          # 'total' | 'delta'
    vals = [it[2] for it in items]
    bottoms, heights, colors = [], [], []
    running = 0.0
    for lab, kind, v in items:
        if kind == "total":
            bottoms.append(0.0)
            heights.append(v)
            colors.append("#5B7DB1")
            running = v
        else:
            bottoms.append(running if v >= 0 else running + v)
            heights.append(abs(v))
            colors.append("#C0504D" if v >= 0 else "#4C9A6A")
            running += v
    ax.bar(labels, heights, bottom=bottoms, color=colors, width=0.5)
    for i, (b, h) in enumerate(zip(bottoms, heights)):
        txt = f"{vals[i]:.2f}" if kinds[i] == "total" else f"{vals[i]:+.2f}"
        ax.text(i, b + h + max(heights) * 0.02, txt, ha="center", fontsize=8.2, color="#333333")
    ax.grid(axis="y", **GRID)
    ax.set_axisbelow(True)
    ax.set_ylabel("元/盒", fontsize=8.5)
    ax.set_ylim(0, max(b + h for b, h in zip(bottoms, heights)) * 1.18)
    ax.tick_params(axis="x", labelsize=8.2)
    ax.set_title("单位成本环比变动瀑布（上月 → 本月）", fontsize=10.5, color="#1F4E79", pad=8, loc="left")
    return _save(fig, name)


def chart_materials(mats, name="chart_material_impact"):
    """主要原材料对单位成本的影响（横向条形）"""
    top = sorted(mats, key=lambda m: -abs(m["变动额_单盒"]))[:8][::-1]
    fig, ax = _new(7.2, max(2.6, 0.42 * len(top) + 1.1))
    names = [m["药材"] for m in top]
    vals = [m["变动额_单盒"] for m in top]
    ax.barh(names, vals, color=["#C0504D" if v >= 0 else "#4C9A6A" for v in vals], height=0.55)
    for i, v in enumerate(vals):
        ax.text(v + (0.01 if v >= 0 else -0.01), i, f"{v:+.3f}", va="center",
                ha="left" if v >= 0 else "right", fontsize=8.2, color="#333333")
    ax.axvline(0, color="#999999", linewidth=0.8)
    ax.grid(axis="x", **GRID)
    ax.set_axisbelow(True)
    ax.set_xlabel("对单位成本影响（元/盒）", fontsize=8.5)
    ax.tick_params(axis="y", labelsize=8.5)
    pad = max(abs(v) for v in vals) * 0.25 if vals else 0.1
    ax.set_xlim(min(vals + [0]) - pad, max(vals + [0]) + pad)
    ax.set_title("主要原材料对单位成本的影响", fontsize=10.5, color="#1F4E79", pad=8, loc="left")
    return _save(fig, name)


def chart_prices(price_series, name="chart_price_trend"):
    """关键药材价格指数走势（以 1 月 = 100）"""
    fig, ax = _new(7.2, 3.2)
    xs = [f"{i}月" for i in range(1, 7)]
    for i, (mat, series) in enumerate(price_series.items()):
        base = series[0] or 1
        ax.plot(xs, [s / base * 100 for s in series], marker="o", markersize=3.4, linewidth=1.7,
                color=PALETTE[i % len(PALETTE)], label=mat)
    ax.axhline(100, color="#AAAAAA", linewidth=0.8, linestyle="--")
    ax.grid(axis="y", **GRID)
    ax.set_axisbelow(True)
    ax.set_ylabel("价格指数（1月=100）", fontsize=8.5)
    ax.legend(fontsize=8, frameon=False, ncol=4, loc="upper left")
    ax.set_title("关键原材料市场价格走势", fontsize=10.5, color="#1F4E79", pad=8, loc="left")
    return _save(fig, name)


def chart_benchmark(bench, name="chart_benchmark"):
    """一厂 vs 二厂 单位成本与三要素对比（分组柱状）"""
    fig, ax = _new(7.2, 3.2)
    groups = ["单位成本", "直接材料", "直接人工", "制造费用"]
    f1 = [bench["一厂"][g] for g in groups]
    f2 = [bench["二厂"][g] for g in groups]
    x = range(len(groups))
    w = 0.36
    b1 = ax.bar([i - w / 2 for i in x], f1, width=w, color=PALETTE[0], label="中药一厂")
    b2 = ax.bar([i + w / 2 for i in x], f2, width=w, color=PALETTE[1], label="中药二厂")
    for bars in (b1, b2):
        for b in bars:
            ax.text(b.get_x() + b.get_width() / 2, b.get_height() + max(f1 + f2) * 0.015,
                    f"{b.get_height():.2f}", ha="center", fontsize=7.8, color="#333333")
    ax.set_xticks(list(x))
    ax.set_xticklabels(groups, fontsize=8.5)
    ax.grid(axis="y", **GRID)
    ax.set_axisbelow(True)
    ax.set_ylabel("元/盒", fontsize=8.5)
    ax.legend(fontsize=8, frameon=False, loc="upper right")
    ax.set_ylim(0, max(f1 + f2) * 1.18)
    ax.set_title(f"两厂成本对标（{bench['产品']}）", fontsize=10.5, color="#1F4E79", pad=8, loc="left")
    return _save(fig, name)


def chart_budget(cmp_rows, name="chart_budget_compare"):
    """本月 / 上月 / 去年同期 / 预算 单位成本对比"""
    fig, ax = _new(5.4, 3.1)
    labels = [r[0] for r in cmp_rows]
    vals = [r[1] or 0 for r in cmp_rows]
    bars = ax.bar(labels, vals, width=0.5,
                  color=[PALETTE[0], "#9DB8D6", "#B9C9DB", PALETTE[1]])
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + max(vals) * 0.02, f"{v:.2f}",
                ha="center", fontsize=8.4, color="#333333")
    ax.grid(axis="y", **GRID)
    ax.set_axisbelow(True)
    ax.set_ylabel("元/盒", fontsize=8.5)
    ax.set_ylim(0, max(vals) * 1.18 if any(vals) else 1)
    ax.set_title("单位成本对比（实际 vs 上月/去年同期/预算）", fontsize=10, color="#1F4E79",
                 pad=8, loc="left")
    return _save(fig, name)


# ---------------- 批量构建 ----------------
def build_all(data):
    """按报告数据一次性生成全部图表，返回 {key: png_path}

    引擎优先级：matplotlib → Pillow → Windows GDI+（零依赖兜底）；
    全部不可用时返回 {}（报告仍可生成，仅缺图，不抛异常）。
    """
    eng = engine()
    if eng is None:
        return {}
    if eng == "pillow":
        from . import charts_pillow
        return charts_pillow.build_all(data)
    if eng == "gdi":
        from . import charts_gdi
        return charts_gdi.build_all(data)
    try:
        _setup_font()
    except Exception:
        from . import charts_pillow
        if charts_pillow.available():
            return charts_pillow.build_all(data)
        from . import charts_gdi
        return charts_gdi.build_all(data) if charts_gdi.available() else {}
    out = {}
    _clean_stale()
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
            out[key] = fn(*args, name=key)      # 统一文件名：<key>.png
        except Exception as e:  # 单张图失败不影响整体报告
            log.warning("生成图表 %s 失败：%s", key, e)
    return out


def _clean_stale():
    """清理上一次生成的图表 PNG，避免残留文件与不同引擎命名不一致造成误读"""
    try:
        for f in os.listdir(CHART_DIR):
            if f.endswith(".png"):
                os.remove(os.path.join(CHART_DIR, f))
    except OSError:
        pass


CAPTIONS = {
    "trend": "图 1  近 6 个月成本趋势（总成本柱 + 单位成本构成折线）",
    "structure": "图 2  本期单位成本构成",
    "waterfall": "图 3  单位成本环比变动瀑布图",
    "material_impact": "图 4  主要原材料对单位成本的影响",
    "price_trend": "图 5  关键原材料市场价格走势（1 月 = 100）",
    "benchmark": "图 6  一厂 / 二厂成本对标",
    "budget_compare": "图 7  单位成本：实际 vs 上期 / 去年同期 / 预算",
}
