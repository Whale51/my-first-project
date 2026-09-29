# -*- coding: utf-8 -*-
"""受限网络/离线环境依赖获取：把 wheel 直接解包到项目内 `vendor/` 目录

适用场景：
* 机器无法写入 Python 的 site-packages（企业管控/容器只读目录）；
* PyPI 官方源速度极慢，需要走国内镜像；
* 需要"免联网"部署：把依赖放进项目内随包分发。

用法：
    python scripts/fetch_vendor.py                 # 默认取 reportlab pillow matplotlib
    python scripts/fetch_vendor.py reportlab       # 只取指定包
    python scripts/fetch_vendor.py --mirror https://pypi.tuna.tsinghua.edu.cn/simple

脚本按镜像顺序重试（清华 → 阿里 → 腾讯 → 官方），逐个 `pip download --no-deps`，
再把 wheel（本质是 zip）解包进 `vendor/`；项目启动时 `app/__init__.py` 会自动把
`vendor/` 加入 sys.path，因此无需真正执行 pip install。
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR = os.path.join(ROOT, "vendor")
WHEELS = os.path.join(VENDOR, "_wheels")

MIRRORS = [
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://mirrors.aliyun.com/pypi/simple",
    "https://mirrors.cloud.tencent.com/pypi/simple",
    "https://pypi.org/simple",
]

# matplotlib 的纯 Python 依赖（numpy 通常已随 pandas 安装）
MATPLOTLIB_DEPS = ["contourpy", "cycler", "fonttools", "kiwisolver", "packaging",
                   "pillow", "pyparsing", "python-dateutil"]

DEFAULT_PKGS = ["reportlab", "pillow", "matplotlib"]


def download_one(pkg, mirror, dest, timeout=90):
    cmd = [sys.executable, "-m", "pip", "download", "--no-deps", "--dest", dest,
           "-i", mirror, "--timeout", "20", "--retries", "1", "--progress-bar", "off", pkg]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "超时"
    if r.returncode == 0:
        return True, "OK"
    return False, (r.stderr or b"").decode("utf-8", "ignore").strip().splitlines()[-1:] or "失败"


def unzip_wheels(dest):
    n = 0
    for whl in glob.glob(os.path.join(dest, "*.whl")):
        try:
            with zipfile.ZipFile(whl) as z:
                z.extractall(VENDOR)
            n += 1
            print(f"  解包 {os.path.basename(whl)}")
        except Exception as e:
            print(f"  解包失败 {os.path.basename(whl)}：{e}")
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("packages", nargs="*", default=None)
    ap.add_argument("--mirror", default=None, help="只用指定镜像")
    ap.add_argument("--timeout", type=int, default=90)
    args = ap.parse_args()

    pkgs = args.packages or DEFAULT_PKGS
    if "matplotlib" in pkgs:
        pkgs = pkgs + [p for p in MATPLOTLIB_DEPS if p not in pkgs]
    mirrors = [args.mirror] if args.mirror else MIRRORS

    os.makedirs(WHEELS, exist_ok=True)
    print(f"目标目录：{VENDOR}")
    for pkg in pkgs:
        ok = False
        for m in mirrors:
            print(f"[{pkg}] 尝试 {m} ...")
            good, msg = download_one(pkg, m, WHEELS, args.timeout)
            if good:
                ok = True
                break
            print(f"    失败：{msg}")
        if not ok:
            print(f"[{pkg}] ❌ 所有镜像均失败")

    n = unzip_wheels(WHEELS)
    print(f"\n共解包 {n} 个 wheel 到 {VENDOR}")
    missing = [m for m in ("reportlab", "PIL", "matplotlib") if not _check(m)]
    if missing:
        print("仍缺失：", missing)
    else:
        print("✅ reportlab / pillow / matplotlib 均已就绪（项目会自动加载 vendor/）")
    return 0


def _check(mod):
    sys.path.insert(0, VENDOR)
    import importlib.util
    return importlib.util.find_spec(mod) is not None


if __name__ == "__main__":
    sys.exit(main())
