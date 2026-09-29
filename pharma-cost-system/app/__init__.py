# -*- coding: utf-8 -*-
"""应用包初始化。

支持"项目内依赖（vendor）"模式：若项目根目录存在 `vendor/`，自动加入 sys.path。
适用受限环境——无法写入 Python 的 site-packages，或需要免联网部署时，可执行：

    python scripts/fetch_vendor.py     # 把 reportlab / pillow / matplotlib 解包到 vendor/

这样无需真正 pip install 也能启用质量更好的图表引擎与 reportlab 原生 PDF 排版引擎。
"""
import os as _os
import sys as _sys

_VENDOR = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "vendor")
if _os.path.isdir(_VENDOR) and _VENDOR not in _sys.path:
    _sys.path.insert(0, _VENDOR)
