<div align="center">

# POSSIM-Web

**珠江口风暴潮淹没推演平台 · 浏览器版**

把一套只能在 Windows 桌面上点开的 `.NET + Fortran` 风暴潮软件，重做成能直接用 URL 打开的交互式大屏。

[**▶ 在线演示**](https://ccccty.github.io/POSSIM_web_china/) · [打开大屏](https://ccccty.github.io/POSSIM_web_china/app.html) · [推演链路](#推演链路) · [数据边界](#数据来源与边界)

![POSSIM-Web 大屏](docs/assets/hero-shot.webp)

`Python 3.10+` · `numpy` · `Fortran / ctypes` · `原生 JS` · `Leaflet` · `ECharts` · `零构建` · `静态可托管`

</div>

---

## 这是什么

原软件 **POSSIM 3.0** 为 Windows 桌面风暴潮淹没推演工具，由 WPF 界面与编译后的 Fortran 物理内核（`storm_surge_physics.dll`）组成，输出为 GeoTIFF 与一张静态网页。

本工程完成三项工作：

1. **分离物理内核与界面。** 该 DLL 为 x64、gfortran 静态链接的独立二进制，不依赖 .NET 与 WPF；按原 C# 中的 `DllImport` 签名以 `ctypes` 调用即可运行，单帧 0.69 s，8 线程下 37 帧约 5–7 s。
2. **实现等价的 numpy 计算链**（Holland 风场 → 反气压 → 风增水 → 波增水 → 四分潮 → 淹没判定），使推演可跨平台运行、参数可调、过程可读。
3. **构建浏览器端演示系统**：1188×864 栅格叠合 Leaflet 底图，按小时逐帧演进；左栏为台风要素与受影响行政村（按当帧水深排序），右栏为潮位、增水与总水位过程线；参数面板支持 Vmax、中心气压与潮汐参数的重新计算。

> 线上版本为 12 组工况的预计算静态包，逐帧结果可在[演示系统](https://ccccty.github.io/POSSIM_web_china/app.html)的时间轴上核对；本地运行 `server.py` 可对任意参数实时计算。

### 与原软件对照

| | POSSIM 3.0（原） | POSSIM-Web（本项目） |
| --- | --- | --- |
| 运行环境 | Windows + .NET 8 Desktop Runtime | 任意现代浏览器 |
| 界面 | WPF 窗体 + 一张 Leaflet 静态页 | 1920×1080 大屏，时间轴 / 图层 / 参数抽屉 |
| 交互 | 点按钮 → 等 → 导出文件 | 逐帧拖动播放、图层开关、参数重算、URL 深链 |
| 计算内核 | 闭源 Fortran DLL | 同一 DLL（ctypes 直连）**或** numpy 复算，可一键切换对比 |
| 部署 | 每台机器装环境 | 静态文件，GitHub Pages / 任意 CDN |

---

## 在线演示

<https://ccccty.github.io/POSSIM_web_china/>

大屏支持 URL 深链，可以直接把某个具体画面发给别人：

| 参数 | 说明 | 示例 |
| --- | --- | --- |
| `tid` | 台风：`mangkhut` / `saola` / `hato` | `app.html?tid=saola` |
| `kernel` | 计算内核：`numpy` / `fortran` | `app.html?kernel=fortran` |
| `amp` | 增幅档：`3.8`（演示）/ `1.0`（物理） | `app.html?amp=1.0` |
| `frame` | 直接跳到第 N 帧（0–36），或 `peak` | `app.html?frame=20` |
| `wind` | 风场形态：`hold` 定帧 / `arrows` 箭头网格 | `app.html?wind=hold` |

例：`app.html?tid=hato&kernel=fortran&frame=21` —— 天鸽、原 Fortran 内核、峰值帧。

---

## 本地运行

### 1. 静态演示（零依赖，30 秒）

```bash
# 仓库已带构建好的 docs/，任何静态服务器都能跑
cd docs && python -m http.server 8849
# 打开 http://127.0.0.1:8849/
```

### 2. 完整模式（任意参数实时重算）

```bash
pip install numpy pillow
python server.py --port 8848
# 打开 http://127.0.0.1:8848/
```

Windows 下双击 `启动.cmd` 等价（会做 Python 检测和中文报错）。

环境要求：Python 3.10+、numpy、Pillow。**不需要** Node、不需要 GDAL、不需要 .NET。

想连原厂内核？把 `storm_surge_physics.dll` 放到 `../01-POSSIM3.0-原工程/Engines/` 或设置环境变量 `POSSIM_FORTRAN_DLL` 指向它，抽屉里切到「原 Fortran 内核」即可；找不到 DLL 会自动退回 numpy 并给出 `warn`，不会白屏。

### 3. 重建静态站点

```bash
python scripts/build_site.py --out docs                    # 12 组工况，约 100 s
POSSIM_FORTRAN_DLL=/path/to/storm_surge_physics.dll \
  python scripts/build_site.py --out docs                 # 指定 DLL 位置
python scripts/build_site.py --out docs --skip-fortran     # 没有 DLL 时只出 numpy（6 组）
```

---

## 功能

- **时间轴**：37 帧（36 小时 / 1 小时步长）拖动、播放、循环、回到峰值帧
- **图层**：淹没水深七级分级、台风风场（6000 粒子流动 / 箭头网格，淡中浓三档）、DEM 底衬、底图切换（影像 / 矢量 / 地形 / 纯色）
- **指标**：最大增水、最大总水位、淹没面积、影响人口，环形进度 + 数字滚动
- **村表**：15 个真实行政村按当前帧水深重排，标注距岸距离、地面高程、转移策略
- **曲线**：潮位 / 增水 / 总水位三条过程线，游标跟随当前帧
- **参数抽屉**：Vmax、中心气压、移速移向、Holland B、Rmax、M2 振幅、增幅档、计算内核
- **导出**：当前帧淹没 PNG + summary JSON
- **自检**：`python -m engine.selftest` → 21 项断言（数据完整性、物理量级、接口契约）

---

## 推演链路

```
台风机理 JSON (lat/lon/pc/vmax/r34/r50/r64)
   │  分段线性插值到逐小时（12–17 报点 → 37 帧）
   ▼
Holland 梯度风 V(r)  ──  四象限风圈不对称展开
   │
   ├─ 反气压增水   dP·100 / (ρw·g)
   ├─ 风增水       Cd(W)·W² / (g·h)     （Cd 随风速分段）
   └─ 波增水       0.15·Hs              （W > 15 m/s 起算）
   ▼
天文潮  M2 / S2 / K1 / O1  →  η(t) = Σ A·cos(ωt + φ)
   ▼
总水位  twl = tide + surge     逐格点 1188×864
   │  淹没判定：twl > 地面高程 → depth = twl − elev
   │  4 连通斑块过滤：丢弃不含海岸种子的孤立噪点
   ▼
七级分级渲染 → RGBA PNG → Leaflet 经纬度贴图
```

关键参数都在 `engine/config.py`，可以按需替换；`engine/README.md` 记录了每个可调项的标定依据。

---

## 双内核对比

同一份 DEM、同一帧参数（山竹 · Hour +020 · 3.8 档）：

| 指标 | numpy 复算 | 原 Fortran 内核 |
| --- | --- | --- |
| 峰值增水 | 1.664 m | **1.178 m** |
| 峰值淹没面积 | 20,846 ha | **14,758 ha** |
| 最大淹没深度 | 1.835 m | 1.466 m |
| 37 帧耗时 | 4.0–4.5 s | 5–7 s（8 线程） |
| 1.0 物理档的峰值增水 | 0.438 m | 0.310 m |

差 1.4 倍，但**不是常数偏差**。把台风压在网格中心，numpy 反而比内核低 30%；台风在 119 km 外，numpy 反而高 59%。原因是径向衰减形状不同 —— 原厂风增水是 **一次项** `0.0291572·W`（与水深、风区无关），numpy 版按风应力平方走 **二次项**。

原厂子公式已用 `scripts/probe_kernel_physics.py` 反解并写进文档：

```
风增水   h_w = 0.0291572 × W                    (W = 10 m 风速)
波增水   h_s = 7.6075e-4 × W² + 0.1 × runup     (W > 15 才起算)
拖曳系数 分段线性：1.475e-3 @9.5 → 1.35e-3 → 2.6125e-3 @32.5 → 1.8e-3 封底
```

> **指标选择**：两套内核之间宜比较**增水**。研究区围田高程多在 0–1 m，总水位变化 0.1 m 即可引起淹没面积大幅变化，面积指标不宜作为精度判据。

---

## 架构

```
┌──────────────────────────── 浏览器 ────────────────────────────┐
│  index.html  项目介绍页（响应式，静态内容）                       │
│  app.html    大屏：Leaflet + ECharts + Canvas 风场 + 原生 JS      │
│      ├─ js/api.js      网络层（live / static / mock 三态）        │
│      ├─ js/static.js   静态数据包适配（GitHub Pages 用）          │
│      ├─ js/map.js      地理配准栅格叠加、台风路径、村庄            │
│      ├─ js/wind.js     6000 粒子风场（Canvas 2D）                 │
│      └─ js/app.js      状态机、时间轴、指标、参数抽屉              │
└───────────────────────────────┬────────────────────────────────┘
                                │ 本地：POST /api/simulate
                                │ 线上：GET data/build/<tid>/<kernel>_<amp>/sim.json
┌───────────────────────────────▼────────────────────────────────┐
│  server.py（Python 标准库 http.server，零 Web 框架）              │
│  engine/api.py      参数合并 → 逐帧推演 → summary / timeseries    │
│  engine/physics.py  Holland 风场 + 增水三项                       │
│  engine/tide.py     四分潮                                        │
│  engine/inundation.py  淹没判定 + 连通性过滤                       │
│  engine/render.py   Pillow → RGBA 叠加图                          │
│  engine/fortran.py  ctypes 驱动 storm_surge_physics.dll           │
└────────────────────────────────────────────────────────────────┘
```

```
POSSIM_web_china/
├── server.py                 本地 HTTP 服务（/api/health|meta|typhoon|simulate|export）
├── engine/                   计算内核（numpy + Fortran 双实现）
├── scripts/
│   ├── build_site.py         ★ 静态站点构建：12 组工况 + 逐帧 PNG + manifest
│   ├── check_fortran.py      两套内核对拍（--all 带耗时）
│   ├── probe_kernel_physics.py  反解原厂子公式
│   ├── fetch_dem.py          SRTM 瓦片下载与重采样
│   ├── make_typhoons.py      台风机理参数表
│   └── make_catalog.py       村庄目录
├── web/                      前端源码（大屏 + 介绍页）
├── data/                     DEM、台风机理、村庄、元数据
└── docs/                     GitHub Pages 站点（由 build_site.py 生成）
```

---

## 数据来源与性质

| 数据项 | 来源 | 性质 |
| --- | --- | --- |
| 地形 DEM | AWS Terrain Tiles（NASA SRTM 1″），重采样至 94.3 m 网格 | 实测 |
| 底图 | ArcGIS World Imagery（WGS84）/ 高德矢量兜底 | 实测 |
| 台风路径 | JTWC / JMA / CMA 公开最佳路径（量级重建） | 实测路径重建 |
| 增水 | 逐格点实时计算（Holland 风场 + 反气压 + 风增水 + 波增水） | 实时计算 |
| 天文潮 | M2 / S2 / K1 / O1 分潮按 η = ΣA·cos(ωt+φ) 实时合成 | 实时计算 |
| 村庄 | 15 个行政村名称与 DEM 吸附坐标 | 实测地名 |

**计算方法说明**：计算未求解潮波方程，未接入 ECMWF/GFS 预报场，潮位在 1188×864 网格上取单一标量。

台风峰值强度取路径参数表中的最强时刻（山竹 928 hPa / 108 kt）。

---

## HTTP API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | `{"ok":true,"engine":true,"dem":true}` |
| GET | `/api/meta` | 站点元数据、台风列表、村庄、默认参数 |
| GET | `/api/typhoon/{id}` | 单场台风机理参数 |
| POST | `/api/simulate` | 入参 `{typhoon_id, kernel, params, overrides, hours}`，返回 37 帧 + 时序 + summary |
| GET | `/api/dem/preview.png` | DEM 晕渲预览 |

`/api/simulate` 单次 3.5–7 s（numpy / Fortran），响应约 490 KB（帧图内联 base64）；静态模式下帧图改为独立文件，可被浏览器缓存。

---

## English

**POSSIM-Web** is a browser implementation of a Windows-only storm-surge inundation tool. The original consists of a WPF shell and a compiled Fortran physics DLL. The DLL is a self-contained x64 binary, so it is invoked directly from Python via `ctypes`, without .NET or any GUI. A parallel NumPy implementation reproduces the same chain (Holland wind field → inverse barometer → wind setup → wave setup → four-constituent tide → inundation), allowing the two kernels to be compared frame by frame.

The front end is a dependency-free dashboard (vanilla JS + Leaflet + ECharts) rendering a 1188×864 gridded surge field over the Pearl River Estuary, 36 simulated hours at 1-hour steps. Twelve scenario combinations (3 historical typhoons × 2 kernels × 2 amplification tiers) are precomputed and shipped as static files, so the live demo runs on GitHub Pages with no backend. Run `python server.py` locally to recompute with arbitrary parameters.

> **Scope.** Intended for scenario comparison, teaching and kernel-porting validation. The tide is a single scalar over the whole grid; no wave-equation solver and no ECMWF/GFS forcing are used. Output is not intended for operational forecasting or flood-control decisions.

---

## 许可

代码以 MIT 许可发布。原 POSSIM 3.0 的 C# 源码与 Fortran 内核 `storm_surge_physics.dll` **不随本仓库分发**（版权归原软件方）；`docs/data/build/**` 中标注为 `fortran` 的帧图由该内核离线计算得到，仅作结果展示。

第三方组件：Leaflet（BSD-2-Clause）、ECharts（Apache-2.0）、地形瓦片（NASA SRTM，公有领域）、底图（Esri / 高德，遵循各自服务条款）。
