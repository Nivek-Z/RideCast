# RideCast 本地交接

## 启动与数据

在 macOS 或 Linux 上，用 Python 3.11/3.12 和带 npm 的 Node.js，从仓库根目录运行以下命令。

```bash
./scripts/start.sh
```

启动脚本安装所需的 `.[research]` 和前端依赖、构建页面，再调用 `ridecast present` 启动本地服务和实验进程。默认地址是 `127.0.0.1:8000`，可以用 `./scripts/start.sh --port 8001` 换端口。

默认数据是仓库内的 [Chicago TNP 原始 ZIP](../data/chicago_tnp.zip)。`ridecast setup-study --archive /path/to/archive.zip` 可核对本地归档并复制到 `var/teacher-data/teacher-data.zip`。数据读取依次采用显式的 `RIDECAST_TEACHER_ARCHIVE`、已配置的本地资料、仓库随附 ZIP。三种方式都使用同一内容校验和空间划分。

单独调试可分别运行 `ridecast serve`、`ridecast worker` 和 `npm --prefix web run dev`。Vite 开发代理使用 `RIDECAST_API` 或默认的 8000 端口。

## 结构与产物

| 入口 | 职责 |
| --- | --- |
| `research_api.py` | 接收空间复现和参数配置请求，读取状态和产物 |
| `research.py` | 从真实数据重新校准、生成、训练与评分，计算区外预测和地图 |
| `teacher_data.py` | 校验、属性矩阵、共享固定空间划分 |
| `worker.py`、`store.py` | 持久化队列、进度、日志和原子发布 |
| `web/src/study/` | 空间地图、参数对比和研究记录 |

默认 `RIDECAST_HOME=var`。数据库在 `var/ridecast.sqlite3`，结果在 `var/research/r_*/result.json`。状态与阶段事件保存在数据库。正式复现结果含留出区的行程份额与预测，以及校准参数；参数对比结果含开发区折间评分及各区的区外预测。地图展示使用简化的投影坐标边界。历史实验的大体积产物放在 `var/teacher-data/`。

批次一次写入队列，每个配置独立完成或失败。取消会在下一个进度检查点生效。模型计算、地图构建均由独立进程执行。结果文件写完后发布，注册表和任务成功在同一个所有权受保护的事务中写入。进程失联时，运行中的任务在心跳过期后标为失败，重新提交可重试。

## 验证与历史时序研究

运行 `pytest -q`、`ruff check .`、`pip check` 和 `npm --prefix web test`。启动 API 与 worker 后，运行 `python scripts/verify_http.py` 检查正式复现、批量竞技场、两种地图范围及发布结果。接口变更后用 `ridecast export-openapi` 更新 `docs/openapi.json`。

复核历史小时仿真与 GRU 时请使用 TensorFlow 2.19.1、Keras 3.11.3、NumPy 2.1.3 和 PyArrow 21 的兼容版本。历史 HTTP 接口可用 `ridecast serve-legacy`、`ridecast worker --legacy` 及 `scripts/verify_legacy_http.py`。原 v1 接口文档可用 `git show c93f122:docs/openapi.json` 查阅。历史模型和本次静态任务的正当用途详见 [建模说明](MODELING.md)。
