# -*- coding: utf-8 -*-
"""路径与配置解析（可移植版）

官方模拟数据目录按以下顺序自动查找，找到第一个存在的：
1) 环境变量 PHARMA_DATA_DIR 指定的目录
2) <项目根>/data_official/创灵境_考题模拟数据          （随包分发的推荐位置）
3) <项目根>/../官方模拟数据/创灵境_考题模拟数据          （与本项目同级）
4) <项目根>/../../官方模拟数据/创灵境_考题模拟数据
5) D:\\AI_tant\\2026-09-09-09-14-18\\官方模拟数据\\创灵境_考题模拟数据
   （开发机原始路径，仅作最后兜底）

同时提供轻量 .env 加载（无需 python-dotenv 依赖）：
在项目根目录创建 .env 文件，写入 KEY=VALUE 即可，例如：
    LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
    LLM_API_KEY=sk-xxxx
    LLM_MODEL=qwen-plus
"""
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "outputs")

OFFICIAL_DATA_NAME = "创灵境_考题模拟数据"

_CANDIDATES = [
    os.path.join(PROJECT_ROOT, "data_official", OFFICIAL_DATA_NAME),
    os.path.join(PROJECT_ROOT, os.pardir, "官方模拟数据", OFFICIAL_DATA_NAME),
    os.path.join(PROJECT_ROOT, os.pardir, os.pardir, "官方模拟数据", OFFICIAL_DATA_NAME),
    r"D:\AI_tant\2026-09-09-09-14-18\官方模拟数据\创灵境_考题模拟数据",
]


def load_dotenv(path=None):
    """读取项目根目录 .env 文件并写入环境变量（已存在的环境变量优先，不覆盖）"""
    path = path or os.path.join(PROJECT_ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


def official_data_dir():
    """返回官方模拟数据目录（绝对路径）；找不到时给出明确指引"""
    env = os.environ.get("PHARMA_DATA_DIR")
    cands = ([env] if env else []) + _CANDIDATES
    for c in cands:
        if c and os.path.isdir(c):
            return os.path.abspath(c)
    raise FileNotFoundError(
        "未找到官方模拟数据目录『%s』。请任选一种方式放置数据：\n"
        "  1) 将官方数据包解压到本项目同级目录：<项目根>/data_official/%s\n"
        "  2) 与本项目放在同一父目录下：<父目录>/官方模拟数据/%s\n"
        "  3) 设置环境变量 PHARMA_DATA_DIR 指向该目录（或在 .env 中配置）\n"
        "已尝试的路径：\n  %s" % (OFFICIAL_DATA_NAME, OFFICIAL_DATA_NAME, OFFICIAL_DATA_NAME,
                                "\n  ".join(c for c in cands if c)))


def rpa_server_file():
    """官方模拟 RPA 服务脚本路径（可能不存在，由调用方判断）"""
    return os.path.join(official_data_dir(), "05_RPA接口文档", "mock_rpa_server.py")


# 载入 .env（放在模块导入时，保证后续 os.environ 取到值）
load_dotenv()
