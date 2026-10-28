# -*- coding: utf-8 -*-
"""
POSSIM-Web · S1 数据代理 —— 地形晕渲 + 分层设色底图
====================================================
读 data/dem.npy，写 data/dem_preview.png（1188x864 RGBA），供前端 Leaflet
imageOverlay 铺在 bbox 上，也作为无瓦片时的兜底底图。

画法：
  * 分层设色：近海平原浅绿 → 丘陵黄褐 → 山地灰白（按高程分段线性插值）
  * 地形晕渲：光从西北（方位 315°、高度角 45°）打来的朗伯光照，叠加到设色上
  * 水体：DEM < 0 的连通区域（>=20 格）涂深蓝 #0a2540，平坦不打光
  * NODATA：全透明（alpha=0），让底图 OSM 透出来
  * 岸线：陆水交界描 1 px 亮蓝灰线，保证喇叭形河口与伶仃洋一眼可辨

用法：
    python scripts/make_preview.py                    # 水系填深蓝（默认，按任务书）
    python scripts/make_preview.py --water-transparent # 水系透明（按 CONTRACT §3 括号的写法）
"""
import argparse
import os
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA_DIR = os.path.join(ROOT, "data")
DEM_PATH = os.path.join(DATA_DIR, "dem.npy")
PNG_PATH = os.path.join(DATA_DIR, "dem_preview.png")

NODATA = -9999.0
WATER_RGB = (10, 37, 64)          # #0a2540
COAST_RGB = (58, 104, 138)        # 岸线描边
MIN_WATER_CELLS = 20              # 小于此的水体连通域按陆地着色（噪声）

# 分层设色（高程 m → RGB）：平原浅绿 → 丘陵黄褐 → 山地灰白
STOPS = [
    (0.0, (226, 234, 205)),
    (2.0, (214, 228, 190)),
    (5.0, (201, 220, 175)),
    (20.0, (183, 212, 157)),
    (50.0, (204, 199, 139)),
    (100.0, (198, 174, 118)),
    (200.0, (176, 148, 112)),
    (350.0, (167, 157, 147)),
    (500.0, (189, 187, 183)),
    (700.0, (214, 214, 212)),
    (950.0, (240, 240, 240)),
]


def shift(a, dr, dc, fill):
    out = np.full_like(a, fill)
    h, w = a.shape
    rs, rd = slice(max(0, dr), h + min(0, dr)), slice(max(0, -dr), h + min(0, -dr))
    cs, cd = slice(max(0, dc), w + min(0, dc)), slice(max(0, -dc), w + min(0, -dc))
    out[rd, cd] = a[rs, cs]
    return out


def blur(a, passes=2):
    """3x3 均值模糊若干次，压制重采样带来的高频噪声（晕渲更干净）。"""
    out = a.astype(np.float64)
    for _ in range(passes):
        acc = np.zeros_like(out)
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                acc += shift(out, dr, dc, np.nan)
        # 用 nan 均值处理边界
        cnt = np.zeros_like(out)
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                cnt += np.where(np.isnan(shift(a.astype(np.float64), dr, dc, np.nan)), 0.0, 1.0)
        out = np.nan_to_num(acc / np.maximum(cnt, 1.0), nan=0.0)
    return out


def water_components(mask, min_cells=MIN_WATER_CELLS):
    """4 连通标记（行段 + 并查集），返回面积 >= min_cells 的水体掩膜。"""
    h, w = mask.shape
    parent = [0]
    runs = []

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    nxt, prev = 1, []
    for r in range(h):
        d = np.diff(np.concatenate(([0], mask[r].view(np.int8), [0])))
        starts = np.flatnonzero(d == 1).tolist()
        ends = np.flatnonzero(d == -1).tolist()
        if not starts:
            prev = []
            continue
        cur = []
        for s, e in zip(starts, ends):
            lab = 0
            for ps, pe, pl in prev:
                if ps < e and s < pe:
                    if lab == 0:
                        lab = find(pl)
                    else:
                        union(lab, pl)
            if lab == 0:
                lab = nxt
                parent.append(lab)
                nxt += 1
            cur.append((s, e, lab))
            runs.append((r, s, e, lab))
        prev = cur
    labels = np.zeros((h, w), dtype=np.int32)
    for r, s, e, lab in runs:
        labels[r, s:e] = find(lab)
    counts = np.bincount(labels.ravel())
    keep = np.zeros(counts.size, dtype=bool)
    keep[1:] = counts[1:] >= min_cells
    return keep[labels], labels


def hillshade(z, cell_mx, cell_my, az_deg=315.0, alt_deg=45.0):
    """朗伯晕渲：光从西北来。z 为已平滑的高程（米）。"""
    dzdx = (shift(z, 0, -1, np.nan) - shift(z, 0, 1, np.nan)) / (2.0 * cell_mx)      # 东向
    dzdy = (shift(z, 1, 0, np.nan) - shift(z, -1, 0, np.nan)) / (2.0 * cell_my)      # 北向（行自北向南）
    dzdx = np.nan_to_num(dzdx)
    dzdy = np.nan_to_num(dzdy)
    az = np.deg2rad(az_deg)
    alt = np.deg2rad(alt_deg)
    lx, ly, lz = np.sin(az) * np.cos(alt), np.cos(az) * np.cos(alt), np.sin(alt)
    nrm = np.sqrt(dzdx * dzdx + dzdy * dzdy + 1.0)
    hs = (lx * (-dzdx) + ly * (-dzdy) + lz) / nrm
    return np.clip(hs, 0.0, 1.0)


def colorize(dem):
    """分层设色：按高程分段线性插值。"""
    stops = np.array([s[0] for s in STOPS], dtype=np.float64)
    cols = np.array([s[1] for s in STOPS], dtype=np.float64)
    z = np.clip(dem, stops[0], stops[-1])
    out = np.empty(dem.shape + (3,), dtype=np.float64)
    for k in range(3):
        out[..., k] = np.interp(z, stops, cols[:, k])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--water-transparent", action="store_true",
                    help="水系透明（按 CONTRACT §3 括号写法）；默认填深蓝 #0a2540（按任务书）")
    args = ap.parse_args()

    dem = np.load(DEM_PATH).astype(np.float64)
    ny, nx = dem.shape
    lon_pix = 1.0 / nx
    lat_pix = 0.8 / ny
    cell_mx = lon_pix * 111320.0 * np.cos(np.deg2rad(22.6))
    cell_my = lat_pix * 111000.0
    print("读入 %s  shape=%s  经向格宽 %.1f m  纬向格宽 %.1f m" % (DEM_PATH, dem.shape, cell_mx, cell_my))

    nod = dem == NODATA
    water, labels = water_components(dem < 0)
    print("水体系数 %d，保留连通水域 %d 像元（%.2f%%），小斑块降级为陆地" % (
        int(labels.max()), int(water.sum()), 100.0 * water.sum() / dem.size))

    # 晕渲：水体与 nodata 压平到 0，避免海岸做出假坡
    zs = np.where(water | nod, 0.0, np.maximum(dem, 0.0))
    zs = blur(zs, passes=2)
    hs = hillshade(zs, cell_mx, cell_my)
    shade = 0.55 + 0.75 * hs                      # 0.55(背光) ~ 1.30(迎光)

    rgb = colorize(np.where(water, 0.0, dem))
    rgb *= shade[..., None]
    rgb = np.clip(rgb, 0, 255)

    # 岸线描边：陆地 1 px 邻接水域
    land = ~water & ~nod
    coast = land & ~(shift(land, 0, -1, True) & shift(land, 0, 1, True)
                     & shift(land, -1, 0, True) & shift(land, 1, 0, True))
    rgb[coast] = np.array(COAST_RGB, dtype=np.float64) * 0.9

    rgba = np.zeros((ny, nx, 4), dtype=np.uint8)
    rgba[..., :3] = rgb.astype(np.uint8)
    rgba[..., 3] = 255
    if args.water_transparent:
        rgba[water, 3] = 0
    else:
        rgba[water, :3] = np.array(WATER_RGB, dtype=np.uint8)
        rgba[coast, :3] = np.array(COAST_RGB, dtype=np.uint8)
    rgba[nod, 3] = 0
    rgba[nod, :3] = 0

    img = Image.fromarray(rgba, mode="RGBA")
    img.save(PNG_PATH, optimize=True)
    print("已写出 %s  %dx%d  水色=%s  岸线=#%02x%02x%02x  透明像元 %d（nodata）" % (
        PNG_PATH, img.size[0], img.size[1],
        "透明" if args.water_transparent else "#0a2540",
        COAST_RGB[0], COAST_RGB[1], COAST_RGB[2], int(nod.sum())))
    print("不透明像元 %.1f%% ；晕渲亮度范围 %.2f ~ %.2f" % (
        100.0 * (rgba[..., 3] > 0).mean(), float(shade.min()), float(shade.max())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
