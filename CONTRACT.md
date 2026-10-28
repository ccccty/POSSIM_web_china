# CONTRACT · 珠江口风暴潮淹没推演平台（POSSIM-Web）

本文件是唯一接口契约。所有子模块必须严格按本文件实现；不得自行改动字段名、目录、颜色、公式结构。
原始参考实现（C#/.NET + Fortran，只读参考，不要修改）：原 POSSIM 3.0 工程。
该工程的 C# 源码与 Fortran 内核因版权原因不随本仓库分发，`engine/fortran.py` 保留完整的调用契约。

## 0. 运行环境事实（已实测，不要假设别的）
- Python 3.14.7，PATH 上的 python 即可；已装 numpy 2.5.4、Pillow 13.0.0。
- 没有 node / npm / vite —— 前端必须是零构建的原生 HTML+CSS+JS，库用 CDN（unpkg / jsdelivr 实测可达）。
- 没有 matplotlib / scipy / flask / gdal。不要 pip install 任何东西，只用标准库 + numpy + Pillow。
- Windows 10/11；headless Edge 在 C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe（仅验收用）。
- 外网可直连：s3.amazonaws.com、unpkg.com、cdn.jsdelivr.net、pypi.tuna.tsinghua.edu.cn。

## 1. 目录与归属（写权限严格互斥）
POSSIM-Web/
  CONTRACT.md        调度者
  data/              S1 数据代理
  engine/            S2 计算代理
  server.py          S3 服务代理
  启动.cmd            S3
  README.md          S3
  web/               S4 前端代理
禁止写到别人的目录；需要别人产物时按契约假定格式即可（缺失时自我降级，不要越界创建文件）。

## 2. 演示区域与地形
- 区域：珠江口-伶仃洋（深圳/珠海/澳门/广州南沙/香港北）。
- 目标 bbox：**W=113.20, S=22.20, E=114.20, N=23.00**（度，EPSG:4326）。
- 目标栅格：**1188 列 × 864 行**，像素 0.000925926 度（约 103 m）等经纬网格，行序**自北向南**（北=第 0 行，与 GDAL/Leaflet 习惯一致）。
- 约束：nx*pixel_deg 必须等于 bbox 东西跨度，ny*pixel_deg 等于南北跨度；行优先索引 idx = row*nx + col。
- 高程：SRTM（s3.amazonaws.com/elevation-tiles-prod/skadi/N22/N22E113.hgt.gz 这种路径，1 角秒，海洋为 0）。降采样到 3 角秒（3x3 块平均即可），float32，单位 m。
- NODATA = -9999。

## 3. data/ 目录产物（S1 负责）
```
data/dem.npy            float32[864,1188] 高程 m，行序自北向南
data/meta.json          见 §3.1，必须能原样作为 GET /api/meta 的响应体
data/dem_preview.png    1188x864 RGBA，地形晕渲+水系底图（无瓦片时的兜底底图，透明=高程<=0 或 nodata）
data/villages.json      影响行政村列表（示意数据，字段见 §3.2）
data/typhoons/*.json    台风样例，字段见 §3.3，至少 3 个
```
### 3.1 data/meta.json
```json
{
  "site": {"name":"珠江口风暴潮淹没推演平台","region":"珠江口-伶仃洋","crs":"EPSG:4326",
           "bbox":[113.20,22.20,114.20,23.00],"nx":1188,"ny":864,"pixel_deg":0.000925926,
           "pixel_m":102.7,"area_km2":12345.6,"note":"数据：SRTM 90m · 台风参数为历史/示意情景"},
  "dem": {"file":"dem.npy","nodata":-9999,"min":-2.0,"max":512.3,"land_pixels":123456},
  "map": {"center":[22.60,113.70],"zoom":11,"bbox_leaflet":[[22.20,113.20],[23.00,114.20]],
          "preview_png":"/api/dem/preview.png"},
  "defaults": {"env_pressure":1013.0,"holland_b":1.7,"wave_runup":1.15,"coarse_amp":3.8,
               "tide":{"M2":[1.0,145.0],"S2":[0.4,45.0],"K1":[0.3,75.0],"O1":[0.2,120.0]},
               "hours":36,"step_hours":1},
  "typhoons": [{"id":"mangkhut","name":"山竹","name_en":"Mangkhut","year":2018,"vmax_kt":115,"pc_mb":905}],
  "villages": [ 同 villages.json 的数组内容 ]
}
```
### 3.2 villages.json（数组，示意/公开量级，10–16 条）
```json
[{"name":"南沙区龙穴街","lat":22.70,"lon":113.62,"pop":12000,"households":4300,
  "dist_coast_km":1.2,"elev_m":2.1,"strategy":"转移","level":"高"}]
```
### 3.3 typhoons/*.json
```json
{"id":"mangkhut","name":"山竹","name_en":"Mangkhut","year":2018,
 "track":[{"t":"2018-09-15T00:00Z","lat":19.5,"lon":118.2,"pc_mb":915,"vmax_kt":115,
           "motion_kt":18,"heading_deg":300,
           "r34":[180,160,140,170],"r50":[110,100,90,105],"r64":[60,55,50,58]}],
 "landfall":{"t":"2018-09-16T09:00Z","lat":22.15,"lon":112.55}}
```
路径点间隔 3–6 小时即可，engine 负责插值到逐小时。r34/r50/r64 顺序固定为 NE,SE,SW,NW（海里）。

## 4. engine/ 目录（S2 负责）
模块划分固定：
- engine/__init__.py
- engine/config.py       默认参数（对齐 meta.defaults）
- engine/tide.py         分潮：eta = sum A_i * cos(w_i * h + phi_i)，h=自 2000-01-01T00:00Z 的小时数；w 用 deg/hr（M2 28.9841042，S2 30.0，K1 15.0410686，O1 13.9430356）
- engine/physics.py      逐像素风暴潮（见 §5）
- engine/inundation.py   淹没+连通性过滤+分级（见 §5.6）
- engine/render.py       Pillow：把深度场渲染成 RGBA PNG（透明背景，只画淹没）
- engine/api.py          唯一对外入口：simulate(payload: dict) -> dict
- engine/selftest.py     自检：python -m engine.selftest 必须通过并打印校验表

### 4.1 simulate 入口签名（S3 只调这个）
```python
from engine.api import simulate
result = simulate(payload_dict)  # 内部不抛未捕获异常；失败返回 {"error": "..."}
```

## 5. 物理模型（POSSIM 同构，逐像素参数化，不做时间积分）
单个时刻、单个像素（像素距台风中心 r km、方位角 brg 度）：
1. dP = Penv - Pc（hPa），Penv 默认 1013。
2. 梯度风（Holland 剖面）：f = 2*7.292115e-5*sin(lat)；rho_a = 1.15；
   rm = r*1000（米）；Rm = Rmax*1000；
   W = sqrt( B*dP*100/rho_a*(Rm/rm)^B*exp(-(Rm/rm)^B) + (0.5*f*rm)^2 ) - 0.5*f*rm   [m/s]
   风速封顶：不超过 Vmax*1.15（Vmax 由 kt 转 m/s）。
3. 非对称：
   a) 四象限风圈：R34/R50/R64 的 NE/SE/SW/NW 按方位角插值，得到该方位上的 R34theta；r <= R34theta 记为"受强风影响区"（用于前端展示/统计，不额外加权）。
   b) 移行非对称：theta = 归一化(brg - heading) 到 [-180,180]；北半球右侧(|theta|<=90)增强：
      k_m = 1 + 0.35*min(motion_kt,50)/50*cos(theta_rad)，左侧 1 - 0.25*min(motion_kt,50)/50*cos(theta_rad)；W *= clamp(k_m,0.6,1.5)
4. 增水三项（单位 m）：
   - 反气压：h_p = (dP*100)/(rho_w*g)，rho_w=1025，g=9.81
   - 风增水：h_w = Cd*rho_a/rho_w * W^2/(g*h_ref) * min(fetch_m,20000)/20000 * 0.06
     Cd = (0.8+0.065*W)*1e-3（封顶 2.5e-3），h_ref = max(10,|DEM|+10)，fetch_m 取该像素到 DEM 边界的距离(m，可用常数近似 20000)
     标定点：W=50 m/s、h_ref=20 m 时 h_w 必须落在 1.2–2.0 m（selftest 断言）。
   - 波增水：仅 W>15 m/s 时 h_wv = 0.15*Hs，Hs = min(9.0, 0.0025*W*W/g*10)
   总增水 h = (h_p + h_w + h_wv) * coarse_amp（coarse_amp 默认 3.8，可被 payload 覆盖为 1.0）
   仅在 DEM>=0（陆地/岸线以上）的像素输出；海洋像素输出 nodata。
5. 总水位 TWL = h + 天文潮(hour)。
6. 淹没：depth = (TWL - DEM) 若 >0 即淹没深度；离岸 1 km 内像素 depth *= wave_runup(1.15)；
   连通性过滤：4 连通洪泛，丢弃像素数 < min_pixels(10) 的孤立斑块，且斑块必须含至少 1 个"海岸种子像素"（DEM 在 [0,1.5] m 且 8 邻域内有 DEM<0）；
   分级（与 POSSIM 一致）：0-0.33 / 0.33-0.5 / 0.5-1 / 1-2 / 2-3 / 3-5 / >5 m；
   淹没面积 = 像素数 * pixel_m^2 / 10000 ha；陆上百分比 = 淹没像元/陆地像元*100。

合理性红线（必须满足，selftest 断言）：Vmax>=100 kt 的台风正对岸段时 max_surge 落在 1.0–3.5 m；max_depth <= max_surge*2.5；淹没面积随 Vmax 单调增；台风中心离 DEM 超过 800 km 的帧淹没面积必须为 0。

## 6. HTTP 接口（S3 实现，S4 消费）
服务：python server.py --port 8848（默认 8848，绑定 127.0.0.1），标准库 http.server。
- GET  /                      -> web/index.html
- GET  /static/*              -> web/ 下静态文件（按扩展名给 Content-Type）
- GET  /api/meta              -> data/meta.json 原样返回
- GET  /api/dem/preview.png   -> data/dem_preview.png
- GET  /api/typhoon/{id}      -> data/typhoons/{id}.json
- POST /api/simulate          -> body 见 §6.1，响应见 §6.2（JSON，utf-8，耗时 3–15 s 可接受）
- GET  /api/health            -> {"ok":true,"engine":true,"dem":true}
跨域允许 *；POST 用 Content-Length 读全量 body。
### 6.1 请求体
```json
{"typhoon_id":"mangkhut",
 "params":{"env_pressure":1013,"holland_b":1.7,"rmax_km":40,"wave_runup":1.15,"coarse_amp":3.8,
           "tide":{"M2":[1.0,145.0],"S2":[0.4,45.0],"K1":[0.3,75.0],"O1":[0.2,120.0]}},
 "hours":36,"step_hours":1,
 "overrides":{"vmax_kt":null,"pc_mb":null,"heading_deg":null,"motion_kt":null}}
```
overrides 允许前端把手动滑块值盖在路径点上（演示"调参真算"）；null 表示不覆盖。
### 6.2 响应体（帧数 = hours/step_hours + 1）
```json
{"ok":true,"elapsed_ms":4200,
 "bbox":[[22.20,113.20],[23.00,114.20]],"nx":1188,"ny":864,
 "frames":[{"i":0,"label":"Hour +000","utc":"2018-09-15T00:00Z",
   "center":[19.5,118.2],"pc_mb":915,"vmax_kt":115,"motion_kt":18,"heading_deg":300,
   "tide":0.62,"max_surge":1.83,"max_twl":2.45,"max_depth":1.21,
   "flooded_ha":1832.5,"flooded_pct":4.7,
   "categories":{"0-0.33":123,"0.33-0.5":45,"0.5-1":30,"1-2":8,"2-3":2,"3-5":0,">5":0},
   "png_b64":"...","png_w":1188,"png_h":864}],
 "timeseries":[{"utc":"...","tide":0.62,"surge":1.83,"twl":2.45,"wind_ms":42.1,"wind_kt":115,"pc_mb":915,"dist_km":182.0,"flooded_ha":1832.5}],
 "summary":{"max_depth":{"value":1.21,"label":"Hour +018"},
            "max_area":{"value":1832.5,"label":"Hour +018"},
            "max_surge":{"value":1.83,"label":"Hour +018"},
            "affected_pop":12345,"affected_villages":6}}
```
png_b64 = 该帧淹没深度 RGBA PNG（透明背景）。前端用 L.imageOverlay 叠在 Leaflet 上。
速度：36 帧、1188x864，单次 simulate <= 15 s（内部可先降采样到 594x432 再放大渲染，但 png_w/h 必须如实返回 1188/864）。

## 7. 前端（S4 负责，web/ 目录）
### 7.1 版式（严格照参考图的信息架构，换数据）
- 设计基准 1920x1080，整体用 transform: scale(min(vw/1920, vh/1080)) 自适应居中，深色背景铺满。
- 顶栏：左侧 4 个分段按钮「推演 / 参数 / 影响 / 报告」；中间大标题「珠江口风暴潮淹没推演平台」+ 小号副标题「POSSIM · 风暴潮淹没推演」；右侧分段按钮「图层 / 对比 / 导出 / 关于」。标题下方一道发光线。
- 左上第 1 卡「台风态势」：行式键值对（圆形图标 + 标签 + 值）= 编号 / 命名 / 强度等级 / 中心气压 / 最大风速 / 移速移向 / 七级风圈 / 预警级别。
- 左上第 2 卡「目标海域」：数字块（面积 km²、平均高程 m、DEM 分辨率）+ 右侧小缩略图（dem_preview）。
- 左下第 3 卡「影响行政村」：表格（村名/人口/户数/距岸km/最大水深/等级），可滚动，随帧高亮危险行。
- 右上指标行：4 个指标块 = 最大增水 m / 最大总水位 m / 淹没面积 ha / 影响人口。
- 右侧第 1 卡「潮位与增水」：ECharts 折线（潮位/增水/总水位 三条 + 当前时刻游标线）+ 顶部小 tab。
- 右侧第 2 卡「风场与工况」：风速（kt 与 m/s）、气压、距岸距离 + 工况信息表（闸泵/避风锚地/转移人数，用 villages 汇总）。
- 中间地图：Leaflet（CDN 1.9.4）+ OSM 或高德瓦片；pane 顺序 底图 < dem_preview < 淹没PNG < 影响村 < 台风路径与中心。
- 地图右下角小卡（对应参考图「流域视角/水库视角」）：「全区边界 / 重点岸段」两张缩略图切换。
- 地图下方时间轴：播放/暂停按钮、滑块、当前「Hour +018 / 36h」、帧时刻 UTC。
- 页脚一行小字：数据来源与"演示情景"说明。
### 7.2 交互（必须有，且都是真连后端）
1. 选台风 -> GET /api/typhoon/{id} -> 前端先画路径。
2. 点「推演」-> POST /api/simulate -> 时间轴可拖可播（2 s/帧），地图逐帧换 PNG overlay，右侧曲线游标移动，指标块数字滚动更新，村表按当前帧最大水深重排/标色。
3. 「参数」抽屉：Vmax、Pc、移速、移向、Holland B、Rmax、M2 振幅、coarse_amp 开关 -> 点「重新推演」-> 带 overrides 重算，结果必须真的变化，并显示"已按手动参数重算"提示条。
4. 「导出」-> 下载当前帧 PNG 与 summary JSON；「关于」-> 弹窗说明。
### 7.3 视觉规范
- 背景 #04101f -> #061a2e 径向渐变；卡片 rgba(8,25,45,.86)，1px 边框 #17456b，外发光 0 0 12px rgba(53,224,255,.12)，圆角 4px，切角可选。
- 主色 #35e0ff，次色 #2f81f7，警示 #ffb020，危险 #ff5c5c，文字 #cfe6ff，次要 #7ba7c9。
- 字体：微软雅黑/思源黑体；数字 font-variant-numeric: tabular-nums；标题 28–32px，卡片标题 15px 带 4px 青色竖条，正文 12–13px。
- 卡片与顶栏带细扫描线/角标装饰（纯 CSS，不要用图片）；禁止 emoji 当图标，用内联 SVG。
### 7.4 前端技术约束
- 零构建：index.html 直接引 CDN（echarts 5.5.1、leaflet 1.9.4），不得用 node_modules / 构建工具。
- 所有 API 走相对路径（同源），必须通过 http://127.0.0.1:8848/ 打开（file:// 不支持，README 要写清）。
- 首屏 3 s 内可交互；API 失败显示中文错误条，不能白屏。

## 8. 验收方式（调度者执行）
1. python server.py --port 8848 起服务 -> GET /api/health 为 ok。
2. POST /api/simulate（mangkhut，36 帧）-> ok:true、帧数正确、指标满足 §5 红线。
3. headless Edge 截图 http://127.0.0.1:8848/ 1280x720 与 1920x1080 各一张，比对参考图三区布局、无重叠、无溢出。
4. 参数覆盖重算一次，验证数字确实变化。

## 9. 契约修订记录 v1.1（S1 交付后由调度者裁定，优先级高于上文示例值）
1. **格距**：纬向格距 = `meta.site.pixel_deg_y` = 0.000925926°；**经向格距 = bbox 东西跨度 / nx = 0.000841751°**（= `meta.site.pixel_deg_x`）。
   任何模块（engine / 前端）换算像素↔经纬度**一律用 bbox + nx/ny**，禁止用 `pixel_deg` 去算经度（会偏 11 km）。
   `pixel_deg` 仅为兼容旧字段而保留，等于 pixel_deg_y。
2. **像元面积**：`meta.site.pixel_m` = 94.3 m（等效方格边长，使 pixel_m² 等于真实格面积 86.5 m × 102.8 m）；淹没面积 = 像元数 × pixel_m² / 10000 ha。
3. **水面高程**：本数据集的海洋/大水体为常量 **-2.0 m**（不是 0，也不是真实水深）。引擎判"水"用 DEM < 0；判"海岸种子"仍用 DEM∈[0,1.5] 且 8 邻域含 DEM<0。
4. `data/meta.json` 允许存在契约未列出的**额外键**（pixel_deg_x / pixel_deg_y / pixel_m_lon / pixel_m_lat 等），前端与引擎只读契约字段，忽略未知键。
5. `dem_preview.png` 的"水面"用深蓝 #0a2540 实色绘制（契约 §3 括号里"透明"的写法作废；实际透明仅用于 nodata）。


## 10. 风场图层契约 v1.2（台风风场演示图：风速 + 流向）

目标：在地图上叠加"台风风场"动态图层，既能看到**风速大小**（配色/图例）又能看到**流向**（粒子运动 / 箭头指向），随帧推进而旋转移动。

### 10.1 数据来源：前端本地计算，后端零改动
不新增任何 API 字段（/api/simulate 响应保持不变）。前端用当前帧已有的
center[lat,lon] / pc_mb / vmax_kt / motion_kt / heading_deg，配合 params 里的 env_pressure(1013) / holland_b(1.7) / rmax_km(40)，
按与 §5 完全相同的 Holland 式本地算风场。这样 37 帧不增包体、可 30 fps 连续动画，且台风中心在 bbox 外时也能看到风场扫进来。

### 10.2 公式（必须与 §5.2 同式）
对像素点 P，r = 距台风中心距离(米)，θ = 中心→P 的方位角（正北为 0，顺时针）：
    Rm = rmax_km*1000;  X = (Rm/r)^B;  dP = (env_pressure - pc_mb)*100
    W = sqrt( B*dP/rho_a * X * exp(-X) + (0.5*f*r)^2 ) - 0.5*f*r      [m/s]
    W = min(W, 1.15*Vmax_ms)，r < Rm*0.15 时按 W≈0 处理避免奇点
    f = 2*7.292115e-5*sin(lat_p)，rho_a = 1.15
移行非对称（北半球）：km = 1 + 0.35*min(motion_kt,50)/50*cos(θ - heading)，左侧自动 <1；W *= clamp(km, 0.6, 1.5)。
**矢量方向**（x=东，y=北，单位向量）：
    r_hat = (sinθ, cosθ)；t_hat = (-cosθ, sinθ)   // 北半球逆时针切向
    v = W * ( cos(α)*t_hat − sin(α)*r_hat )，入流角 α = 22°（可配）
即风矢量 = 切向分量 + 向内分量，画面上表现为气旋式旋转并向中心收拢。

### 10.3 渲染规范
- 容器：一个 pointer-events:none 的 canvas，挂在 Leaflet 自定义 pane（风场层）里，层序：瓦片 < 遮罩 < 淹没 < **风场** < 村庄/路径/中心气泡；canvas 必须随 move/zoom/resize 重新对齐（用 containerPoint 换算，不要用 CSS transform 硬缩放）。
- 两种模式，默认粒子：
  1) 粒子流动（默认）：≥1200 个粒子，尾迹透明衰减，按风速着色；速度与 W 成正比；每帧粒子随风矢量推进；
  2) 箭头网格：约 24×18 网格箭头，长度 ∝ W，可按需切换。
- 配色（风速 m/s，图例需同款）：0–5 #4b6b8a / 5–10 #3ba0d8 / 10–17 #35e0ff / 17–25 #27d17a / 25–33 #ffb020 / 33–45 #ff7a45 / >45 #ff2d55。
- 图例：地图右下角现有"淹没水深分级"小卡下方追加"风速 (m/s)"色带；两卡不得重叠。
- 开关：顶栏「图层」弹层里加"台风风场"总开关 + 粒子/箭头单选；默认开启粒子。关闭后必须停止 rAF 循环（省 CPU）。
- 性能红线：开启风场后，播放 37 帧、1920×1080 下不得出现 >100 ms 的单帧卡顿；粒子数可随窗口面积缩放（下限 600）。

### 10.4 验收
1. 峰值帧（Hour +020）截图：能看到明显的螺旋状风场（中心在范围外时，图画上呈现风场从边缘扫入、方向一致地气旋式旋转）；
2. 切换帧到 Hour +012 与 Hour +030，粒子方向随 heading 改变（截图对比）；
3. 关闭风场开关后 canvas 清空、rAF 停止（探针可读）；
4. 风速图例出现且不与淹没图例、视角缩略图重叠；1280×720 下同样不重叠。

