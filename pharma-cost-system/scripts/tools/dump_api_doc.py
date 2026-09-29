# -*- coding: utf-8 -*-
"""从 FastAPI 应用导出接口文档（TODO P3「补充 API 文档」）

    python scripts/tools/dump_api_doc.py            # 生成 docs/API速查.md + outputs/eval/openapi.json
    python scripts/tools/dump_api_doc.py --stdout   # 只打印

产物：
* `docs/API速查.md`：按标签分组的接口速查表（路径 / 方法 / 摘要 / 关键入参），可截图进技术方案；
* `outputs/eval/openapi.json`：OpenAPI 3.1 规范原文，可导入 Postman/Swagger UI 复核。
"""
import argparse
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
_VENDOR = os.path.join(ROOT, "vendor")
if os.path.isdir(_VENDOR):
    sys.path.insert(0, _VENDOR)

from app.main import app        # noqa: E402

OUT_MD = os.path.join(ROOT, "docs", "API速查.md")
OUT_JSON = os.path.join(ROOT, "outputs", "eval", "openapi.json")

METHODS = ("get", "post", "put", "delete", "patch")


def collect():
    spec = app.openapi()
    groups = {}
    for path, ops in spec.get("paths", {}).items():
        for method, op in ops.items():
            if method.lower() not in METHODS:
                continue
            tags = op.get("tags") or ["未分组"]
            for tag in tags:
                groups.setdefault(tag, []).append({
                    "method": method.upper(), "path": path,
                    "summary": op.get("summary", ""),
                    "params": [(p.get("name"), p.get("in"), p.get("required"), p.get("schema", {}).get("type"))
                               for p in op.get("parameters", [])],
                    "body": bool(op.get("requestBody")),
                })
    return spec, groups


def to_markdown(spec, groups):
    lines = ["# API 速查（由 `scripts/tools/dump_api_doc.py` 自动生成，请勿手改）", "",
             f"服务：{spec['info']['title']} v{spec['info']['version']}", "",
             "在线交互文档：运行服务后访问 **/docs**（Swagger UI）或 **/redoc**；",
             "OpenAPI 规范原文：`outputs/eval/openapi.json`。", ""]
    tag_order = [t["name"] for t in spec.get("tags", [])]
    for tag in tag_order + [t for t in groups if t not in tag_order]:
        if tag not in groups:
            continue
        desc = next((t.get("description", "") for t in spec.get("tags", []) if t["name"] == tag), "")
        lines += [f"## {tag}", "", desc, "",
                  "| 方法 | 路径 | 说明 | 关键入参 |", "|---|---|---|---|"]
        for op in sorted(groups[tag], key=lambda x: (x["path"], x["method"])):
            params = "、".join(
                f"`{n}`({loc}{'*' if req else ''})" for n, loc, req, _t in op["params"]
                if loc in ("query", "path")) or "—"
            body = "请求体 JSON" if op["body"] else "—"
            lines.append(f"| {op['method']} | `{op['path']}` | {op['summary']} | {params}；{body} |")
        lines.append("")
    lines += ["---", "",
              "说明：带 `*` 的参数为必填；所有接口对非法入参返回 **400 + `{error: 提示}`**，",
              "文件下载类接口仅允许 `docx/pdf/png` 白名单扩展名。", ""]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stdout", action="store_true", help="只打印，不写文件")
    args = ap.parse_args()
    spec, groups = collect()
    md = to_markdown(spec, groups)
    if args.stdout:
        print(md)
        return 0
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    io.open(OUT_MD, "w", encoding="utf-8", newline="").write(md)
    with io.open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=1)
    n = sum(len(v) for v in groups.values())
    print(f"✅ 已导出 {n} 个接口 / {len(groups)} 个分组")
    print("   ", OUT_MD)
    print("   ", OUT_JSON)
    return 0


if __name__ == "__main__":
    sys.exit(main())
