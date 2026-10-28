#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""scripts/build_site.py — 把 POSSIM-Web 大屏构建成「纯静态站点」。

为什么要这一步
--------------
大屏默认跑在 `python server.py` 起的本地服务上（numpy / 原 Fortran 内核实时推演）。
但简历链接需要「点开就能看」，而 GitHub Pages 只能托管静态文件。所以这里把

    3 场台风 × 2 套计算内核 × 2 个增幅档 = 12 组工况

全部离线算完，逐帧写成 PNG + 元数据 JSON，再让前端（web/js/static.js）改成读这些
静态文件。这样线上站点零后端、零依赖、可被 CDN 缓存，而本地 `server.py` 仍然是
完整能力（任意参数实时重算）。

用法
----
    python scripts/build_site.py --out ../docs          # 默认输出到仓库 docs/
    python scripts/build_site.py --out ../docs --skip-fortran   # 没 DLL 时只出 numpy
    python scripts/build_site.py --out ../docs --only mangkhut  # 调试单场台风

产物
----
    <out>/index.html                 项目介绍页（落地页）
    <out>/app.html                   大屏（静态数据模式）
    <out>/css|js/...                 前端资源
    <out>/data/meta.json             站点元数据（preview 指向静态 webp）
    <out>/data/typhoons/*.json       台风机理参数
    <out>/data/build/manifest.json   12 组工况索引 + 指标 + 灵敏度扫描
    <out>/data/build/<tid>/<kernel>_<amp>/sim.json + f000.png ...
    <out>/assets/*.webp              展示用截图
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import api as A  # noqa: E402

TYPHOONS = [("mangkhut", "山竹", "Mangkhut", 2018),
            ("saola", "苏拉", "Saola", 2023),
            ("hato", "天鸽", "Hato", 2017)]
KERNELS = [("numpy", "numpy 复算"), ("fortran", "原 Fortran 内核")]
AMPS = [3.8, 1.0]


def log(msg: str) -> None:
    print(msg, flush=True)


def amp_key(amp: float) -> str:
    return "3.8" if float(amp) >= 2 else "1.0"


# ------------------------------------------------------------------ 单组工况
def build_case(out: Path, tid: str, kernel: str, amp: float) -> dict | None:
    payload = {
        "typhoon_id": tid,
        "kernel": kernel,
        "fortran_amp": float(amp),
        "params": {"coarse_amp": float(amp)},
    }
    t0 = time.perf_counter()
    res = A.simulate(payload)
    wall = time.perf_counter() - t0
    if res.get("error"):
        log("  !! %s/%s/%s 失败：%s" % (tid, kernel, amp_key(amp), res["error"]))
        return None
    actual = (res.get("kernel") or {}).get("kernel", "numpy")
    if actual != kernel:
        log("  !! %s/%s/%s 请求内核不可用，已回退 %s（跳过该组）"
            % (tid, kernel, amp_key(amp), actual))
        return None

    rel = Path("data/build") / tid / ("%s_%s" % (kernel, amp_key(amp)))
    dest = out / rel
    dest.mkdir(parents=True, exist_ok=True)

    frames = res.get("frames") or []
    for f in frames:
        b64 = f.pop("png_b64", "") or ""
        if b64:
            (dest / ("f%03d.png" % int(f.get("i", 0)))).write_bytes(base64.b64decode(b64))
        f["img"] = str(rel).replace("\\", "/") + "/f%03d.png" % int(f.get("i", 0))

    sim = {
        "ok": True,
        "static": True,
        "typhoon_id": res.get("typhoon_id", tid),
        "bbox": res.get("bbox"),
        "nx": res.get("nx"),
        "ny": res.get("ny"),
        "pixel_m": res.get("pixel_m"),
        "kernel": res.get("kernel"),
        "params": res.get("params"),
        "frames": frames,
        "timeseries": res.get("timeseries") or [],
        "summary": res.get("summary") or {},
        "elapsed_ms": int(round((res.get("elapsed_ms") or wall * 1000))),
        "warn": res.get("warn"),
    }
    with open(dest / "sim.json", "w", encoding="utf-8") as fh:
        json.dump(sim, fh, ensure_ascii=False, separators=(",", ":"))

    s = sim["summary"]
    entry = {
        "tid": tid,
        "kernel": kernel,
        "amp": amp_key(amp),
        "dir": str(rel).replace("\\", "/") + "/",
        "frames": len(frames),
        "elapsed_ms": sim["elapsed_ms"],
        "peak": {
            "surge_m": (s.get("max_surge") or {}).get("value"),
            "surge_at": (s.get("max_surge") or {}).get("label"),
            "area_ha": (s.get("max_area") or {}).get("value"),
            "area_at": (s.get("max_area") or {}).get("label"),
            "depth_m": (s.get("max_depth") or {}).get("value"),
            "villages": s.get("affected_villages"),
            "pop": s.get("affected_pop"),
        },
        "summary": s,
    }
    log("  ok %-9s %-8s amp=%-3s  %2d 帧  %.1f s  surge=%.3f m  area=%.0f ha"
        % (tid, kernel, amp_key(amp), len(frames), sim["elapsed_ms"] / 1000.0,
           entry["peak"]["surge_m"] or 0, entry["peak"]["area_ha"] or 0))
    return entry


# ------------------------------------------------------------------ 灵敏度扫描
def build_sensitivity(out: Path) -> dict:
    """固定山竹 + numpy + 3.8 档，扫 Vmax，得到「峰值增水 / 淹没面积」响应曲线。

    这张曲线是落地页里「参数真的在算，不是切图」的证据：Vmax 一改，两个量都跟着变。
    """
    rows = []
    for vmax in (75, 90, 105, 120, 135, 150, 165):
        res = A.simulate({
            "typhoon_id": "mangkhut",
            "kernel": "numpy",
            "params": {"coarse_amp": 3.8},
            "overrides": {"vmax_kt": vmax},
        })
        if res.get("error"):
            continue
        s = res.get("summary") or {}
        peak_surge = (s.get("max_surge") or {}).get("value")
        peak_area = (s.get("max_area") or {}).get("value")
        # 逐帧最大总水位，作为「水位—面积」关系的补充
        twl = max([float(f.get("max_twl") or 0) for f in (res.get("frames") or [])] or [0])
        rows.append({"vmax_kt": vmax, "surge_m": peak_surge, "area_ha": peak_area,
                     "twl_m": round(twl, 3), "pop": s.get("affected_pop"),
                     "villages": s.get("affected_villages")})
        log("  sweep Vmax=%3d kt  surge=%.3f m  area=%.0f ha" % (vmax, peak_surge or 0, peak_area or 0))
    return {"typhoon": "mangkhut", "kernel": "numpy", "amp": "3.8",
            "note": "Vmax 覆盖整条路径点；其余参数取 meta.defaults", "rows": rows}


# ------------------------------------------------------------------ 资源拷贝
def base_path_of(out: Path) -> str:
    """GitHub Pages 子路径自动推断：.../POSSIM_web_china/docs → /POSSIM_web_china"""
    parts = out.resolve().parts
    if "docs" in parts:
        i = parts.index("docs")
        if i > 0:
            return "/" + parts[i - 1]
    return ""


def copy_assets(out: Path) -> None:
    web = ROOT / "web"
    for sub in ("css", "js"):
        src = web / sub
        dst = out / sub
        dst.mkdir(parents=True, exist_ok=True)
        for f in sorted(src.glob("*")):
            if f.is_file():
                shutil.copy2(f, dst / f.name)
    # 字体/图标暂无本地资源；第三方库仍走 CDN（见 index.html 注释）

    data = ROOT / "data"
    (out / "data" / "typhoons").mkdir(parents=True, exist_ok=True)
    for f in sorted((data / "typhoons").glob("*.json")):
        shutil.copy2(f, out / "data" / "typhoons" / f.name)
    shutil.copy2(data / "villages.json", out / "data" / "villages.json")

    meta = json.load(open(data / "meta.json", encoding="utf-8"))
    meta.setdefault("map", {})["preview_png"] = "data/dem_preview.webp"
    meta["site"]["note"] = ("数据：SRTM 1 角秒（约 30 m）重采样至 94.3 m 网格 · "
                            "台风参数为历史量级重放，非预报")
    with open(out / "data" / "meta.json", "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1)

    try:
        from PIL import Image
    except Exception:
        shutil.copy2(data / "dem_preview.png", out / "data" / "dem_preview.png")
        return 1
    im = Image.open(data / "dem_preview.png").convert("RGB")
    im.save(out / "data" / "dem_preview.webp", "WEBP", quality=82, method=6)
    return 0


def build_gallery(out: Path, shots_dir: Path | None = None) -> list:
    """把验收截图压成网站可用尺寸，落地页做「成品图」与首屏主视觉。

    源截图放在仓库外（体积大），用 --shots 指定；找不到源图时，只要 out/assets
    里已有同名 webp 就直接复用，保证二次构建不会把落地页的图弄丢。
    """
    from PIL import Image

    src_dir = shots_dir or (ROOT.parent / "03-截图")
    dst_dir = out / "assets"
    dst_dir.mkdir(parents=True, exist_ok=True)
    # (源文件, 目标文件, 图注, 目标宽度)
    mapping = [
        ("clean_numpy_h020.png", "hero-shot.webp", "山竹 +020h 淹没范围（numpy 内核）", 1600),
        ("clean_numpy_h020.png", "shot-numpy-clean.webp", "山竹 · +020h · numpy 内核 · 3.8 档", 1400),
        ("clean_fortran_h021.png", "shot-fortran-clean.webp", "天鸽 · +021h · 原 Fortran 内核 · 3.8 档", 1400),
        ("wind_fix_1920_h24.png", "shot-wind.webp", "粒子风场图层（山竹 +024h，6000 粒子）", 1400),
        ("final_1280.png", "shot-1280.webp", "1280×720 下的自适应布局", 1280),
    ]
    made = []
    for src, dst, cap, width in mapping:
        p = src_dir / src
        if p.exists():
            im = Image.open(p).convert("RGB")
            if im.width > width:
                h = int(round(im.height * width / float(im.width)))
                im = im.resize((width, h), Image.LANCZOS)
            im.save(dst_dir / dst, "WEBP", quality=82, method=6)
            log("  webp %-28s %6.0f KB" % (dst, (dst_dir / dst).stat().st_size / 1024))
        elif not (dst_dir / dst).exists():
            log("  !! 缺少源图 %s，且 %s 也不存在，跳过" % (src, dst))
            continue
        else:
            log("  keep %-28s（复用已有 webp）" % dst)
        made.append({"file": "assets/" + dst, "caption": cap})
    # 首屏主视觉单独标记
    for m in made:
        if m["file"].endswith("hero-shot.webp"):
            m["hero"] = True
    return made


# ------------------------------------------------------------------ HTML
def build_app_html(out: Path) -> None:
    src = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    s = src.replace('<meta name="robots" content="noindex">',
                    '<meta name="robots" content="index,follow">')
    s = s.replace('<meta name="viewport" content="width=device-width,initial-scale=1">',
                  '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
                  '<meta name="theme-color" content="#04101f">\n'
                  '<meta name="description" content="POSSIM-Web：珠江口风暴潮淹没推演大屏'
                  '（1188×864 栅格、36 小时、37 帧、原 Fortran 内核直连）。静态演示版。">\n'
                  '<meta property="og:type" content="website">\n'
                  '<meta property="og:title" content="珠江口风暴潮淹没推演平台 · POSSIM-Web">\n'
                  '<meta property="og:image" content="assets/hero-shot.webp">\n'
                  '<meta name="twitter:card" content="summary_large_image">\n'
                  '<link rel="icon" href="data:image/svg+xml,'
                  '%3Csvg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 32 32\'%3E'
                  '%3Crect width=\'32\' height=\'32\' rx=\'7\' fill=\'%23061a2e\'/%3E'
                  '%3Cpath d=\'M6 20c3-6 6 2 9-3s5 1 9-4\' stroke=\'%2335e0ff\' stroke-width=\'2.4\' '
                  'fill=\'none\' stroke-linecap=\'round\'/%3E%3C/svg%3E">')
    # 静态模式注入：先置标记，再在 api.js 之后挂 static.js 覆盖数据源
    s = s.replace('<script src="js/api.js"></script>',
                  '<script>window.POSSIM_STATIC = true;</script>\n'
                  '<script src="js/api.js"></script>\n'
                  '<script src="js/static.js"></script>')
    # 顶栏左上角加「返回项目主页」
    s = s.replace('<header class="topbar">',
                  '<header class="topbar">\n        <a class="home-link" href="index.html" title="返回项目介绍">'
                  '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" '
                  'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">'
                  '<path d="M15 5l-7 7 7 7"/></svg>项目主页</a>')
    s = s.replace('<div class="loading-box">',
                  '<div class="loading-box">')
    (out / "app.html").write_text(s, encoding="utf-8")
    log("  app.html 已生成（静态模式注入）")


def build_landing(out: Path) -> None:
    src = ROOT / "web" / "landing"
    if not src.exists():
        log("  ! web/landing 不存在，跳过落地页")
        return
    for f in sorted(src.glob("*")):
        if f.is_file():
            shutil.copy2(f, out / f.name)
            log("  landing %s" % f.name)


# ------------------------------------------------------------------ main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT.parent / "docs"), help="输出目录")
    ap.add_argument("--only", default="", help="只构建指定台风 id")
    ap.add_argument("--skip-fortran", action="store_true")
    ap.add_argument("--skip-sweep", action="store_true")
    ap.add_argument("--shots", default="", help="验收截图源目录（默认 ../03-截图）")
    ap.add_argument("--html-only", action="store_true",
                    help="只重生成 html/资源，不动已算好的帧数据（改前端时用，秒级）")
    args = ap.parse_args()

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    log("== 输出目录 %s" % out)

    if args.html_only:
        log("· --html-only：跳过推演，仅重生成 HTML 与静态资源")
        copy_assets(out)
        build_app_html(out)
        build_landing(out)
        log("== 完成（html-only）")
        return 0

    typhoons = [t for t in TYPHOONS if not args.only or t[0] == args.only]
    kernels = [k for k in KERNELS if not (args.skip_fortran and k[0] == "fortran")]

    cases = []
    t_all = time.perf_counter()
    for tid, name, name_en, year in typhoons:
        for kernel, _label in kernels:
            for amp in AMPS:
                log("· 构建 %s / %s / %s" % (tid, kernel, amp_key(amp)))
                e = build_case(out, tid, kernel, amp)
                if e:
                    e.update({"name": name, "name_en": name_en, "year": year})
                    cases.append(e)

    expected = len(typhoons) * len(kernels) * len(AMPS)
    if len(cases) < expected:
        log("\n!! 只构建了 %d/%d 组工况 —— 原厂 DLL 不可用时 fortran 组会被跳过。" % (len(cases), expected))
        log("   要出完整的 12 组，请设置 POSSIM_FORTRAN_DLL 指向 storm_surge_physics.dll 后重跑：")
        log("   POSSIM_FORTRAN_DLL=<path>/storm_surge_physics.dll python scripts/build_site.py --out docs")

    sweep = {} if args.skip_sweep else build_sensitivity(out)

    manifest = {
        "built_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generator": "scripts/build_site.py",
        "site": {"title": "POSSIM-Web · 珠江口风暴潮淹没推演平台",
                 "region": "珠江口-伶仃洋", "bbox": [113.2, 22.2, 114.2, 23.0],
                 "grid": [1188, 864], "pixel_m": 94.3, "hours": 36, "frames": 37},
        "typhoons": [{"tid": t[0], "name": t[1], "name_en": t[2], "year": t[3]} for t in TYPHOONS],
        "kernels": [{"id": k[0], "label": k[1]} for k in KERNELS],
        "cases": cases,
        "sensitivity": sweep,
    }
    (out / "data" / "build").mkdir(parents=True, exist_ok=True)
    with open(out / "data" / "build" / "manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)

    log("· 拷贝前端资源")
    no_pil = copy_assets(out)
    build_app_html(out)
    build_landing(out)
    gallery = []
    if not no_pil:
        log("· 压缩展示截图")
        gallery = build_gallery(out, Path(args.shots) if args.shots else None)
        manifest["gallery"] = gallery
        with open(out / "data" / "build" / "manifest.json", "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=1)

    total = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    n_png = len(list(out.rglob("f*.png")))
    log("\n== 完成：%d 组工况 · %d 张帧图 · 站点 %.1f MB · 用时 %.0f s"
        % (len(cases), n_png, total / 1048576.0, time.perf_counter() - t_all))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
