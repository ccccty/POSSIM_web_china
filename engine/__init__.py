# -*- coding: utf-8 -*-
"""POSSIM-Web 计算内核（S2）。

对外唯一入口：
    from engine.api import simulate
    result = simulate(payload_dict)
"""

from .api import simulate   # noqa: F401

__all__ = ["simulate"]
__version__ = "1.0.0"
