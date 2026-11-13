# Linear 暗色候选与逐页对比 · 2026-11-13

当前候选分支：`codex/frontend-linear-dark`，基于 `3310e2a`。Warp 候选 `codex/frontend-warp-dark` 保留在 `3310e2a`；Vercel 候选 `codex/frontend-monochrome` 保留在 `2ce541c`；原版 `main` 保留在 `5e1e0e0`。

## 界面

按 [Linear 参考](https://styles.refero.design/style/90ce5883-bb24-4466-93f7-801cd617b0d1) 使用近黑背景、灰白文字、细边框、12px 面板与 6px 按钮圆角。主要操作和当前导航用黄绿色标记，其余图表与组件保持中性配色。

采用顶部导航与独立城市/数据集栏；KPI 合并为分隔栏。小时与星期分布按标签切换，环境卡去掉与 KPI 重复的完成率环图，保留区域均值和观测时刻。仅暗色，无主题切换和宣传标语；不新增运行依赖。

数据请求、模型和 HTTP 契约沿用原版本。保留口径、未知供给、历史回放、经验区间、未执行调配及键盘操作约定。

## 验证

| 检查 | 本机结果 |
| --- | --- |
| `npm run build` | TypeScript 与 Vite 构建通过 |
| `pytest -q` | 90 passed，19 条依赖弃用警告 |
| `ruff check .` | 通过 |
| `pip check` | 通过，沿用既有 Python 3.12 兼容栈 |
| API + 独立 worker，`scripts/verify_http.py` | 68/68 通过 |
| 浏览器验收 | 55 项通过，运行时异常 0 |
| 七页 × 1440 / 1024 / 768 / 390px | 页面无横向溢出 |
| 逐页对比页 | 页面、候选切换，叠图滑杆、手机布局均通过 |

浏览器检查包含实际 API 窗口统计、地图联动、订单筛选分页、历史回放与 OD、模型曲线和时间划分、情景预测、未执行调配、焦点约束、旧浅色偏好、分布切换、未知供给、空工作区与断线提示。空工作区和断线场景使用浏览器请求拦截，其余主链路访问真实本地 API。HTTP 验证按原脚本生成了忽略目录内的小型联调资源。

## 对比交付

目录：`var/frontend-linear/comparison/`。

- `index.html`：三个候选、七个页面的对比页。默认左 Warp / 右 Linear，支持候选切换、原始尺寸、并排与叠图。图片内嵌，无外部图床。
- `dashboard / map / orders / models / forecast / jobs / system-warp-vs-linear.png`：七张完整尺寸并排对照图，各自左 Warp / 右 Linear。
- `vercel/`、`warp/`、`linear/`：三个候选的 21 张原图。
- `capture-manifest.json`：版本截图使用的世界、数据集、模型和尺寸。
- `comparison-smoke.json`：对比页交互检查结果。

三版均为暗色、1440×1080 首屏，使用相同霁川市 45 天合成数据（100,550 个请求）、同一已训练模型、默认 7 天/全城筛选和默认回放、预测步长。Warp 与 Vercel 从对应 Git 提交导出后独立运行，未修改保留分支。

应用验收文件保存在 `var/frontend-linear/browser-smoke.json` 与 `http-smoke.json`。截图、对比页和运行时文件留在忽略的 `var/`。

## 切换候选

工作区干净时运行：

```bash
git switch codex/frontend-linear-dark  # 当前候选
git switch codex/frontend-warp-dark    # Warp 候选
git switch codex/frontend-monochrome   # Vercel 候选
git switch main                       # 项目原版
```

切换代码不删除 `var/` 中的数据、模型和对比文件。
