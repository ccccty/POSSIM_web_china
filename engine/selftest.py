# -*- coding: utf-8 -*-
"""engine.selftest · 契约 §5 合理性红线自检。

用法：
    python -m engine.selftest
全部 PASS 返回码 0，任何一项 FAIL 返回码 1（并打印校验表）。
"""

from __future__ import annotations

import io
import json
import struct
import sys
import time

import numpy as np

from . import api, config as C, physics, render, tide as tide_mod


ROWS = []


def _setup_stdout():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def check(name: str, measured, ok: bool, expect: str):
    ROWS.append((name, measured, expect, bool(ok)))


def _fmt(v):
    if isinstance(v, float):
        return "%.4f" % v
    return str(v)


def print_table() -> bool:
    head = ("项", "实测", "阈值/期望", "结论")
    w = [max([len(head[i])] + [len(_fmt(r[i])) for r in ROWS]) for i in range(3)]
    w.append(6)
    line = "+-" + "-+-".join("-" * x for x in w) + "-+"
    print(line)
    print("| " + " | ".join(head[i].ljust(w[i]) for i in range(4)) + " |")
    print(line)
    for name, measured, expect, ok in ROWS:
        print("| " + name.ljust(w[0]) + " | " + _fmt(measured).rjust(w[1]) + " | "
              + expect.ljust(w[2]) + " | " + ("PASS" if ok else "FAIL").ljust(4) + " |")
    print(line)
    failed = [r for r in ROWS if not r[3]]
    print("共 %d 项，PASS %d，FAIL %d" % (len(ROWS), len(ROWS) - len(failed), len(failed)))
    for r in failed:
        print("  FAIL: %s（实测 %s，要求 %s）" % (r[0], _fmt(r[1]), r[2]))
    return not failed


def _env_info():
    print("=" * 78)
    print("POSSIM-Web engine selftest")
    print("python %s | numpy %s" % (sys.version.split()[0], np.__version__))
    try:
        import PIL
        print("Pillow %s" % PIL.__version__)
    except Exception:
        print("Pillow 不可用")
    print("data dir:", api.DATA_DIR, "exists:", api.DATA_DIR.exists())
    print("=" * 78)


def _png_info(b64_text: str):
    """解码 PNG：返回 (宽, 高, 模式, ndarray)。"""
    import base64
    raw = base64.b64decode(b64_text)
    from PIL import Image
    im = Image.open(io.BytesIO(raw))
    arr = np.array(im)
    return im.width, im.height, im.mode, arr


def _far_track() -> dict:
    """构造一条始终远离 DEM 中心的路径，用于验证"800 km 外必须零淹没"。"""
    pts = []
    for k in range(5):
        pts.append({
            "t": "2018-09-15T%02d:00Z" % (k * 3),
            "lat": 12.0 + 0.3 * k, "lon": 130.0 - 0.4 * k,
            "pc_mb": 900, "vmax_kt": 130, "motion_kt": 20, "heading_deg": 300,
            "r34": [200, 190, 170, 195], "r50": [130, 120, 105, 125], "r64": [70, 65, 55, 68],
        })
    return {"id": "faraway", "name": "远场测试", "name_en": "Faraway", "year": 2018,
            "track": pts, "landfall": {"t": "2018-09-16T00:00Z", "lat": 12.0, "lon": 130.0}}


def main() -> int:
    _setup_stdout()
    _env_info()
    t_all = time.perf_counter()

    # ---------------- 1. 主推演（mangkhut，36 帧） ----------------
    payload = {"typhoon_id": "mangkhut", "hours": 36, "step_hours": 1}
    t0 = time.perf_counter()
    res = api.simulate(payload)
    wall = time.perf_counter() - t0
    if not res.get("ok"):
        print("simulate 失败：", res.get("error"))
        return 1
    frames = res["frames"]
    ts = res["timeseries"]
    print("主推演：ok=%s 帧数=%d 耗时 %.2f s（engine 自报 %d ms），warn=%s"
          % (res["ok"], len(frames), wall, res["elapsed_ms"], res.get("warn")))
    print("参数：", json.dumps(res["params"], ensure_ascii=False))

    surges = [f["max_surge"] for f in frames]
    depths = [f["max_depth"] for f in frames]
    areas = [f["flooded_ha"] for f in frames]
    max_surge = max(surges)
    max_depth = max(depths)
    max_area = max(areas)

    check("帧数 = hours/step + 1", len(frames), len(frames) == 37, "== 37")
    check("max_surge（§5 红线 1.0~3.5 m）", max_surge,
          1.0 <= max_surge <= 3.5, "1.0 <= v <= 3.5")
    check("max_depth <= max_surge*2.5", max_depth,
          max_depth <= max_surge * 2.5 + 1e-6, "<= %.3f" % (max_surge * 2.5))
    check("峰值淹没面积 > 0 ha", max_area, max_area > 0.0, "> 0")
    check("Hour +018 淹没面积 > 0 ha", frames[18]["flooded_ha"],
          frames[18]["flooded_ha"] > 0.0, "> 0（验收命令用帧）")

    # 分级计数与淹没像元数一致
    px_px = round(frames[int(np.argmax(areas))]["flooded_ha"] * 1e4 / res["pixel_m"] ** 2)
    cat_sum = sum(frames[int(np.argmax(areas))]["categories"].values())
    check("分级计数之和 == 淹没像元数", cat_sum, cat_sum == px_px, "== %d" % px_px)

    # 时间序列与帧一致
    check("timeseries 与 frames 等长", len(ts),
          len(ts) == len(frames) and abs(ts[10]["tide"] - frames[10]["tide"]) < 1e-6, "== 帧数")

    # ---------------- 2. PNG 形态 ----------------
    w, h, mode, arr = _png_info(frames[0]["png_b64"])
    check("首帧 PNG 尺寸", "%dx%d" % (w, h), (w, h) == (res["nx"], res["ny"]), "1188x864")
    check("首帧 PNG 模式", mode, mode == "RGBA", "RGBA")
    check("首帧 PNG ndarray 形状", str(arr.shape),
          arr.shape == (res["ny"], res["nx"], 4) and arr.dtype == np.uint8, "(864,1188,4) uint8")
    # 有淹没的帧 alpha 必须真的有非零值
    hi = int(np.argmax(areas))
    _, _, _, arr_hi = _png_info(frames[hi]["png_b64"])
    check("峰值帧 PNG 非透明像元数", int((arr_hi[..., 3] > 0).sum()),
          int((arr_hi[..., 3] > 0).sum()) > 100, "> 100")
    check("无淹没帧（Hour +000）完全透明",
          int((_png_info(frames[0]["png_b64"])[3][..., 3] > 0).sum()),
          int((_png_info(frames[0]["png_b64"])[3][..., 3] > 0).sum()) == 0, "== 0")

    # ---------------- 3. 风增水标定点（契约 §5.4） ----------------
    hw = float(physics.wind_setup(np.float32(50.0), np.float32(20.0), C.FETCH_REF_M))
    check("风增水标定点 h_w(W=50,h_ref=20)", hw, 1.2 <= hw <= 2.0, "1.2 <= h_w <= 2.0")

    # ---------------- 4. overrides 必须真的生效 ----------------
    base = api.simulate({"typhoon_id": "mangkhut", "hours": 36, "step_hours": 2})
    up = api.simulate({"typhoon_id": "mangkhut", "hours": 36, "step_hours": 2,
                       "overrides": {"vmax_kt": 140}})
    if not base.get("ok") or not up.get("ok"):
        print("overrides 测试失败：", base.get("error"), up.get("error"))
        return 1
    b_surge = max(f["max_surge"] for f in base["frames"])
    u_surge = max(f["max_surge"] for f in up["frames"])
    check("overrides vmax 115->140 后 max_surge 变大",
          "%.4f -> %.4f" % (b_surge, u_surge), u_surge > b_surge + 1e-3, "严格变大")
    b_pc = api.simulate({"typhoon_id": "mangkhut", "hours": 12, "step_hours": 4,
                         "overrides": {"pc_mb": 880}})
    if b_pc.get("ok"):
        pc_surge = max(f["max_surge"] for f in b_pc["frames"])
        ref = api.simulate({"typhoon_id": "mangkhut", "hours": 12, "step_hours": 4})
        ref_surge = max(f["max_surge"] for f in ref["frames"]) if ref.get("ok") else 0.0
        check("overrides pc_mb 930->880 后 max_surge 变大",
              "%.4f -> %.4f" % (ref_surge, pc_surge), pc_surge > ref_surge + 1e-3, "严格变大")

    # ---------------- 5. 淹没面积随 Vmax 单调不减 ----------------
    sweep = []
    for vmax in (70, 95, 115, 140):
        r = api.simulate({"typhoon_id": "mangkhut", "hours": 36, "step_hours": 2,
                          "overrides": {"vmax_kt": vmax}})
        if not r.get("ok"):
            print("Vmax 扫描失败 vmax=%s：%s" % (vmax, r.get("error")))
            return 1
        sweep.append((vmax, sum(f["flooded_ha"] for f in r["frames"])))
    vals = [v for _, v in sweep]
    mono = all(vals[i + 1] >= vals[i] - 1e-9 for i in range(len(vals) - 1))
    check("淹没面积随 Vmax 单调不减", " / ".join("%dkt:%.1f" % (v, a) for v, a in sweep),
          mono, "逐档不减")

    # ---------------- 6. >800 km 帧必须零淹没 ----------------
    far = api.simulate({"_typhoon": _far_track(), "typhoon_id": "faraway",
                        "hours": 12, "step_hours": 1})
    if not far.get("ok"):
        print("远场测试失败：", far.get("error"))
        return 1
    far_areas = [f["flooded_ha"] for f in far["frames"]]
    far_dist = [t["dist_km"] for t in far["timeseries"]]
    all_far = all(d > 800.0 for d in far_dist)
    check("远场路径中心距 DEM 中心（km）", "%.0f" % min(far_dist), all_far, "全部 > 800")
    check(">800 km 的帧 flooded_ha == 0", max(far_areas), max(far_areas) == 0.0, "== 0")

    # ---------------- 7. 潮位与异常兜底 ----------------
    amp = tide_mod.tide_range(C.DEFAULTS["tide"])
    tvals = [tide_mod.tide_level(h, C.DEFAULTS["tide"]) for h in np.arange(0, 720, 0.5)]
    check("天文潮上界 <= 分潮振幅之和", "%.3f / %.3f" % (max(abs(v) for v in tvals), amp),
          max(abs(v) for v in tvals) <= amp + 1e-6, "<= %.3f" % amp)
    bad = api.simulate({"hours": "不是数字", "typhoon_id": "__nope__"})
    check("异常输入不抛异常（返回 ok 或 error）",
          "ok=%s" % bad.get("ok"), ("ok" in bad) and ("error" in bad or bad.get("ok") is True),
          "有 ok/error 字段")

    # ---------------- 8. 端到端耗时 ----------------
    check("单次 simulate 端到端耗时（s）", round(wall, 2), wall <= 15.0, "<= 15")
    print("主推演 summary：", json.dumps(res["summary"], ensure_ascii=False))
    print("主推演帧摘要（每 6 帧）：")
    for f in frames[::6]:
        print("   %s %s 中心(%6.2f,%7.2f) tide=%.2f surge=%.2f twl=%.2f depth=%.2f ha=%.1f"
              % (f["label"], f["utc"], f["center"][0], f["center"][1], f["tide"],
                 f["max_surge"], f["max_twl"], f["max_depth"], f["flooded_ha"]))

    ok = print_table()
    print("selftest 总耗时 %.1f s" % (time.perf_counter() - t_all))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
