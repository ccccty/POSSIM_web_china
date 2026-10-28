# -*- coding: utf-8 -*-
"""深度场 -> 透明背景 RGBA PNG（Pillow）。

整幅图的着色是一次 numpy 高级索引查表完成的，没有任何逐像素 Python 循环：
    idx = 0(未淹) 或 1..7(分级)，rgba = PALETTE[idx]
"""

from __future__ import annotations

import base64
import io

import numpy as np
from PIL import Image, ImageDraw

from . import config as C


def _palette() -> np.ndarray:
    pal = np.zeros((len(C.CATEGORY_RGBA) + 1, 4), dtype=np.uint8)
    for i, rgba in enumerate(C.CATEGORY_RGBA):
        pal[i + 1] = rgba
    return pal


PALETTE = _palette()


def depth_to_rgba(depth, mask):
    """深度场 -> (ny, nx, 4) uint8 RGBA；未淹没像素 alpha=0。"""
    d = np.asarray(depth, dtype=np.float32)
    idx = np.zeros(d.shape, dtype=np.uint8)
    if mask is not None and bool(np.asarray(mask).any()):
        m = np.asarray(mask, dtype=bool)
        bins = np.asarray(C.CATEGORY_BINS[1:], dtype=np.float32)
        idx[m] = (np.digitize(d[m], bins) + 1).astype(np.uint8)
    return PALETTE[idx]


def _draw_legend(img: Image.Image) -> None:
    """右下角极简色带图例（只画色块，不写字）。"""
    n = len(C.CATEGORY_RGBA)
    bw, bh, pad = C.LEGEND_BLOCK_W, C.LEGEND_BLOCK_H, C.LEGEND_PAD
    w = bw + pad * 2
    h = n * bh + pad * 2
    x0 = img.width - w - 14
    y0 = img.height - h - 14
    if x0 < 0 or y0 < 0:
        return
    draw = ImageDraw.Draw(img, "RGBA")
    draw.rectangle([x0, y0, x0 + w - 1, y0 + h - 1], fill=(4, 16, 31, 168),
                   outline=(53, 224, 255, 120))
    for i, rgba in enumerate(C.CATEGORY_RGBA):
        top = y0 + pad + i * bh
        draw.rectangle([x0 + pad, top, x0 + pad + bw - 1, top + bh - 2], fill=rgba)


def render_png(depth, mask, width: int, height: int, legend: bool = C.DRAW_LEGEND) -> bytes:
    """把深度场渲染成 PNG 字节串（RGBA，透明背景）。"""
    rgba = depth_to_rgba(depth, mask)
    img = Image.fromarray(rgba, mode="RGBA")
    if img.width != width or img.height != height:
        img = img.resize((width, height), Image.NEAREST)
    if legend:
        _draw_legend(img)
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=C.PNG_COMPRESS_LEVEL, optimize=False)
    return buf.getvalue()


def render_b64(depth, mask, width: int, height: int, legend: bool = C.DRAW_LEGEND) -> str:
    return base64.b64encode(render_png(depth, mask, width, height, legend)).decode("ascii")


def check_png(b64_text: str):
    """自检用：解码 PNG 并返回 (宽, 高, 模式)。"""
    raw = base64.b64decode(b64_text)
    with Image.open(io.BytesIO(raw)) as im:
        return im.width, im.height, im.mode
