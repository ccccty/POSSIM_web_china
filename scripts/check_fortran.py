# -*- coding: utf-8 -*-
"""对比「原 POSSIM Fortran 内核」与「numpy 复算」，可反复运行的验收脚本。

用法：
    python scripts/check_fortran.py            # 两个工况的增水对比
    python scripts/check_fortran.py --all      # 额外测 37 帧并行耗时

只读：不改 data/，不写文件。
"""
import argparse
import os
import sys
import time
from datetime import timedelta

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from engine import api as A, fortran, physics, tide as T  # noqa: E402


def load_site():
    warns = []
    meta = A._load_meta(warns)
    dem, _ = A._load_dem(meta, warns)
    ny, nx = dem.shape
    w, s, e, n = meta["site"]["bbox"]
    gt = (w, (e - w) / nx, 0.0, n, 0.0, -(n - s) / ny)
    params = A._merge_params(meta, {})
    return meta, dem, nx, ny, gt, params


def mangkhut_frames(meta, dem, n=37, step=1.0):
    warns = []
    typhoon, _ = A._load_typhoon("mangkhut", {}, warns)
    pts = A._track_series(typhoon)
    t0 = A._frame_start(typhoon, pts, n, step, A._grid_center(meta, dem))
    times = [t0 + timedelta(hours=i * step) for i in range(n)]
    return A._build_states(pts, times, {}), times


CENTER = {"lat": 22.6, "lon": 113.7, "pc_mb": 900, "vmax_kt": 110, "motion_kt": 20,
          "heading_deg": 300, "r34": [180, 165, 145, 175], "r50": [100, 90, 78, 98],
          "r64": [55, 48, 40, 52]}


def compare(meta, dem, nx, ny, gt, params, st, tide_m, label):
    grid = physics.Grid(nx, ny, tuple(meta["site"]["bbox"]))
    row = {"label": label, "tide": round(tide_m, 3),
           "numpy": float(physics.surge_field(grid, dem, st, params)["h"].max())}
    ok, msg = fortran.available()
    if not ok:
        row["note"] = msg
        return row
    row["f1"] = fortran.surge_field(dem, nx, ny, gt, st, params, tide_m, amp=1.0)["max_surge"]
    row["f38"] = fortran.surge_field(dem, nx, ny, gt, st, params, tide_m, amp=3.8)["max_surge"]
    row["dist"] = fortran.surge_field(dem, nx, ny, gt, st, params, tide_m, amp=1.0)["storm_distance_km"]
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="额外跑 37 帧测并行耗时")
    args = ap.parse_args()

    meta, dem, nx, ny, gt, params = load_site()
    print("DEM %dx%d  pixel %.1f m  gt[1]=%.8f°"
          % (nx, ny, meta["site"]["pixel_m"], gt[1]))
    print("原 C# 的 3.8 倍判据：|gt[1]| > 0.001° -> %s"
          % ("触发" if abs(gt[1]) > 0.001 else "不触发（这份 94 m DEM 在原软件里不放大）"))

    states, times = mangkhut_frames(meta, dem)
    k = int(np.argmax([s["r34"][0] if s.get("r34") else 0 for s in states]))  # 占位，下面用固定帧
    k = 24
    tide_k = T.tide_level(T.hours_since_epoch(times[k]), params["tide"])

    rows = [compare(meta, dem, nx, ny, gt, params, CENTER, 0.0, "台风压中心 pc900/v110"),
            compare(meta, dem, nx, ny, gt, params, states[k], tide_k, "山竹 %s" % T.iso_z(times[k]))]

    print("\n%-26s %9s %11s %11s %9s" % ("工况", "numpy", "内核×1.0", "内核×3.8", "中心距"))
    for r in rows:
        if "f1" not in r:
            print("%-26s %9.4f %11s" % (r["label"], r["numpy"], "DLL缺失：" + r.get("note", "")))
            continue
        print("%-26s %9.4f %11.4f %11.4f %9.0f" % (r["label"], r["numpy"], r["f1"], r["f38"], r["dist"]))
    print("\n单位 m（最大增水）。numpy 列按 meta 默认 coarse_amp=3.8；内核两列分别是不放大与放大 3.8。")

    if args.all:
        tides = [T.tide_level(T.hours_since_epoch(t), params["tide"]) for t in times]
        t0 = time.perf_counter()
        fortran.surge_fields(dem, nx, ny, gt, states, tides, params, amp=3.8, workers=8)
        print("\n内核 37 帧并行流水线耗时：%.2f s" % (time.perf_counter() - t0))


if __name__ == "__main__":
    main()
