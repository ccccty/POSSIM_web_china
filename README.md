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

原软件 **POSSIM 3.0** 是一套 Windows 桌面风暴潮淹没推演工具：WPF 外壳 + 编译好的 Fortran 物理内核（`storm_surge_physics.dll`），结果只能导出 GeoTIFF 和一张静态网页，交互几乎为零，也没法演示。

本项目做了三件事：

1. **把物理内核从 GUI 里拆出来。** 那个 DLL 是 x64 + gfortran 静态链接的独立二进制，不需要 .NET、不需要 WPF —— 按原 C# 里的 `DllImport` 签名用 `ctypes` 直接驱动就能跑，一帧 0.69 s，8 线程 37 帧 7 秒。
2. **重写了一套等价的 numpy 计算链**（Holland 风场 → 反气压 → 风增水 → 波增水 → 四分潮 → 淹没判定），让整个推演可以跨平台、可读、可调参。
3. **做了一套浏览器大屏**：1188×864 逐像素栅格叠到 Leaflet 底图上，时间轴逐小时演进，左侧行政村按当前帧水深实时重排，右侧是潮位—增水—总水位过程线；参数抽屉可以改 Vmax / 气压 / 潮汐后重算。

> 所有数字都可以在[在线演示](https://ccccty.github.io/POSSIM_web_china/app.html)里点到对应时刻核对。线上是 12 组工况的预计算帧（所以能零后端托管），本地起 `server.py` 则是任意参数实时重算。

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
| 37 帧耗时 | 4.2 s | 5.2 s（8 线程） |
| 1.0 物理档的峰值增水 | 0.438 m | 0.310 m |

差 1.4 倍，但**不是常数偏差**。把台风压在网格中心，numpy 反而比内核低 30%；台风在 119 km 外，numpy 反而高 59%。原因是径向衰减形状不同 —— 原厂风增水是 **一次项** `0.0291572·W`（与水深、风区无关），numpy 版按风应力平方走 **二次项**。

原厂子公式已用 `scripts/probe_kernel_physics.py` 反解并写进文档：

```
风增水   h_w = 0.0291572 × W                    (W = 10 m 风速)
波增水   h_s = 7.6075e-4 × W² + 0.1 × runup     (W > 15 才起算)
拖曳系数 分段线性：1.475e-3 @9.5 → 1.35e-3 → 2.6125e-3 @32.5 → 1.8e-3 封底
```

> **口径提醒**：比较两套内核要比**增水**，不要比**淹没面积**。珠江口围田高程多在 0–1 m，总水位差 10 cm 就能让面积跳几千公顷 —— 面积在这个地形上不是精度证据。

---

## 架构

```
┌──────────────────────────── 浏览器 ────────────────────────────┐
│  index.html  项目介绍页（本次新增，响应式）                       │
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

## 数据来源与边界

| 数据项 | 来源 | 性质 |
| --- | --- | --- |
| 地形 DEM | AWS Terrain Tiles（NASA SRTM 1″），重采样至 94.3 m 网格 | **真实** |
| 底图 | ArcGIS World Imagery（WGS84）/ 高德矢量兜底 | **真实** |
| 台风路径 | 手工编排，量级参考 JTWC / JMA / CMA 最佳路径 | 历史重放，**非预报** |
| 增水 | 本地按 Holland 系公式计算 | 公式有文献出处，量级经标定 |
| 天文潮 | M2 / S2 / K1 / O1 四个分潮常量 | 示意值，非调和常数库 |
| 村庄 | 15 个真实地名，坐标吸附到 DEM 陆地格 | 地名真，人口户数为示意 |

**边界说清楚**：这是情景演示，不是水动力模型。没有接 ECMWF/GFS 预报场，没有求解潮波方程，潮位在整个 1188×864 网格上共用一个标量。它擅长演示「一场台风会把多少平方公里推上水面」「两套内核的差在哪」，**不适合防汛调度与决策**。

台风峰值强度按「让演示更明显」手工对齐过（例如山竹写作 928 hPa / 108 kt，比登陆时实测强一档）。

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

**POSSIM-Web** is a browser port of a Windows-only storm-surge inundation tool. The original ships as a WPF shell around a compiled Fortran physics DLL. The DLL turns out to be a self-contained x64 binary, so it is driven directly from Python via `ctypes` — no .NET, no GUI. A parallel NumPy implementation reproduces the same chain (Holland wind field → inverse barometer → wind setup → wave setup → four-constituent tide → inundation) and both kernels can be compared frame by frame.

The front end is a dependency-free dashboard (vanilla JS + Leaflet + ECharts) rendering a 1188×864 gridded surge field over the Pearl River Estuary, 36 simulated hours at 1-hour steps. Twelve scenario combinations (3 historical typhoons × 2 kernels × 2 amplification tiers) are precomputed and shipped as static files, so the live demo runs on GitHub Pages with no backend. Run `python server.py` locally to recompute with arbitrary parameters.

> Scenario demo only — not a hydrodynamic model, not for operational flood forecasting.

---

## 许可

代码以 MIT 许可发布。原 POSSIM 3.0 的 C# 源码与 Fortran 内核 `storm_surge_physics.dll` **不随本仓库分发**（版权归原软件方）；`docs/data/build/**` 中标注为 `fortran` 的帧图由该内核离线计算得到，仅作结果展示。

第三方组件：Leaflet（BSD-2-Clause）、ECharts（Apache-2.0）、地形瓦片（NASA SRTM，公有领域）、底图（Esri / 高德，遵循各自服务条款）。
