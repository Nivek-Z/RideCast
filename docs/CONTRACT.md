# RideCast 研究接口

研究接口由本机 HTTP 服务提供，OpenAPI 位于 `/openapi.json` 和仓库的 `docs/openapi.json`。当前前端与 API 默认同源。

| 方法与路径 | 用途 |
| --- | --- |
| `GET /api/research/status` | 本地数据配置、实验进程状态 |
| `POST /api/research/reproduce` | 以固定的历史配置排队完整复现，返回 202 和任务 |
| `POST /api/research/arena` | 一次提交 1–12 个参数组合，返回 202 和任务列表 |
| `GET /api/research/runs` | 按时间倒序读取每次运行和结果摘要，支持 `limit`、`offset` |
| `GET /api/research/runs/{id}` | 单次运行状态与结果摘要 |
| `GET /api/research/runs/{id}/result` | 已发布的空间地图和诊断记录，未发布返回 409 |
| `DELETE /api/research/runs/{id}` | 停止排队或运行中的任务 |

单组竞技场请求体如下。批量将同一批种子的多个配置放在数组中。世界数取 8、16、32、64，校准正则取 0.1、0.3、1、3，种子为 1–5 个互异的非负整数。列表返回 `summary: null` 直到结果成功发布。

```json
{
  "configurations": [{"worlds_per_seed": 16, "teacher_alpha": 1.0, "seeds": [7, 42, 99]}]
}
```

任务的 `state` 为 `queued`、`running`、`cancelling`、`succeeded`、`failed` 或 `cancelled`。用 `progress`（0–1）、`stage` 和 `message` 展示实际工作进度。成功后 `summary` 包含协议版本、数据内容校验值、配置、分区数、计算耗时和阶段记录。`metrics` 中的 TV 以百分数表示，波动使用百分点。

空间结果地图的 `ids`、`paths`、`development`、`blocks` 按同一个 795 区顺序。路径是已简化和缩放的 SVG 坐标，视框为 `0 0 440 500`。 `regions` 给出本次评分对应的区域索引，`actual`、`predicted`、`teacher` 按该列表排列，各占比数组总和为 100。正式复现为 431 区留出；竞技场汇总 364 区的区外预测。竞技场排名使用逐折归一化后形成的平均 TV。各折预测汇总后再次归一化供地图展示。

历史时序任务的数据和模型契约保留在 Git 的 `c93f122:docs/CONTRACT.md`。旧版 `target_semantics`、时间切窗和种子复现约定继续适用于历史实验。
