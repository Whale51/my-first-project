# -*- coding: utf-8 -*-
"""RAG 混合检索评测（TODO P1.1 证据留档）

对比三种检索模式的检索质量，并验证多格式（PDF/Word/TXT）知识文档接入能力：

    python scripts/verify/verify_rag.py              # 全量评测并落盘证据
    python scripts/verify/verify_rag.py --topk 5

指标：Hit@1 / Hit@3 / MRR（人工标注 10 条查询的目标来源文档）
产物：outputs/eval/rag_eval.json（含逐条命中明细，可截图进评测报告）
"""
import argparse
import io
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
_VENDOR = os.path.join(ROOT, "vendor")
if os.path.isdir(_VENDOR):
    sys.path.insert(0, _VENDOR)

from app import rag                                     # noqa: E402
from app.paths import OUTPUT_DIR, official_data_dir     # noqa: E402

# 人工标注：查询 → 期望命中的来源文档关键字（命中任一即算正确）
# 前 6 条偏"关键词型"（BM25 友好），后 6 条偏"口语/近义改写型"（考向量语义召回），
# 用于观察混合检索相对单路的增益。
CASES = [
    ("连翘单耗上升的原因", ["历史成本异常处理记录", "生产工艺路线"]),
    ("金银花价格上涨对材料成本的影响", ["药材市场行情分析", "行情"]),
    ("六味地黄胶囊的处方组成与单盒用量", ["产品配方文档_六味地黄胶囊"]),
    ("车间有哪些提取与制剂设备", ["车间设备清单"]),
    ("GMP 对药材供应商与采购变更的合规要求", ["GMP"]),
    ("板蓝根颗粒的功能主治与规格", ["产品配方文档_板蓝根颗粒"]),
    ("这种药丸由哪些草本成分搭配而成", ["产品配方文档"]),
    ("提取的时候水温和时间怎么控制", ["生产工艺路线", "生产工艺文档"]),
    ("谁负责处理药材发霉变质的问题", ["历史成本异常处理记录"]),
    ("采购价格签了合同之后还能不能随便改", ["GMP合规要求摘要", "GMP"]),
    ("两套生产装置哪一套更省人工", ["同集团对标基线说明", "对标"]),
    ("药材库房太潮会带来什么损失", ["GMP合规要求摘要", "GMP", "历史成本异常处理记录"]),
]


def evaluate(mode, topk=3, fusion=None):
    rows, hit1, hit3, rr_sum = [], 0, 0, 0.0
    for query, expects in CASES:
        hits = rag.kb.search(query, top_k=topk, mode=mode, fusion=fusion)
        got = [h["source"] for h in hits]
        rank = None
        for i, src in enumerate(got, start=1):
            if any(e in src or src in e for e in expects):
                rank = i
                break
        hit1 += 1 if rank == 1 else 0
        hit3 += 1 if rank and rank <= 3 else 0
        rr_sum += (1.0 / rank) if rank else 0.0
        rows.append({"查询": query, "期望来源": expects, "命中来源": got, "首次命中排名": rank})
    n = len(CASES)
    return {"模式": mode, "融合": fusion or "weighted", "Hit@1": round(hit1 / n, 3),
            "Hit@3": round(hit3 / n, 3), "MRR": round(rr_sum / n, 3), "明细": rows}


def check_formats():
    """验证 PDF / Word / TXT 三种格式的知识文档都能直接接入"""
    off = official_data_dir()
    targets = [
        (os.path.join(off, "03_制药知识文档", "产品配方文档_板蓝根颗粒.pdf"), ["板蓝根"]),
        (os.path.join(off, "04_报告模板", "月度成本分析报告模板.docx"), ["成本", "报告"]),
        (os.path.join(ROOT, "data", "knowledge", "生产工艺路线.txt"), ["提取"]),
    ]
    out = []
    for path, keys in targets:
        if not os.path.exists(path):
            out.append({"文件": os.path.basename(path), "状态": "文件不存在"})
            continue
        t0 = time.time()
        try:
            text = rag.load_document_cached(path)
            out.append({"文件": os.path.basename(path), "格式": os.path.splitext(path)[1].lstrip("."),
                        "抽取字符数": len(text), "耗时秒": round(time.time() - t0, 2),
                        "关键内容命中": all(k in text for k in keys),
                        "部首噪声残留": sum(1 for ch in text if 0x2E80 <= ord(ch) <= 0x2FDF),
                        "状态": "OK"})
        except Exception as e:
            out.append({"文件": os.path.basename(path), "状态": f"失败：{e}"})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topk", type=int, default=3)
    args = ap.parse_args()

    print("=" * 92)
    st = rag.kb.stats()
    print(f"知识库：文档 {st['文档数']} 份 / 知识块 {st['知识块数']} 个 / 平均块长 {st['平均块长']} 字")
    print(f"切分参数：{st['切分参数']}")
    print(f"向量后端：{st['向量后端']}｜嵌入器：{(st['嵌入器'] or {}).get('name')}"
          f"（{(st['嵌入器'] or {}).get('说明', '')}）")
    print(f"知识图谱：{st['知识图谱']}")
    print("=" * 92)

    results = []
    for mode in ("bm25", "vector", "hybrid"):
        r = evaluate(mode, args.topk)
        results.append(r)
        print(f"{mode:8s} Hit@1={r['Hit@1']:.3f}  Hit@3={r['Hit@3']:.3f}  MRR={r['MRR']:.3f}")

    # 融合方式对比：加权归一化 vs 倒数排名融合（RRF）
    r_rrf = evaluate("hybrid", args.topk, fusion="rrf")
    results.append(r_rrf)
    print(f"{'hybrid+RRF':8s} Hit@1={r_rrf['Hit@1']:.3f}  Hit@3={r_rrf['Hit@3']:.3f}  MRR={r_rrf['MRR']:.3f}")

    # 融合权重扫描（说明 RAG_HYBRID_ALPHA 可调，并给出本语料最优取值）
    sweep = []
    for alpha in (0.0, 0.3, 0.5, 0.7, 1.0):
        rows, rr, h1 = [], 0.0, 0
        for query, expects in CASES:
            hits = rag.kb.search(query, top_k=args.topk, mode="hybrid", alpha=alpha)
            rank = next((i for i, h in enumerate(hits, 1)
                         if any(e in h["source"] or h["source"] in e for e in expects)), None)
            rr += (1.0 / rank) if rank else 0.0
            h1 += 1 if rank == 1 else 0
        n = len(CASES)
        sweep.append({"alpha": alpha, "Hit@1": round(h1 / n, 3), "MRR": round(rr / n, 3)})
        print(f"alpha={alpha:<4} Hit@1={h1 / n:.3f}  MRR={rr / n:.3f}")

    # 关闭图谱增强做一次消融对比
    saved, rag.kb.graph = rag.kb.graph, None
    r0 = evaluate("hybrid", args.topk)
    rag.kb.graph = saved
    print(f"{'hybrid(无图谱)':8s} Hit@1={r0['Hit@1']:.3f}  Hit@3={r0['Hit@3']:.3f}  MRR={r0['MRR']:.3f}")

    print("-" * 92)
    print("多格式知识文档接入自检：")
    fmt = check_formats()
    for f in fmt:
        print("  ", json.dumps(f, ensure_ascii=False))

    ev = {"生成时间": time.strftime("%Y-%m-%d %H:%M:%S"),
          "知识库统计": st, "评测": results, "alpha扫描": sweep, "消融_无图谱增强": r0,
          "多格式接入": fmt, "top_k": args.topk}
    out_dir = os.path.join(OUTPUT_DIR, "eval")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "rag_eval.json")
    with io.open(out, "w", encoding="utf-8") as f:
        json.dump(ev, f, ensure_ascii=False, indent=1)
    print("-" * 92)
    print("证据已写入：", out)

    best = max(results, key=lambda x: (x["MRR"], x["Hit@1"]))
    kg_gain = round((best["MRR"] - r0["MRR"]) * 100, 1)
    print(f"结论：MRR 最优检索模式 = {best['模式']}（{best['MRR']}）；"
          f"知识图谱增强带来 MRR {kg_gain:+} 个百分点（{r0['MRR']} → {best['MRR']}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
