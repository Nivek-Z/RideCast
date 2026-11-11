# RideCast · 网约车服务需求建模后端

Python + TensorFlow/Keras 的可运行课题系统：种子确定的演化城市、真实订单导入、时空需求预测、可视化仪表盘 API、持久任务与模型版本。

## 快速运行

需要 **Python 3.11 或 3.12**。建议为项目新建虚拟环境。

```bash
git clone https://github.com/Nivek-Z/RideCast.git
cd RideCast
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[ml,dev]'
ridecast init
ridecast demo --days 45 --epochs 30 --tune --output reports/current-demo.json
```

当前v0.2默认训练“训练集条件率＋GRU计数残差＋因果水平校准”模型，`tune`比较Poisson/Huber候选，验证集选择融合，独立后段校准区间；旧v1模型仍能直接预测。演示命令实际生成订单、训练评估并保存样例，不自动激活模型。原 `reports/demo.json` 为保留的v1历史证据。完整依赖见 `requirements.lock.txt`。

启动两个终端，均在本目录并激活同一个虚拟环境：

```bash
# 终端一
ridecast serve
# 终端二
ridecast worker
```

访问 [交互式 API 文档](http://127.0.0.1:8000/docs)、[健康检查](http://127.0.0.1:8000/health)。

仪表盘前端位于 `web/`（需要 Node.js 18+），在第三个终端启动后打开 <http://127.0.0.1:5173>，说明见 [web/README.md](web/README.md)：

```bash
cd web
npm install
npm run dev
```

可选 Docker 单机部署：`docker compose up --build`，API 与 worker 共享命名卷；当前交付环境未启动 Docker daemon，容器启动不作为已验证结果。

## 虚拟世界如何演化

种子确定地图的非均匀街区、土地用途、人口和岗位。演化以绝对 UTC 时间桶为锚点：平滑天气锋面与季节/昼夜周期、城市与区域需求状态、已公告活动、前桶实际需求引发的供给和拥堵反馈共同生成下一时段状态。订单随机流也绑定绝对时间，同一窗口重复提取及相邻分段拼接保持一致。

静态城市版本为seeded-city-v2，演化规则为absolute-time-v2；至少5个区域时包含居住、商务、商贸、枢纽和文娱五类，布局与额外区域用途由种子决定。版本随世界及数据快照保存，城市静态版本也纳入自动world_id计算。

`GET /api/v1/worlds/{id}/state?timestamp=...&seed=42` 可查询任意仿真时刻，无需生成整天订单。`/simulations` 将时间段物化为可训练数据，`/replay` 回放历史订单。世界种子、演化种子、场景、生成器版本与时间范围共同定义一次确定性实验。宏观供给和直线插值车辆标记有明确语义，不是逐车交通数字孪生。

## 交付入口

* [后端交接与部署](docs/HANDOFF.md)：模块、启动、配置、任务状态、恢复、Git 阶段。
* [前端仪表盘接入](docs/FRONTEND.md)：图层/热力/流向/回放/训练状态接口与示例。
* [真实数据导入](docs/DATA_IMPORT.md)：GeoJSON、CSV、时区、覆盖声明、质量检查。
* [建模与实验说明](docs/MODELING.md)：特征、GRU、时序分区、基线、区间与结果边界。
* [模型升级独立验收](reports/model-quality.md)：预登记的10组未见世界等预算比较；[协议](docs/MODEL_QUALITY_PROTOCOL.md)。
* [架构决策](docs/ARCHITECTURE.md)、[一手资料调研](docs/research-notes.md)、[内部数据契约](docs/CONTRACT.md)。
* `docs/openapi.json`：导出的 HTTP 契约；重新生成：`ridecast export-openapi`。
* `reports/`：实际运行的演示、随机世界实验与验证报告。大数据/模型位于 `var/`，不进入 Git。

## 随机世界实验与检查

```bash
python scripts/benchmark_worlds.py --seeds 7 42 99 --days 28 --train --epochs 5 --output var/benchmarks/new-run
pytest -q
ruff check .
pip check
```

基准在每个世界独立重训，衡量不同仿真世界中的建模稳定性；不声称一个模型零样本迁移到未见过的城市。合成数据效果不能代替真实业务验证。API 默认仅监听本机；跨机器部署时可配置 `RIDECAST_API_KEY` 并通过网关提供 TLS。
