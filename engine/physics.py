# -*- coding: utf-8 -*-
"""逐像素风暴潮（契约 §5，POSSIM 同构，不做时间积分）。

一个时刻、一个像素的完整链路：
  1) 局地切平面上算 r(km) 与相对台风中心的东/北偏移（避免每帧 1e6 次三角函数）
  2) 气压亏损 dP = Penv - Pc，Holland 径向气压剖面给出局地亏损 dP_loc
  3) Holland 梯度风 W（用观测 Vmax 归一化）
  4) 移行非对称 k_m（北半球右侧增强；cos(theta) 直接由偏移分量算出，不用 atan2）
  5) 增水三项：反气压 h_p + 风增水 h_w + 波增水 h_wv，乘 coarse_amp 得总增水 h
所有量都是 (ny, nx) 的 numpy 数组，一帧内没有任何 Python 逐像素循环。
"""

from __future__ import annotations

import math

import numpy as np

from . import config as C

DEG_M = math.pi * C.R_EARTH / 180.0      # 1 度弧长（m）


# ------------------------------------------------------------------ 网格
class Grid(object):
    """等经纬网格。行序自北向南（北 = 第 0 行，契约 §2）。"""

    def __init__(self, nx: int, ny: int, bbox):
        self.nx = int(nx)
        self.ny = int(ny)
        w, s, e, n = [float(v) for v in bbox]
        self.bbox = (w, s, e, n)
        self.dlon = (e - w) / self.nx
        self.dlat = (n - s) / self.ny
        self.lon1d = w + (np.arange(self.nx, dtype=np.float64) + 0.5) * self.dlon
        self.lat1d = n - (np.arange(self.ny, dtype=np.float64) + 0.5) * self.dlat
        lat2 = np.broadcast_to(self.lat1d[:, None], (self.ny, self.nx))
        lon2 = np.broadcast_to(self.lon1d[None, :], (self.ny, self.nx))
        self.lat2 = np.ascontiguousarray(lat2, dtype=np.float32)
        self.lon2 = np.ascontiguousarray(lon2, dtype=np.float32)
        # 纬向弧长因子用参考纬度一次性给出（局部切平面近似，误差 < 1%）
        self.lat_ref_cos = float(math.cos(math.radians((n + s) * 0.5)))
        self.center = ((n + s) * 0.5, (e + w) * 0.5)       # (lat, lon)
        self.pixel_area_km2 = None

    def to_rowcol(self, lat: float, lon: float):
        w, s, e, n = self.bbox
        col = int(math.floor((lon - w) / self.dlon))
        row = int(math.floor((n - lat) / self.dlat))
        col = min(max(col, 0), self.nx - 1)
        row = min(max(row, 0), self.ny - 1)
        return row, col


# ------------------------------------------- 局地切平面偏移（无三角函数）
def local_offset(grid: Grid, lat0: float, lon0: float):
    """返回 (dy_m 向北, dx_m 向东, r_m)；r 用局地切平面近似。"""
    dy = (grid.lat2 - np.float32(lat0)) * np.float32(DEG_M)
    kx = np.float32(DEG_M * math.cos(math.radians(lat0)))
    dx = (grid.lon2 - np.float32(lon0)) * kx
    r2 = dy * dy
    r2 += dx * dx
    r_m = np.sqrt(r2, dtype=np.float32)
    return dy, dx, r_m


def great_circle_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """两点大圆距离（km），标量。"""
    la1 = math.radians(lat1)
    la2 = math.radians(lat2)
    dla = la2 - la1
    dlo = math.radians(lon2 - lon1)
    h = math.sin(dla * 0.5) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlo * 0.5) ** 2
    h = min(1.0, max(0.0, h))
    return 2.0 * C.R_EARTH * math.asin(math.sqrt(h)) / 1000.0


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """从点1指向点2的初始方位角（北=0，顺时针，契约 §3.3）。"""
    la1 = math.radians(lat1)
    la2 = math.radians(lat2)
    dlo = math.radians(lon2 - lon1)
    y = math.sin(dlo) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(dlo)
    return math.degrees(math.atan2(y, x)) % 360.0


# ------------------------------------------------------------------ 风场
def theory_vmax_ms(dP: float, b: float) -> float:
    """Holland 理论最大风速 Vmax = sqrt(B*dP*100/(rho_a*e))。"""
    if dP <= 0.0:
        return 0.0
    return math.sqrt(max(b * dP * 100.0 / (C.RHO_AIR * C.EULER), 0.0))


def holland_wind(dP: float, b: float, r_km, rm_km: float, sinlat):
    """Holland 梯度风（m/s）与气压剖面量 q=(Rm/r)^B。

    W = sqrt(B*dP*100/rho_a*q*exp(-q) + (0.5*f*r)^2) - 0.5*f*r
    """
    rm = np.maximum(r_km * np.float32(1000.0), np.float32(1000.0))
    rmax_m = np.float32(max(rm_km, 1.0) * 1000.0)
    q = rmax_m / rm
    np.log(q, out=q)
    q *= np.float32(b)
    np.exp(q, out=q)                     # q = (Rm/r)^B
    f = np.float32(2.0 * C.OMEGA) * sinlat
    frm = f * rm
    frm *= np.float32(0.5)               # 0.5*f*r
    root = q * np.exp(-q, dtype=np.float32)
    root *= np.float32(b * dP * 100.0 / C.RHO_AIR)
    root += frm * frm
    np.maximum(root, 0.0, out=root)
    np.sqrt(root, out=root)
    root -= frm
    root = np.maximum(root, 0.0, out=root)
    return root, q


def asymmetry_cos(dy, dx, r_m, heading_deg: float):
    """cos(theta) = cos(brg - heading)，theta 为方位角相对移向的偏角。"""
    h = math.radians(heading_deg)
    denom = np.maximum(r_m, np.float32(1.0))
    ct = dy * np.float32(math.cos(h))
    ct += dx * np.float32(math.sin(h))
    ct /= denom
    return ct


def asymmetry_factor(cos_theta, motion_kt: float):
    """移行非对称系数（契约 §5.3b）：|theta|<=90 右侧增强，否则左侧。"""
    m = np.float32(min(max(float(motion_kt), 0.0), 50.0) / 50.0)
    k_right = np.float32(1.0) + np.float32(0.35) * m * cos_theta
    k_left = np.float32(1.0) - np.float32(0.25) * m * cos_theta
    k = np.where(cos_theta >= np.float32(0.0), k_right, k_left)
    return np.clip(k, np.float32(0.6), np.float32(1.5)).astype(np.float32)


# ------------------------------------------------- 增水分项（可独立标定）
def wind_setup(w, h_ref, fetch_m: float = C.FETCH_REF_M):
    """风增水 h_w（m）。

    契约 §5.4 结构：(Cd*rho_a/rho_w) * W^2/(g*h_ref) * fetch/20000，
    Cd = (0.8+0.065W)*1e-3 封顶 2.5e-3；标定系数 WIND_CAL 见 config。
    """
    w = np.asarray(w, dtype=np.float32)
    h_ref = np.asarray(h_ref, dtype=np.float32)
    cd = np.minimum((np.float32(0.8) + np.float32(0.065) * w) * np.float32(1e-3), np.float32(2.5e-3))
    base = cd * np.float32(C.RHO_AIR / C.RHO_WATER) / (np.float32(C.G) * h_ref)
    base *= w
    base *= w
    base *= np.float32(C.WIND_CAL * fetch_m / C.FETCH_REF_M)
    return base.astype(np.float32)


def wave_setup(w):
    """波增水 h_wv = 0.15*Hs，仅 W>15 m/s 生效；Hs = min(9.0, 0.0025*W^2/g)。"""
    w = np.asarray(w, dtype=np.float32)
    hs = w * w
    hs *= np.float32(0.0025 / C.G)
    np.minimum(hs, np.float32(9.0), out=hs)
    hs *= np.float32(0.15)
    return np.where(w > np.float32(15.0), hs, np.float32(0.0)).astype(np.float32)


# ------------------------------------------------- 一帧的完整增水场
def surge_field(grid: Grid, dem, st: dict, params: dict):
    """计算一帧的逐像素增水场。

    st  : {"lat","lon","pc_mb","vmax_kt","motion_kt","heading_deg"}
    params: {"env_pressure","holland_b","rmax_km","coarse_amp",...}
    返回 dict：h(总增水 m)、w(格点风速 m/s)、w_max、r_min_km、dP
    """
    env = float(params["env_pressure"])
    b = float(params["holland_b"])
    rm_km = float(params["rmax_km"])
    amp = float(params["coarse_amp"])
    dP = max(env - float(st["pc_mb"]), 0.0)

    lat0 = float(st["lat"])
    lon0 = float(st["lon"])
    dy, dx, r_m = local_offset(grid, lat0, lon0)
    r_km = r_m * np.float32(1e-3)

    # 3) Holland 梯度风 + 用观测 Vmax 归一化（Holland 场峰值对齐 Vmax）
    w, q = holland_wind(dP, b, np.maximum(r_km, np.float32(0.3)), rm_km,
                        np.float32(2.0 * C.OMEGA) * np.sin(np.float32(math.radians(lat0)),
                                                            dtype=np.float32))
    vmax_ms = max(float(st["vmax_kt"]), 0.0) * C.KT_TO_MS
    v_th = theory_vmax_ms(dP, b)
    if v_th > 1.0 and vmax_ms > 0.0:
        w *= np.float32(min(max(vmax_ms / v_th, 0.3), 2.5))
    if vmax_ms > 0.0:
        np.minimum(w, np.float32(vmax_ms * 1.15), out=w)      # 契约 §5.2 风速封顶
    # 4) 移行非对称
    ct = asymmetry_cos(dy, dx, r_m, float(st["heading_deg"]))
    w *= asymmetry_factor(ct, float(st["motion_kt"]))
    del ct

    # 5) 三项增水
    dP_loc = np.float32(1.0) - np.exp(-q, dtype=np.float32)
    dP_loc *= np.float32(dP * 100.0 / (C.RHO_WATER * C.G))
    h_ref = np.abs(dem)
    h_ref += np.float32(10.0)
    np.maximum(h_ref, np.float32(10.0), out=h_ref)
    h = dP_loc
    h += wind_setup(w, h_ref, C.FETCH_EFF_M)
    h += wave_setup(w)
    # 软限幅：浅水漫滩后增水不再随风应力线性增长，同时保证 §5 红线不被击穿
    cap = np.float32(C.SURGE_RAW_CAP)
    h /= -cap
    np.exp(h, out=h)
    h -= np.float32(1.0)
    h *= np.float32(-cap * amp)

    return {
        "h": h,
        "w": w,
        "w_max": float(np.max(w)),
        "r_min_km": float(np.min(r_km)),
        "dP": dP,
        "vmax_ms": vmax_ms,
    }


# ------------------------------------------------- 四象限风圈插值（展示统计用）
def quadrant_radius(radii, brg_deg):
    """r34/r50/r64 四象限( NE,SE,SW,NW )按方位角线性插值。"""
    try:
        r = [float(v) for v in radii]
    except (TypeError, ValueError):
        return None
    if len(r) < 4:
        return None
    centers = np.array([45.0, 135.0, 225.0, 315.0])
    vals = np.array(r[:4])
    ang = (np.asarray(brg_deg, dtype=np.float32) + 360.0) % 360.0
    ang_ext = np.concatenate([centers - 360.0, centers, centers + 360.0])
    val_ext = np.concatenate([vals, vals, vals])
    return np.interp(ang, ang_ext, val_ext).astype(np.float32)
