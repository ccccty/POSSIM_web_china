# -*- coding: utf-8 -*-
"""
POSSIM-Web · S1 数据代理 —— 真地形获取与网格化
=================================================
把 AWS SRTM 1 角秒瓦片（skadi）拼成马赛克，再重采样到 CONTRACT §2 要求的目标网格。

用法：
    python scripts/fetch_dem.py             # 幂等：瓦片已在 scripts/.cache/ 就复用，不重复下载
    python scripts/fetch_dem.py --refresh   # 强制重新下载瓦片

产物：
    data/dem.npy     float32[864, 1188]，单位 m，行序自北向南，NODATA = -9999

一、网格对齐（CONTRACT §2 内部有一处数值矛盾，S1 的裁决如下）
    契约冻结 nx=1188、ny=864、pixel_deg=0.000925926。
    纬度方向：864 * 0.000925926 = 0.800000°，与南北跨度 0.8° 精确自洽 ✅
    经度方向：1188 * 0.000925926 = 1.100000°，而东西跨度为 1.000000° ❌（差 0.1° ≈ 10 km）
    本脚本按 bbox 对齐生成数据：经度像素 = 1.0/1188 = 0.000841751°（3.0303 角秒），
    纬度像素 = 0.8/864 = 0.000925926°（3.3333 角秒，与契约一致）。
    理由：前端用 Leaflet imageOverlay 把 1188x864 的 PNG 拉伸铺在 bbox_leaflet 上，引擎也按
    bbox+nx/ny 做像素↔经纬度换算；若按 1.1° 铺格，珠江口与海岛会整体错位约 10 km。

二、水体/高程约定（实测结论，不是假造地形）
    这份 skadi 数据把**水面编码成一个常量 -2 m 的水面旗标**（伶仃洋 24x24 角秒窗口内全部恒为
    -2.0，不是真实水深）；三角洲平原是"鱼塘/河网 = -2 旗标 + 围田 = 0..+4 m"的棋盘格。
    陆地高程本身是真实 SRTM：最高点 946.3 m 落在大帽山（真值 957 m 附近），
    相邻的 1 角秒原始值也与独立数据源 terrarium 抽样 6 点吻合（±4 m）。
    所以不能把 raw<0 直接当海洋——否则番禺/万顷沙/民众的围田会有一半被当成海水，
    淹没图会变成筛子。S1 的降采样策略（可复现，只清理"水面旗标"，不动真实高程）：
      (1) 面积加权算出每格的水面占比 water_frac 与"陆地像元均值 land_elev / 陆地覆盖率"；
      (2) water_frac >= 0.5 记为候选水域；
      (3) 半径 6 格（≈620 m）的形态学开运算，去掉细窄水体（河涌、鱼塘、运河）；
      (4) 4 连通标记，只保留**与 bbox 边界连通且面积 >= 20 km²** 的连通域为海洋
          （喇叭形河口 + 伶仃洋 + 珠江口外海）；
      (5) 海洋格写 -2.0（沿用数据集自身的水面编码，也正好对上 CONTRACT §3.1 的 min）；
          其余格为陆地，高程取该格内陆地源像元的面积加权均值；无陆地源像元的格用
          8 邻域迭代填充；0 m 滩涂/围田保持 0 m。
    -32768（瓦片无数据）与 bbox 外 → NODATA(-9999)。
"""
import argparse
import gzip
import os
import sys
import time
import urllib.request

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA_DIR = os.path.join(ROOT, "data")
CACHE_DIR = os.path.join(HERE, ".cache")
DEM_PATH = os.path.join(DATA_DIR, "dem.npy")

# ---------- 契约常量（CONTRACT §2/§3，冻结） ----------
BBOX = (113.20, 22.20, 114.20, 23.00)      # W, S, E, N（度，EPSG:4326）
NX, NY = 1188, 864
PIXEL_DEG = 0.000925926                     # 契约像素度（纬度方向精确成立）
NODATA = -9999.0

# ---------- 实现常量 ----------
SRTM_SIDE = 3601                            # 1 角秒瓦片边长（含首尾）
ARCSEC = 3600.0
WATER_FLAG = -2.0                           # 数据集的水面编码值（实测）
S3_BASE = "https://s3.amazonaws.com/elevation-tiles-prod/skadi"
CHUNK_ROWS = 512                            # 列方向重采样的分块行数（控内存）
OPEN_RADIUS = 4                             # 形态学开运算半径（格），≈410 m（实测标定：见回报）
MIN_OCEAN_CELLS = 2250                      # 海洋连通域最小格数，≈20 km²

W, S, E, N = BBOX
LON_PIX = (E - W) / NX                      # 0.000841751 度（按 bbox 对齐）
LAT_PIX = (N - S) / NY                      # 0.000925926 度（与契约一致）
CELL_M2 = (LON_PIX * 111320.0 * np.cos(np.deg2rad(22.6))) * (LAT_PIX * 111000.0)


# ============================ 瓦片下载与解压 ============================
def tile_id(lat0, lon0):
    ns = "N" if lat0 >= 0 else "S"
    ew = "E" if lon0 >= 0 else "W"
    return "%s%02d%s%03d" % (ns, abs(int(lat0)), ew, abs(int(lon0)))


def tiles_for_bbox(w, s, e, n):
    """与 bbox 相交（含仅相切于边界）的瓦片列表，行优先排序。"""
    out = []
    lat0 = int(np.floor(s))
    while lat0 <= n:                       # <= 让"仅相切北边界"的整行瓦片也进来
        lon0 = int(np.floor(w))
        while lon0 <= e:
            out.append((lat0, lon0))
            lon0 += 1
        lat0 += 1
    return out


def fetch_tile(lat0, lon0, refresh=False, retries=3):
    """下载并缓存 .hgt.gz；返回 (路径 或 None, 说明)。失败重试 3 次。"""
    tid = tile_id(lat0, lon0)
    sub = os.path.join(CACHE_DIR, tid[:3])
    os.makedirs(sub, exist_ok=True)
    gz = os.path.join(sub, tid + ".hgt.gz")

    if (not refresh) and os.path.isfile(gz) and os.path.getsize(gz) > 1_000_000:
        return gz, "cache"

    url = "%s/%s/%s.hgt.gz" % (S3_BASE, tid[:3], tid)
    last = None
    for attempt in range(1, retries + 1):
        tmp = gz + ".part"
        try:
            t0 = time.time()
            urllib.request.urlretrieve(url, tmp)
            size = os.path.getsize(tmp)
            if size < 1_000_000:
                raise IOError("大小异常 %d B" % size)
            os.replace(tmp, gz)
            return gz, "download %.1f MB %.1fs" % (size / 1048576.0, time.time() - t0)
        except Exception as exc:           # noqa: BLE001 网络错误统一重试
            last = exc
            if os.path.isfile(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            print("    [重试 %d/%d] %s：%s" % (attempt, retries, tid, exc))
            time.sleep(1.5 * attempt)
    print("    [失败] %s 连续 %d 次下载失败：%s" % (tid, retries, last))
    return None, "fail: %s" % last


def load_tile(gz):
    """解压 .hgt.gz → int16[3601,3601]（big-endian）。"""
    with gzip.open(gz, "rb") as fh:
        buf = fh.read()
    arr = np.frombuffer(buf, dtype=">i2")
    if arr.size != SRTM_SIDE * SRTM_SIDE:
        raise ValueError("%s 尺寸异常：%d，应为 %d" % (gz, arr.size, SRTM_SIDE ** 2))
    return arr.reshape(SRTM_SIDE, SRTM_SIDE)


# ============================ 马赛克拼接 ============================
# 马赛克几何：列 c 对应经度 113.000 + c/3600（lon 113..115）；
# 行 r 对应纬度 23.000 - (r-1)/3600（r=1 即纬度 23.0，r=0 留给 N23 瓦片的最低一行）。
MOSAIC_COLS = (115 - 113) * 3600 + 1
MOSAIC_ROWS = 3600 + 2


def assemble_mosaic(tile_files):
    mosaic = np.full((MOSAIC_ROWS, MOSAIC_COLS), -32768, dtype=np.int16)
    placed = []
    for lat0, lon0, gz in tile_files:
        tile = load_tile(gz)
        r_lo = 1 + (22 - int(lat0)) * 3600
        c_lo = (int(lon0) - 113) * 3600
        rr0, rr1 = max(0, r_lo), min(MOSAIC_ROWS, r_lo + SRTM_SIDE)
        cc0, cc1 = max(0, c_lo), min(MOSAIC_COLS, c_lo + SRTM_SIDE)
        if rr0 >= rr1 or cc0 >= cc1:
            placed.append((tile_id(lat0, lon0), "out-of-window"))
            continue
        mosaic[rr0:rr1, cc0:cc1] = tile[rr0 - r_lo:rr1 - r_lo, cc0 - c_lo:cc1 - c_lo]
        placed.append((tile_id(lat0, lon0), "rows %d..%d cols %d..%d" % (rr0, rr1 - 1, cc0, cc1 - 1)))
    return mosaic, placed


def extract_window(mosaic):
    r0 = 1 + int(round((23.0 - N) * ARCSEC))
    r1 = r0 + int(round((N - S) * ARCSEC))
    c0 = int(round((W - 113.0) * ARCSEC))
    c1 = c0 + int(round((E - W) * ARCSEC))
    return mosaic[r0:r1 + 1, c0:c1 + 1], (r0, r1, c0, c1)


# ============================ 面积加权重采样 ============================
def _edges(idx_span, n_out):
    """目标格边界在源索引坐标下的一维边界；源样本 j 占 [j, j+1)。"""
    return np.arange(n_out + 1, dtype=np.float64) * (idx_span / float(n_out))


def _floor_frac(edges, n):
    i = np.clip(np.floor(edges).astype(np.int64), 0, n - 1)
    return i, edges - i


def resample_cols(src, valid, edges, chunk=CHUNK_ROWS):
    """沿列（axis=1）面积加权重采样。返回 (值=有效像元均值, 有效覆盖率)。"""
    rows, n = src.shape
    m = len(edges) - 1
    i0, f0 = _floor_frac(edges[:-1], n)
    i1, f1 = _floor_frac(edges[1:], n)
    span = edges[1:] - edges[:-1]
    out = np.empty((rows, m), dtype=np.float64)
    cov = np.empty((rows, m), dtype=np.float64)
    for r in range(0, rows, chunk):
        a = src[r:r + chunk].astype(np.float64)
        v = valid[r:r + chunk].astype(np.float64)
        a *= v
        ca = np.concatenate([np.zeros((a.shape[0], 1)), np.cumsum(a, axis=1)], axis=1)
        cv = np.concatenate([np.zeros((v.shape[0], 1)), np.cumsum(v, axis=1)], axis=1)

        def at(C, x, i, f):
            return C[:, i] + f[None, :] * x[:, i]

        num = at(ca, a, i1, f1) - at(ca, a, i0, f0)
        den = at(cv, v, i1, f1) - at(cv, v, i0, f0)
        out[r:r + chunk] = num / np.maximum(den, 1e-12)
        cov[r:r + chunk] = den / span[None, :]
    return out.astype(np.float32), cov.astype(np.float32)


def resample_rows(src, cov, edges):
    """沿行（axis=0）面积加权重采样，权重用上一级返回的有效覆盖率。"""
    n, cols = src.shape
    m = len(edges) - 1
    i0, f0 = _floor_frac(edges[:-1], n)
    i1, f1 = _floor_frac(edges[1:], n)
    span = edges[1:] - edges[:-1]
    src = src.astype(np.float64)
    covd = cov.astype(np.float64)

    def integ(arr):
        return np.concatenate([np.zeros((1, cols)), np.cumsum(arr, axis=0)], axis=0)

    def take(C, x, i, f):
        return C[i] + f[:, None] * x[i]

    ca, cv = integ(src), integ(covd)
    num = take(ca, src, i1, f1) - take(ca, src, i0, f0)     # 值×覆盖率 的积分
    den = take(cv, covd, i1, f1) - take(cv, covd, i0, f0)   # 覆盖率 的积分
    return (num / np.maximum(den, 1e-12)).astype(np.float32), (den / span[:, None]).astype(np.float32)


def resample_field(values, valid, row_edges, col_edges):
    """两级面积加权重采样：返回 (有效像元均值, 有效覆盖率)。"""
    mid, covc = resample_cols(values, valid, col_edges)
    return resample_rows(mid, covc, row_edges)


# ============================ 形态学 / 连通域 ============================
def _shift(a, dr, dc, fill=0):
    out = np.full_like(a, fill)
    h, w = a.shape
    rs, rd = slice(max(0, dr), h + min(0, dr)), slice(max(0, -dr), h + min(0, -dr))
    cs, cd = slice(max(0, dc), w + min(0, dc)), slice(max(0, -dc), w + min(0, -dc))
    out[rd, cd] = a[rs, cs]
    return out


def _disk(r):
    return [(dr, dc) for dr in range(-r, r + 1) for dc in range(-r, r + 1)
            if 0 < dr * dr + dc * dc <= r * r]


def erode(mask, offs):
    out = mask.copy()
    for dr, dc in offs:
        out &= _shift(mask, dr, dc, fill=False)
    return out


def dilate(mask, offs):
    out = mask.copy()
    for dr, dc in offs:
        out |= _shift(mask, dr, dc, fill=False)
    return out


def ccl4(mask):
    """4 连通标记（行段 + 并查集）。返回 labels(int32, 0=背景)。"""
    h, w = mask.shape
    runs = []
    parent = [0]

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    nxt = 1
    prev = []
    for r in range(h):
        row = mask[r].view(np.int8)
        d = np.diff(np.concatenate(([0], row, [0])))
        starts = np.flatnonzero(d == 1)
        ends = np.flatnonzero(d == -1)
        if starts.size == 0:
            prev = []
            continue
        cur = []
        for s, e in zip(starts.tolist(), ends.tolist()):
            lab = 0
            for ps, pe, pl in prev:
                if ps < e and s < pe:          # 上下行段列区间相交 → 4 连通
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
    if labels.max() > 0:
        # 压缩成 0=背景、1..k 连续标签（inv 本身就是压缩后的编号）
        uniq, inv = np.unique(labels, return_inverse=True)
        labels = inv.reshape(labels.shape).astype(np.int32)
    return labels


def fill_undefined(vals, defined, iters=80):
    """用 8 邻域已知值均值迭代填充"没有陆地源像元"的格。"""
    v = vals.astype(np.float64).copy()
    m = defined.copy()
    for _ in range(iters):
        if m.all():
            break
        acc = np.zeros_like(v)
        cnt = np.zeros_like(v)
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr or dc:
                    acc += _shift(np.where(m, v, 0.0), dr, dc, fill=0.0)
                    cnt += _shift(m.astype(np.float64), dr, dc, fill=0.0)
        upd = (~m) & (cnt > 0)
        if not upd.any():
            break
        v[upd] = acc[upd] / cnt[upd]
        m |= upd
    v[~m] = 0.0
    return v, m


def clean_ocean(water_frac, radius=OPEN_RADIUS, min_cells=MIN_OCEAN_CELLS):
    """候选水域 → 形态学开运算 → 只保留与 bbox 边界连通且足够大的连通域。"""
    raw = water_frac >= 0.5
    offs = _disk(radius)
    opened = dilate(erode(raw, offs), offs)
    labels = ccl4(opened)
    border = set(np.unique(np.concatenate([
        labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]])).tolist())
    border.discard(0)
    if not border:
        return np.zeros_like(raw), raw, opened, {}
    counts = np.bincount(labels.ravel())
    keep = [lb for lb in border if counts[lb] >= min_cells]
    ocean = np.isin(labels, keep) if keep else np.zeros_like(raw)
    info = {
        "候选水域格数": int(raw.sum()),
        "开运算后格数": int(opened.sum()),
        "触界连通域数": len(border),
        "保留海洋连通域数": len(keep),
        "海洋格数": int(ocean.sum()),
        "海洋面积 km2": float(ocean.sum() * CELL_M2 / 1e6),
        "最大连通域面积 km2": float(counts[1:].max() * CELL_M2 / 1e6) if counts.size > 1 else 0.0,
    }
    return ocean, raw, opened, info


# ============================ 统计与自检 ============================
LANDMARKS = [
    ("伶仃洋中心(应为海)", 22.550, 113.720, "海"),
    ("南沙龙穴岛", 22.680, 113.630, "0-5"),
    ("万顷沙围田", 22.600, 113.560, "0-5"),
    ("中山民众", 22.620, 113.470, "0-8"),
    ("珠海淇澳岛", 22.420, 113.650, "0-20"),
    ("深圳前海湾", 22.530, 113.880, "0-10"),
    ("香港屯门", 22.390, 113.960, "0-20"),
    ("深圳梧桐山", 22.580, 114.190, "300-950"),
    ("香港大帽山", 22.410, 114.120, "500-960"),
    ("香港凤凰山", 22.249, 113.913, "700-960"),
    ("广州番禺石楼", 22.950, 113.480, "0-30"),
    ("东莞大岭山", 22.900, 113.850, "50-600"),
]


def sample(dem, lat, lon):
    r = int(np.floor((N - lat) / LAT_PIX))
    c = int(np.floor((lon - W) / LON_PIX))
    if not (0 <= r < dem.shape[0] and 0 <= c < dem.shape[1]):
        return None, None, None
    return float(dem[r, c]), r, c


def ascii_map(dem, ncol=60, nrow=40):
    h, w = dem.shape
    lines = []
    for i in range(nrow):
        r0 = int(i * h / nrow); r1 = max(r0 + 1, int((i + 1) * h / nrow))
        line = []
        for j in range(ncol):
            c0 = int(j * w / ncol); c1 = max(c0 + 1, int((j + 1) * w / ncol))
            v = float(np.mean(dem[r0:r1, c0:c1]))
            if v == NODATA:
                line.append("?")
            elif v < -1.5:
                line.append("~")
            elif v <= 0:
                line.append("v")
            elif v < 5:
                line.append(".")
            elif v < 20:
                line.append(":")
            elif v < 100:
                line.append("+")
            elif v < 300:
                line.append("#")
            else:
                line.append("@")
        lines.append("".join(line))
    print("     " + "".join(str(j // 10) for j in range(ncol)))
    for i, line in enumerate(lines):
        print("%5.2f " % (N - (i + 0.5) * (N - S) / nrow) + line)
    print("      图例 ~=海(-2)  v<=0  .<5m  :<20m  +<100m  #<300m  @>=300m")


def report(dem, stats, checks):
    land = dem > 0
    coast = (dem == 0)
    water = dem < 0
    nod = dem == NODATA
    n = dem.size
    print("")
    print("================ DEM 统计 ================")
    print("形状 / dtype           : %s  %s  行序自北向南（北=第 0 行）" % (dem.shape, dem.dtype))
    print("经度像素 / 纬度像素    : %.9f° (%.4f″) / %.9f° (%.4f″)"
          % (LON_PIX, LON_PIX * ARCSEC, LAT_PIX, LAT_PIX * ARCSEC))
    print("单格地面尺寸           : %.1f m × %.1f m = %.4f km²" % (
        LON_PIX * 111320.0 * np.cos(np.deg2rad(22.6)), LAT_PIX * 111000.0, CELL_M2 / 1e6))
    print("NODATA(-9999) 占比     : %.4f %%  (%d 像元)" % (nod.mean() * 100.0, int(nod.sum())))
    print("陆地像元 (>0)          : %d (%.2f%%)   0 m 像元 %d (%.2f%%)" % (
        int(land.sum()), 100.0 * land.sum() / n, int(coast.sum()), 100.0 * coast.sum() / n))
    print("水域像元 (<0)          : %d (%.2f%%)   其中最小值 %.1f m" % (
        int(water.sum()), 100.0 * water.sum() / n, float(dem[water].min()) if water.any() else 0.0))
    print("min / max              : %.2f m / %.2f m" % (float(dem[~nod].min()), float(dem[~nod].max())))
    r, c = divmod(int(np.argmax(np.where(nod, -1e9, dem))), dem.shape[1])
    print("最高点位置             : 行 %d 列 %d ≈ %.4fN %.4fE" % (
        r, c, N - (r + 0.5) * LAT_PIX, W + (c + 0.5) * LON_PIX))
    print("陆地平均高程           : %.2f m" % float(dem[land].mean()) if land.any() else "陆地平均高程: n/a")
    print("总面积                 : %.1f km²" % (n * CELL_M2 / 1e6))

    print("")
    print("---- 陆地像元高程区间占比 ----")
    lv = dem[land]
    for lo, hi in [(0, 5), (5, 20), (20, 50), (50, 100), (100, 200), (200, 500), (500, 100000)]:
        cnt = int(((lv >= lo) & (lv < hi)).sum())
        label = "%g-%gm" % (lo, hi) if hi < 100000 else ">=500m"
        print("  %-9s : %8d   占陆地 %5.2f%%   占全图 %5.2f%%"
              % (label, cnt, 100.0 * cnt / max(1, lv.size), 100.0 * cnt / n))

    print("")
    print("---- bbox 四角抽样（经纬度 → 高程）----")
    for plat, plon, tag in [(N - LAT_PIX / 2, W + LON_PIX / 2, "西北"), (N - LAT_PIX / 2, E - LON_PIX / 2, "东北"),
                            (S + LAT_PIX / 2, W + LON_PIX / 2, "西南"), (S + LAT_PIX / 2, E - LON_PIX / 2, "东南")]:
        z, rr, cc = sample(dem, plat, plon)
        print("  %s 格心 (%.4fN, %.4fE) 行%.3d 列%.4d → %s"
              % (tag, plat, plon, rr, cc, ("%.1f m" % z) if z is not None else "越界"))

    print("")
    print("---- 地标核对（人工可读）----")
    print("  %-20s %-18s %10s %s" % ("地标", "经纬度", "高程 m", "预期量级"))
    for name, plat, plon, expect in LANDMARKS:
        z, _, _ = sample(dem, plat, plon)
        print("  %-20s %.3fN %.3fE %10s %s"
              % (name, plat, plon, ("%.1f" % z) if z is not None else "越界", expect))

    print("")
    print("---- 粗网格地形概览（60x40，上=北 左=西，每格≈%.1f km）----"
          % (dem.shape[1] / 60.0 * LON_PIX * 111320.0 * np.cos(np.deg2rad(22.6)) / 1000.0))
    ascii_map(dem, 60, 40)

    print("")
    print("---- 数据组装自检 ----")
    for k, v in checks.items():
        print("  %-34s : %s" % (k, v))
    return dict(stats)


def main():
    ap = argparse.ArgumentParser(description="SRTM 1角秒 → POSSIM-Web 目标 DEM 网格")
    ap.add_argument("--refresh", action="store_true", help="忽略缓存，强制重新下载瓦片")
    args = ap.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)

    print("bbox = W %.2f S %.2f E %.2f N %.2f ; nx=%d ny=%d ; 契约 pixel_deg=%.9f"
          % (W, S, E, N, NX, NY, PIXEL_DEG))
    print("瓦片缓存目录：%s" % CACHE_DIR)

    wanted = tiles_for_bbox(W, S, E, N)
    print("")
    print("[1/6] 下载 SRTM 瓦片（幂等）：%s" % ", ".join(tile_id(a, b) for a, b in wanted))
    files, fails = [], []
    for lat0, lon0 in wanted:
        gz, how = fetch_tile(lat0, lon0, refresh=args.refresh)
        print("  %s : %s" % (tile_id(lat0, lon0), how))
        (fails if gz is None else files).append(tile_id(lat0, lon0) if gz is None else (lat0, lon0, gz))
    if fails:
        print("")
        print("[警告] 瓦片下载失败：%s" % ", ".join(fails))
        print("[警告] 降级方案：只用已下载瓦片能覆盖的范围，缺口写 NODATA(-9999)，不做插值造假；"
              "check_data.py 会报 nodata 比例。")

    print("")
    print("[2/6] 拼接 1 角秒马赛克")
    mosaic, placed = assemble_mosaic(files)
    for tid, info in placed:
        print("  %s ← %s" % (tid, info))
    checks = {"马赛克 -32768 缺口": "%d / %d (%.3f%%)" % (
        int((mosaic == -32768).sum()), mosaic.size, 100.0 * (mosaic == -32768).sum() / mosaic.size)}
    try:
        t23 = load_tile(os.path.join(CACHE_DIR, "N23", "N23E113.hgt.gz"))
        t22 = load_tile(os.path.join(CACHE_DIR, "N22", "N22E113.hgt.gz"))
        same = bool(np.array_equal(t23[3600, :], t22[0, :]))
        checks["N23E113行3600==N22E113行0"] = "是（边界样本一致）" if same else "否（按 N22 为准）"
    except Exception as exc:  # noqa: BLE001
        checks["N23 边界样本一致性"] = "跳过（%s）" % exc

    print("")
    print("[3/6] 裁剪 bbox 窗口")
    win, (r0, r1, c0, c1) = extract_window(mosaic)
    print("  1角秒窗口：马赛克行 %d..%d 列 %d..%d → %s" % (r0, r1, c0, c1, win.shape))
    valid = win != -32768
    water_src = valid & (win < 0)
    land_src = valid & (win >= 0)
    land_val = np.where(land_src, win.astype(np.float32), 0.0).astype(np.float32)
    checks["窗口内水面旗标(<0)像元"] = "%d (%.1f%%)" % (int(water_src.sum()), 100.0 * water_src.sum() / win.size)
    checks["窗口内陆地像元(>=0)"] = "%d (%.1f%%)" % (int(land_src.sum()), 100.0 * land_src.sum() / win.size)
    checks["窗口内无数据像元"] = "%d (%.3f%%)" % (int((~valid).sum()), 100.0 * (~valid).sum() / win.size)
    print("  水面旗标 %d (%.1f%%) / 陆地 %d (%.1f%%) / 无数据 %d" % (
        int(water_src.sum()), 100.0 * water_src.sum() / win.size,
        int(land_src.sum()), 100.0 * land_src.sum() / win.size, int((~valid).sum())))

    print("")
    print("[4/6] 面积加权重采样到 %dx%d" % (NX, NY))
    row_edges = _edges((N - S) * ARCSEC, NY)
    col_edges = _edges((E - W) * ARCSEC, NX)
    wfrac, wcov = resample_field(water_src.astype(np.float32), valid, row_edges, col_edges)
    lval, lcov = resample_field(land_val, land_src, row_edges, col_edges)
    print("  水面占比 / 陆地覆盖率 / 陆地均值 三张 %s 图已生成" % (wfrac.shape,))

    print("")
    print("[5/6] 水体清理（开运算 + 连通域筛选）")
    ocean, ocean_raw, ocean_open, oinfo = clean_ocean(wfrac)
    for k, v in oinfo.items():
        checks[k] = ("%.1f" % v) if isinstance(v, float) else str(v)
    landmask = ~ocean
    filled, filled_ok = fill_undefined(lval.astype(np.float64), lcov > 0.02)
    dem = np.where(ocean, WATER_FLAG, np.maximum(filled, 0.0)).astype(np.float32)
    dem[(wcov < 0.5)] = NODATA
    checks["由水体改判为陆地的格"] = "%d (%.1f%%)" % (
        int((ocean_raw & ~ocean).sum()), 100.0 * (ocean_raw & ~ocean).sum() / NX / NY)
    checks["无陆地源像元(被填充)的格"] = "%d (%.2f%%)" % (
        int((landmask & ~filled_ok).sum()), 100.0 * (landmask & ~filled_ok).sum() / NX / NY)
    checks["NODATA 格数"] = "%d" % int((dem == NODATA).sum())
    print("  海洋格 %d (%.1f%%)  陆地格 %d (%.1f%%)  其中被填充 %d" % (
        int(ocean.sum()), 100.0 * ocean.mean(), int(landmask.sum()), 100.0 * landmask.mean(),
        int((landmask & ~filled_ok).sum())))

    print("")
    print("[6/6] 写出 data/dem.npy 并统计")
    dem = np.ascontiguousarray(dem, dtype=np.float32)
    np.save(DEM_PATH, dem)
    print("  已写出：%s (%.2f MB)" % (DEM_PATH, os.path.getsize(DEM_PATH) / 1048576.0))

    stats = dict(min=float(dem[dem != NODATA].min()), max=float(dem[dem != NODATA].max()),
                 land_pixels=int((dem > 0).sum()) + int((dem == 0).sum()),
                 water_pixels=int((dem < 0).sum()), nodata_pixels=int((dem == NODATA).sum()))
    report(dem, stats, checks)

    print("")
    print("---- 红线快检 ----")
    def chk(name, ok, extra=""):
        print("  %-34s : %s %s" % (name, "OK" if ok else "异常", extra))

    z_coast, _, _ = sample(dem, 22.60, 113.560)
    z_hill, _, _ = sample(dem, 22.249, 113.913)
    z_sea, _, _ = sample(dem, 22.55, 113.72)
    chk("岸线围田 0-5 m", 0.0 <= z_coast <= 5.0, "%.1f m" % z_coast)
    chk("内陆山地 > 200 m", z_hill > 200, "%.1f m" % z_hill)
    chk("伶仃洋为水域(<0)", z_sea < 0, "%.1f m" % z_sea)
    chk("nodata 占比 < 20%", stats["nodata_pixels"] / dem.size < 0.2,
        "%.4f%%" % (100.0 * stats["nodata_pixels"] / dem.size))
    chk("陆地像元 >= 30%", stats["land_pixels"] / dem.size >= 0.30,
        "%.2f%%" % (100.0 * stats["land_pixels"] / dem.size))
    chk("形状/ dtype", dem.shape == (NY, NX) and dem.dtype == np.float32,
        "%s %s" % (dem.shape, dem.dtype))
    return 0


if __name__ == "__main__":
    sys.exit(main())
