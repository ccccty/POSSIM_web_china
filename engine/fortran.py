# -*- coding: utf-8 -*-
"""原 POSSIM 3.0 的 Fortran 计算内核驱动（ctypes，不依赖 .NET / WPF / GDAL）。

背景
----
POSSIM 3.0 的物理内核是编译好的 `Engines/storm_surge_physics.dll`（gfortran 静态链接，
只依赖 KERNEL32 + msvcrt，导出 `compute_surge_fortran`）。原 C# 前端用 P/Invoke 调它，
签名在 `Engines/StormSurgeEngine.cs:123`。本模块把同一份二进制直接接到 Web 后端，
让大屏可以用**原内核**出图，而不是 numpy 复算。

签名（照抄 C# 的 DllImport，CallingConvention.Cdecl，标量全部按引用传）：

    compute_surge_fortran(
        int* nx, int* ny, float* dem, double* gt[6],
        float* surge_out, float* total_water_out,
        float* max_surge, float* max_total_water,
        double* center_lat, double* center_lon, double* pressure_mb,
        double* max_wind_kt, double* rmax_km, double* b_param,
        double* motion_kt, double* heading_deg,
        double* r34[4], double* r50[4], double* r64[4],
        double* tide_height, int* use_projected,
        double* storm_x, double* storm_y, double* storm_distance_km)

实测（本机）：
  * 单帧 1188x864 = 102.6 万像元，约 0.69 s；
  * 内核无全局可写状态，多线程并发结果与串行**逐位相同**（8 线程加速 4.1x）。
"""
from __future__ import annotations

import ctypes
import glob
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

DLL_NAME = "storm_surge_physics.dll"

_lock = threading.RLock()   # 注意：_load() 持锁期间会调用 resolve()，必须可重入
_lib = None
_fn = None
_state = {"tried": False, "path": None, "error": None}

_c_double_p = ctypes.POINTER(ctypes.c_double)
_c_float_p = ctypes.POINTER(ctypes.c_float)
_c_int_p = ctypes.POINTER(ctypes.c_int)


def _candidates():
    """按优先级找 DLL：环境变量 → 迁移包同级 01 目录 → 项目内 Engines/ → 上级目录全局搜索。"""
    env = os.environ.get("POSSIM_FORTRAN_DLL")
    if env:
        yield env
    for base in (os.path.dirname(ROOT), ROOT, os.path.dirname(os.path.dirname(ROOT))):
        yield os.path.join(base, "01-POSSIM3.0-原工程", "Engines", DLL_NAME)
        yield os.path.join(base, "Engines", DLL_NAME)
        yield os.path.join(base, DLL_NAME)


def resolve():
    """返回 (dll 路径 或 None, 说明)。只做一次探测，结果缓存。"""
    if _state["tried"]:
        return _state["path"], _state["error"] or ""
    with _lock:
        if _state["tried"]:
            return _state["path"], _state["error"] or ""
        _state["tried"] = True
        for p in _candidates():
            try:
                if p and os.path.isfile(p):
                    _state["path"] = os.path.abspath(p)
                    return _state["path"], ""
            except OSError:
                continue
        # 兜底：从迁移包根目录往下搜一次（层级不同也能找到）
        for base in (os.path.dirname(ROOT), os.path.dirname(os.path.dirname(ROOT))):
            try:
                hits = glob.glob(os.path.join(base, "**", DLL_NAME), recursive=True)
            except OSError:
                hits = []
            if hits:
                _state["path"] = os.path.abspath(hits[0])
                return _state["path"], ""
        _state["error"] = ("未找到原内核 %s：把 01-POSSIM3.0-原工程 与本项目放在同一目录，"
                           "或用环境变量 POSSIM_FORTRAN_DLL 指定路径" % DLL_NAME)
        return None, _state["error"]


def _load():
    global _lib, _fn
    if _fn is not None:
        return _fn
    with _lock:
        if _fn is not None:
            return _fn
        path, err = resolve()
        if not path:
            raise RuntimeError(err)
        lib = ctypes.CDLL(path)
        fn = lib.compute_surge_fortran
        fn.restype = None
        fn.argtypes = [
            _c_int_p, _c_int_p,
            _c_float_p,
            _c_double_p,
            _c_float_p, _c_float_p,
            _c_float_p, _c_float_p,
            _c_double_p, _c_double_p,
            _c_double_p, _c_double_p,
            _c_double_p, _c_double_p,
            _c_double_p, _c_double_p,
            _c_double_p, _c_double_p, _c_double_p, _c_double_p,
            _c_double_p, _c_double_p, _c_double_p, _c_double_p,
            _c_double_p, _c_double_p, _c_double_p, _c_double_p,
            _c_double_p,
            _c_int_p,
            _c_double_p, _c_double_p,
            _c_double_p,
        ]
        _lib, _fn = lib, fn
        return _fn


def available():
    """(是否可用, 说明)。不抛异常，供 /api/health 与前端探测。"""
    try:
        _load()
        path, _ = resolve()
        return True, path or ""
    except Exception as exc:                       # DLL 缺失 / 位数不符 / 缺依赖
        return False, str(exc)


def auto_amp(gt):
    """还原原 C# 的粗网格放大判据：|gt[1]| > 0.001°（约 100 m）才乘 3.8。

    见 Engines/StormSurgeEngine.cs:254。返回 (amp, 是否触发)。
    """
    try:
        px = abs(float(gt[1]))
    except Exception:
        return 1.0, False
    return (3.8, True) if px > 0.001 else (1.0, False)


def surge_field(dem, nx, ny, gt, st, params, tide_m, amp=1.0):
    """调一次内核，返回单帧增水场。

    dem : float32[ny, nx]，行序自北向南（与原软件读 GeoTIFF 的顺序一致）
    gt  : GDAL 六参数 (x0, dx, 0, y0, 0, dy)
    st  : 帧状态 {lat, lon, pc_mb, vmax_kt, motion_kt, heading_deg, r34, r50, r64}
    """
    fn = _load()
    n = nx * ny
    dem_c = np.ascontiguousarray(dem.reshape(-1), dtype=np.float32)
    gt_c = (ctypes.c_double * 6)(*[float(v) for v in gt])
    surge = np.zeros(n, dtype=np.float32)
    twl = np.zeros(n, dtype=np.float32)

    c_nx = ctypes.c_int(int(nx))
    c_ny = ctypes.c_int(int(ny))
    mx = ctypes.c_float(0.0)
    mt = ctypes.c_float(0.0)

    def rad(name):
        v = st.get(name) or [0, 0, 0, 0]
        return [ctypes.c_double(float(x)) for x in (list(v) + [0, 0, 0, 0])[:4]]

    r34, r50, r64 = rad("r34"), rad("r50"), rad("r64")
    c_lat = ctypes.c_double(float(st["lat"]))
    c_lon = ctypes.c_double(float(st["lon"]))
    c_pc = ctypes.c_double(float(st["pc_mb"]))
    c_vmax = ctypes.c_double(float(st["vmax_kt"]))
    c_rmax = ctypes.c_double(float(params.get("rmax_km") or 40.0))
    c_b = ctypes.c_double(float(params.get("holland_b") or 1.7))
    c_mot = ctypes.c_double(float(st.get("motion_kt") or 0.0))
    c_hdg = ctypes.c_double(float(st.get("heading_deg") or 0.0))
    c_tide = ctypes.c_double(float(tide_m))
    c_proj = ctypes.c_int(0)
    c_sx = ctypes.c_double(0.0)
    c_sy = ctypes.c_double(0.0)
    c_dist = ctypes.c_double(0.0)

    fn(ctypes.byref(c_nx), ctypes.byref(c_ny),
       dem_c.ctypes.data_as(_c_float_p),
       gt_c,
       surge.ctypes.data_as(_c_float_p),
       twl.ctypes.data_as(_c_float_p),
       ctypes.byref(mx), ctypes.byref(mt),
       ctypes.byref(c_lat), ctypes.byref(c_lon),
       ctypes.byref(c_pc), ctypes.byref(c_vmax),
       ctypes.byref(c_rmax), ctypes.byref(c_b),
       ctypes.byref(c_mot), ctypes.byref(c_hdg),
       ctypes.byref(r34[0]), ctypes.byref(r34[1]), ctypes.byref(r34[2]), ctypes.byref(r34[3]),
       ctypes.byref(r50[0]), ctypes.byref(r50[1]), ctypes.byref(r50[2]), ctypes.byref(r50[3]),
       ctypes.byref(r64[0]), ctypes.byref(r64[1]), ctypes.byref(r64[2]), ctypes.byref(r64[3]),
       ctypes.byref(c_tide), ctypes.byref(c_proj),
       ctypes.byref(c_sx), ctypes.byref(c_sy),
       ctypes.byref(c_dist))

    h = surge.reshape(ny, nx)
    if amp != 1.0:
        h = h * float(amp)
    return {
        "h": h,
        "max_surge": float(mx.value) * float(amp),
        "max_twl": float(mt.value) * float(amp),
        "storm_distance_km": float(c_dist.value),
    }


def surge_fields(dem, nx, ny, gt, states, tides, params, amp=1.0, workers=8):
    """多帧并行（线程）。内核无可写全局状态，实测并发与串行逐位一致。"""
    _load()

    def one(i):
        return surge_field(dem, nx, ny, gt, states[i], params, tides[i], amp)

    workers = max(1, min(int(workers or 1), len(states) or 1))
    if workers == 1 or len(states) < 3:
        return [one(i) for i in range(len(states))]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(one, range(len(states))))
