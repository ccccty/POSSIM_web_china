# -*- coding: utf-8 -*-
"""把原厂 DLL 里导出的子公式扫出来（风增水 / 波增水 / 拖曳系数）。

这是《原内核接入说明.md》第三节那张公式表的取证脚本：证明原厂的风增水是
0.0291572×W 的线性项、波增水是 7.6e-4×W²、Cd 是分段线性，而不是照抄文档。

用法：
    python scripts/probe_kernel_physics.py

说明：DLL 里的模块级函数名带 gfortran 修饰（__storm_surge_physics_MOD_xxx），
参数全部按引用传（Fortran 约定），返回值走 xmm0（real(8)）。
"""
import ctypes
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from engine import fortran  # noqa: E402


def bind(lib, name, n):
    f = getattr(lib, name)
    f.restype = ctypes.c_double
    f.argtypes = [ctypes.POINTER(ctypes.c_double)] * n
    return lambda *a: f(*[ctypes.byref(ctypes.c_double(float(v))) for v in a])


def main():
    path, err = fortran.resolve()
    if not path:
        print("找不到 DLL：", err)
        return 1
    print("DLL:", path)
    lib = ctypes.CDLL(path)
    wind = bind(lib, "__storm_surge_physics_MOD_calculate_wind_setup", 3)
    wave = bind(lib, "__storm_surge_physics_MOD_calculate_wave_setup", 4)
    drag = bind(lib, "__storm_surge_physics_MOD_get_wind_drag_coefficient", 1)

    print("\n=== 风拖曳系数 Cd(W) 的拐点 ===")
    prev = None
    for i in range(2, 121):
        w = i * 0.5
        v = drag(w)
        if prev is None or abs(v - prev) > 1e-9:
            print("  W=%5.1f m/s   Cd=%.6e" % (w, v))
        prev = v

    print("\n=== 风增水 h_w(W, depth, fetch) —— 只跟 W 有关 ===")
    print("  %-8s %10s %10s %10s" % ("W(m/s)", "fetch=1km", "fetch=20km", "depth=2m"))
    for W in (10, 20, 30, 40, 50, 60):
        print("  %-8.0f %10.4f %10.4f %10.4f"
              % (W, wind(W, 20.0, 1000.0), wind(W, 20.0, 20000.0), wind(W, 2.0, 20000.0)))
    print("  线性系数 k = h_w(50)/50 = %.7f  (m per m/s)" % (wind(50.0, 20.0, 20000.0) / 50.0))

    print("\n=== 波增水 h_wv(W, depth, fetch, runup) ===")
    prev = None
    for i in range(120, 400):
        w = i / 10.0
        v = wave(w, 20.0, 20000.0, 0.0)
        if prev is None or (prev == 0) != (v == 0):
            print("  起算阈值附近：W=%.1f -> %.5f" % (w, v))
        prev = v
    for W in (20, 30, 40, 50):
        print("  W=%2.0f  runup=0 -> %.4f   runup=1.0 -> %.4f   runup=1.15 -> %.4f"
              % (W, wave(W, 20.0, 20000.0, 0.0), wave(W, 20.0, 20000.0, 1.0), wave(W, 20.0, 20000.0, 1.15)))
    print("  二次系数 k = h_wv(50,runup=0)/50^2 = %.6e" % (wave(50.0, 20.0, 20000.0, 0.0) / 2500.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
