# -*- coding: utf-8 -*-
"""文本规范化与中文切词工具（RAG / 知识图谱共用）

背景：官方知识文档是从 PDF 抽取的，存在两类典型噪声，会显著伤害检索质量：
1. **康熙部首/兼容表意文字**：如 `⽅`(U+2F45)、`⻩`(U+2EC9)、`⼭`(U+2F2D) 代替了 `方/黄/山`，
   导致 jieba 分词与关键词匹配全部失配；
2. **全角空格/多余空行**：表格抽取后残留大量空白。
`normalize_text()` 用 NFKC 规范化把部首还原为常规汉字，并压缩空白，检索前统一处理。
"""
import re
import unicodedata

import jieba

STOP = set("的 了 在 是 与 及 和 或 等 中 为 对 由 从 把 将 被 而 且 但 并 可 需 应 以 之 其 本 该 各 每 约 上 下 内 外".split())
_NUM_ONLY = re.compile(r"^[\d.,%\-/（）()：:、\s]+$")

# CJK 部首补充区（U+2E80–U+2EFF）NFKC **不** 还原，需按实际语料手工映射。
# 由 scripts/tools/scan_text_noise.py 扫描官方知识文档得到（出现频次从高到低）。
_RADICAL_MAP = {
    "\u2EE9": "黄", "\u2ECB": "车", "\u2EDB": "风", "\u2EC4": "西", "\u2EC5": "见",
    "\u2EDC": "飞", "\u2EA0": "民", "\u2EC6": "角", "\u2ED3": "长", "\u2EE3": "骨",
    "\u2EF0": "龙", "\u2E8B": "气", "\u2EB6": "舌", "\u2EE6": "高",
}
_RADICAL_TABLE = str.maketrans(_RADICAL_MAP)


def normalize_text(text):
    """PDF/OCR 文本规范化：部首→汉字（NFKC + 手工映射）+ 空白压缩

    官方知识文档由 PDF 抽取，`方/黄/山/车` 等字经常被抽成康熙部首（U+2F00–2FDF）
    或 CJK 部首补充区字符（U+2E80–2EFF）。前者 NFKC 可还原，后者需手工映射；
    不做处理会导致 jieba 分词与关键词匹配全部失配（实测影响 60 个字符、约 700 处）。
    """
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)
    t = t.translate(_RADICAL_TABLE)
    t = t.replace("\u00a0", " ").replace("\u3000", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def tokens(text, stop=True, min_len=1):
    """jieba 切词 + 去停用词/纯数字符号"""
    out = []
    for w in jieba.lcut(str(text)):
        w = w.strip()
        if not w or _NUM_ONLY.match(w):
            continue
        if stop and w in STOP:
            continue
        if len(w) < min_len:
            continue
        out.append(w)
    return out


def sentences(text, min_len=8):
    """按中文标点切句（用于抽取"证据句"）"""
    parts = re.split(r"(?<=[。；;！!？?\n])", text or "")
    return [p.strip() for p in parts if len(p.strip()) >= min_len]
