# 实际验收记录

日期：2026-11-11（Asia/Shanghai）。RideCast 0.1.0。环境：macOS arm64、Python 3.12、TensorFlow 2.19.1、Keras 3.11.3、NumPy 2.1.3、Pandas 2.3.2。

## 验证结果

| 检查 | 实际结果 |
| --- | --- |
| `pytest -q` | **90 passed**，7.01秒；含真实TensorFlow候选训练、保存重载、新进程预测 |
| `ruff check .` | 通过 |
| `python -m pip check` | No broken requirements found |
| `python -m compileall -q src scripts` | 通过 |
| `git diff --check` | 通过 |
| `python scripts/verify_http.py` | **68/68通过**，5.808秒，真实TCP HTTP，未使用TestClient |
| 三随机世界复现实验 | 3/3：同种子地图一致，末日重叠面板与订单逐值一致 |
| Docker/GitHub Actions | 提供配置；本机Docker daemon未运行，未列为已验证 |

测试含28项仿真/导入、35项特征/信息边界，其余覆盖仪表盘、队列、HTTP、基准与ML。19条warnings来自Starlette/AnyIO和Keras/NumPy上游弃用提示，没有失败测试。沙箱下CPU缓存探测受限提示不影响本次训练；TensorFlow 2.20/PyArrow原生冲突已通过锁定兼容版本解决。

## 完整训练演示

```bash
ridecast demo --days 45 --epochs 12 --tune --rows 3 --cols 3 --seed 42 --output reports/demo.json
```

版本seeded-city-v2 / absolute-time-v2，9区域、45天、9,720面板行、100,550请求（97,524完成，3,026取消）、146,998乘客。观测区间：2026-01-01 00:00至2026-02-15 00:00，Asia/Shanghai，右端不包含。

| 类型 | 实际标识 |
| --- | --- |
| world | `w_85be8c7d5740fade` |
| dataset | `d_31f4cb3b7651420a` |
| model | `m_e24df79b24c14311` |

过去24桶预测未来3桶；两个候选各12轮，验证MAE为3.0768/3.1762，选择候选0。训练阶段约10.56秒（不含订单生成/聚合）。测试4,320个区域×预测起点×步长单元：

| 方法 | MAE（订单） | RMSE（订单） | WAPE |
| --- | ---: | ---: | ---: |
| 选中GRU | 3.1886 | 4.5194 | 41.01% |
| 验证集选定主基线：前周同期 | 4.8366 | 7.0463 | 62.21% |
| 保留基线：前日同期 | 3.4801 | 5.1958 | 44.76% |
| 保留基线：最近值 | 4.4741 | 6.3231 | 57.55% |

验证残差经验区间测试覆盖89.05%，平均宽度12.39单。主基线在验证集最佳，但在测试集不如前日基线，反映时段变化；不能只报告相对主基线34.07%改进而隐藏其他基线。该演示不能证明真实城市精度或满意度提升。完整记录见 [demo.json](demo.json)。

## 随机世界重复建模

三种子7/42/99、各28天、3×3地图、同配置小GRU最多5轮，分别独立重训。模型MAE=2.9125/4.1891/3.3191，对应基线2.6842/3.9360/3.3591；模型均值3.4736，基线均值3.3265，仅1/3世界优于基线。未根据测试分数追选种子或反复调参。这是不同虚拟世界下重复建模稳定性实验，不是零样本跨城市迁移。

详细结果、确定性检查、生成器版本、源码摘要和最终特征重算一致性见 [world-benchmark.md](world-benchmark.md) / [JSON](world-benchmark.json)。较早生成器的产物单独保留于var目录，未混入最终比较。

## HTTP 与前端交接验收

真实连接本机Uvicorn，与独立worker通过SQLite队列协作。68项检查涵盖地图/随机时刻状态、仪表盘读接口、模型/评估、27行三步预测、默认/显式整数供给调配、非法事件422、终态SSE、学习曲线；新小世界真正排队模拟→训练→预测；GeoJSON及multipart CSV经worker导入。小模型仅验证接口，不用于效果声明。只激活小测试模型，完整演示模型未自动激活。

记录见 [http-smoke.json](http-smoke.json)，前端可参考 [api-examples.json](api-examples.json) 的真实响应。资源发布、事务认领、取消清理、失联worker隔离及确定世界并发创建有回归覆盖。

本机入口：[API文档](http://127.0.0.1:8000/docs)，健康检查 `/health`；停止后使用 `ridecast serve` 和第二终端 `ridecast worker` 重启。报告ID对应当前工作区var数据；新环境运行demo会产生新dataset/model ID。

## 边界

没有真实城市数据训练/在线数据源接入、高并发/亿级数据压测。宏观供给、合成公告活动和直线回放有明确假设；区间不是覆盖保证；调配未执行，未计道路路由及服务时长约束。CSV与GeoJSON导入可接真实数据，运营效果需另做真实时序留出与业务验证。
