# 真实数据导入与数据字典

系统以统一的区域×时间桶面板连接仿真和真实数据。真实接入路径为：准备 WGS84 区域 GeoJSON → 导入世界 → 准备订单 CSV 和可选上下文 CSV → 明确观测覆盖及标签口径 → 提交导入任务 → 检查质量报告 → 训练。当前HTTP上传CSV；Parquet等来源应先离线转换，不伪造缺失字段。

## 地图

用 `GET /api/v1/worlds/{id}` 导出完整世界 JSON 作为结构样例，替换 name/timezone/zones/roads/calendar/events 后提交 `POST /api/v1/worlds/import`。导入会生成新 world_id；不要覆盖已有世界。zones 为 FeatureCollection，geometry 是有效非空 Polygon/MultiPolygon，区域面积不能重叠，允许共享边界。zone_id 为唯一非空字符串，最多64字符，最多100个区域；坐标须为WGS84经度[-180,180]、纬度[-85,85]，投影坐标应先转换。

每个区域 properties 最少提供 zone_id，推荐 name/kind/base_demand/population/jobs/attractiveness。缺省name采用zone_id，kind="mixed"、base_demand=10、population=10000、jobs=5000、attractiveness=1；这些占位属性不等同真实调查值。roads 可为空 FeatureCollection，提供时支持 LineString/MultiLineString；当前仿真道路用于图层，不做路网路径搜索。真实地图 `metadata.fictional=false`，不应保留虚拟世界的描述；`metadata.procedural_events=false` 关闭自动虚构公告，真实活动由已知计划提供。

```json
{
  "schema_version":"1.0", "name":"实际研究区域", "timezone":"Asia/Shanghai", "bucket_minutes":60,
  "seed":42,
  "zones":{"type":"FeatureCollection","features":[
    {"type":"Feature","properties":{"zone_id":"A","name":"区域A","kind":"mixed"},
     "geometry":{"type":"Polygon","coordinates":[[[120,30],[120.01,30],[120.01,30.01],[120,30.01],[120,30]]]}}
  ]},
  "roads":{"type":"FeatureCollection","features":[]},
  "calendar":{"holiday_dates":[],"working_dates":[]}, "events":[],
  "metadata":{"fictional":false,"procedural_events":false}
}
```

修改区域几何/属性或时间桶应创建新世界并重新训练。日历/活动场景变化不改变物理地图兼容性，但必须保证是真正预测时可知的计划。

## 订单 CSV

| 字段 | 必需性 | 语义 |
| --- | --- | --- |
| order_id | 必需 | 唯一非空订单ID，不超过128字符 |
| requested_at | 必需 | 请求时间；completed_trips可映射上车/行程起始时间，口径需说明 |
| passenger_count | 必需 | 1–8整数人数；未知时先明确缺失处理，不默认为真实人数 |
| pickup_zone_id | 与上车坐标二选一 | 已导入地图区域 |
| pickup_lon/pickup_lat | 与区域ID二选一 | 成对出现；提供坐标与ID时必须一致 |
| status | requests必需 | completed 或 cancelled；completed_trips可省略并视为completed |
| dropoff_zone_id | 可选 | 目的地；未知时null，不产生假OD线 |
| dropoff_lon/dropoff_lat | 可选 | 成对有效坐标，与目的地区域一致 |
| driver_id | 可选 | 匿名司机标识；取消订单不绑定服务车辆 |
| travel_minutes | 可选 | (0,1440]有限分钟数；仅供完成订单回放估计 |

输入列可用 `column_map` 映射：`{"requested_at":"pickup_datetime","pickup_zone_id":"PULocationID"}`，方向是规范字段→源CSV列。不可把多个规范字段映射到同一源字段。坐标在共享边界上且未给zone_id时，选字典序最小区域；不在地图范围的坐标拒绝或按策略丢弃。

同order_id且规范字段完全一致的重复行去重并记录数量。内容冲突的同ID在reject策略下失败；drop策略删除该ID的全部冲突行并记录，避免任意取一条。无效订单会报告行号/原因；drop仅保存前30个错误示例并保留总计。非空原始文件若所有订单都被丢弃，不生成成功数据集。

## 观测覆盖与时间

导入options示例见 `examples/import-options.json`：

```json
{"observation_start":"2026-01-01T00:00:00+08:00","observation_end":"2026-01-03T00:00:00+08:00",
 "coverage_attested":true,"target_semantics":"requests","timezone":"Asia/Shanghai","invalid_policy":"reject","column_map":{}}
```

coverage_attested=true是提供者确认该区间全部区域完整采集，才允许空桶补零。采集中断不能当作零需求，应分段导入完整区间。observation_end为不包含的右端，端点须对齐UTC时间桶。订单在区间外按策略拒绝/丢弃。无时区时间按options.timezone解释，夏令时模糊/不存在时刻拒绝；有偏移的时间统一转UTC。所有内部持久数据使用带时区UTC，日历按world.timezone。

世界日历将未列为working_dates的周末也视为holiday；holiday_dates与working_dates不能包含同一天。自动创建城市的2026-01-01只是示例假日，真实研究应按当时已公布的节假日及调休安排提供日历。

`target_semantics=requests` 表示观察全部发起请求，含取消；`completed_trips` 仅表示已实现行程量，无法观察未接单/取消/潜在需求。不能因为完成行程全部成功就报告真实满意率100%。公开TLC数据包含行程及区域字段，但应按官方数据字典适配，并保留其准确性/完整性限制，参见[官方数据页](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page)。

## 上下文 CSV

必须有timestamp、zone_id；每个桶区域至多一行，时间对齐且在观测区间，zone_id合法。可提供 temperature(℃)、precipitation(降雨强度，mm/h)、wind_speed(m/s)、traffic_index(无量纲)、available_drivers(非负整数快照)、holiday(0/1)、event_intensity(非负强度)。合成降雨为强度指标，不随桶长度做累计；真实来源若提供每桶累计雨量，应先除以桶小时数再导入。所有数值须有限且已提供的单元格不能缺失；temperature范围[-80,65]，available_drivers范围[0,100000]且为整数，其余风/雨/交通/活动数值范围[0,1000]。

缺失上下文使用 temperature=20、precipitation=0、wind_speed=2、traffic_index=1、available_drivers=0 的明确占位，holiday/events由世界日历推导，质量报告记录补值行数与列。0供给占位不意味着真的无车；只有合成数据或完整覆盖且明确提供供给列时，仪表盘才视供给已观测。未来天气与供给默认沿用预测起点最后已知值；此上传是历史上下文，不能把未来实测天气用于回测。

panel.parquet中保存的holiday/event_intensity列用于数据溯源；当前模型历史分支未直接使用这两列，未来日历/事件按世界公告和显式forecast scenario重算。真实活动若只放在历史context列，不会自动成为未来已知活动，请同步导入world.events公告计划。

## 调用样例

先创建2×2示例世界，把实际返回ID写入WORLD_ID，然后：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/datasets/import \
  -F "world_id=$WORLD_ID" \
  -F 'options=<examples/import-options.json' \
  -F 'orders=@examples/orders.csv;type=text/csv' \
  -F 'context=@examples/context.csv;type=text/csv'
```

若配置key，加 `-H "X-API-Key: $RIDECAST_API_KEY"`。上传后检查任务终态；成功时 `GET /api/v1/datasets/{id}` 的metadata.quality和 `GET /api/v1/datasets/{id}/overview` 的quality给出input_rows、accepted_rows、duplicate_rows、conflicting_id_rows、rejected_rows、invalid_examples、补零、上下文默认、取消及warnings等信息。样例只有少量订单，旨在验证导入契约，不能当作足以训练的真实数据。

保存时会重新验证完整面板、每桶每区域聚合与订单一致、需求=完成+取消、乘客总数、时间连续性、未知区域、订单ID唯一性，并生成文件SHA256。大数据应分段规范化后接入，当前单次HTTP每文件32MiB、最多500,000订单/150,000面板行。
