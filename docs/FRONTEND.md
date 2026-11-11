# 视觉仪表盘前端接口交接

Base URL：`http://127.0.0.1:8000/api/v1`。交互文档 `/docs`；可用 `ridecast export-openapi` 导出 `docs/openapi.json` 生成请求客户端。持久时间和常规响应时间采用 ISO8601 UTC，演化状态另有 `local_time`；展示时按 world.timezone 转换，所有请求时间建议显式携带时区。时间过滤 `[start,end)`。经纬度为 WGS84，数组顺序 `[longitude,latitude]`。地图为 GeoJSON，前端可选 MapLibre、Leaflet、deck.gl 等；底图可由本地道路/区域直接绘制，不依赖外部地图密钥。

## 页面与接口映射

| 画面/动作 | 接口 | 关键内容 |
| --- | --- | --- |
| 系统状态、worker灯 | GET /system | 能力、限制、job_counts、workers/online、server_time |
| 城市选择/新建 | GET /worlds；POST /worlds | name/rows/cols/seed/timezone/bucket_minutes |
| 地图图层 | GET /worlds/{id}/map | zones、roads 两个 FeatureCollection，is_virtual |
| 连续演化时间游标 | GET /worlds/{id}/state?timestamp=...&seed=42 | 单桶 zones/totals/weather/active_events，绝对时间可随机访问 |
| 历史场景生成 | POST /simulations | 返回202与任务；不立即返回数据集 |
| 数据集选择/来源 | GET /datasets；GET /datasets/{id} | synthetic/imported、requests/completed_trips、quality、时间范围 |
| KPI/小时周内分布 | GET /datasets/{id}/overview | kpis、zones、weather、distributions、quality、latest_bucket |
| 城市/区域需求曲线 | GET /datasets/{id}/timeseries | zone_id可选，start/end/limit，rows/total_count/truncated |
| 区域热力/供需差 | GET /datasets/{id}/heatmap?timestamp=... | rows+着色可用geojson；同时天气/活动/供给 |
| OD流向弧线 | GET /datasets/{id}/flows?start=...&end=...&limit=100 | rows+geojson；未知目的地区域保留null，不画假线 |
| 动画回放 | GET /datasets/{id}/replay?timestamp=...&limit=100 | orders、vehicles、zones、events及近似说明 |
| 订单列表 | GET /datasets/{id}/orders?zone_id=...&status=...&offset=0&limit=100 | rows、total_count、next_offset |
| 发起训练 | POST /training-jobs | dataset_id+config；返回202任务 |
| 任务卡片/日志 | GET /jobs；GET /jobs/{id}；GET /jobs/{id}/events | state/progress/stage/message/error/时间/持久事件 |
| 训练中学习曲线 | GET /jobs/{id}/history | 候选/epoch/loss/val_loss/duration_seconds；可轮询 |
| 取消 | DELETE /jobs/{id} | 合作取消，运行中先返回cancelling |
| 模型比较 | GET /models；GET /models/{id} | metadata.metrics：模型/基线/分步/分区/区间；metadata.split与candidates另列 |
| 最终训练曲线 | GET /models/{id}/history | 各候选epochs及选中曲线 |
| 真实/预测叠图 | GET /models/{id}/evaluation?offset=0&limit=1000 | rows内actual_demand/predicted_demand/baseline_demand/lower/upper，含forecast_origin/horizon |
| 模型启用标记 | POST /models/{id}/activate；GET /worlds/{id}/active-model | 显式切换指针；预测仍显式选model_id |
| 未来需求热力/区间 | POST /forecasts | forecast_origin、rows、totals、interval、exogenous_assumption、scenario |
| 调配建议弧线 | POST /dispatch-suggestions | zones/recommendations/conservation，未执行的启发式建议 |

资源列表 `/worlds`、`/datasets`、`/models` 和任务列表 `/jobs` 支持 limit/offset，返回 `{items,total,limit,offset}`。`POST /worlds`、`GET /datasets/{id}`、`GET /models/{id}` 返回资源封装 `{id,kind,name,status,metadata,created_at}`；`GET /worlds/{id}` 直接返回世界字典。数据来源、时间范围、质量和模型指标应从资源的 `metadata` 读取，不能假定所有接口字段都在顶层。

`status` 的订单取值 completed/cancelled，表示数据集最终结果。回放位置为占用车辆的直线插值，不是原始GPS，也没有逐车跨桶连续性保证。演化单桶状态无需模型；forecast 必须先完成训练，不能把仿真真值当模型预测画图。OpenAPI 提供请求参数与限制；聚合响应目前为动态字典，准确字段还应参照本文及 `reports/demo.json` 的实测示例。

## 演化状态与历史回放

`/worlds/{id}/state` 的顶层 `status="synthetic_observation"`；每个 `zones[]` 的 `state` 为 `undersupplied` 或 `balanced`，依据该桶 `cancelled>0` 判定。`active_events` 是 `{zone_id,intensity}` 列表；它不是含事件名称的完整日程。返回的 `latent_regime`、`previous_bucket_demand` 是仿真诊断字段，应与模型可见特征区分。时间游标由前端控制，任意时刻映射到包含它的桶，接口本身不维护实时世界时钟。

相同世界种子、运行seed、绝对时间、场景及生成器版本会重现同一状态。HTTP state 使用注册世界的原始日程，当前不接收 scenario 参数；若历史数据集生成时覆盖了天气/供给倍数或活动日程，应使用该数据集的 heatmap/replay 查看对应场景，不能拿默认 state 混作同一历史。

`heatmap.timestamp` 为桶起点。`replay.timestamp` 是桶内回放游标，默认最新桶中点；`mode="historical_replay"`、`is_live=false`。`orders` 包含该桶全部请求，可能有晚于游标的订单；前端应按 requested_at 决定是否已出现。`vehicles` 只包含游标时刻可根据 driver_id、坐标、travel_minutes 推导的占用车辆，可能包含上一桶仍未结束的行程。假设行程在 requested_at 开始，坐标作起终点直线插值；缺少数据时不给车辆点，不生成虚构空闲车位置。`order_count/vehicle_count` 为截断前总数，另有 `orders_truncated/vehicles_truncated`。

## 推荐联调流程

```http
POST /api/v1/worlds
Content-Type: application/json

{"name":"霁川市","rows":3,"cols":3,"seed":42,"bucket_minutes":60,"timezone":"Asia/Shanghai"}
```

响应资源的 `id` 为 world_id。相同参数的新建可复用相同确定性世界。然后：

```json
{
  "world_id": "替换为实际w_标识",
  "start": "2026-01-01T00:00:00+08:00",
  "days": 45,
  "seed": 42,
  "scenario": {"rain_multiplier": 1.2, "supply_multiplier": 0.9}
}
```

提交到 `/simulations`，202响应的 `Location` 指向任务。成功后 `result={resource_type:"dataset",resource_id:"d_..."}`，读取 overview/map/heatmap。再向 `/training-jobs` 提交：

```json
{"dataset_id":"实际d_标识","config":{"lookback":24,"horizon":3,"epochs":30,"units":32,"batch_size":128,"learning_rate":0.001,"seed":42,"tune":true}}
```

成功得到 m_ 标识，向 `/forecasts` 提交 `{"model_id":"m_...","dataset_id":"d_...","horizon":3}`。模型预测仅从该数据集的 observation_end 向前，不能用该接口回填训练期曲线；回测图用 evaluation。`lookback/horizon` 单位是时间桶，15分钟桶下24代表6小时。

## 任务状态及实时展示

`progress` 范围0–1，前端显示百分比时乘100。任务 `state` 用于颜色和交互，不用progress判断完成。成功资源只在succeeded后可用。queued通常表示等待worker；`/system.worker_online=false` 时应提示启动worker。failed时展示 `error.code/message`，`error.type` 可选，并提供重新提交；cancelled为终态。资源的 `status="ready"`、学习曲线的 `status="starting"/"training"/"ready"` 和任务 `state` 属于不同字段，不能共用枚举。

训练中的候选/epoch/损失取 `/jobs/{id}/history` 的结构化字段，不解析message字符串。`candidates[].epochs[]` 为各候选曲线，顶层 `epochs` 为当前或选中候选；候选编号从0开始，epoch从1开始。每条epoch含 `loss/val_loss/duration_seconds`；损失单位读取候选或history.loss_unit：v1是log1p-MSE，v2默认Poisson计数损失、可选Huber；Poisson省略目标常数后可为负，不能标成“订单数误差”或跨loss直接比较。初始曲线为空是正常状态；尚无快照时响应有 `state`，产生快照后改用曲线自身的 `status`，任务状态仍以 `/jobs/{id}` 为准。

SSE支持 `Last-Event-ID` 和 `?after=<id>` 续读。`state/progress` 事件的 data 为 `{id,job_id,event_type,payload,created_at}`，其中 payload 是局部补丁：progress事件通常只有 progress/stage/message，终态state事件可能只有 state/result/error，不应把它当完整任务覆盖已有字段。事件id是持久游标，可原样保存续读。

终态会额外发送 `event: done`，其 data 直接为 `{"state":"succeeded|failed|cancelled"}`，**没有 payload 包装，也没有新的事件id**。连接随后结束；前端主动关闭，重新GET任务读取完整终态并按需刷新资源，避免EventSource自动重连。可用每1–2秒轮询降级。心跳是 `: keepalive` 注释，不是JSON事件。

```js
const events = new EventSource(`${base}/jobs/${jobId}/events`);
events.addEventListener('progress', event => {
  const {payload} = JSON.parse(event.data);
  renderProgress(payload.progress, payload.stage, payload.message);
});
events.addEventListener('state', event => mergeJobPatch(JSON.parse(event.data).payload));
events.addEventListener('done', async event => {
  const {state} = JSON.parse(event.data);
  events.close();
  await refreshJob(jobId); // GET完整任务，包含最终result/error
  if (state === 'succeeded') refreshResources();
});
```

配置API key时，浏览器原生EventSource不能自定义 X-API-Key，改用带header的 fetch SSE解析或轮询，不把key放URL。所有REST调用可统一附 `X-API-Key`。默认CORS允许localhost:3000/5173，需要127.0.0.1或其他端口时修改环境配置。

## 数值和图表语义

* demand为每桶发起订单数（或真实导入显式标记的completed_trips），passengers为人数；预测predicted_demand是非负期望值，可为小数。
* completed/cancelled为最终结果；completed_trips来源无法观察取消/潜在未满足需求。看 `target_semantics/outcomes_observed`，不能把未知取消率绘为0。
* available_drivers为一个时刻的宏观司机数量快照。跨区域同一桶可求和，跨多个小时求和不能当独立司机数。`supply_observed=false` 时面板中的0可能是缺失占位；heatmap/timeseries仍保留该原始值，供给KPI/供需差为null，前端须据flag显示“未知”。synthetic来源的true表示模拟已知，仍不是现实观测。
* heatmap/timeseries的 `supply_gap=demand-available_drivers` 为请求数与司机数的原始计数差，正数只表示请求数较多，**不等于未满足请求数**。仿真每司机可服务量随桶时长和拥堵变化；实际合成未服务请求看 cancelled/unserved_requests，演化state也据cancelled判定。completed_trips口径的unserved_requests和supply_gap为null。
* WAPE/sMAPE的返回数值已经乘100，显示时直接加 `%`；WAPE可超过100，sMAPE范围通常为0–200。`model_mae_improvement_percent` 也是百分数，负值表示不及基线。完成/取消率、区间test_coverage和nominal_marginal_coverage为0–1；MAE/RMSE单位订单。零真实总需求时WAPE为null。
* evaluation 同一目标时刻可能有不同预测起点/步长。画一条预测曲线时筛选固定horizon或forecast_origin，不能直接把重复时刻加总。
* lower/upper为验证残差经验区间；查看区间说明和测试覆盖率，不显示“保证90%”。
* dispatch只对首个预测桶规划。默认从数据最新桶取已知供给，返回supply_snapshot_timestamp说明时效；真实导入无完整供给上下文时 supply_known=false、recommendations为空。也可传完整的 `{zone_id:非负整数}` supply映射。`required_drivers=ceil(predicted_demand)`，按“下一桶每司机服务一单”计算，独立于仿真的服务容量公式；distance_km是区域质心球面直线距离，driver_km=司机数×该距离，均未考虑道路/到达时间/服务时长。conservation只保证建议转移前后司机数量守恒，executed=false。

timeseries按时间升序返回最早limit条，`total_count` 为过滤后的桶数，`truncated` 表示还有后续；继续获取可将start设为最后一桶末尾。没有zone_id时，订单/乘客/司机数仅在同一桶内跨区域求和，天气为区域简单均值，event_intensity为区域最大值。OD `rows[].orders` 包含取消请求；OD几何为区域质心连线，不代表真实轨迹。evaluation分页总数叫 `total`，订单分页总数叫 `total_count`，请分别处理。

## 错误、限流与缓存

错误结构 `{"error":{"code":"invalid_input","message":"...","details":null},"request_id":"..."}`；校验422，资源不存在404，key错误401，上传超限413，内部错误500。定位问题请记录响应 `X-Request-ID`。限制/字段范围以 `/system` 和 OpenAPI 为准。

资源版本不可变：地图、已完成数据/模型可按ID缓存。回放建议前端节流/预取，相同时间桶热力无需逐帧重复请求；动画在客户端插值，后端提供桶状态和有限车辆快照。当前未实现WebSocket推送仿真时钟，时间游标由前端控制。

当前v2 metadata.blending 显式说明纯神经/神经季节融合/基线回退和神经权重；metrics.model为实际服务效果，metrics.neural为纯神经效果，operational含城市/高峰/高需求分组。派单建议请求支持scenario，前端应复用已展示forecast的scenario，确保建议与预测属于同一情景。
