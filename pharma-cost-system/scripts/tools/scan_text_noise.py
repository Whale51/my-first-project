# -*- coding: utf-8 -*-
"""扫描知识库文本中的"部首/兼容字符"噪声，输出映射建议（开发辅助，可重复运行）

    python scripts/tools/scan_text_noise.py
"""
import os
import sys
import unicodedata
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app.paths import PROJECT_ROOT   # noqa: E402

DIRS = [os.path.join(PROJECT_ROOT, "data", "knowledge"),
        os.path.join(PROJECT_ROOT, "data", "knowledge_official")]


def main():
    cnt = Counter()
    for d in DIRS:
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.lower().endswith((".txt", ".md")):
                continue
            with open(os.path.join(d, fn), encoding="utf-8", errors="ignore") as f:
                for ch in f.read():
                    o = ord(ch)
                    if 0x2E80 <= o <= 0x2FDF or 0xF900 <= o <= 0xFAFF:
                        cnt[ch] += 1
    print(f"发现 {len(cnt)} 个非标准汉字（部首/兼容区），按出现次数排序：")
    for ch, n in cnt.most_common():
        nfkc = unicodedata.normalize("NFKC", ch)
        flag = "NFKC可还原" if nfkc != ch else "需手工映射"
        print(f"  {ch}  U+{ord(ch):04X}  ×{n:<4} NFKC→{nfkc}  ({flag})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
