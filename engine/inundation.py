# -*- coding: utf-8 -*-
"""淹没判定、连通性过滤与深度分级（契约 §5.6）。

纯 numpy 实现，只有连通斑块标记的内部使用 Python 循环（循环次数是"行程数"，
量级 1e3~1e4，与像素总数无关），不做逐像素 Python 循环。
"""

from __future__ import annotations

import math

import numpy as np

from . import config as C


# ------------------------------------------------------------------ 静态掩膜
def build_masks(dem, pixel_m: float):
    """一次性预计算与帧无关的掩膜（陆地/海洋/海岸种子/近岸带）。"""
    dem = np.asarray(dem, dtype=np.float32)
    nodata = dem <= np.float32(-9000.0)
    has_neg = bool((dem < np.float32(0.0)).any())
    # 契约 §2 说 SRTM 海洋为 0，§5 又说 <0 是海洋；两种数据都能正确识别
    sea = (dem < 0.0) if has_neg else (dem <= 0.0)
    sea = sea & (~nodata)
    land = (~sea) & (~nodata)

    # 8 邻域膨胀 N 次 ≈ 距海 N 个像素
    n_ring = max(1, int(math.ceil(C.NEARSHORE_M / max(pixel_m, 1.0))))
    near1 = _dilate8(sea, 1)
    nearshore = _dilate8(sea, n_ring)
    # 海岸种子像素：DEM 在 [0, 1.5] m 且 8 邻域内有海洋像素
    seeds = land & (dem <= np.float32(C.COAST_SEED_MAX_DEM)) & near1
    return {
        "nodata": nodata,
        "sea": sea,
        "land": land,
        "seeds": seeds,
        "nearshore": nearshore & land,
        "land_pixels": int(land.sum()),
        "near_pixels": n_ring,
    }


def _dilate8(mask, n: int):
    """8 邻域二值膨胀 n 次（n 很小，逐次 numpy 平移即可）。"""
    out = mask.copy()
    if n <= 0:
        return out
    for _ in range(n):
        src = out
        acc = src.copy()
        acc[1:, :] |= src[:-1, :]
        acc[:-1, :] |= src[1:, :]
        acc[:, 1:] |= src[:, :-1]
        acc[:, :-1] |= src[:, 1:]
        acc[1:, 1:] |= src[:-1, :-1]
        acc[1:, :-1] |= src[:-1, 1:]
        acc[:-1, 1:] |= src[1:, :-1]
        acc[:-1, :-1] |= src[1:, 1:]
        out = acc
    return out


# ------------------------------------------------------------------ 连通性
class _UnionFind(object):
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        p = self.parent
        root = x
        while p[root] != root:
            root = p[root]
        while p[x] != root:
            p[x], x = root, p[x]
        return root

    def union(self, a: int, b: int) -> None:
        ra = self.find(a)
        rb = self.find(b)
        if ra != rb:
            # 小的挂到大的下面（按秩合并的简化版）
            if ra < rb:
                self.parent[rb] = ra
            else:
                self.parent[ra] = rb


def _runs(mask_2d):
    """把二值掩膜拆成"行内水平行程"。返回 (start, end, 行数指针)。"""
    ny, nx = mask_2d.shape
    flat = mask_2d.ravel()
    m = flat.view(np.int8) if flat.dtype == np.bool_ else flat.astype(np.int8)
    size = flat.size
    if not flat.any():
        empty_i = np.empty(0, dtype=np.int64)
        return empty_i, empty_i
    diff = np.diff(m)
    starts = np.flatnonzero(diff == 1) + 1
    ends = np.flatnonzero(diff == -1) + 1
    if m[0]:
        starts = np.concatenate(([0], starts))
    if m[-1]:
        ends = np.concatenate((ends, [size]))
    # 行程不得跨行：行首所在处若左右像素都淹，则在该处分段
    parts = [starts, ends, np.array([0, size], dtype=np.int64)]
    if ny > 1:
        bnd = np.arange(1, ny, dtype=np.int64) * nx
        bnd = bnd[(flat[bnd - 1]) & (flat[bnd])]
        if bnd.size:
            parts.append(bnd)
    cuts = np.concatenate(parts)
    cuts.sort()                     # 排序去重（比 np.union1d 的哈希实现快得多）
    keep = np.empty(cuts.size, dtype=bool)
    keep[0] = True
    np.not_equal(cuts[1:], cuts[:-1], out=keep[1:])
    cuts = cuts[keep]
    a = cuts[:-1]
    b = cuts[1:]
    keep = flat[a]
    return a[keep], b[keep]


def connected_filter(mask_2d, seeds_2d, min_pixels: int = C.MIN_PIXELS):
    """4 连通洪泛过滤：丢弃像素数 < min_pixels 或缺少海岸种子像素的斑块。"""
    mask_2d = np.ascontiguousarray(mask_2d, dtype=bool)
    if not mask_2d.any():
        return mask_2d.copy()
    ny, nx = mask_2d.shape
    a, b = _runs(mask_2d)
    nruns = a.size
    if nruns == 0:
        return np.zeros_like(mask_2d)
    lens = (b - a).astype(np.int64)
    rows = a // nx
    cols0 = a - rows * nx
    cols1 = b - rows * nx

    uf = _UnionFind(nruns)
    row_ptr = np.searchsorted(rows, np.arange(ny + 1, dtype=np.int64))
    for r in range(ny - 1):
        i0, i1 = int(row_ptr[r]), int(row_ptr[r + 1])
        j0, j1 = int(row_ptr[r + 1]), int(row_ptr[r + 2])
        if i0 >= i1 or j0 >= j1:
            continue
        c0 = cols0[i0:i1]
        c1 = cols1[i0:i1]
        k0 = cols0[j0:j1]
        k1 = cols1[j0:j1]
        lo = np.searchsorted(k1, c0, side="right")
        hi = np.searchsorted(k0, c1, side="left")
        for i in range(i1 - i0):
            li = int(lo[i])
            hi_i = int(hi[i])
            if li >= hi_i:
                continue
            for j in range(li, hi_i):
                uf.union(i0 + i, j0 + j)

    roots = np.empty(nruns, dtype=np.int64)
    for i in range(nruns):
        roots[i] = uf.find(i)
    px_count = np.bincount(roots, weights=lens.astype(np.float64), minlength=nruns)
    seed_flat = np.ascontiguousarray(seeds_2d, dtype=bool).ravel()
    sp = np.zeros(seed_flat.size + 1, dtype=np.int64)
    np.cumsum(seed_flat, dtype=np.int64, out=sp[1:])
    seed_in_run = (sp[b] - sp[a]).astype(np.float64)
    seed_count = np.bincount(roots, weights=seed_in_run, minlength=nruns)

    # px_count / seed_count 是按"连通分量根"聚合的，必须先回代到每个行程上
    keep_root = (px_count >= float(min_pixels)) & (seed_count >= 0.5)
    keep_run = keep_root[roots]
    if not keep_run.any():
        return np.zeros_like(mask_2d)

    # 把"行程级"的结论展布回像素
    total = int(lens.sum())
    run_of_pixel = np.repeat(np.arange(nruns, dtype=np.int64), lens)
    offsets = np.cumsum(lens) - lens
    base = np.repeat(a, lens)
    seq = np.arange(total, dtype=np.int64) - np.repeat(offsets, lens)
    out = np.zeros(mask_2d.size, dtype=bool)
    out[base + seq] = keep_run[run_of_pixel]
    return out.reshape(mask_2d.shape)


# ------------------------------------------------------------------ 分级
def classify(depth):
    """深度分级索引 0..6（对应 CATEGORY_KEYS），负值/0 归 0 档。"""
    d = np.asarray(depth, dtype=np.float32)
    idx = np.digitize(d, np.asarray(C.CATEGORY_BINS[1:], dtype=np.float32))
    return idx.astype(np.int8)


# ------------------------------------------------------------------ 单帧淹没
def inundate(dem, h, tide: float, masks: dict, params: dict):
    """由增水场 h 与天文潮位算出该帧淹没深度场。"""
    land = masks["land"]
    runup = float(params["wave_runup"])
    twl = h + np.float32(tide)
    depth = (twl - dem).astype(np.float32)
    # 离岸 1 km 内叠加 wave_runup
    depth = np.where(masks["nearshore"], depth * np.float32(runup), depth).astype(np.float32)
    flood = land & (depth > np.float32(0.0)) & (h >= np.float32(C.SURGE_GATE))
    flood = connected_filter(flood, masks["seeds"], C.MIN_PIXELS)
    depth = np.where(flood, depth, np.float32(0.0)).astype(np.float32)

    max_surge = float(h.max(where=land, initial=0.0))
    if flood.any():
        max_depth = float(depth.max())
        px = int(np.count_nonzero(flood))
        cat_idx = classify(depth[flood])
        counts = np.bincount(cat_idx, minlength=len(C.CATEGORY_KEYS))
    else:
        max_depth = 0.0
        px = 0
        counts = np.zeros(len(C.CATEGORY_KEYS), dtype=np.int64)
    return {
        "depth": depth,
        "mask": flood,
        "max_surge": max_surge,
        "max_twl": max_surge + float(tide),
        "max_depth": max_depth,
        "flooded_px": px,
        "category_counts": {k: int(v) for k, v in zip(C.CATEGORY_KEYS, counts)},
    }


def flooded_ha(px: int, pixel_m: float) -> float:
    return px * pixel_m * pixel_m / 10000.0


def flooded_pct(px: int, land_px: int) -> float:
    if land_px <= 0:
        return 0.0
    return px / float(land_px) * 100.0
