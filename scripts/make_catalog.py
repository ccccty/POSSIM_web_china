# -*- coding: utf-8 -*-
"""
POSSIM-Web · S1 数据代理 —— 影响行政村 + meta.json
===================================================
1) 依据真实地理位置给出 15 个珠江口两岸受影响行政村/社区（南沙、中山、珠海、深圳、香港、澳门），
   坐标不必手写精确：脚本会在 DEM 上就近"吸附"到符合条件的陆地格
   （高程 1~5 m、距岸 0.3~3 km），并把吸附距离打印出来供人工核对。位移过大就会显式报警。
2) 人口/户数为公开量级的示意值（契约已声明"示意数据"），高程与距岸是**从 DEM 实测算出来的**。
3) 生成 data/meta.json：site/dem/map/defaults/typhoons/villages 全部按 CONTRACT §3.1，
   数值（min/max/land_pixels/area_km2/pixel_m）都是实算，typhoons 数组由 data/typhoons/*.json 汇总，
   villages 字段与 data/villages.json 完全一致，可直接当 GET /api/meta 的响应体。

用法：
    python scripts/make_catalog.py
"""
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA_DIR = os.path.join(ROOT, "data")
DEM_PATH = os.path.join(DATA_DIR, "dem.npy")
VILLAGES_PATH = os.path.join(DATA_DIR, "villages.json")
META_PATH = os.path.join(DATA_DIR, "meta.json")
TYPHOON_DIR = os.path.join(DATA_DIR, "typhoons")

# ---------- 契约常量 ----------
W, S, E, N = 113.20, 22.20, 114.20, 23.00
NX, NY = 1188, 864
PIXEL_DEG = 0.000925926
NODATA = -9999
LON_PIX = (E - W) / NX
LAT_PIX = (N - S) / NY
PIX_M_LON = LON_PIX * 111320.0 * np.cos(np.deg2rad(22.6))     # 86.5 m
PIX_M_LAT = LAT_PIX * 111000.0                                 # 102.8 m
PIX_M_EQ = float(np.sqrt(PIX_M_LON * PIX_M_LAT))               # 等效方格 94.2888 m
# meta.site.pixel_m 对外公布值（1 位小数）。像元面积一律按"公布值"算，保证任何人拿
# meta.site.pixel_m 自己复算 land_pixels*pixel_m²/1e6 都能得到 meta.site.land_area_km2。
PIX_M_PUB = round(PIX_M_EQ, 1)                                 # 94.3 m
# CONTRACT §9.3：本数据集把海洋/大水体编码成常量 -2.0 m（不是 0，也不是真实水深）。
# 引擎判"水"用 DEM < 0；meta.dem.ocean_level 只把这个常量告诉前端，供图例/说明使用。
OCEAN_LEVEL = -2.0

# 候选村： (名称, 纬度, 经度, 人口, 户数, 策略)
# 说明：中山民众/横门、珠海斗门白蕉、香港东涌 在本数据集里离主河口水面 >3 km
# （该瓦片把三角洲内河网与黄茅海都没有编成水面），按"距岸 0.3-3 km"的要求不做入选，
# 改用同一岸线上的真实受淹村/社区（南朗崖口、香湾、流浮山等），见回报说明。
CANDIDATES = [
    ("南沙区龙穴街道", 22.685, 113.635, 12000, 4300, "转移"),
    ("南沙区万顷沙镇", 22.610, 113.555, 24000, 8200, "转移"),
    ("南沙区珠江街道", 22.7394, 113.6003, 15000, 5400, "转移"),
    ("中山市南朗镇崖口村", 22.492, 113.550, 6800, 2400, "转移"),
    ("东莞市虎门镇", 22.810, 113.670, 42000, 14000, "避险"),
    ("珠海市唐家湾镇", 22.360, 113.595, 32000, 11000, "避险"),
    ("珠海市淇澳社区", 22.425, 113.660, 3200, 1200, "转移"),
    ("珠海市香湾街道", 22.290, 113.575, 26000, 9000, "避险"),
    ("珠海市香洲区湾仔街道", 22.215, 113.535, 18000, 6300, "避险"),
    ("深圳市宝安区西乡街道", 22.572, 113.855, 45000, 15000, "避险"),
    ("深圳市南山区前海社区", 22.5394, 113.8957, 21000, 7400, "转移"),
    ("深圳市南山区蛇口街道", 22.478, 113.918, 38000, 13000, "避险"),
    ("香港屯门区三圣邨", 22.383, 113.965, 15000, 5500, "避险"),
    ("香港元朗区流浮山", 22.470, 113.985, 9000, 3200, "待命"),
    ("澳门花地玛堂区", 22.213, 113.548, 22000, 7600, "避险"),
]


def shift(a, dr, dc, fill):
    out = np.full_like(a, fill)
    h, w = a.shape
    rs, rd = slice(max(0, dr), h + min(0, dr)), slice(max(0, -dr), h + min(0, -dr))
    cs, cd = slice(max(0, dc), w + min(0, dc)), slice(max(0, -dc), w + min(0, -dc))
    out[rd, cd] = a[rs, cs]
    return out


def dist_to_water(water, max_cells=500):
    """到最近水体的距离（格数）：从水体出发做逐圈膨胀。"""
    dist = np.full(water.shape, 10 ** 6, dtype=np.int32)
    dist[water] = 0
    frontier = water.copy()
    for k in range(1, max_cells + 1):
        nxt = np.zeros_like(water)
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr or dc:
                    nxt |= shift(frontier, dr, dc, False)
        nxt &= dist > k
        if not nxt.any():
            break
        dist[nxt] = k
        frontier = nxt
    return dist


def to_rc(lat, lon):
    return int(np.floor((N - lat) / LAT_PIX)), int(np.floor((lon - W) / LON_PIX))


def to_latlon(r, c):
    return N - (r + 0.5) * LAT_PIX, W + (c + 0.5) * LON_PIX


def snap(dem, dist, lat, lon, radius=10):
    """在半径 radius 内挑最合适的陆地格：高程 1-5 m、距岸 0.3-3 km 优先，其次尽量靠近原位。

    逐项加罚分（单位=格），最后取罚分+位移最小者；同时避免孤岛式单格。
    """
    r0, c0 = to_rc(lat, lon)
    land = dem >= 0
    best = None
    for dr in range(-radius, radius + 1):
        for dc in range(-radius, radius + 1):
            r, c = r0 + dr, c0 + dc
            if not (0 <= r < dem.shape[0] and 0 <= c < dem.shape[1]):
                continue
            z = float(dem[r, c])
            if z == NODATA or z < 0:
                continue
            dkm = float(dist[r, c]) * PIX_M_EQ / 1000.0
            pen = 0.0
            if z < 1.0:
                pen += 12.0 * (1.0 - z) + 4.0          # 0 m 滩涂不优先
            elif z > 5.0:
                pen += 4.0 + 0.6 * (z - 5.0)           # 高出 5 m 逐步加罚
            if dkm < 0.3:
                pen += 8.0
            elif dkm > 3.0:
                pen += 8.0 + 2.0 * (dkm - 3.0)
            # 3x3 陆地占比低 → 孤岛/碎块，加罚
            n0, n1 = max(0, r - 1), min(dem.shape[0], r + 2)
            m0, m1 = max(0, c - 1), min(dem.shape[1], c + 2)
            if land[n0:n1, m0:m1].mean() < 0.7:
                pen += 3.0
            score = pen + float(np.hypot(dr, dc))
            if best is None or score < best[0]:
                best = (score, r, c, z, dkm)
    return best, r0, c0


def level_of(elev, dkm, pop):
    """风险分级（可解释）：高程权重最大，其次贴岸程度与人口规模。

    s = 0.45 * 低洼度 + 0.30 * 贴岸度 + 0.25 * 人口规模        s >= 0.58 → 高，>= 0.38 → 中，否则低
    低洼度 = (5 - min(elev,5)) / 4      高程 1 m 得满、5 m 得 0
    贴岸度 = (3 - min(dkm,3)) / 3       距岸 0 km 得满、3 km 得 0
    规模   = min(pop,45000) / 45000
    """
    s = 0.45 * (5.0 - min(elev, 5.0)) / 4.0 \
        + 0.30 * (3.0 - min(dkm, 3.0)) / 3.0 \
        + 0.25 * min(pop, 45000) / 45000.0
    if s >= 0.58:
        return "高", s
    if s >= 0.38:
        return "中", s
    return "低", s


def build_villages(dem, dist):
    out, warns = [], []
    print("%-22s %-19s %-19s %7s %8s %7s %6s %5s %s" % (
        "村/社区", "候选经纬度", "吸附后经纬度", "位移m", "高程m", "距岸km", "人口", "等级", "策略"))
    for name, lat, lon, pop, hh, strat in CANDIDATES:
        best, r0, c0 = snap(dem, dist, lat, lon)
        if best is None:
            warns.append("%s 吸附失败（半径内无合格陆地格）" % name)
            continue
        _, r, c, z, dkm = best
        alat, alon = to_latlon(r, c)
        moved = float(np.hypot((r - r0) * PIX_M_LAT, (c - c0) * PIX_M_LON))
        if moved > 800:
            warns.append("%s 吸附位移 %.0f m，位置可能不准" % (name, moved))
        lvl, score = level_of(z, dkm, pop)
        strat = {"高": "转移", "中": "避险", "低": "待命"}[lvl]
        out.append({
            "name": name,
            "lat": round(alat, 4),
            "lon": round(alon, 4),
            "pop": int(pop),
            "households": int(hh),
            "dist_coast_km": round(max(dkm, 0.1), 1),
            "elev_m": round(z, 1),
            "strategy": strat,
            "level": lvl,
        })
        print("%-22s %8.4fN %.4fE %8.4fN %.4fE %7.0f %8.1f %7.1f %6d %5s %s  (score %.2f)" % (
            name, lat, lon, alat, alon, moved, z, dkm, pop, lvl, out[-1]["strategy"], score))
    return out, warns


def build_meta(dem, villages):
    typhoons = []
    for fn in sorted(os.listdir(TYPHOON_DIR)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(TYPHOON_DIR, fn), encoding="utf-8") as fh:
            doc = json.load(fh)
        typhoons.append({
            "id": doc["id"], "name": doc["name"], "name_en": doc["name_en"], "year": doc["year"],
            "vmax_kt": int(max(p["vmax_kt"] for p in doc["track"])),
            "pc_mb": int(min(p["pc_mb"] for p in doc["track"])),
        })
    # 山竹为默认演示台风（正面珠江口），其余按年份倒序
    typhoons.sort(key=lambda t: (0 if t["id"] == "mangkhut" else 1, -t["year"]))

    nod = dem == NODATA
    land_mask = (dem >= 0) & ~nod
    land = int(land_mask.sum())
    # 陆地像元高程统计（前端"目标海域 / 平均高程"要用，前端拿不到 dem.npy，只能读 meta）
    lv = dem[land_mask].astype(np.float64)
    mean_elev = round(float(lv.mean()), 1)          # 陆地平均高程 m，1 位小数
    median_elev = round(float(np.median(lv)), 1)    # 陆地高程中位数 m，1 位小数
    land_ratio = round(float(land) / float(dem.size), 3)          # 陆地像元占比，3 位小数
    land_area_km2 = round(land * PIX_M_PUB * PIX_M_PUB / 1e6, 1)  # 陆地面积 km²，1 位小数
    meta = {
        "site": {
            "name": "珠江口风暴潮淹没推演平台",
            "region": "珠江口-伶仃洋",
            "crs": "EPSG:4326",
            "bbox": [W, S, E, N],
            "nx": NX, "ny": NY,
            "pixel_deg": PIXEL_DEG,
            "pixel_deg_x": round(LON_PIX, 9),
            "pixel_deg_y": round(LAT_PIX, 9),
            "pixel_m": PIX_M_PUB,
            "pixel_m_lon": round(PIX_M_LON, 1),
            "pixel_m_lat": round(PIX_M_LAT, 1),
            "area_km2": round(NX * NY * PIX_M_EQ * PIX_M_EQ / 1e6, 1),
            "land_area_km2": land_area_km2,
            "note": "数据：SRTM 1角秒(约30 m)重采样至网格（等效格 %.1f m）· 台风参数为历史/示意情景" % PIX_M_EQ,
        },
        "dem": {
            "file": "dem.npy",
            "nodata": NODATA,
            "min": round(float(dem[~nod].min()), 1),
            "max": round(float(dem[~nod].max()), 1),
            "land_pixels": land,
            "mean_elev": mean_elev,
            "median_elev": median_elev,
            "land_ratio": land_ratio,
            "ocean_level": OCEAN_LEVEL,
        },
        "map": {
            "center": [22.60, 113.70],
            "zoom": 11,
            "bbox_leaflet": [[S, W], [N, E]],
            "preview_png": "/api/dem/preview.png",
        },
        "defaults": {
            "env_pressure": 1013.0,
            "holland_b": 1.7,
            "wave_runup": 1.15,
            "coarse_amp": 3.8,
            "tide": {"M2": [1.0, 145.0], "S2": [0.4, 45.0], "K1": [0.3, 75.0], "O1": [0.2, 120.0]},
            "hours": 36,
            "step_hours": 1,
        },
        "typhoons": typhoons,
        "villages": villages,
    }
    return meta


def validate_meta(meta, dem):
    """新字段量级与内部一致性自检。返回问题列表（空 = 通过）。

    这一步必须在写 meta.json *之前* 调用，坏数据不落盘。
    """
    bad = []
    d, st = meta["dem"], meta["site"]
    mean_elev, median_elev = d["mean_elev"], d["median_elev"]
    land_ratio, ocean_level = d["land_ratio"], d["ocean_level"]
    if not (5.0 <= mean_elev <= 60.0):
        bad.append("mean_elev=%.1f 不在 5-60 m（珠江口三角洲+丘陵的合理量级）" % mean_elev)
    if not (0.0 < median_elev < mean_elev <= d["max"]):
        bad.append("median_elev=%.1f 异常（应 0 < 中位数 < 均值 %.1f <= max %.1f）"
                   % (median_elev, mean_elev, d["max"]))
    if not (0.0 < land_ratio < 1.0):
        bad.append("land_ratio=%s 不在 (0,1)" % land_ratio)
    if land_ratio != round(d["land_pixels"] / float(dem.size), 3):
        bad.append("land_ratio %s != round(land_pixels %d / %d, 3)"
                   % (land_ratio, d["land_pixels"], dem.size))
    water = dem[dem < 0]
    if water.size and not bool(np.allclose(water, ocean_level)):
        bad.append("水域并非全为 ocean_level=%.1f（实际出现 %s）"
                   % (ocean_level, np.unique(water)[:5]))
    want_area = d["land_pixels"] * st["pixel_m"] ** 2 / 1e6
    if abs(want_area - st["land_area_km2"]) > 0.1:
        bad.append("land_area_km2 %.1f != land_pixels*pixel_m²/1e6 %.1f"
                   % (st["land_area_km2"], want_area))
    return bad


def main():
    dem = np.load(DEM_PATH).astype(np.float32)
    print("读入 %s  shape=%s  等效格 %.2f m（经向 %.1f × 纬向 %.1f）" % (
        DEM_PATH, dem.shape, PIX_M_EQ, PIX_M_LON, PIX_M_LAT))
    water = dem < 0
    nod = dem == NODATA
    print("水域 %.2f%%  陆地 %.2f%%  nodata %.2f%%" % (
        100.0 * water.mean(), 100.0 * (dem >= 0).mean(), 100.0 * nod.mean()))
    dist = dist_to_water(water)
    print("到水体距离场：最远 %d 格 (%.1f km)" % (int(dist[~water].max() if (~water).any() else 0),
                                          float(dist[~water].max() if (~water).any() else 0) * PIX_M_EQ / 1000.0))

    villages, warns = build_villages(dem, dist)
    with open(VILLAGES_PATH, "w", encoding="utf-8") as fh:
        json.dump(villages, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print("")
    print("已写出 %s  %d 条" % (VILLAGES_PATH, len(villages)))

    meta = build_meta(dem, villages)
    bad = validate_meta(meta, dem)
    if bad:
        print("")
        print("新字段自检失败（meta.json 未写入）：")
        for x in bad:
            print("  - %s" % x)
        return 2
    with open(META_PATH, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print("已写出 %s  (%.1f KB)" % (META_PATH, os.path.getsize(META_PATH) / 1024.0))
    print("  site : nx=%d ny=%d pixel_deg=%.9f pixel_m=%.1f area=%.1f km²" % (
        meta["site"]["nx"], meta["site"]["ny"], meta["site"]["pixel_deg"],
        meta["site"]["pixel_m"], meta["site"]["area_km2"]))
    print("  dem  :")
    for k, val in meta["dem"].items():
        print("         %-12s %s" % (k, val))
    print("  site.land_area_km2 = %.1f km²" % meta["site"]["land_area_km2"])
    print("  typhoons: %s" % ", ".join("%s(%d %dkt %dhPa)" % (t["id"], t["year"], t["vmax_kt"], t["pc_mb"])
                                        for t in meta["typhoons"]))
    print("  villages 与 villages.json 同源：%d 条" % len(meta["villages"]))
    print("")
    print("等级分布：高 %d / 中 %d / 低 %d ；策略：转移 %d / 避险 %d / 待命 %d" % (
        sum(1 for v in villages if v["level"] == "高"),
        sum(1 for v in villages if v["level"] == "中"),
        sum(1 for v in villages if v["level"] == "低"),
        sum(1 for v in villages if v["strategy"] == "转移"),
        sum(1 for v in villages if v["strategy"] == "避险"),
        sum(1 for v in villages if v["strategy"] == "待命")))
    want_area = meta["dem"]["land_pixels"] * meta["site"]["pixel_m"] ** 2 / 1e6
    print("")
    print("新字段自检通过（写盘前已校验）：5 m <= mean_elev %.1f m <= 60 m ；"
          "ocean_level %.1f 覆盖全部水域像元 %d 个（%s）"
          % (meta["dem"]["mean_elev"], meta["dem"]["ocean_level"], int((dem < 0).sum()),
             np.unique(dem[dem < 0])))
    print("                land_ratio %s × %d 像元 = land_pixels %d ；"
          "land_area_km2 %.1f ≈ %.1f km²"
          % (meta["dem"]["land_ratio"], dem.size, meta["dem"]["land_pixels"],
             meta["site"]["land_area_km2"], want_area))

    if warns:
        print("")
        print("警告：")
        for x in warns:
            print("  - %s" % x)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
