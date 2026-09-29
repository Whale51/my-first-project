# -*- coding: utf-8 -*-
"""成本预测模块（加分项）：多模型回测对比 + 自动选优预测月度单位成本。

方法（四个模型并跑，各自回测）：
    1. 线性回归      —— np.polyfit 拟合一次趋势；
    2. 移动平均      —— 最近 3 个月算术平均（水平预测）；
    3. Holt线性趋势  —— 指数平滑（水平 α + 趋势 β），α、β 在 [0.1, 0.9] 网格上
                        按样本内一步预测误差（MSE）网格选优；
    4. ARIMA(1,1,0)  —— 一阶差分上拟合 AR(1)：优先用 statsmodels.tsa.arima.model.ARIMA，
                        不可用时降级为 numpy 最小二乘估计，并在“说明”中标注来源。

回测口径（留出法 / hold-out）：
    用序列前 n-2 个点拟合、预测最后 2 个点，逐点计算绝对百分比误差后取均值得到 MAPE
    （分母用 max(|实际值|, 1e-9) 保护除零）。样本不足（< 4 个月）时跳过该产品建模，
    并在“模型对比”中给出说明。MAPE 越小精度越高，0 表示完美拟合。

集成预测：权重 = 1/MAPE 归一化（MAPE 为 0 时用 1e-6 极小值保护，无有效回测的模型权重为 0），
按权重加权得到“预测单位成本”；同时输出“最优模型”（回测 MAPE 最小者）及其纯预测值，
便于前端对比“集成 vs 单模型”。

健壮性：只依赖 numpy + 标准库（pandas 经由 data_service 间接使用）；
statsmodels / sklearn 均为可选导入，缺失时优雅降级；全流程不产生 NaN/inf
（除零、序列过短、常数序列均有保护）；任何单模型异常只记入“说明”，不影响其它模型与整体结果；
返回值全部为 Python 原生类型（float/str/list/dict），可被 json 直接序列化。
"""
import math

import numpy as np

from .data_service import ds

# ---------- 常量 ----------
MODELS = ["线性回归", "移动平均", "Holt线性趋势", "ARIMA(1,1,0)"]
MA_WINDOW = 3            # 移动平均窗口（月）
HOLT_GRID = [round(0.1 * i, 1) for i in range(1, 10)]  # α、β 网格：[0.1, 0.2, ..., 0.9]
EPS_MAPE = 1e-6          # MAPE 为 0 时的权重保护极小值
EPS_DENOM = 1e-9         # 百分比误差分母保护
EPS_DIFF = 1e-12         # 常数序列差分判定阈值
MAX_MAPE = 10.0          # MAPE 上限（1000%），超过视为回测不可靠
REL_TOL = 1e-9           # 数值比较相对容差


def _f(v, default=0.0):
    """安全转 float：NaN/inf/非法值一律回落到 default，保证不污染下游计算。"""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(x):
        return default
    return x


def _json_num(v, ndigits=None):
    """转成 JSON 可序列化的 Python float（可选四舍五入）。"""
    x = _f(v)
    return round(x, ndigits) if ndigits is not None else x


def _finite_list(arr):
    """把序列转成有限 float 列表（NaN/inf 剔除），供各模型安全使用。"""
    out = []
    for v in arr:
        x = _f(v, float("nan"))
        if math.isfinite(x):
            out.append(x)
    return out


def _safe_mape(actual, pred):
    """逐点绝对百分比误差取均值；点数为 0 或超出上限返回 None（回测不可靠）。"""
    if not actual or len(actual) != len(pred):
        return None
    errs = []
    for a, p in zip(actual, pred):
        a, p = _f(a, float("nan")), _f(p, float("nan"))
        if not math.isfinite(a) or not math.isfinite(p):
            return None
        errs.append(abs(p - a) / max(abs(a), EPS_DENOM))
    if not errs:
        return None
    m = float(np.mean(errs))
    if not math.isfinite(m) or m > MAX_MAPE:
        return None
    return m


def _clip_preds(preds, ys):
    """数值兜底：把发散/非有限的预测裁剪到历史取值域的合理邻域内。

    规则：以历史 [min, max] 为基准，向两侧各放宽一个历史波动幅度；
    同时限制单步变动不超过 2 倍历史最大单步变动，避免短序列外推出离谱数值。
    """
    lo, hi = min(ys), max(ys)
    span = hi - lo
    if span <= 0:
        span = max(abs(hi), 1.0) * 0.05
    floor, ceil = lo - span, hi + span
    diffs = [abs(ys[i + 1] - ys[i]) for i in range(len(ys) - 1)]
    max_step = max(diffs) if diffs else span
    out, prev = [], ys[-1]
    for p in preds:
        p = _f(p, prev)
        p = min(max(p, floor), ceil)
        if abs(p - prev) > 2 * max_step:
            p = prev + math.copysign(2 * max_step, p - prev)
        out.append(_f(p, prev))
        prev = p
    return out


# ---------- 数据准备 ----------
def _series(product):
    """返回 (xs, ys, ms)：xs 为等距时间序号，ys 为单位成本，ms 为对应月份（缺月自动跳过）。"""
    months = ds.months_list()
    xs, ys, ms = [], [], []
    for i, m in enumerate(months):
        r = ds.row(m, product)
        if r and r.get("单位成本") is not None:
            v = _f(r["单位成本"], float("nan"))
            if math.isfinite(v):
                xs.append(i)
                ys.append(v)
                ms.append(m)
    return xs, ys, ms


def _future_months(steps):
    """基于历史最后一个月份生成未来月份标签。"""
    months = ds.months_list()
    if not months:
        return []
    last = months[-1]
    y, m = int(last[:4]), int(last[5:7])
    out = []
    for s in range(1, steps + 1):
        mm = m + s
        yy = y + (mm - 1) // 12
        out.append("%d-%02d" % (yy, (mm - 1) % 12 + 1))
    return out


# ---------- 单模型实现（统一接口）----------
# 每个模型提供：fit(xs, ys) / insample(xs, ys)（样本内一步预测，用于回测）/
#               forecast(xs, ys, steps)（外推预测） / note（文字说明）

class _LinReg:
    """线性回归：np.polyfit 一次趋势 y = k*x + b。"""

    name = "线性回归"

    def __init__(self):
        self.k = self.b = None
        self.note = "np.polyfit 一次趋势拟合"

    def fit(self, xs, ys):
        if len(xs) < 2:
            self.note = "样本不足 2 点，跳过线性回归"
            return False
        # 常数 x 会导致 polyfit 病态，退化用均值
        if max(xs) - min(xs) <= EPS_DIFF:
            self.k, self.b = 0.0, float(np.mean(ys))
            self.note = "时间序号退化，改用均值预测"
            return True
        with np.errstate(all="ignore"):
            k, b = np.polyfit(np.array(xs, dtype=float), np.array(ys, dtype=float), 1)
        self.k, self.b = _f(k), _f(b)
        if abs(self.k) < EPS_DIFF:
            self.k = 0.0
        self.note = "np.polyfit 一次趋势拟合，斜率=%.4f" % self.k
        return True

    def _at(self, x):
        return self.k * x + self.b

    def insample(self, xs, ys):
        return [self._at(x) for x in xs]

    def forecast(self, xs, ys, steps):
        base = xs[-1]
        return [self._at(base + s) for s in range(1, steps + 1)]


class _MovingAverage:
    """移动平均：最近 MA_WINDOW 个月的算术平均。"""

    name = "移动平均"

    def __init__(self, window=MA_WINDOW):
        self.window = window
        self.level = None
        self.note = "%d 个月移动平均" % window

    def fit(self, xs, ys):
        if len(ys) < self.window:
            self.note = "样本不足 %d 个月，跳过移动平均" % self.window
            return False
        with np.errstate(all="ignore"):
            self.level = _f(np.mean(np.array(ys[-self.window:], dtype=float)))
        self.note = "最近 %d 个月均值=%.4f（水平预测）" % (self.window, self.level)
        return True

    def insample(self, xs, ys):
        # 历史各点用其前 window 个点的均值（不足则用已有均值）作为一步预测
        out = []
        for i in range(len(ys)):
            hist = ys[max(0, i - self.window):i]
            out.append(_f(np.mean(hist), ys[i]) if hist else ys[i])
        return out

    def forecast(self, xs, ys, steps):
        return [self.level] * steps


class _Holt:
    """Holt 线性趋势：指数平滑水平+趋势，α、β 在网格上按样本内一步误差选优。"""

    name = "Holt线性趋势"

    def __init__(self, grid=None):
        self.grid = grid or HOLT_GRID
        self.alpha = self.beta = None
        self.level = self.trend = None
        self.best_mse = None
        self.note = "指数平滑水平+趋势"

    @staticmethod
    def _init(ys):
        """初始水平取首点，初始趋势取首两步差分（不足则 0）。"""
        level = ys[0]
        trend = (ys[1] - ys[0]) if len(ys) >= 2 else 0.0
        return level, trend

    def _run(self, ys, alpha, beta):
        """返回 (一步预测序列, 末水平, 末趋势)。预测序列从第 2 点开始才有意义。"""
        level, trend = self._init(ys)
        preds = []
        for i, y in enumerate(ys):
            f = level + trend
            preds.append(f)
            prev_level = level
            level = alpha * y + (1 - alpha) * (level + trend)
            trend = beta * (level - prev_level) + (1 - beta) * trend
        for name, v in (("level", level), ("trend", trend)):
            if not math.isfinite(_f(v, float("nan"))):
                level = _f(level, ys[-1])
                trend = 0.0
                break
        return preds, level, trend

    def fit(self, xs, ys):
        if len(ys) < 3:
            self.note = "样本不足 3 点，跳过 Holt 指数平滑"
            return False
        best = None
        with np.errstate(all="ignore"):
            for a in self.grid:
                for b in self.grid:
                    preds, lv, tr = self._run(ys, a, b)
                    # 第 1 个预测即初始水平，不参与误差统计
                    errs = [(preds[i] - ys[i]) ** 2 for i in range(1, len(ys))]
                    mse = float(np.mean(errs)) if errs else float("inf")
                    if not math.isfinite(mse):
                        continue
                    if best is None or mse < best[0]:
                        best = (mse, a, b, lv, tr)
        if best is None:
            self.note = "Holt 网格搜索未找到有限解，回退为水平预测"
            self.alpha, self.beta = 0.5, 0.1
            self.level, self.trend = ys[-1], 0.0
            self.best_mse = None
            return True
        self.best_mse, self.alpha, self.beta, self.level, self.trend = best
        self.note = "α=%.1f, β=%.1f 网格选优（样本内一步 MSE=%.6f）" % (self.alpha, self.beta, self.best_mse)
        return True

    def insample(self, xs, ys):
        preds, _, _ = self._run(ys, self.alpha, self.beta)
        return preds

    def forecast(self, xs, ys, steps):
        return [self.level + (s + 1) * self.trend for s in range(steps)]


class _ARIMA110:
    """ARIMA(1,1,0)：一阶差分上拟合 AR(1)，STATSMODELS 可用时优先调用。"""

    name = "ARIMA(1,1,0)"

    def __init__(self):
        self.phi = None
        self.const = 0.0
        self.last = None
        self.backend = "numpy最小二乘（statsmodels 不可用，优雅降级）"
        self.note = self.backend

    def fit(self, xs, ys):
        if len(ys) < 3:
            self.note = "样本不足 3 点，跳过 ARIMA(1,1,0)"
            return False
        self.last = ys[-1]
        statsmodels = None
        try:  # 可选依赖：仅在真正安装时才使用
            from statsmodels.tsa.arima.model import ARIMA as _SM_ARIMA  # noqa: N813
            statsmodels = _SM_ARIMA
        except Exception:
            statsmodels = None
        if statsmodels is not None:
            try:
                with np.errstate(all="ignore"):
                    res = statsmodels(np.array(ys, dtype=float), order=(1, 1, 0)).fit()
                    phi = _f(res.params[1] if len(res.params) > 1 else 0.0)
                    const = _f(res.params[0]) if len(res.params) > 1 else 0.0
                    fc = res.forecast(steps=2)
                if math.isfinite(phi) and abs(phi) < 10:
                    self.phi, self.const = phi, const
                    self.backend = "statsmodels.tsa.arima.model.ARIMA(1,1,0)"
                    self.note = self.backend + "，φ=%.4f" % phi
                    if fc is not None and len(fc) >= 1:
                        self._fc_probe = _f(fc[0])
                    return True
            except Exception as e:  # 估计失败则降级
                self.note = "statsmodels 估计失败(%s)，降级 numpy 最小二乘" % type(e).__name__
        # 降级实现：Δy_t = c + φ·Δy_{t-1} + ε，OLS 闭式解
        d = [ys[i + 1] - ys[i] for i in range(len(ys) - 1)]
        if max(d) - min(d) <= EPS_DIFF:  # 常数增量序列：AR 系数无意义
            self.phi, self.const = 0.0, float(np.mean(d)) if d else 0.0
            self.backend = "numpy最小二乘（差分近常数，退化为随机游走+漂移）"
            self.note = self.backend
            return True
        y_lag = np.array(d[:-1], dtype=float)
        y_cur = np.array(d[1:], dtype=float)
        try:
            with np.errstate(all="ignore"):
                a = np.vstack([np.ones_like(y_lag), y_lag]).T
                sol, *_ = np.linalg.lstsq(a, y_cur, rcond=None)
            const, phi = _f(sol[0]), _f(sol[1])
        except Exception:
            const, phi = float(np.mean(d)), 0.0
        # 稳定性保护：|φ| >= 1 时预测会发散，退回随机游走
        if not math.isfinite(phi) or abs(phi) >= 1:
            phi, const = 0.0, float(np.mean(d)) if d else 0.0
            self.backend = "numpy最小二乘（φ 越界，退化为随机游走+漂移）"
        self.phi, self.const = phi, const
        self.note = self.backend + "，φ=%.4f, c=%.4f" % (phi, const)
        return True

    def insample(self, xs, ys):
        """样本内一步预测：用 Δy_t = c + φ·Δy_{t-1} 还原水平（第 1 点用原值）。"""
        out = [ys[0]]
        for i in range(1, len(ys)):
            d_prev = ys[i] - ys[i - 1]
            out.append(out[-1] + self.const + self.phi * d_prev)
        return out

    def forecast(self, xs, ys, steps):
        d = ys[-1] - ys[-2] if len(ys) >= 2 else 0.0
        out, cur = [], ys[-1]
        for _ in range(steps):
            d = self.const + self.phi * d
            cur = cur + d
            out.append(cur)
        return out


def _build_models():
    """每件预测都新建模型实例，避免状态串味。"""
    return [_LinReg(), _MovingAverage(), _Holt(), _ARIMA110()]


# ---------- 回测 ----------
def _backtest(inst, xs, ys):
    """留出法回测：前 n-2 点拟合，预测最后 2 点，返回 (MAPE 或 None)。"""
    if len(xs) < 4:
        return None, "样本不足 4 个月，未做回测"
    tx, ty = xs[:-2], ys[:-2]
    try:
        if not inst.fit(tx, ty):
            return None, inst.note
        with np.errstate(all="ignore"):
            preds = list(inst.forecast(tx, ty, 2))
        mape = _safe_mape(ys[-2:], preds)
    except Exception as e:  # 单模型回测异常不影响其它模型
        return None, "回测异常(%s)：%s" % (type(e).__name__, e)
    if mape is None:
        return None, (inst.note + "；回测误差不可用（超出容差上限或数值异常）")
    return mape, inst.note


def _weights_from_mape(mape_map, digits=4):
    """权重 = 1/MAPE 归一化；MAPE 为 0 用极小值保护；无有效回测的模型权重为 0。"""
    valid = {k: v for k, v in mape_map.items() if v is not None and math.isfinite(v)}
    if not valid:
        return {k: 0.0 for k in MODELS}
    inv = {k: 1.0 / max(v, EPS_MAPE) for k, v in valid.items()}
    total = sum(inv.values())
    w = {k: (inv[k] / total if k in inv and total > 0 else 0.0) for k in MODELS}
    return {k: _json_num(v, digits) for k, v in w.items()}


def _pct(x):
    """MAPE 小数转百分比字符串。"""
    return "%.2f%%" % (x * 100) if x is not None else "—"


# ---------- 主入口 ----------
def forecast(product=None, steps=3):
    """对指定产品（或全部产品）预测未来 steps 个月单位成本。

    :param product: 产品名称；None 表示全部产品
    :param steps: 预测月数（正整数，越界自动纠正到 [1, 12]）
    :return: {"months": [...], "items": [ {...}, ... ]}
    """
    try:
        steps = int(steps)
    except (TypeError, ValueError):
        steps = 3
    steps = min(max(steps, 1), 12)

    products = [product] if product else ds.products_list()
    future = _future_months(steps)
    items = []

    for p in products:
        xs, ys, ms = _series(p)
        # 样本不足：跳过建模，但保留说明（键名与正常项一致，前端不会取空）
        if len(ys) < 4:
            items.append({
                "产品": p,
                "历史序列": [{"月份": m_, "单位成本": _json_num(v, 3)} for m_, v in zip(ms, ys)],
                "预测月份": future,
                "预测单位成本": [],
                "模型权重": {k: 0.0 for k in MODELS},
                "回测MAPE": {k: None for k in MODELS},
                "预测趋势": "持平",
                "最优模型": None,
                "模型对比": [{"模型": k, "回测MAPE": None, "MAPE百分比": "—", "下月预测": None,
                              "权重": 0.0, "说明": "历史数据不足 4 个月（当前 %d 个月），跳过回测与预测" % len(ys)}
                             for k in MODELS],
            })
            continue

        # 1) 回测：每个模型各自留出最后 2 点
        models = _build_models()
        mape_map, notes = {}, {}
        for inst in models:
            mape, note = _backtest(inst, xs, ys)
            mape_map[inst.name] = mape
            notes[inst.name] = "回测(前%d点)：%s" % (len(ys) - 2, note)

        # 2) 权重：1/MAPE 归一化（MAPE 为 0 用极小值保护）
        weights = _weights_from_mape(mape_map)

        # 3) 全量拟合 + 集成预测
        preds_by_model, preds_ok = {}, {}
        for inst in models:
            try:
                if not inst.fit(xs, ys):
                    preds_ok[inst.name] = False
                    continue
                with np.errstate(all="ignore"):
                    raw = list(inst.forecast(xs, ys, steps))
                if len(raw) != steps:
                    raise ValueError("预测长度异常")
                preds_by_model[inst.name] = _clip_preds(raw, ys)
                preds_ok[inst.name] = True
                notes[inst.name] = notes[inst.name] + "；全量拟合(后%d点)：%s" % (steps, inst.note)
            except Exception as e:  # 单模型异常不影响整体
                preds_ok[inst.name] = False
                notes[inst.name] = "预测异常(%s)：%s" % (type(e).__name__, e)

        usable = [k for k in MODELS if preds_ok.get(k) and k in preds_by_model]
        ensemble = []
        if usable:
            wsum = sum(weights.get(k, 0.0) for k in usable)
            for s in range(steps):
                if wsum > 0:
                    v = sum(weights.get(k, 0.0) * preds_by_model[k][s] for k in usable) / wsum
                else:  # 权重全为 0（回测全不可用）：等权平均
                    v = sum(preds_by_model[k][s] for k in usable) / len(usable)
                ensemble.append(_json_num(v, 3))
            ensemble = _clip_preds(ensemble, ys)
            ensemble = [_json_num(v, 3) for v in ensemble]
        else:  # 所有模型都失败：退化为最后一个月水平
            ensemble = [_json_num(ys[-1], 3)] * steps

        # 4) 最优模型：回测 MAPE 最小且预测可用者
        candidates = [k for k in MODELS if mape_map.get(k) is not None and preds_ok.get(k)]
        best = min(candidates, key=lambda k: mape_map[k]) if candidates else None
        if best is None and usable:  # 回测全不可用：取权重最大者兜底
            best = max(usable, key=lambda k: weights.get(k, 0.0))

        # 若最优模型可用，则直接输出其纯预测（自动选优）；否则输出集成结果
        if best in preds_by_model:
            preds_out = [_json_num(v, 3) for v in preds_by_model[best]]
            basis = "最优模型「%s」纯预测" % best
        else:
            preds_out = list(ensemble)
            basis = "集成加权预测（无可用最优模型）"

        # 5) 组装“模型对比”证据
        compare = []
        for k in MODELS:
            mape = mape_map.get(k)
            nm = preds_by_model.get(k, [None] * steps)[0] if preds_ok.get(k) else None
            desc = notes.get(k, "")
            if k == best:
                desc = ("【最优模型】" + desc) if desc else "【最优模型】"
            if mape is None and desc:
                desc = desc + "；回测 MAPE 不可用，权重按 0 处理（不参与集成）"
            compare.append({
                "模型": k,
                "回测MAPE": _json_num(mape, 4) if mape is not None else None,
                "MAPE百分比": _pct(mape),
                "下月预测": _json_num(nm, 3) if nm is not None else None,
                "权重": _json_num(weights.get(k, 0.0), 4),
                "说明": desc,
            })

        last_actual, last_pred = ys[-1], preds_out[-1]
        tol = max(abs(last_actual), 1.0) * REL_TOL
        if last_pred > last_actual + tol:
            trend = "上升"
        elif last_pred < last_actual - tol:
            trend = "下降"
        else:
            trend = "持平"

        items.append({
            "产品": p,
            "历史序列": [{"月份": m_, "单位成本": _json_num(v, 3)} for m_, v in zip(ms, ys)],
            "预测月份": future,
            "预测单位成本": preds_out,
            "模型权重": {k: _json_num(weights.get(k, 0.0), 4) for k in MODELS},
            "回测MAPE": {k: (_json_num(mape_map[k], 4) if mape_map.get(k) is not None else None) for k in MODELS},
            "预测趋势": trend,
            "最优模型": best,
            "预测依据": basis,
            "集成预测": list(ensemble),
            "集成权重": {k: _json_num(weights.get(k, 0.0), 4) for k in MODELS},
            "模型对比": compare,
        })

    return {"months": future, "items": items}
