# 内部接口约定（v1）

所有持久时间采用带时区 UTC；展示/日历特征按 world.timezone（默认 Asia/Shanghai）。桶左闭右开。需求标签 demand=每桶区域发起订单数，passengers 是人数，不作为同桶输入。

## world 字典

`schema_version: "1.0", world_id: "w_<16hex>", name, timezone, bucket_minutes: 60, seed, zones: GeoJSON FeatureCollection, roads: GeoJSON FeatureCollection, calendar: {holiday_dates: [YYYY-MM-DD], working_dates: []}, events: [{name, zone_id, start: ISO, end: ISO, intensity: float}]`。

zone properties 必有 `zone_id: str, name, kind, base_demand: float, population: float, jobs: float, attractiveness: float`。Geometry 为 WGS84 Polygon/MultiPolygon；不相交；zone_id 稳定有序。道路 LineString，properties 可含 road_id/speed_kph。地图为虚构城市，禁止表述为真实城市复刻。

## dataset 目录与函数

`panel.parquet`: timestamp(UTC), zone_id(str), demand(int), passengers(int), completed(int), cancelled(int), temperature(float), precipitation(float), wind_speed(float), holiday(int), event_intensity(float), traffic_index(float), available_drivers(int)。每桶每区恰一行、均匀完整面板，无缺失。`orders.parquet`: order_id, requested_at(UTC), pickup_zone_id, dropoff_zone_id, passenger_count, status(completed/cancelled), pickup_lon, pickup_lat, dropoff_lon, dropoff_lat, driver_id（空值表示未服务）, travel_minutes。真实导入允许可选字段为空。

`manifest.json`: schema_version, dataset_id(d_<16hex>), world_id, source(synthetic/imported), target_semantics(requests/completed_trips), observation_start, observation_end(exclusive), bucket_minutes, row_count, order_count, zone_count, seed, scenario, quality, files, created_at。另存 `world.json` 快照。

函数（由 simulation/data 模块实现）：

* `simulation.create_world(name="霁川市", rows=4, cols=4, seed=42, bucket_minutes=60, timezone="Asia/Shanghai", **kwargs) -> dict`
* `simulation.validate_world(world: dict) -> dict`（校验并返回规范化对象，不写盘）。
* `simulation.simulate(world, start: str, days: int, seed: int, scenario: dict | None=None, progress=None) -> (panel: DataFrame, orders: DataFrame, metadata: dict)`；progress(stage: str, fraction: float, message: str)。scenario 含 demand_multiplier/rain_multiplier/traffic_multiplier/supply_multiplier，可带 holiday_dates/working_dates/events。按地图区域实际采样坐标。
* `data.write_dataset(output_dir: Path, world: dict, panel, orders, metadata: dict, dataset_id: str) -> manifest: dict`。
* `data.import_orders(csv_path: Path, world: dict, output_dir: Path, dataset_id: str, options: dict, context_path: Path | None=None, progress=None) -> manifest: dict`。options 必有 observation_start/end、coverage_attested(true)、target_semantics；可含 column_map(canonical -> source)、timezone、invalid_policy(reject/drop)。必须校验去重，不能把重复订单数作需求；导入采用相同完整面板与契约。

## ML 模块约定

* `ml.train_model(dataset_dir: Path, output_dir: Path, model_id: str, config: dict, progress=None) -> metadata: dict`。config epochs/batch_size/lookback/horizon/units/learning_rate/seed/tune/loss，当前默认30轮、loss=poisson，支持huber；tune对比两种计数损失。读取 panel、world、manifest；写 model.keras、metadata.json、history.json、test_predictions.parquet。真实TensorFlow/Keras季节残差GRU；v1模型由冻结legacy实现兼容。
* `ml.forecast(model_dir: Path, dataset_dir: Path, horizon: int | None=None, scenario: dict | None=None) -> dict`。返回 forecast_origin、bucket_minutes、model_id、dataset_id、rows=[timestamp,zone_id,predicted_demand,lower,upper]、exogenous_assumption；默认严格从 dataset observation_end 向前预测，horizon 不超过训练头长度；scenario 为未来已知外因覆盖。
* metadata 必有 model_id,dataset_id,world_id,config,metrics（模型与基线，MAE/RMSE/WAPE/sMAPE，分horizon和分zone）、split（完整时间边界）, feature_schema, status, created_at；v2新增blending/validation_subsplit、纯neural指标和operational分组。验证前65%选择，后35%校准，短数据不能分开时明确标记；测试仅最终评估。训练集拟合缩放，不能读未来实测天气。自动模型晋升不默认执行。

## 主进程与 worker

主模块 store/settings/api/jobs/cli 由主代理实现。SQLite 保存资源元信息与 durable jobs/events；API 只排队，独立 `python -m ridecast.worker` 执行 simulate/import/train。任务返回 202/Location，状态 queued/running/succeeded/failed；progress 单调非递减，记录 stage/log/error/heartbeat。worker 可重启，孤立运行任务明确标记失败。

仪表盘需：world GeoJSON、dataset overview/quality、timeseries、heatmap、flows、replay、orders分页，模型 metrics/history、资源及 jobs 列表、SSE 事件流、forecast、dispatch（启发式建议，不能声称已调度）。API OpenAPI 为对外契约。
