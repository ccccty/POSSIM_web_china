# -*- coding: utf-8 -*-
"""
POSSIM-Web · S1 数据代理 —— 台风样例路径
========================================
生成 data/typhoons/{mangkhut,hato,saola}.json，字段严格按 CONTRACT §3.3：
  id / name / name_en / year / track[{t,lat,lon,pc_mb,vmax_kt,motion_kt,heading_deg,r34,r50,r64}] / landfall{t,lat,lon}
  r34/r50/r64 顺序固定 NE,SE,SW,NW，单位海里。

数据来源与处理：
  * 位置与强度取自公开最佳路径（JTWC / JMA / CMA 公开量级）：
      山竹 Mangkhut 2018 —— 正面珠江口，台山沿海登陆（22 时前后，925-940 hPa 量级）
      天鸽 Hato     2017 —— 珠海金湾登陆，小而强、近岸爆发，942 hPa / 100 kt
      苏拉 Saola    2023 —— 沿广东东部近海西行，掠过香港以南再于珠海金湾登陆
  * 只给 lat/lon/pc_mb/vmax_kt 与四象限风圈；motion_kt 与 heading_deg 由相邻路径点**算出来**
    （中心差分），保证"位置—移速—移向"三者自洽，不会出现方向与位置打架。
  * 强度曲线遵守常识：登陆前气压逐步降低、风速逐步升高，登陆后迅速减弱填塞。
    契约 §3.1 的 note 已声明"台风参数为历史/示意情景"，故峰值安排在路径窗口内、贴近登陆时刻。
  * 风圈自洽：vmax < 64 kt 时 r64 自动归零，vmax < 50 kt 时 r50 自动归零（见 build）。
  * 路径时间跨度 = 登陆前 30~42 h 到登陆后 12 h，点间隔 3~6 h（引擎负责插值到逐小时）。

用法：
    python scripts/make_typhoons.py
"""
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_DIR = os.path.join(ROOT, "data", "typhoons")

KM_PER_DEG_LAT = 111.0
KM_PER_DEG_LON = 103.0      # 22.6°N 附近


# 每条路径： (t, lat, lon, pc_mb, vmax_kt, r34[NE,SE,SW,NW], r50, r64)
TYPHOONS = [
    {
        "id": "mangkhut", "name": "山竹", "name_en": "Mangkhut", "year": 2018,
        "landfall": {"t": "2018-09-16T10:00Z", "lat": 21.78, "lon": 112.90},
        "track": [
            ("2018-09-14T18:00Z", 18.00, 122.90, 985, 60,
             [120, 110, 95, 115], [55, 48, 40, 52], [0, 0, 0, 0]),
            ("2018-09-15T00:00Z", 18.50, 121.60, 975, 70,
             [135, 123, 106, 130], [65, 57, 48, 62], [10, 8, 6, 9]),
            ("2018-09-15T06:00Z", 19.00, 120.30, 965, 80,
             [150, 137, 118, 144], [78, 68, 58, 74], [28, 24, 20, 26]),
            ("2018-09-15T12:00Z", 19.45, 119.00, 960, 85,
             [165, 150, 130, 158], [90, 79, 67, 85], [42, 36, 31, 40]),
            ("2018-09-15T18:00Z", 19.90, 117.70, 955, 88,
             [178, 162, 140, 170], [98, 86, 73, 93], [50, 44, 37, 47]),
            ("2018-09-16T00:00Z", 20.40, 116.40, 950, 92,
             [188, 171, 148, 180], [105, 92, 79, 100], [58, 51, 43, 55]),
            ("2018-09-16T06:00Z", 21.15, 114.30, 940, 100,
             [205, 187, 162, 196], [120, 106, 90, 114], [70, 61, 52, 66]),
            ("2018-09-16T09:00Z", 21.60, 113.30, 928, 108,
             [215, 196, 170, 205], [128, 113, 96, 122], [78, 68, 58, 74]),
            ("2018-09-16T12:00Z", 22.05, 112.40, 965, 72,
             [175, 159, 138, 167], [95, 83, 70, 90], [18, 15, 12, 17]),
            ("2018-09-16T15:00Z", 22.30, 111.70, 985, 52,
             [150, 136, 118, 143], [40, 35, 29, 38], [0, 0, 0, 0]),
            ("2018-09-16T18:00Z", 22.55, 111.00, 995, 35,
             [120, 109, 94, 114], [0, 0, 0, 0], [0, 0, 0, 0]),
            ("2018-09-16T21:00Z", 22.85, 110.30, 1003, 22,
             [95, 86, 74, 90], [0, 0, 0, 0], [0, 0, 0, 0]),
        ],
    },
    {
        "id": "hato", "name": "天鸽", "name_en": "Hato", "year": 2017,
        "landfall": {"t": "2017-08-23T04:50Z", "lat": 22.00, "lon": 113.10},
        "track": [
            ("2017-08-21T18:00Z", 17.20, 120.50, 992, 45,
             [90, 80, 70, 85], [0, 0, 0, 0], [0, 0, 0, 0]),
            ("2017-08-22T00:00Z", 17.80, 118.80, 985, 55,
             [95, 85, 75, 90], [30, 26, 22, 28], [0, 0, 0, 0]),
            ("2017-08-22T06:00Z", 18.60, 117.10, 975, 65,
             [100, 90, 80, 95], [40, 35, 30, 38], [12, 10, 8, 11]),
            ("2017-08-22T12:00Z", 19.40, 115.60, 965, 75,
             [105, 95, 85, 100], [48, 42, 36, 45], [20, 17, 14, 18]),
            ("2017-08-22T18:00Z", 20.30, 114.30, 955, 85,
             [110, 100, 88, 105], [58, 52, 45, 55], [30, 26, 22, 28]),
            ("2017-08-22T21:00Z", 20.75, 113.75, 950, 92,
             [115, 105, 92, 110], [65, 58, 50, 62], [36, 32, 27, 34]),
            ("2017-08-23T00:00Z", 21.30, 113.40, 945, 100,
             [120, 108, 95, 112], [70, 62, 54, 66], [42, 37, 32, 40]),
            ("2017-08-23T03:00Z", 21.75, 113.20, 942, 100,
             [118, 106, 93, 110], [70, 62, 54, 66], [42, 37, 32, 40]),
            ("2017-08-23T06:00Z", 22.20, 112.80, 975, 60,
             [100, 90, 78, 95], [40, 35, 30, 38], [0, 0, 0, 0]),
            ("2017-08-23T09:00Z", 22.55, 112.20, 992, 35,
             [80, 72, 62, 76], [0, 0, 0, 0], [0, 0, 0, 0]),
            ("2017-08-23T12:00Z", 22.85, 111.50, 1000, 22,
             [60, 54, 46, 57], [0, 0, 0, 0], [0, 0, 0, 0]),
            ("2017-08-23T16:00Z", 23.10, 110.60, 1006, 15,
             [45, 40, 35, 43], [0, 0, 0, 0], [0, 0, 0, 0]),
        ],
    },
    {
        "id": "saola", "name": "苏拉", "name_en": "Saola", "year": 2023,
        "landfall": {"t": "2023-09-01T19:30Z", "lat": 22.00, "lon": 112.95},
        "track": [
            ("2023-08-31T02:00Z", 20.10, 119.20, 962, 55,
             [105, 95, 82, 100], [45, 40, 33, 42], [0, 0, 0, 0]),
            ("2023-08-31T08:00Z", 20.40, 117.90, 952, 68,
             [118, 107, 93, 112], [58, 51, 43, 55], [10, 8, 6, 9]),
            ("2023-08-31T14:00Z", 20.75, 116.60, 945, 78,
             [132, 120, 104, 126], [70, 62, 52, 66], [28, 24, 20, 26]),
            ("2023-08-31T20:00Z", 21.10, 115.40, 938, 88,
             [142, 129, 112, 135], [78, 69, 58, 74], [40, 35, 29, 38]),
            ("2023-09-01T02:00Z", 21.35, 114.60, 932, 95,
             [150, 136, 118, 143], [84, 74, 63, 80], [48, 42, 35, 45]),
            ("2023-09-01T08:00Z", 21.55, 114.10, 928, 100,
             [155, 141, 122, 148], [88, 78, 66, 84], [52, 46, 38, 49]),
            ("2023-09-01T12:00Z", 21.70, 113.75, 926, 100,
             [156, 142, 123, 149], [89, 79, 67, 85], [53, 47, 39, 50]),
            ("2023-09-01T15:00Z", 21.80, 113.50, 926, 98,
             [152, 138, 120, 145], [87, 77, 65, 83], [51, 45, 38, 48]),
            ("2023-09-01T18:00Z", 21.90, 113.20, 926, 96,
             [148, 134, 116, 141], [85, 75, 64, 81], [49, 43, 36, 46]),
            ("2023-09-01T21:00Z", 22.10, 112.70, 958, 68,
             [118, 107, 93, 112], [56, 49, 42, 53], [10, 8, 6, 9]),
            ("2023-09-02T00:00Z", 22.25, 112.20, 978, 45,
             [95, 86, 74, 90], [0, 0, 0, 0], [0, 0, 0, 0]),
            ("2023-09-02T04:00Z", 22.40, 111.60, 992, 30,
             [72, 65, 56, 68], [0, 0, 0, 0], [0, 0, 0, 0]),
            ("2023-09-02T08:00Z", 22.55, 111.00, 1000, 20,
             [55, 49, 42, 51], [0, 0, 0, 0], [0, 0, 0, 0]),
        ],
    },
]


def parse_iso(s):
    """'2018-09-16T10:00Z' → 自 2000-01-01T00:00Z 起的小时数（与引擎 timescale 一致）。"""
    import datetime as dt
    d = dt.datetime.strptime(s, "%Y-%m-%dT%H:%MZ").replace(tzinfo=dt.timezone.utc)
    base = dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc)
    return (d - base).total_seconds() / 3600.0


def motion_heading(track, i):
    """中心差分算移速(kt)与移向(度，正北=0，顺时针)。"""
    if i == 0:
        a, b = track[0], track[1]
    elif i == len(track) - 1:
        a, b = track[-2], track[-1]
    else:
        a, b = track[i - 1], track[i + 1]
    dt_h = parse_iso(b[0]) - parse_iso(a[0])
    dy = (b[1] - a[1]) * KM_PER_DEG_LAT
    dx = (b[2] - a[2]) * KM_PER_DEG_LON
    kt = math.hypot(dx, dy) / dt_h / 1.852          # km/h → kt
    hdg = (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0
    return int(round(kt)), int(round(hdg)) % 360


def validate(t, doc):
    """自检：时间递增、风圈嵌套、强度曲线、登陆时刻与位置。"""
    tr = t["track"]
    errs = []
    ts = [parse_iso(p[0]) for p in tr]
    if any(b <= a for a, b in zip(ts, ts[1:])):
        errs.append("时间未严格递增")
    for p in doc["track"]:
        r34, r50, r64 = p["r34"], p["r50"], p["r64"]
        if not all(r34[i] >= r50[i] >= r64[i] for i in range(4)):
            errs.append("%s 风圈未嵌套 r34>=r50>=r64" % p["t"])
        if p["vmax_kt"] < 64 and any(r64):
            errs.append("%s vmax %d kt 却有 r64" % (p["t"], p["vmax_kt"]))
        if p["vmax_kt"] < 50 and any(r50):
            errs.append("%s vmax %d kt 却有 r50" % (p["t"], p["vmax_kt"]))
        if not (0 <= p["heading_deg"] < 360):
            errs.append("%s heading 越界" % p["t"])
        if p["motion_kt"] <= 0:
            errs.append("%s 移速非正" % p["t"])
    lf = parse_iso(t["landfall"]["t"])
    if not (ts[0] < lf < ts[-1]):
        errs.append("登陆时刻不在路径时间范围内")
    pre = [p for p, x in zip(tr, ts) if x <= lf]
    post = [p for p, x in zip(tr, ts) if x > lf]
    pcs = [p[3] for p in pre]
    if any(b > a for a, b in zip(pcs, pcs[1:])):
        errs.append("登陆前中心气压没有逐步降低")
    vmax_pre = [p[4] for p in pre]
    if vmax_pre[-1] < vmax_pre[0]:
        errs.append("登陆前最大风速没有增强")
    if post and post[0][3] <= min(pcs):
        errs.append("登陆后气压没有迅速填塞")
    dur_pre, dur_post = lf - ts[0], ts[-1] - lf
    if not (30 <= dur_pre <= 48):
        errs.append("登陆前跨度 %.0f h 不在 30-48 h" % dur_pre)
    if not (10 <= dur_post <= 16):
        errs.append("登陆后跨度 %.0f h 不在 10-16 h" % dur_post)
    d = math.hypot((t["landfall"]["lat"] - pre[-1][1]) * KM_PER_DEG_LAT,
                   (t["landfall"]["lon"] - pre[-1][2]) * KM_PER_DEG_LON)
    if d > 120:
        errs.append("登陆点离最近路径点 %.0f km，太远" % d)
    return errs, dur_pre, dur_post


def build(t):
    """组装 JSON 文档，并把与 vmax 不自洽的风圈清零。"""
    pts, fixed = [], 0
    tr = t["track"]
    for i, (ts, lat, lon, pc, vmax, r34, r50, r64) in enumerate(tr):
        kt, hdg = motion_heading(tr, i)
        r50 = list(r50)
        r64 = list(r64)
        if vmax < 50 and any(r50):
            r50 = [0, 0, 0, 0]
            fixed += 1
        if vmax < 64 and any(r64):
            r64 = [0, 0, 0, 0]
            fixed += 1
        pts.append({
            "t": ts, "lat": round(lat, 2), "lon": round(lon, 2),
            "pc_mb": int(pc), "vmax_kt": int(vmax),
            "motion_kt": kt, "heading_deg": hdg,
            "r34": [int(v) for v in r34], "r50": [int(v) for v in r50], "r64": [int(v) for v in r64],
        })
    doc = {
        "id": t["id"], "name": t["name"], "name_en": t["name_en"], "year": t["year"],
        "track": pts,
        "landfall": {"t": t["landfall"]["t"], "lat": round(t["landfall"]["lat"], 2),
                     "lon": round(t["landfall"]["lon"], 2)},
    }
    return doc, fixed


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    ok = True
    total_bytes = 0
    print("输出目录：%s" % OUT_DIR)
    for t in TYPHOONS:
        doc, fixed = build(t)
        errs, dur_pre, dur_post = validate(t, doc)
        path = os.path.join(OUT_DIR, doc["id"] + ".json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        size = os.path.getsize(path)
        total_bytes += size
        tr = doc["track"]
        pk = max(tr, key=lambda p: p["vmax_kt"])
        lo = min(tr, key=lambda p: p["pc_mb"])
        ok &= not errs
        print("")
        print("%s %s (%d) → %s  (%.1f KB)" % (doc["id"], doc["name"], doc["year"], path, size / 1024.0))
        print("  %d 点；峰值 %d kt / 最低 %d hPa；登陆前 %.1f h、登陆后 %.1f h；风圈自洽修正 %d 处"
              % (len(tr), pk["vmax_kt"], lo["pc_mb"], dur_pre, dur_post, fixed))
        print("  登陆：%s  (%.2fN %.2fE)" % (doc["landfall"]["t"], doc["landfall"]["lat"], doc["landfall"]["lon"]))
        print("  %-18s %9s %9s %6s %6s %7s %7s  %-22s %-20s %s" % (
            "t", "lat", "lon", "pc", "vmax", "motion", "heading", "r34", "r50", "r64"))
        for p in tr:
            print("  %-18s %9.2f %9.2f %6d %6d %7d %7d  %-22s %-20s %s" % (
                p["t"], p["lat"], p["lon"], p["pc_mb"], p["vmax_kt"], p["motion_kt"], p["heading_deg"],
                p["r34"], p["r50"], p["r64"]))
        print("  校验：%s" % ("PASS" if not errs else "FAIL " + "；".join(errs)))
    print("")
    print("合计 %d 个文件，%d 字节" % (len(TYPHOONS), total_bytes))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
