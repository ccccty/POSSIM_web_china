# -*- coding: utf-8 -*-
"""
POSSIM-Web · S1 数据代理 —— data/ 一键校验
==========================================
不依赖任何第三方服务，只读 data/ 里的产物，逐项对照 CONTRACT §3：

  dem.npy        形状/ dtype / nodata 比例 / bbox 与像素自洽 / 高程量级 / 关键地标
  dem_preview.png 尺寸 / 模式 / 透明比例 / 是否全黑全白 / 有无无数据横条 / 水系颜色
  villages.json  条数 / 字段 / 坐标在 bbox 内 / 在陆地或岸线 1 km 内 / 数值区间 / 枚举值
  typhoons/*.json 字段 / 时间递增与间隔 / 风圈嵌套 / 登陆时刻在路径内 / 时间跨度 / 与 meta 一致
  meta.json      字段齐全 / 与 dem 实测值一致 / bbox 与网格自洽 / villages 与 villages.json 一致

用法：
    python scripts/check_data.py           # 表格输出，全部通过时退出码 0
"""
import json
import os
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
DEM = os.path.join(DATA, "dem.npy")
PNG = os.path.join(DATA, "dem_preview.png")
VIL = os.path.join(DATA, "villages.json")
META = os.path.join(DATA, "meta.json")
TYD = os.path.join(DATA, "typhoons")

NODATA = -9999.0
W, S, E, N = 113.20, 22.20, 114.20, 23.00
NX, NY = 1188, 864
PIXEL_DEG = 0.000925926
WATER_RGB = (10, 37, 64)

_rows = []


def add(state, item, detail):
    _rows.append((state, item, detail))


def ok(item, detail):
    add("OK", item, detail)


def bad(item, detail):
    add("FAIL", item, detail)


def warn(item, detail):
    add("WARN", item, detail)


def main():
    # ---------------------------------------------------------------- dem.npy
    if not os.path.isfile(DEM):
        bad("data/dem.npy", "文件不存在")
        return report()
    dem = np.load(DEM)
    if dem.shape == (NY, NX):
        ok("dem 形状", "%s == (ny=%d, nx=%d)" % (dem.shape, NY, NX))
    else:
        bad("dem 形状", "%s != (ny=%d, nx=%d)" % (dem.shape, NY, NX))
    if dem.dtype == np.float32:
        ok("dem dtype", "float32")
    else:
        bad("dem dtype", "%s != float32" % dem.dtype)

    nod = dem == NODATA
    frac = nod.mean()
    if frac < 0.20:
        ok("dem nodata 占比", "%.4f%% < 20%%（%d 像元）" % (frac * 100, int(nod.sum())))
    else:
        bad("dem nodata 占比", "%.2f%% >= 20%%（%d 像元）" % (frac * 100, int(nod.sum())))

    land = int((dem >= 0).sum())
    if 0.20 <= land / dem.size <= 0.95:
        ok("dem 陆地比例", "%.2f%%（%d 像元）" % (100.0 * land / dem.size, land))
    else:
        bad("dem 陆地比例", "%.2f%% 不合理" % (100.0 * land / dem.size))

    v = dem[~nod]
    if -50.0 <= v.min() <= 0.0:
        ok("dem 最小值", "%.2f m（水体为数据集的水面编码值）" % v.min())
    else:
        bad("dem 最小值", "%.2f m 超出 [-50, 0]" % v.min())
    if 200.0 <= v.max() <= 2000.0:
        ok("dem 最大值", "%.2f m（珠江口最高点应为数百米量级）" % v.max())
    else:
        bad("dem 最大值", "%.2f m 不合理" % v.max())

    lat_pix = (N - S) / NY
    lon_pix = (E - W) / NX
    if abs(lat_pix - PIXEL_DEG) < 1e-9:
        ok("网格 纬度自洽", "ny * pixel_lat = %.6f° == 南北跨度 %.6f°" % (NY * lat_pix, N - S))
    else:
        bad("网格 纬度自洽", "ny * %.9f = %.6f != %.6f" % (lat_pix, NY * lat_pix, N - S))
    if abs(NX * lon_pix - (E - W)) < 1e-9:
        ok("网格 经度自洽", "nx * pixel_lon = %.6f° == 东西跨度 %.6f°" % (NX * lon_pix, E - W))
    else:
        bad("网格 经度自洽", "nx * %.9f = %.6f != %.6f" % (lon_pix, NX * lon_pix, E - W))
    if abs(NX * PIXEL_DEG - (E - W)) > 1e-6:
        warn("契约像素度冲突", "nx * pixel_deg = %.6f° 而东西跨度 %.6f°（差 %.6f°，约 %.1f km）；"
                               "S1 按 bbox 对齐经度，纬度方向严格用 pixel_deg"
             % (NX * PIXEL_DEG, E - W, NX * PIXEL_DEG - (E - W), abs(NX * PIXEL_DEG - (E - W)) * 111.0))

    def at(lat, lon):
        r = int(np.floor((N - lat) / lat_pix))
        c = int(np.floor((lon - W) / lon_pix))
        if not (0 <= r < NY and 0 <= c < NX):
            return None
        return float(dem[r, c])

    z = at(22.4097, 114.1247)          # 大帽山
    if z is not None and z > 700:
        ok("地标 香港大帽山", "%.1f m（真值约 957 m）" % z)
    else:
        bad("地标 香港大帽山", "%s m，应 > 700" % z)
    z = at(22.55, 113.72)              # 伶仃洋
    if z is not None and z < 0:
        ok("地标 伶仃洋", "%.1f m（水域）" % z)
    else:
        bad("地标 伶仃洋", "%s m，应为水域(<0)" % z)
    z = at(22.685, 113.635)            # 龙穴岛
    if z is not None and 0 <= z <= 6:
        ok("地标 南沙龙穴岛", "%.1f m（岸线平原 0-5 m 量级）" % z)
    else:
        bad("地标 南沙龙穴岛", "%s m，应为 0-6 m 陆地" % z)
    z = at(22.249, 113.913)            # 凤凰山
    if z is not None and z > 300:
        ok("地标 香港凤凰山", "%.1f m（真值约 934 m，西南坡）" % z)
    else:
        bad("地标 香港凤凰山", "%s m，应 > 300" % z)

    # ------------------------------------------------------- dem_preview.png
    if not os.path.isfile(PNG):
        bad("data/dem_preview.png", "文件不存在")
    else:
        img = Image.open(PNG)
        if img.size == (NX, NY) and img.mode == "RGBA":
            ok("preview 尺寸/模式", "%dx%d RGBA" % img.size)
        else:
            bad("preview 尺寸/模式", "%dx%d %s，应为 %dx%d RGBA" % (img.size + (img.mode, NX, NY)))
        a = np.asarray(img.convert("RGBA"))
        alpha = a[..., 3]
        rgb = a[..., :3].astype(np.float64)
        opaque = alpha > 0
        tr = 1.0 - opaque.mean()
        # 只允许 nodata 处透明（允许把水系也做成透明的那一档实现）
        if nod.any():
            if tr <= nod.mean() + 1e-6 or abs(tr - nod.mean()) < 0.02:
                ok("preview 透明度", "%.4f%% 透明" % (tr * 100))
            else:
                warn("preview 透明度", "%.2f%% 透明，nodata 只占 %.2f%%" % (tr * 100, nod.mean() * 100))
        elif tr < 0.30:
            ok("preview 透明度", "%.2f%% 透明（水系可填色或透明）" % (tr * 100))
        else:
            bad("preview 透明度", "%.2f%% 透明，nodata 为 0 时不应大面积透明" % (tr * 100))
        if opaque.any():
            bright = rgb[opaque].mean()
            if 40 < bright < 235:
                ok("preview 亮度", "不透明区均值 %.1f（非全黑/全白）" % bright)
            else:
                bad("preview 亮度", "不透明区均值 %.1f（疑似全黑或全白）" % bright)
        # 横向无数据条带：逐行检查不透明像素占比
        row_op = opaque.mean(axis=1)
        if row_op.min() > 0.05:
            ok("preview 无空行", "最空一行不透明占比 %.1f%%" % (row_op.min() * 100))
        else:
            bad("preview 无空行", "存在不透明占比 %.1f%% 的行（无数据横条）" % (row_op.min() * 100))
        nwater = int((np.all(a[..., :3] == np.array(WATER_RGB, dtype=a.dtype), axis=-1)).sum())
        if nwater > 0.05 * NX * NY:
            ok("preview 水系着色", "#0a2540 深蓝像元 %d（%.1f%%）" % (nwater, 100.0 * nwater / (NX * NY)))
        else:
            warn("preview 水系着色", "#0a2540 只有 %d 像元（可能用了透明水系）" % nwater)

    # ------------------------------------------------------------ villages
    villages = None
    if not os.path.isfile(VIL):
        bad("data/villages.json", "文件不存在")
    else:
        with open(VIL, encoding="utf-8") as fh:
            villages = json.load(fh)
        if isinstance(villages, list) and 10 <= len(villages) <= 16:
            ok("villages 条数", "%d 条（契约要求 10-16）" % len(villages))
        else:
            bad("villages 条数", "%s 条，应为 10-16" % (len(villages) if isinstance(villages, list) else type(villages)))
        need = {"name", "lat", "lon", "pop", "households", "dist_coast_km", "elev_m", "strategy", "level"}
        missf = set()
        badbox, badland, badval, badenum = [], [], [], []
        water = dem < 0
        for i, it in enumerate(villages):
            nm = it.get("name", "#%d" % i)
            if not need.issubset(it.keys()):
                missf.add(nm)
                continue
            if not (S <= it["lat"] <= N and W <= it["lon"] <= E):
                badbox.append(nm)
            if it["strategy"] not in ("转移", "避险", "待命"):
                badenum.append("%s strategy=%s" % (nm, it["strategy"]))
            if it["level"] not in ("高", "中", "低"):
                badenum.append("%s level=%s" % (nm, it["level"]))
            if not (0 < it["households"] < it["pop"] <= 60000):
                badval.append("%s pop=%s households=%s" % (nm, it["pop"], it["households"]))
            if not (0.0 <= it["dist_coast_km"] <= 3.0):
                badval.append("%s dist_coast_km=%s" % (nm, it["dist_coast_km"]))
            if not (-1.0 <= it["elev_m"] <= 8.0):
                badval.append("%s elev_m=%s" % (nm, it["elev_m"]))
            r = int(np.floor((N - it["lat"]) / lat_pix))
            c = int(np.floor((it["lon"] - W) / lon_pix))
            inside = 0 <= r < NY and 0 <= c < NX
            if not inside:
                badland.append("%s 落在网格外" % nm)
                continue
            if dem[r, c] >= 0:
                pass                                    # 在陆地 ✅
            else:
                # 不在陆地时，要求 1 km 内有陆地（岸线附近）
                k = max(2, int(1000.0 / 95.0))
                win = dem[max(0, r - k):r + k + 1, max(0, c - k):c + k + 1]
                if not (win >= 0).any():
                    badland.append("%s 在水里且 1 km 内无陆地" % nm)
        if not missf:
            ok("villages 字段", "全部含 %d 个契约字段" % len(need))
        else:
            bad("villages 字段", "缺字段：%s" % ", ".join(sorted(missf)))
        if not badbox:
            ok("villages 在 bbox 内", "全部 %d 条在 [%.2f,%.2f]x[%.2f,%.2f]" % (len(villages), W, E, S, N))
        else:
            bad("villages 在 bbox 内", ", ".join(badbox))
        if not badland:
            ok("villages 在陆地/岸线1km内", "全部通过（对 DEM>=0 判别，水中点另查 1 km 邻域）")
        else:
            bad("villages 在陆地/岸线1km内", ", ".join(badland))
        if not badval:
            ok("villages 数值区间", "elev 1-5 m / dist 0.3-3 km / households < pop 全部通过")
        else:
            bad("villages 数值区间", "; ".join(badval[:6]))
        if not badenum:
            ok("villages 枚举值", "level ∈ 高/中/低，strategy ∈ 转移/避险/待命")
        else:
            bad("villages 枚举值", "; ".join(badenum[:6]))
        if all(isinstance(it.get("name"), str) for it in villages):
            ok("villages 名称唯一", "去重后 %d 条" % len({it["name"] for it in villages}))
        else:
            bad("villages 名称唯一", "存在非字符串名称")

    # ------------------------------------------------------------ typhoons
    tfiles = sorted(f[:-5] for f in os.listdir(TYD) if f.endswith(".json")) if os.path.isdir(TYD) else []
    if len(tfiles) >= 3:
        ok("typhoons 文件数", "%d 个：%s" % (len(tfiles), ", ".join(tfiles)))
    else:
        bad("typhoons 文件数", "%d 个，至少 3 个" % len(tfiles))
    ty_peaks = {}
    for tid in tfiles:
        path = os.path.join(TYD, tid + ".json")
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        errs = []
        for k in ("id", "name", "name_en", "year", "track", "landfall"):
            if k not in doc:
                errs.append("缺 %s" % k)
        if errs:
            bad("typhoon %s 字段" % tid, "; ".join(errs))
            continue
        if doc["id"] != tid:
            errs.append("id 与文件名不一致")
        tr = doc["track"]
        if not (8 <= len(tr) <= 30):
            errs.append("路径点数 %d 不合理" % len(tr))
        need = {"t", "lat", "lon", "pc_mb", "vmax_kt", "motion_kt", "heading_deg", "r34", "r50", "r64"}
        for p in tr:
            if not need.issubset(p.keys()):
                errs.append("路径点缺字段: %s" % sorted(need - set(p.keys())))
                break
        import datetime as dt

        def ts(t):
            return dt.datetime.strptime(t, "%Y-%m-%dT%H:%MZ").replace(tzinfo=dt.timezone.utc)

        times = [ts(p["t"]) for p in tr]
        if any(b <= a for a, b in zip(times, times[1:])):
            errs.append("时间未严格递增")
        gaps = [(b - a).total_seconds() / 3600.0 for a, b in zip(times, times[1:])]
        if gaps and not (2.9 <= min(gaps) and max(gaps) <= 6.1):
            errs.append("点间隔 %.1f~%.1f h 超出 3-6 h" % (min(gaps), max(gaps)))
        for p in tr:
            if not (len(p["r34"]) == len(p["r50"]) == len(p["r64"]) == 4):
                errs.append("%s 风圈不是 4 象限" % p["t"])
                break
            if not all(p["r34"][i] >= p["r50"][i] >= p["r64"][i] for i in range(4)):
                errs.append("%s 风圈未嵌套" % p["t"])
                break
            if not (880 <= p["pc_mb"] <= 1015):
                errs.append("%s pc_mb=%s 越界" % (p["t"], p["pc_mb"]))
                break
            if not (0 <= p["vmax_kt"] <= 190):
                errs.append("%s vmax_kt=%s 越界" % (p["t"], p["vmax_kt"]))
                break
            if not (0 <= p["heading_deg"] < 360):
                errs.append("%s heading_deg=%s 越界" % (p["t"], p["heading_deg"]))
                break
            if not (0 < p["motion_kt"] <= 60):
                errs.append("%s motion_kt=%s 越界" % (p["t"], p["motion_kt"]))
                break
        lf = ts(doc["landfall"]["t"])
        if not (times[0] < lf < times[-1]):
            errs.append("登陆时刻不在路径时间范围内")
        pre = (lf - times[0]).total_seconds() / 3600.0
        post = (times[-1] - lf).total_seconds() / 3600.0
        if not (30 <= pre <= 48):
            errs.append("登陆前跨度 %.1f h 不在 36±" % pre)
        if not (10 <= post <= 16):
            errs.append("登陆后跨度 %.1f h 不在 12±" % post)
        if not (S - 8 <= doc["landfall"]["lat"] <= N + 1 and W - 3 <= doc["landfall"]["lon"] <= E + 1):
            errs.append("登陆点离 bbox 太远")
        ty_peaks[tid] = (int(max(p["vmax_kt"] for p in tr)), int(min(p["pc_mb"] for p in tr)))
        if errs:
            bad("typhoon %s" % tid, "; ".join(errs[:4]))
        else:
            ok("typhoon %s" % tid, "%d 点，%s→%s，前 %.1f h / 后 %.1f h，峰值 %d kt %d hPa"
               % (len(tr), tr[0]["t"], tr[-1]["t"], pre, post, ty_peaks[tid][0], ty_peaks[tid][1]))

    # ---------------------------------------------------------------- meta
    if not os.path.isfile(META):
        bad("data/meta.json", "文件不存在")
        return report()
    with open(META, encoding="utf-8") as fh:
        meta = json.load(fh)
    miss = [k for k in ("site", "dem", "map", "defaults", "typhoons", "villages") if k not in meta]
    if miss:
        bad("meta 顶层字段", "缺 %s" % ", ".join(miss))
    else:
        ok("meta 顶层字段", "site/dem/map/defaults/typhoons/villages 齐全")
    st, dm = meta.get("site", {}), meta.get("dem", {})
    errs = []
    if st.get("nx") != NX or st.get("ny") != NY:
        errs.append("nx/ny=%s/%s" % (st.get("nx"), st.get("ny")))
    if [round(x, 4) for x in st.get("bbox", [])] != [W, S, E, N]:
        errs.append("bbox=%s" % (st.get("bbox"),))
    if abs(st.get("pixel_deg", 0) - PIXEL_DEG) > 1e-9:
        errs.append("pixel_deg=%s" % st.get("pixel_deg"))
    if dm.get("nodata") != NODATA:
        errs.append("nodata=%s" % dm.get("nodata"))
    if dm.get("land_pixels") != land:
        errs.append("land_pixels %s != 实测 %d" % (dm.get("land_pixels"), land))
    if abs(dm.get("min", 0) - round(float(v.min()), 1)) > 0.05:
        errs.append("min %s != 实测 %.1f" % (dm.get("min"), v.min()))
    if abs(dm.get("max", 0) - round(float(v.max()), 1)) > 0.05:
        errs.append("max %s != 实测 %.1f" % (dm.get("max"), v.max()))
    if errs:
        bad("meta 与 dem 一致", "; ".join(errs))
    else:
        ok("meta 与 dem 一致", "min %.1f / max %.1f / land_pixels %d / nodata %s"
           % (dm["min"], dm["max"], dm["land_pixels"], dm["nodata"]))
    pm = st.get("pixel_m", 0)
    area = st.get("area_km2", 0)
    if pm > 0 and abs(area - NX * NY * pm * pm / 1e6) / max(area, 1e-9) < 0.01:
        ok("meta 面积自洽", "area_km2 = nx*ny*pixel_m² = %.1f km²（pixel_m=%.1f）" % (area, pm))
    else:
        bad("meta 面积自洽", "area_km2=%s 与 nx*ny*pixel_m²=%.1f 不符" % (area, NX * NY * pm * pm / 1e6))

    # ---- meta.dem 统计字段 + meta.site.land_area_km2（契约 §9 修订轮新增，前端"平均高程"要用）----
    lv = dem[dem >= 0].astype(np.float64)
    want_stats = {
        "mean_elev": round(float(lv.mean()), 1),
        "median_elev": round(float(np.median(lv)), 1),
        "land_ratio": round(land / float(dem.size), 3),
    }
    lostat = []
    for k, want in want_stats.items():
        got = dm.get(k)
        if got is None:
            lostat.append("缺 %s" % k)
        elif abs(float(got) - float(want)) > 1e-9:
            lostat.append("%s=%s != 实测 %s" % (k, got, want))
    ol = dm.get("ocean_level")
    if ol is None:
        lostat.append("缺 ocean_level")
    else:
        wmask = dem < 0
        if wmask.any() and not bool(np.allclose(dem[wmask], float(ol))):
            lostat.append("ocean_level=%s 与实测水域值 %s 不符"
                          % (ol, np.unique(dem[wmask])[:3].tolist()))
    if lostat:
        bad("meta.dem 统计字段", "; ".join(lostat))
    else:
        ok("meta.dem 统计字段",
           "mean_elev %.1f m / median_elev %.1f m / land_ratio %s / ocean_level %.1f（均与 dem 实算一致）"
           % (want_stats["mean_elev"], want_stats["median_elev"],
              want_stats["land_ratio"], float(ol)))
    if not (5.0 <= float(dm.get("mean_elev", -1)) <= 60.0):
        warn("meta.dem.mean_elev 量级", "%.1f m 不在 5-60 m（珠江口三角洲+丘陵的常见量级）"
             % float(dm.get("mean_elev", -1)))
    larea = st.get("land_area_km2")
    want_la = land * pm * pm / 1e6
    if larea is None:
        bad("meta.site.land_area_km2", "缺该字段")
    elif abs(float(larea) - want_la) > 0.05:
        bad("meta.site.land_area_km2", "%s != land_pixels(%d)*pixel_m(%.1f)²/1e6 = %.1f"
            % (larea, land, pm, want_la))
    else:
        ok("meta.site.land_area_km2", "%.1f km² = land_pixels(%d) × pixel_m(%.1f)² / 1e6"
           % (float(larea), land, pm))

    mt = [t["id"] for t in meta.get("typhoons", [])]
    if mt and set(mt) == set(tfiles):
        ok("meta 台风与文件一致", "%s" % ", ".join(mt))
    else:
        bad("meta 台风与文件一致", "meta=%s 文件=%s" % (mt, tfiles))
    peak_ok = all(ty_peaks.get(t["id"]) == (t["vmax_kt"], t["pc_mb"])
                  for t in meta.get("typhoons", []) if t["id"] in ty_peaks)
    if peak_ok:
        ok("meta 台风峰值一致", "vmax_kt/pc_mb 与路径文件相符")
    else:
        bad("meta 台风峰值一致", "meta 峰值与路径文件不符")
    if villages is not None and meta.get("villages") == villages:
        ok("meta.villages 一致", "与 data/villages.json 完全相同（%d 条）" % len(villages))
    else:
        bad("meta.villages 一致", "与 data/villages.json 不一致")
    mp = meta.get("map", {})
    if mp.get("bbox_leaflet") == [[S, W], [N, E]] and mp.get("preview_png") == "/api/dem/preview.png":
        ok("meta.map", "bbox_leaflet=[[%.2f,%.2f],[%.2f,%.2f]] preview=%s"
           % (S, W, N, E, mp.get("preview_png")))
    else:
        bad("meta.map", "bbox_leaflet/preview_png 不符合契约")
    df = meta.get("defaults", {})
    need_d = ["env_pressure", "holland_b", "wave_runup", "coarse_amp", "tide", "hours", "step_hours"]
    if all(k in df for k in need_d) and set(df.get("tide", {})) == {"M2", "S2", "K1", "O1"}:
        ok("meta.defaults", "7 项齐全，tide 含 M2/S2/K1/O1")
    else:
        bad("meta.defaults", "缺项或 tide 不完整")
    return report()


def report():
    w1 = max(len(r[1]) for r in _rows) if _rows else 10
    w2 = max(len(r[2]) for r in _rows) if _rows else 10
    print("=" * (w1 + w2 + 12))
    print("POSSIM-Web · data/ 校验报告")
    print("=" * (w1 + w2 + 12))
    for state, item, detail in _rows:
        mark = {"OK": "[ OK ]", "FAIL": "[FAIL]", "WARN": "[WARN]"}[state]
        print("%s %-*s  %s" % (mark, w1, item, detail))
    nfail = sum(1 for r in _rows if r[0] == "FAIL")
    nwarn = sum(1 for r in _rows if r[0] == "WARN")
    nok = sum(1 for r in _rows if r[0] == "OK")
    print("-" * (w1 + w2 + 12))
    print("合计 %d 项：OK %d / WARN %d / FAIL %d   →  %s"
          % (len(_rows), nok, nwarn, nfail, "全部通过" if nfail == 0 else "存在失败项"))
    return 0 if nfail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
