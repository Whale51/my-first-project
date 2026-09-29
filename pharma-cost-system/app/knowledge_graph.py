# -*- coding: utf-8 -*-
"""知识图谱增强 RAG（TODO P2 / 赛题 6.2 加分项）

把散落在知识库里的「产品配方 / 生产工艺 / 车间设备 / 药材行情 / 历史异常 / GMP 约束」
抽取成 **实体-关系-实体** 三元组，用于：
1. **检索增强**：查询命中实体后，沿图谱做 1 跳扩展，把"相关实体"所在文段加权，
   弥补纯文本检索"问 A 却需要 B 的知识"的短板（`KnowledgeGraph.expand`）；
2. **可解释性**：每条关系都带 `证据` 原句与 `来源` 文档，可直接引用进报告；
3. **可视化**：`to_echarts()` 输出 ECharts graph 数据，前端「知识图谱」页签展示；
4. **路径解释**：`path(a, b)` 给出两个实体间的关系路径（如 材料成本 → 收率 → 提取工序 → 提取罐）。

抽取方式为**词表 + 规则**（不依赖大模型、结果确定可复现），实体类型：
    产品 / 药材 / 工序 / 设备 / 车间 / 法规 / 记录 / 指标
"""
import os
import re

from .data_service import ds
from .logging_conf import get_logger
from .paths import PROJECT_ROOT
from .textutil import normalize_text, sentences

log = get_logger(__name__)

KB_DIRS = [os.path.join(PROJECT_ROOT, "data", "knowledge"),
           os.path.join(PROJECT_ROOT, "data", "knowledge_official")]

# ---------- 词表 ----------
PROCESS_LEXICON = ["提取", "浓缩", "醇沉", "粉碎", "过筛", "称量配料", "配液", "制粒", "干燥",
                   "灌装", "灭菌", "灯检", "洗瓶", "内包装", "外包装", "入库检验", "纯化水制备"]
WORKSHOP_LEXICON = {"提取车间": ["提取", "浓缩"], "制剂车间": ["制粒", "干燥", "灌装", "灭菌", "灯检"],
                    "包装车间": ["内包装", "外包装"]}
DEVICE_SUFFIX = ("罐", "器", "机", "柜", "泵", "塔", "炉", "线", "床", "筛")
RISK_LEVELS = ("高", "中", "低")
TREND_WORDS = {"上行": ["上涨", "上行", "高位", "冲高", "涨价"], "平稳": ["平稳", "稳定", "震荡", "基本"],
               "下行": ["下行", "回落", "下跌", "供大于求", "小幅下行"]}
REGULATION_TOPICS = {
    "物料管理": ["物料管理", "供应商须经审计", "采购变更"],
    "批记录": ["批记录", "偏差须在", "调查闭环"],
    "储存养护": ["储存养护", "阴凉库", "霉变"],
    "委托检验": ["委托检验", "检验费"],
    "成本合规红线": ["成本合规红线", "禁止通过减少检验批次"],
}


def _read_docs(dirs=None):
    """读取知识库文本（仅 txt/md；PDF 抽取文本已在 knowledge_official 中）"""
    out = {}
    for d in (dirs or KB_DIRS):
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.lower().endswith((".txt", ".md")):
                continue
            try:
                with open(os.path.join(d, fn), encoding="utf-8", errors="ignore") as f:
                    out[fn] = normalize_text(f.read())
            except OSError as e:
                log.warning("知识图谱读取失败 %s：%s", fn, e)
    return out


def _doc_key(docs, *keywords):
    """按文件名关键字取文档"""
    for fn, text in docs.items():
        if all(k in fn for k in keywords):
            return fn, text
    return None, ""


def _evidence(text, name, max_len=90):
    """取包含实体的那句原文作为证据"""
    for s in sentences(text):
        if name in s:
            return s[:max_len]
    return ""


class KnowledgeGraph:
    """轻量知识图谱（邻接表实现，无第三方依赖）"""

    def __init__(self, nodes=None, edges=None, sources=None):
        self.nodes = nodes or {}         # name -> {"type":..., "attrs": {...}}
        self.edges = edges or []         # [{"头","关系","尾","证据","来源"}]
        self.sources = sources or {}     # 来源文档 -> 字符数
        self._adj = {}
        self._dirty = True
        self._build_adj()

    # ---------- 构建 ----------
    def _build_adj(self):
        self._adj = {n: [] for n in self.nodes}
        for e in self.edges:
            self._adj.setdefault(e["头"], []).append((e["关系"], e["尾"], e))
            self._adj.setdefault(e["尾"], []).append((e["关系"] + "(被)", e["头"], e))
        self._dirty = False

    def _ensure_adj(self):
        """邻接表懒重建（批量构建时避免每次 add_edge 都 O(E) 重建）"""
        if self._dirty:
            self._build_adj()
        return self._adj

    def add_node(self, name, ntype="其他", **attrs):
        if not name or len(str(name)) < 2:
            return
        if name not in self.nodes:
            self.nodes[name] = {"type": ntype}
            self._dirty = True
        self.nodes[name].setdefault("type", ntype)
        self.nodes[name].update({k: v for k, v in attrs.items() if v is not None})

    def add_edge(self, head, rel, tail, evidence="", source=""):
        if not head or not tail or head == tail:
            return
        if head not in self.nodes:
            self.add_node(head)
        if tail not in self.nodes:
            self.add_node(tail)
        for e in self.edges:
            if e["头"] == head and e["关系"] == rel and e["尾"] == tail:
                return
        self.edges.append({"头": head, "关系": rel, "尾": tail, "证据": evidence, "来源": source})
        self._dirty = True

    # ---------- 查询 ----------
    @property
    def products(self):
        return [n for n, v in self.nodes.items() if v.get("type") == "产品"]

    def entities(self, text, min_len=2):
        """从文本中识别图谱实体（长名优先，避免"山药"命中"怀山药"式歧义）"""
        found = []
        for name in sorted(self.nodes, key=lambda x: -len(x)):
            if len(name) >= min_len and name in (text or ""):
                found.append(name)
        return found

    def neighbors(self, name, hops=1):
        """返回 {实体: [{"关系","方向","证据","来源"}]}（广度优先，最多 hops 跳）"""
        adj = self._ensure_adj()
        seen = {name}
        frontier = [name]
        out = {}
        for _ in range(max(hops, 1)):
            nxt = []
            for cur in frontier:
                for rel, other, e in adj.get(cur, []):
                    item = {"关系": rel, "方向": "出" if e["头"] == cur else "入",
                            "证据": e["证据"], "来源": e["来源"], "起点": cur}
                    out.setdefault(other, []).append(item)
                    if other not in seen:
                        seen.add(other)
                        nxt.append(other)
            frontier = nxt
        return out

    def expand(self, query, product_hint=None, hops=1):
        """检索增强：命中实体 → 邻居实体 → 相关文段加权参数"""
        seeds = self.entities(query)
        if product_hint:
            seeds += [p for p in self.products if p == product_hint and p not in seeds]
        related = {}
        relations = []
        for s in seeds:
            for other, rels in self.neighbors(s, hops=hops).items():
                related.setdefault(other, 0)
                for r in rels:
                    relations.append({"头": s if r["方向"] == "出" else other,
                                      "关系": r["关系"].replace("(被)", ""),
                                      "尾": other if r["方向"] == "出" else s,
                                      "证据": r["证据"], "来源": r["来源"]})
            related.setdefault(s, 0)
        # 加权：种子实体 1.0；1 跳邻居 0.45（按命中次数递减）
        weights = {}
        for name in related:
            base = 1.0 if name in seeds else 0.45
            weights[name] = round(base, 3)
        return {"entities": seeds, "related": sorted(related, key=lambda x: -weights[x]),
                "chunk_weights": weights, "relations": relations[:40]}

    def path(self, a, b, max_hops=3):
        """两个实体间的最短关系路径（用于"为什么会涨"这类链路解释）"""
        if a not in self.nodes or b not in self.nodes:
            return None
        adj = self._ensure_adj()
        queue, seen = [(a, [a])], {a}
        while queue:
            cur, route = queue.pop(0)
            if cur == b:
                return route
            if len(route) > max_hops:
                continue
            for _rel, other, _e in adj.get(cur, []):
                if other not in seen:
                    seen.add(other)
                    queue.append((other, route + [other]))
        return None

    # ---------- 输出 ----------
    def stats(self):
        by_type, by_rel = {}, {}
        for v in self.nodes.values():
            by_type[v.get("type", "其他")] = by_type.get(v.get("type", "其他"), 0) + 1
        for e in self.edges:
            by_rel[e["关系"]] = by_rel.get(e["关系"], 0) + 1
        return {"节点数": len(self.nodes), "关系数": len(self.edges),
                "实体类型分布": by_type, "关系分布": by_rel,
                "来源文档数": len(self.sources)}

    def triples(self, limit=None, who=None):
        rows = self.edges
        if who:
            rows = [e for e in rows if e["头"] == who or e["尾"] == who]
        return rows[:limit] if limit else rows

    def to_echarts(self, focus=None, limit=90):
        """输出 ECharts graph 数据（nodes/links/categories）"""
        types = ["产品", "药材", "工序", "设备", "车间", "法规", "记录", "指标", "其他"]
        cat_index = {t: i for i, t in enumerate(types)}
        edges = self.edges
        if focus:
            keep = {focus} | set(self.neighbors(focus, hops=2).keys())
            edges = [e for e in edges if e["头"] in keep or e["尾"] in keep]
        edges = edges[:limit]
        names = []
        for e in edges:
            for n in (e["头"], e["尾"]):
                if n not in names:
                    names.append(n)
        nodes = []
        for n in names:
            t = self.nodes.get(n, {}).get("type", "其他")
            attrs = {k: v for k, v in self.nodes.get(n, {}).items() if k != "type"}
            nodes.append({"name": n, "category": cat_index.get(t, len(types) - 1),
                          "symbolSize": 26 if t == "产品" else (18 if t in ("药材", "工序") else 13),
                          "value": t, "attrs": attrs})
        links = [{"source": e["头"], "target": e["尾"], "value": e["关系"],
                  "evidence": e["证据"], "source_doc": e["来源"]} for e in edges]
        return {"nodes": nodes, "links": links, "categories": [{"name": t} for t in types]}


# ==================== 抽取规则 ====================
def _medicine_lexicon(docs):
    """药材词表：官方行情表 + 配方文档中出现的药材名"""
    lex = set(ds.price.keys())
    for fn, text in docs.items():
        if "配方" not in fn:
            continue
        for line in text.split("\n"):
            m = re.match(r"^\s*\d*\s*([\u4e00-\u9fa5]{2,5})\s+([0-9]+\.[0-9]+)\s", line)
            if m and m.group(1) not in ("合计", "序号", "原料", "药材", "包装", "空心", "以上的"):
                lex.add(m.group(1))
    lex |= {"熟地黄", "山茱萸", "山药", "泽泻", "茯苓", "牡丹皮", "板蓝根", "金银花", "黄芩",
            "连翘", "当归", "黄芪", "党参", "酸枣仁", "远志", "空心胶囊", "包装材料"}
    return {x for x in lex if len(x) >= 2}


def _extract(docs):
    g = KnowledgeGraph(sources={fn: len(t) for fn, t in docs.items()})
    meds = _medicine_lexicon(docs)
    for m in meds:
        g.add_node(m, "药材")
    for p in ds.products_list():
        g.add_node(p, "产品")
    for s in PROCESS_LEXICON:
        g.add_node(s, "工序")
    for w in WORKSHOP_LEXICON:
        g.add_node(w, "车间")

    # ① 产品配方：产品-含药材、药材-产地/风险等级
    for fn, text in docs.items():
        if "配方" not in fn:
            continue
        product = next((p for p in ds.products_list() if p in fn or p in text[:200]), None)
        if not product:
            continue
        for m in meds:
            if m in text:
                g.add_edge(product, "配方含", m, _evidence(text, m), fn)
                g.nodes[m].setdefault("类型", "药材")
        for line in text.split("\n"):
            mm = re.match(r"^\s*([\u4e00-\u9fa5]{2,5})\s*~?\s*([0-9]+)%(.*?)(高|中|低)\s*$", line.strip())
            if mm and mm.group(1) in g.nodes:
                g.nodes[mm.group(1)]["占总材料成本"] = f"~{mm.group(2)}%"
                g.nodes[mm.group(1)]["风险等级"] = mm.group(4)
                g.add_edge(mm.group(1), "风险等级", mm.group(4), line.strip()[:90], fn)
        for line in text.split("\n"):
            mm = re.match(r"^\s*([\u4e00-\u9fa5]{2,5})\s+[0-9.]+g\s+[0-9.]+\s*元\s*/\s*kg", line)
            if mm and mm.group(1) in g.nodes:
                g.nodes[mm.group(1)]["单盒用量"] = re.search(r"([0-9.]+g)", line).group(1)

    # ② 生产工艺路线：工序链 + 工序-成本关联
    proc_fn, proc_text = _doc_key(docs, "生产工艺路线")
    if proc_text:
        order = []
        first = proc_text.split("\n")[1] if len(proc_text.split("\n")) > 1 else ""
        for s in PROCESS_LEXICON:
            if s in first:
                order.append(s)
        for i in range(len(order) - 1):
            g.add_edge(order[i], "前置工序", order[i + 1], first[:90], proc_fn)
        for s in order:
            g.add_node(s, "工序")
        for s in sentences(proc_text):
            if "收率" in s:
                g.add_node("收率", "指标", 说明="提取工序理论收率 85%~88%，低于 80% 触发偏差调查")
                g.add_edge("收率", "影响", "直接材料", s[:90], proc_fn)
                g.add_edge("提取", "决定", "收率", s[:90], proc_fn)
                g.add_edge("收率", "关联", "工艺偏差调查", s[:90], proc_fn)
                g.add_node("工艺偏差调查", "记录")
            if "蒸汽" in s:
                g.add_node("动力费", "指标")
                g.add_edge("提取", "消耗", "动力费", s[:90], proc_fn)

    # ③ 车间设备清单：车间-含设备、工序-使用设备
    dev_fn, dev_text = _doc_key(docs, "车间设备清单")
    if dev_text:
        for line in dev_text.split("\n"):
            m = re.match(r"^\s*(.{2,6}车间)\s*[：:]\s*(.+)$", line)
            if not m:
                continue
            workshop, rest = m.group(1), m.group(2)
            g.add_node(workshop, "车间")
            for piece in re.split(r"[、,，]", rest):
                piece = piece.strip()
                dev = re.split(r"\s+[A-Z]{1,3}[0-9\-/]*", piece)[0].strip()
                if not dev or len(dev) < 3 or not dev.endswith(DEVICE_SUFFIX):
                    continue
                g.add_node(dev, "设备")
                g.add_edge(workshop, "配置设备", dev, line.strip()[:90], dev_fn)
                for proc in WORKSHOP_LEXICON.get(workshop, []):
                    g.add_edge(proc, "使用设备", dev, line.strip()[:90], dev_fn)
        for s in sentences(dev_text):
            if "蒸汽单耗" in s or "动力成本" in s:
                g.add_node("动力费", "指标")
                g.add_edge("提取车间", "关注", "动力费", s[:90], dev_fn)
                g.add_edge("动力费", "影响", "制造费用", s[:90], dev_fn)

    # ④ 药材行情：药材-行情趋势
    trend_fn, trend_text = _doc_key(docs, "行情")
    if trend_text:
        for m in list(g.nodes):          # 快照遍历：add_edge 可能新增节点（趋势名）
            if g.nodes[m].get("type") != "药材" or m not in trend_text:
                continue
            for sent in sentences(trend_text):
                if m not in sent:
                    continue
                for trend, kws in TREND_WORDS.items():
                    if any(k in sent for k in kws):
                        g.nodes[m]["行情趋势"] = trend
                        g.add_edge(m, "行情趋势", trend, sent[:90], trend_fn)
                        break
        # 官方行情 CSV 的趋势分析文字也算证据
        for m in list(g.nodes):
            if g.nodes[m].get("type") == "药材" and m in ds.price:
                g.nodes[m]["官方行情趋势"] = ds.price[m].get("趋势", "")

    # ⑤ 历史异常处理记录：记录-涉及药材/工序
    rec_fn, rec_text = _doc_key(docs, "历史成本异常")
    if rec_text:
        for sent in sentences(rec_text):
            m = re.match(r"^\s*记录\s*(\d+)\s*[（(]([^）)]*)[）)]\s*[：:]?\s*(.*)$", sent)
            if not m:
                continue
            rid = f"异常记录{m.group(1)}"
            g.add_node(rid, "记录", 时间=m.group(2), 摘要=m.group(3)[:60])
            for name in list(g.nodes):
                if g.nodes[name].get("type") in ("药材", "工序") and name in sent:
                    g.add_edge(rid, "涉及", name, sent[:90], rec_fn)
            if "收率" in sent:
                g.add_edge(rid, "涉及", "收率", sent[:90], rec_fn)

    # ⑥ GMP 合规约束：法规主题-约束业务域
    gmp_fn, gmp_text = _doc_key(docs, "GMP")
    if gmp_text:
        domain_map = {"物料管理": ["采购", "直接材料"], "批记录": ["生产", "质量"],
                      "储存养护": ["仓储", "直接材料"], "委托检验": ["检验费用", "制造费用"],
                      "成本合规红线": ["成本管理"]}
        for topic, kws in REGULATION_TOPICS.items():
            if not any(k in gmp_text for k in kws):
                continue
            g.add_node(topic, "法规")
            for d in domain_map.get(topic, []):
                g.add_node(d, "指标")
                g.add_edge(topic, "约束", d, _evidence(gmp_text, kws[0]), gmp_fn)

    # ⑦ 对标基线：二厂-对标-一厂（口径说明）
    bl_fn, bl_text = _doc_key(docs, "对标")
    if bl_text:
        g.add_node("中药二厂", "车间")
        g.add_node("中药一厂", "车间")
        g.add_edge("中药二厂", "对标于", "中药一厂", _evidence(bl_text, "对标对象"), bl_fn)
        g.add_edge("中药一厂", "对标要素", "直接材料", "同产品同期、同成本要素对比", bl_fn)
        g.add_edge("中药一厂", "对标要素", "直接人工", "同产品同期、同成本要素对比", bl_fn)
        g.add_edge("中药一厂", "对标要素", "制造费用", "同产品同期、同成本要素对比", bl_fn)
    return g


_GRAPH = None


def build_graph(dirs=None, force=False):
    """构建（并缓存）知识图谱；任何异常都返回空图谱而不影响主流程"""
    global _GRAPH
    if _GRAPH is not None and not force:
        return _GRAPH
    try:
        docs = _read_docs(dirs)
        _GRAPH = _extract(docs)
        log.info("知识图谱构建完成：%s", _GRAPH.stats())
    except Exception as e:
        log.warning("知识图谱构建失败（不影响检索）：%s", e)
        _GRAPH = KnowledgeGraph()
    return _GRAPH


def graph():
    """单例访问"""
    return build_graph()


if __name__ == "__main__":      # 手动查看：python -m app.knowledge_graph
    g = graph()
    print(g.stats())
    for t in g.triples(limit=40):
        print(f"  {t['头']} --{t['关系']}--> {t['尾']}   证据：{t['证据'][:40]}")
