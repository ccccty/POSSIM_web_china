# -*- coding: utf-8 -*-
"""engine.api · 唯一对外入口 simulate(payload) -> dict（契约 §4.1 / §6.1 / §6.2）。

内部绝不把异常抛给 HTTP 层：任何失败都返回 {"error": "中文说明"}。
数据缺失时自我降级（合成 DEM / 内置台风路径 / 内置示意村点），并在返回里给 warn。
"""

from __future__ import annotations

import copy
import json
import math
import time
from datetime import timedelta
from pathlib import Path

import numpy as np

from . import config as C
from . import fortran, inundation, physics, render, tide as tide_mod

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


# ================================================================ 数据装载
def _read_json(path: Path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_meta(warns):
    path = DATA_DIR / "meta.json"
    if not path.exists():
        warns.append("meta_missing")
        return {}
    try:
        return _read_json(path)
    except Exception:
        warns.append("meta_broken")
        return {}


def _synthetic_dem(nx: int, ny: int, bbox):
    """data/dem.npy 缺失时的降级地形：珠江口漏斗状河口 + 两侧三角洲平原 + 东部山地。"""
    w, s, e, n = [float(v) for v in bbox]
    lon = w + (np.arange(nx, dtype=np.float64) + 0.5) * (e - w) / nx
    lat = n - (np.arange(ny, dtype=np.float64) + 0.5) * (n - s) / ny
    lon2 = np.broadcast_to(lon[None, :], (ny, nx))
    lat2 = np.broadcast_to(lat[:, None], (ny, nx))
    # 河口半宽：南宽北窄的漏斗
    half = 0.10 + 0.42 * np.clip((22.92 - lat2) / 0.66, 0.0, 1.0)
    s_chan = np.abs(lon2 - 113.70) - half          # >0 即在河岸之外
    s_sea = lat2 - 22.36                           # >0 即在开阔海域以北
    inland = np.minimum(s_chan, s_sea)             # 到水体的"内陆度"（度）
    land = np.maximum(inland, 0.0)
    elev = 0.30 + 120.0 * np.power(land, 1.6)
    # 东部山地（大帽山 / 梧桐山）
    for (mlat, mlon, amp, sig) in ((22.42, 114.14, 620.0, 0.075), (22.58, 114.19, 560.0, 0.065),
                                   (22.30, 114.02, 300.0, 0.05)):
        elev = elev + amp * np.exp(-(((lat2 - mlat) ** 2 + (lon2 - mlon) ** 2) / (2 * sig * sig)))
    # 确定性纹理（不依赖随机种子）
    elev = elev + 0.8 * np.sin(lon2 * 61.0) * np.cos(lat2 * 47.0)
    dem = np.where(inland > 0.0, np.maximum(elev, 0.02),
                   -3.0 - 30.0 * np.maximum(-inland, 0.0))
    return dem.astype(np.float32)


def _load_dem(meta, warns):
    path = DATA_DIR / "dem.npy"
    if path.exists():
        try:
            dem = np.load(path)
            if dem.ndim != 2 or dem.size < 1000:
                raise ValueError("dem.npy 形状异常")
            return np.ascontiguousarray(dem, dtype=np.float32), False
        except Exception:
            warns.append("dem_broken")
    site = meta.get("site") or {}
    nx = int(site.get("nx") or C.DEFAULT_NX)
    ny = int(site.get("ny") or C.DEFAULT_NY)
    bbox = site.get("bbox") or C.DEFAULT_BBOX
    warns.append("dem_missing")
    return _synthetic_dem(nx, ny, bbox), True


def _load_typhoon(typhoon_id: str, payload: dict, warns):
    inline = payload.get("_typhoon")
    if isinstance(inline, dict) and inline.get("track"):
        return inline, False
    tid = str(typhoon_id or "mangkhut")
    path = DATA_DIR / "typhoons" / (tid + ".json")
    if path.exists():
        try:
            return _read_json(path), False
        except Exception:
            warns.append("typhoon_broken")
    if tid in C.FALLBACK_TRACKS:
        warns.append("typhoon_missing")
        return copy.deepcopy(C.FALLBACK_TRACKS[tid]), True
    warns.append("typhoon_missing")
    return copy.deepcopy(C.FALLBACK_TRACKS["mangkhut"]), True


def _load_villages(meta, warns):
    if isinstance(meta.get("villages"), list) and meta["villages"]:
        return meta["villages"], True
    path = DATA_DIR / "villages.json"
    if path.exists():
        try:
            arr = _read_json(path)
            if isinstance(arr, list) and arr:
                return arr, True
        except Exception:
            warns.append("villages_broken")
    warns.append("villages_fallback")
    return copy.deepcopy(C.FALLBACK_VILLAGES), False


# ================================================================ 参数合并
def _merge_params(meta: dict, payload: dict):
    params = copy.deepcopy(C.DEFAULTS)
    md = meta.get("defaults") if isinstance(meta.get("defaults"), dict) else {}
    for key in ("env_pressure", "holland_b", "rmax_km", "wave_runup", "coarse_amp"):
        if md.get(key) is not None:
            try:
                params[key] = float(md[key])
            except (TypeError, ValueError):
                pass
    if isinstance(md.get("tide"), dict):
        params["tide"] = _merge_tide(params["tide"], md["tide"])

    src = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    for key in ("env_pressure", "holland_b", "rmax_km", "wave_runup", "coarse_amp"):
        if src.get(key) is not None:
            try:
                params[key] = float(src[key])
            except (TypeError, ValueError):
                pass
    if isinstance(src.get("tide"), dict):
        params["tide"] = _merge_tide(params["tide"], src["tide"])

    # 数值兜底，避免前端传入离谱值把模型搞崩
    params["env_pressure"] = min(max(params["env_pressure"], 850.0), 1060.0)
    params["holland_b"] = min(max(params["holland_b"], 0.5), 3.0)
    params["rmax_km"] = min(max(params["rmax_km"], 5.0), 200.0)
    params["wave_runup"] = min(max(params["wave_runup"], 0.5), 3.0)
    params["coarse_amp"] = min(max(params["coarse_amp"], 0.1), 10.0)
    return params


def _merge_tide(base: dict, override: dict):
    out = copy.deepcopy(base)
    for name, val in override.items():
        if name not in C.TIDE_OMEGA or val is None:
            continue
        try:
            out[name] = [float(val[0]), float(val[1])]
        except (TypeError, IndexError, ValueError):
            continue
    return out


# ================================================================ 路径插值
def _track_series(typhoon: dict):
    pts = []
    for p in typhoon.get("track") or []:
        dt = tide_mod.parse_utc(p.get("t", ""))
        pts.append({
            "dt": dt,
            "lat": float(p.get("lat", 0.0)),
            "lon": float(p.get("lon", 0.0)),
            "pc_mb": float(p.get("pc_mb", 1000.0)),
            "vmax_kt": float(p.get("vmax_kt", 0.0)),
            "r34": p.get("r34"), "r50": p.get("r50"), "r64": p.get("r64"),
        })
    if not pts:
        raise ValueError("台风路径为空")
    pts.sort(key=lambda x: x["dt"])
    return pts


def _interp_at(pts, key, hours):
    xs = np.array([(p["dt"] - pts[0]["dt"]).total_seconds() / 3600.0 for p in pts], dtype=np.float64)
    ys = np.array([p[key] for p in pts], dtype=np.float64)
    if xs.size == 1:
        return float(ys[0])
    return float(np.interp(hours, xs, ys))


def _grid_center(meta: dict, dem):
    site = meta.get("site") or {}
    bbox = site.get("bbox") or C.DEFAULT_BBOX
    return ((bbox[1] + bbox[3]) * 0.5, (bbox[0] + bbox[2]) * 0.5)


def _anchor_time(typhoon: dict, pts, center):
    """推演窗口的锚点时刻：优先取登陆时刻，否则取离 DEM 中心最近时刻。

    契约没有规定窗口起点。真实路径往往比请求时长长的多（如 mangkhut 的路径
    从 09-14T18Z 到 09-16T21Z，共 51 h），若一律从路径首点起算，36 h 的窗口
    会在台风还在远海时就结束，推演结果全是 0。因此把"最强影响时刻"对准窗口
    中线：既能覆盖登陆前的逼近过程，也能看到登陆后的退水。
    """
    t0, t1 = pts[0]["dt"], pts[-1]["dt"]
    landfall = typhoon.get("landfall")
    if isinstance(landfall, dict) and landfall.get("t"):
        try:
            tf = tide_mod.parse_utc(landfall["t"])
            if t0 <= tf <= t1:
                return tf
        except Exception:
            pass
    # 退化：逐小时搜索离 DEM 中心最近的时刻
    best_t, best_d = t0, 1e18
    span = (t1 - t0).total_seconds() / 3600.0
    for k in range(int(span) + 1):
        t = t0 + timedelta(hours=k)
        la = _interp_at(pts, "lat", k)
        lo = _interp_at(pts, "lon", k)
        d = physics.great_circle_km(la, lo, center[0], center[1])
        if d < best_d:
            best_d, best_t = d, t
    return best_t


def _frame_start(typhoon: dict, pts, n_frames: int, step_hours: float, grid_center):
    """窗口起点：让锚点时刻落在帧序列中间，并限制在路径时间范围内。"""
    anchor = _anchor_time(typhoon, pts, grid_center)
    lead = (n_frames - 1) * step_hours * 0.5
    t0, t1 = pts[0]["dt"], pts[-1]["dt"]
    start = anchor - timedelta(hours=lead)
    # 窗口尽量落在路径时间范围内：优先"锚点居中"，放不下就贴住路径末点
    latest = t1 - timedelta(hours=(n_frames - 1) * step_hours)
    if start > latest:
        start = latest
    if start < t0:
        start = t0
    if start > t1:
        start = t1
    return start


def _build_states(pts, frame_times, overrides: dict):
    """把 3~6 小时间隔的路径点插值到逐帧（位置/气压/风速线性，移速移向由差分算）。"""
    t0 = pts[0]["dt"]
    states = []
    for ft in frame_times:
        h = (ft - t0).total_seconds() / 3600.0
        lat = _interp_at(pts, "lat", h)
        lon = _interp_at(pts, "lon", h)
        pc = _interp_at(pts, "pc_mb", h)
        vmax = _interp_at(pts, "vmax_kt", h)
        # 移速/移向：用前后各 0.5 h 的位置做中心差分（北半球顺时针，0=正北）
        la_a = _interp_at(pts, "lat", h - 0.5)
        lo_a = _interp_at(pts, "lon", h - 0.5)
        la_b = _interp_at(pts, "lat", h + 0.5)
        lo_b = _interp_at(pts, "lon", h + 0.5)
        heading = physics.bearing_deg(la_a, lo_a, la_b, lo_b)
        dist_km = physics.great_circle_km(la_a, lo_a, la_b, lo_b)
        motion = dist_km / 1.0 / 1.852          # 1 kt = 1.852 km/h
        for key, val in overrides.items():
            if val is None:
                continue
            if key == "vmax_kt":
                vmax = float(val)
            elif key == "pc_mb":
                pc = float(val)
            elif key == "heading_deg":
                heading = float(val) % 360.0
            elif key == "motion_kt":
                motion = float(val)
        states.append({
            "utc": ft, "lat": lat, "lon": lon, "pc_mb": pc, "vmax_kt": vmax,
            "motion_kt": motion, "heading_deg": heading,
            "r34": pts[-1].get("r34"), "r50": pts[-1].get("r50"), "r64": pts[-1].get("r64"),
        })
    return states


# ================================================================ 主流程
def simulate(payload: dict) -> dict:
    t_start = time.perf_counter()
    try:
        return _run(payload or {}, t_start)
    except Exception as exc:            # 兜底：绝不把 traceback 抛给 HTTP 层
        return {"error": "推演失败：%s" % (exc,), "ok": False}


def _run(payload: dict, t_start: float) -> dict:
    warns: list[str] = []
    meta = _load_meta(warns)
    dem, dem_is_fake = _load_dem(meta, warns)
    ny, nx = int(dem.shape[0]), int(dem.shape[1])

    site = meta.get("site") or {}
    bbox = site.get("bbox") or C.DEFAULT_BBOX
    if dem_is_fake:
        bbox = site.get("bbox") or C.DEFAULT_BBOX
    try:
        pixel_m = float(site.get("pixel_m") or C.DEFAULT_PIXEL_M)
    except (TypeError, ValueError):
        pixel_m = C.DEFAULT_PIXEL_M

    typhoon_id = payload.get("typhoon_id") or "mangkhut"
    typhoon, _ = _load_typhoon(typhoon_id, payload, warns)
    params = _merge_params(meta, payload)

    md = meta.get("defaults") if isinstance(meta.get("defaults"), dict) else {}
    try:
        def_hours = float(md.get("hours") or 36.0)
        def_step = float(md.get("step_hours") or 1.0)
    except (TypeError, ValueError):
        def_hours, def_step = 36.0, 1.0
    try:
        hours = float(payload.get("hours") if payload.get("hours") is not None else def_hours)
        step_hours = float(payload.get("step_hours") if payload.get("step_hours") is not None else def_step)
    except (TypeError, ValueError):
        hours, step_hours = def_hours, def_step
    if not math.isfinite(hours) or hours <= 0:
        hours = 36.0
    if not math.isfinite(step_hours) or step_hours <= 0:
        step_hours = 1.0
    n_frames = int(round(hours / step_hours)) + 1
    if n_frames > C.MAX_FRAMES:
        n_frames = C.MAX_FRAMES
    if n_frames < 1:
        n_frames = 1

    pts = _track_series(typhoon)
    t_first = _frame_start(typhoon, pts, n_frames, step_hours, grid_center=_grid_center(meta, dem))
    frame_times = [t_first + timedelta(hours=i * step_hours) for i in range(n_frames)]
    overrides = payload.get("overrides") if isinstance(payload.get("overrides"), dict) else {}
    states = _build_states(pts, frame_times, overrides)

    grid = physics.Grid(nx, ny, bbox)
    masks = inundation.build_masks(dem, pixel_m)
    villages, villages_ok = _load_villages(meta, warns)

    frames = []
    series = []
    village_peak = [0.0] * len(villages)
    village_frame = [-1] * len(villages)

    # ---------------- 计算内核选择：numpy 复算（默认） / 原 POSSIM Fortran 内核 ----------------
    kernel_req = str(payload.get("kernel") or "numpy").strip().lower()
    use_fortran = kernel_req in ("fortran", "orig", "original", "dll")
    kernel_meta = {"kernel": "numpy", "label": "numpy 复算"}
    h_list = None
    if use_fortran:
        ok, msg = fortran.available()
        if not ok:
            warns.append("fortran_unavailable")
            use_fortran = False
            kernel_meta["fallback_reason"] = msg
        else:
            gt = (float(bbox[0]), (float(bbox[2]) - float(bbox[0])) / nx, 0.0,
                  float(bbox[3]), 0.0, -(float(bbox[3]) - float(bbox[1])) / ny)
            amp_req = payload.get("fortran_amp", "auto")
            if amp_req is None or (isinstance(amp_req, str) and amp_req.strip().lower() == "auto"):
                amp, amp_auto = fortran.auto_amp(gt)
            else:
                try:
                    amp, amp_auto = float(amp_req), False
                except (TypeError, ValueError):
                    amp, amp_auto = fortran.auto_amp(gt)
            tides_all = [tide_mod.tide_level(tide_mod.hours_since_epoch(ft), params["tide"])
                         for ft in frame_times]
            try:
                t_f = time.perf_counter()
                h_list = fortran.surge_fields(dem.astype(np.float32, copy=False), nx, ny, gt,
                                              states, tides_all, params, amp=amp,
                                              workers=int(payload.get("workers") or 8))
                kernel_meta = {
                    "kernel": "fortran",
                    "label": "原 POSSIM Fortran 内核",
                    "dll": msg,
                    "amp": round(amp, 3),
                    "amp_auto": bool(amp_auto),
                    "amp_rule": "|gt[1]| > 0.001° 触发 3.8 倍（照抄 StormSurgeEngine.cs:254）",
                    "kernel_ms": int((time.perf_counter() - t_f) * 1000),
                }
            except Exception as exc:               # 内核崩溃不该拖垮服务
                warns.append("fortran_failed")
                kernel_meta["fallback_reason"] = "%s: %s" % (type(exc).__name__, exc)
                use_fortran = False
                h_list = None

    # 村点取值的搜索半径：1 km（约 10 个像素）
    vwin = max(1, int(round(1000.0 / max(pixel_m, 1.0))))
    for i, ft in enumerate(frame_times):
        st = states[i]
        tide_m = tide_mod.tide_level(tide_mod.hours_since_epoch(ft), params["tide"])
        if use_fortran:
            h_field = h_list[i]["h"]
        else:
            h_field = physics.surge_field(grid, dem, st, params)["h"]
        res = inundation.inundate(dem, h_field, tide_m, masks, params)
        ha = inundation.flooded_ha(res["flooded_px"], pixel_m)
        pct = inundation.flooded_pct(res["flooded_px"], masks["land_pixels"])
        png_b64 = render.render_b64(res["depth"], res["mask"], nx, ny)
        label = "Hour +%03d" % i
        frames.append({
            "i": i,
            "label": label,
            "utc": tide_mod.iso_z(ft),
            "center": [round(st["lat"], 4), round(st["lon"], 4)],
            "pc_mb": round(st["pc_mb"], 1),
            "vmax_kt": round(st["vmax_kt"], 1),
            "motion_kt": round(st["motion_kt"], 1),
            "heading_deg": round(st["heading_deg"] % 360.0, 1),
            "tide": round(tide_m, 3),
            "max_surge": round(res["max_surge"], 3),
            "max_twl": round(res["max_twl"], 3),
            "max_depth": round(res["max_depth"], 3),
            "flooded_ha": round(ha, 1),
            "flooded_pct": round(pct, 2),
            "categories": res["category_counts"],
            "png_b64": png_b64,
            "png_w": nx,
            "png_h": ny,
        })
        dist_center = physics.great_circle_km(st["lat"], st["lon"], grid.center[0], grid.center[1])
        series.append({
            "utc": tide_mod.iso_z(ft),
            "tide": round(tide_m, 3),
            "surge": round(res["max_surge"], 3),
            "twl": round(res["max_twl"], 3),
            "wind_ms": round(st["vmax_kt"] * C.KT_TO_MS, 1),
            "wind_kt": round(st["vmax_kt"], 1),
            "pc_mb": round(st["pc_mb"], 1),
            "dist_km": round(dist_center, 1),
            "flooded_ha": round(ha, 1),
        })
        # 村点最大水深（取村点 1 km 半径窗口内的最大淹没水深）
        for k, v in enumerate(villages):
            try:
                row, col = grid.to_rowcol(float(v["lat"]), float(v["lon"]))
                r0 = max(0, row - vwin)
                r1 = min(ny, row + vwin + 1)
                c0 = max(0, col - vwin)
                c1 = min(nx, col + vwin + 1)
                d = float(res["depth"][r0:r1, c0:c1].max())
            except (KeyError, TypeError, ValueError):
                d = 0.0
            if d > village_peak[k]:
                village_peak[k] = d
                village_frame[k] = i

    def _best(key):
        best = max(frames, key=lambda f: f[key])
        return {"value": best[key], "label": best["label"]}

    affected = [v for k, v in enumerate(villages) if village_peak[k] > C.Village_HIT_DEPTH]
    affected_pop = int(sum(float(v.get("pop") or 0) for v in affected))

    summary = {
        "max_depth": _best("max_depth"),
        "max_area": _best("flooded_ha"),
        "max_surge": _best("max_surge"),
        "affected_pop": affected_pop,
        "affected_villages": len(affected),
        "affected_note": ("按村点最近像素在本时段内的最大淹没水深 > %.2f m 判定受影响，"
                          "人口取 villages.pop 之和（估算，非承灾体普查）" % C.Village_HIT_DEPTH),
    }

    out = {
        "ok": True,
        "elapsed_ms": int((time.perf_counter() - t_start) * 1000),
        "bbox": [[bbox[1], bbox[0]], [bbox[3], bbox[2]]],
        "nx": nx,
        "ny": ny,
        "pixel_m": round(pixel_m, 1),
        "typhoon_id": typhoon.get("id", typhoon_id),
        "frames": frames,
        "timeseries": series,
        "summary": summary,
        "params": {
            "env_pressure": params["env_pressure"],
            "holland_b": params["holland_b"],
            "rmax_km": params["rmax_km"],
            "wave_runup": params["wave_runup"],
            "coarse_amp": params["coarse_amp"],
            "tide": params["tide"],
        },
        "villages_ready": bool(villages_ok),
        "kernel": kernel_meta,
    }
    if warns:
        out["warn"] = ",".join(warns)
    return out
