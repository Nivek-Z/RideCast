# RideCast 研究工作台

React、TypeScript、Vite 和 MUI，使用 Material 3 风格，支持浅色和深色模式。页面支持手机布局和系统减少动态效果设置。真实几何、运行进度和评分来自本地研究 API。历史汇总由构建适配器从仓库自带的报告生成，构建时没有读取老师原始数据。

```bash
npm ci
npm run build
npm test
```

在项目根目录运行 `./scripts/start.sh` 以构建并启动 Web 和实验进程。原始 ZIP 随仓库提供。开发时从本目录运行 `npm run dev`，项目根目录另启 `ridecast serve` 与 `ridecast worker`。

- **空间验证**按 Chicago TNP 的累计行程量估计区域份额。可切换空间划分、观测份额、生成模型估计和份额差异，查看实际运行阶段、用时与评分。
- **参数对比**可复制一条成绩的配置后单组运行，也可选择多个样本规模和正则强度做组合。按种子组查看排行，勾选最多三组对照同色阶误差地图，并查看逐折和种子得分。
- **研究记录**包含数据划分、稳定性证据和四个实验阶段。历史结果与现场结果均以实际计算产物呈现。

接口、工作进程和数据文件见 [交接说明](../docs/HANDOFF.md)。

界面参考 Material 3 的角色配色、排版和布局规范，使用色调表面、桌面导航轨和移动端底部导航。 [颜色角色](https://m3.material.io/styles/color/the-color-system/) · [排版](https://m3.material.io/styles/typography/applying-type) · [布局](https://m3.material.io/foundations/layout/canonical-examples/overview)

顶栏的色样可直接切换 Pixel 蓝、经典紫和青绿，浏览器会记住选择。默认配色使用 Android Material You 的后备蓝色种子。 [Android 动态色规范](https://source.android.com/docs/core/display/dynamic-color)

顶栏的月亮／太阳按钮切换深色或浅色模式。首次打开跟随系统，手动切换后由浏览器保存。三套配色、地图和图例均适配两种模式。
