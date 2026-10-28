# -*- coding: utf-8 -*-
"""天文潮分潮叠加（契约 §4 tide.py）。

eta(h) = sum_i A_i * cos(w_i * h + phi_i)
  h : 自 2000-01-01T00:00Z 起的小时数（可为浮点）
  w : 度/小时（M2 28.9841042 / S2 30.0 / K1 15.0410686 / O1 13.9430356）
  phi : 度
全程只做标量运算（一帧只有一个潮位），numpy 不是必需，但保持同一套常数。
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from . import config


def parse_utc(text: str) -> datetime:
    """解析契约里的 ISO8601 UTC 字符串（形如 2018-09-15T00:00Z）。"""
    t = (text or "").strip()
    if not t:
        raise ValueError("台风路径点缺少时间字段")
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    dt = datetime.fromisoformat(t)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def epoch() -> datetime:
    return parse_utc(config.TIDE_EPOCH_ISO)


def hours_since_epoch(dt: datetime) -> float:
    """自 2000-01-01T00:00Z 起的小时数。"""
    delta = dt - epoch()
    return delta.total_seconds() / 3600.0


def tide_level(hours: float, components: dict | None = None) -> float:
    """分潮叠加求天文潮位（m）。components 形如 {"M2":[振幅m, 相位deg], ...}。"""
    comp = components or config.DEFAULTS["tide"]
    total = 0.0
    for name, amp_phase in comp.items():
        if name not in config.TIDE_OMEGA:
            continue
        try:
            amp = float(amp_phase[0])
            phase = float(amp_phase[1])
        except (TypeError, IndexError, ValueError):
            continue
        if not math.isfinite(amp) or not math.isfinite(phase):
            continue
        w = config.TIDE_OMEGA[name]
        total += amp * math.cos(math.radians(w * hours + phase))
    return total


def tide_at(dt: datetime, components: dict | None = None) -> float:
    return tide_level(hours_since_epoch(dt), components)


def tide_range(components: dict | None = None) -> float:
    """分潮振幅之和，即该潮组的理论上界（用于估算最大潮位）。"""
    comp = components or config.DEFAULTS["tide"]
    total = 0.0
    for name, amp_phase in comp.items():
        if name not in config.TIDE_OMEGA:
            continue
        try:
            total += abs(float(amp_phase[0]))
        except (TypeError, IndexError, ValueError):
            continue
    return total


def time_axis(start: datetime, hours: float, step_hours: float) -> list[datetime]:
    """按 step_hours 生成帧时刻（帧数 = hours/step + 1，契约 §6.2）。"""
    n = int(round(hours / step_hours))
    return [start + timedelta(hours=i * step_hours) for i in range(n + 1)]


def iso_z(dt: datetime) -> str:
    """输出形如 2018-09-15T00:00Z 的字符串。"""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
