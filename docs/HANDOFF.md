# RideCast 本地交接

## 启动与数据

从项目根目录启动。环境使用 Python 3.11/3.12。当前工作台需安装 `.[research]`。复核历史实验时，沿用历史 TensorFlow 2.19.1、Keras 3.11.3、NumPy 2.1.3、PyArrow 21 的兼容版本。

```bash
source .venv/bin/activate
npm --prefix web run build
ridecast setup-study --archive '/absolute/path/网约车选题 数据.zip'
ridecast present
```

`setup-study` 检查原 ZIP 内的 GeoJSON 校验值、字段及几何，随后复制到 `var/teacher-data/teacher-data.zip`。路径也可通过 `RIDECAST_TEACHER_ARCHIVE` 指定。相同的数据用于复现历史数值和固定空间划分。

`present` 启动绑定在 `127.0.0.1:8000` 的本地服务，并启动独立 worker。调试可以分开运行 `ridecast serve` 和 `ridecast worker`，前端可用 `npm --prefix web run dev`。Vite 开发代理使用 `RIDECAST_API` 或默认的 8000 端口。

## 结构与产物

| 入口 | 职责 |
| --- | --- |
| `research_api.py` | 接收空间复现和参数配置请求，读取状态和产物 |
| `research.py` | 从真实数据重新校准、生成、训练与评分，计算区外预测和地图 |
| `teacher_data.py` | 校验、属性矩阵、共享固定空间划分 |
| `worker.py`、`store.py` | 持久化队列、进度、日志和原子发布 |
| `web/src/study/` | 空间地图、参数对比和研究记录 |

默认 `RIDECAST_HOME=var`。数据库在 `var/ridecast.sqlite3`，结果在 `var/research/r_*/result.json`。状态与阶段事件保存在数据库。正式复现结果含留出区的行程份额与预测，以及校准参数；参数对比结果含开发区折间评分及各区的区外预测。地图展示使用简化的投影坐标边界。各轮历史实验的原始资料和大产物继续放在 `var/teacher-data/`。

批次一次写入队列，每个配置独立完成或失败。取消会在下一个进度检查点生效。模型计算、地图构建均由独立进程执行。结果文件写完后发布，注册表和任务成功在同一个所有权受保护的事务中写入。进程失联时，运行中的任务在心跳过期后标为失败，重新提交可重试。

## 验证与历史时序研究

运行 `pytest -q`、`ruff check .`、`pip check` 和 `npm --prefix web test`。启动 API 与 worker 后，运行 `python scripts/verify_http.py` 检查正式复现、批量竞技场、两种地图范围及发布结果。接口变更后用 `ridecast export-openapi` 更新 `docs/openapi.json`。

若要复核历史小时仿真与 GRU 的 HTTP 行为，使用 `ridecast serve-legacy`、`ridecast worker --legacy` 及 `scripts/verify_legacy_http.py`。原 v1 接口文档可用 `git show c93f122:docs/openapi.json` 查阅。历史模型和本次静态任务的正当用途详见 [建模说明](MODELING.md)。
