# -*- coding: utf-8 -*-
"""前端自检（开发辅助）：检查 static/index.html 的内联 JS 语法与 DOM id 引用一致性

    python scripts/tools/check_frontend.py

检查项：
1. 内联 `<script>` 能被 Node.js 解析（若本机有 node，执行 `node --check`）；
2. JS 里 `$('xxx')` 引用的 DOM id 都已定义（防拼写错误导致运行期 null）；
3. ECharts 容器 id 在 HTML 中存在；
4. 提示可能忘了关闭的标签（粗略统计 div 开合）。
"""
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HTML = os.path.join(ROOT, "static", "index.html")


def main():
    html = io.open(HTML, encoding="utf-8").read()
    blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
    if not blocks:
        print("❌ 未找到内联 <script>")
        return 1
    js = blocks[-1]
    print(f"内联 JS：{len(js)} 字符，{js.count(chr(10)) + 1} 行")

    defined = set(re.findall(r'id="([\w-]+)"', html))
    used = set(re.findall(r"\$\('([\w-]+)'\)", js))
    missing = sorted(used - defined)
    unused = sorted(defined - used)
    print(f"DOM id：定义 {len(defined)} 个，JS 引用 {len(used)} 个")
    if missing:
        print("❌ JS 引用了未定义的 id：", missing)
    else:
        print("✅ JS 引用的 id 全部存在")
    if unused:
        print("ℹ️  HTML 中未被 $() 引用的 id（可能是结构容器或模板占位）：", unused)

    opens = len(re.findall(r"<div\b", html))
    closes = len(re.findall(r"</div>", html))
    print(f"div 开合：{opens} / {closes}" + ("（不平衡，请检查）" if opens != closes else "（平衡）"))

    node = shutil.which("node")
    if node:
        fd, path = tempfile.mkstemp(suffix=".js")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(js)
        try:
            r = subprocess.run([node, "--check", path], capture_output=True)
            ok = r.returncode == 0
            print(("✅ Node 语法检查通过" if ok else "❌ Node 语法检查失败：\n" +
                   (r.stderr or b"").decode("utf-8", "ignore")[:600]))
        finally:
            os.remove(path)
    else:
        print("ℹ️  未检测到 node，跳过 JS 语法检查")

    echarts_ids = re.findall(r"chart\('([\w-]+)'\)", js)
    bad = [i for i in set(echarts_ids) if i not in defined]
    print("ECharts 容器：" + ("✅ 全部存在" if not bad else f"❌ 缺失 {bad}"))
    return 1 if (missing or bad) else 0


if __name__ == "__main__":
    sys.exit(main())
