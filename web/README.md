# RideCast Console · 前端仪表盘

Vite + React + TypeScript。地图与图表均为本地 SVG 自绘，不依赖外部底图、地图密钥或图表库；支持浅色 / 深色 / 跟随系统三种主题。

## 界面与交互

采用 [Vercel 风格参考](https://styles.refero.design/style/f24daf3a-d43f-4dec-85a9-8ac1d5148a03) 的近白底、黑白层级、细边框和 6px 圆角。借鉴 [Aceternity UI](https://ui.aceternity.com/) 的标签切换、网格背景与悬停高光，以本地 React/CSS 实现；未引入 Tailwind、Motion 或玻璃效果依赖。

顶部常驻七个页面入口，城市与数据集在内容上方切换；页面仅展示简短标题。总览地图支持点击或键盘选择区域并联动需求曲线。模型回测中用不同灰度和虚线区分观测、预测与基线。窄屏自动改为单列，表格和长标签组可在各自容器内横向滚动。

标签组支持方向键、Home/End；弹窗和抽屉支持 Escape、焦点约束与焦点恢复；动画尊重 `prefers-reduced-motion`。数据口径、未知供给、历史回放和「未执行」调配标记继续保留。

本次演示和浏览器验收文件保存在忽略的 `var/frontend-redesign/`，交付摘要见 `reports/frontend-redesign.md`。

## 运行

先在仓库根目录启动后端（两个终端，同一虚拟环境）：

```bash
ridecast serve
ridecast worker
```

再启动前端：

```bash
cd web
npm install
npm run dev
```

打开 <http://127.0.0.1:5173>。开发服务器把 `/api`、`/health` 代理到 `http://127.0.0.1:8000`，浏览器同源访问，无需修改后端 CORS。后端在其他地址时设置环境变量 `RIDECAST_API`，或在「系统 → 连接」里填写 API 地址（此时需把前端来源加入 `RIDECAST_CORS_ORIGINS`）。配置了 `RIDECAST_API_KEY` 时在同一处填写密钥，密钥只保存在本机浏览器并通过 `X-API-Key` 发送。

`npm run build` 做类型检查并产出 `dist/`；`npm run preview` 以同样的代理预览构建结果。

## 页面与接口

| 页面 | 内容 | 主要接口 |
| --- | --- | --- |
| 总览 | KPI、需求曲线、最新桶热力、小时/周内分布、区域表、数据质量 | overview、timeseries、heatmap、worlds/{id}/map |
| 城市实况 | 历史回放（订单脉冲、在途车辆、OD 流向）与世界演化（任意时刻单桶状态） | replay、flows、worlds/{id}/state |
| 订单 | 按上车区域与状态筛选的分页列表 | orders |
| 模型 | 模型与基线对比、回测叠图、学习曲线、时间划分、启用标记 | models、models/{id}/history、evaluation、activate |
| 预测与调配 | 未来需求热力与经验区间、情景倍数、启发式调配建议 | forecasts、dispatch-suggestions |
| 任务 | 持久任务列表、SSE 实时日志、训练中学习曲线、取消 | jobs、jobs/{id}/events、jobs/{id}/history |
| 系统 | API/worker 状态、能力与限制、主题与连接设置、资源清单 | system、worlds、datasets |

「新建」菜单对应 `POST /worlds`、`/simulations`、`/datasets/import`、`/training-jobs`。

## 实现约定

* 时间：后端一律 UTC，界面按 `world.timezone` 显示；提交的时间带显式偏移。
* 任务事件用 fetch 读取 SSE（可带 `X-API-Key`），`payload` 作为局部补丁合并，收到 `done` 后主动关闭并重新 GET 任务。
* 回放车辆位置在客户端按后端同一假设（行程自 `requested_at` 开始、起终点直线插值）逐帧计算，不是 GPS 轨迹。
* `supply_observed=false`、`outcomes_observed=false` 或值为 null 时显示「未知」，不按 0 绘制；`supply_gap` 标注为计数差，不等同未满足请求。
* WAPE/sMAPE/改进率是已乘 100 的百分数；完成率、取消率、区间覆盖率是 0–1 比例。
* 调配建议始终标注「启发式建议 · 未执行」。
