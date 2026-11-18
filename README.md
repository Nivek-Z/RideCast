# RideCast

RideCast 使用 Chicago TNP 汇总数据估计累计行程的区域份额，并对比校准生成训练、真实数据直接训练和统计基线。

- **空间验证**展示历史对照、空间划分和实验流程。从老师的原始 ZIP 开始，一键重新校准、生成、训练、评分。
- **参数对比**支持单组调参和批量运行。按开发区的 8 折空间交叉验证排名，并排查看得分、折间和种子间波动、逐区误差及用时。
- **研究记录**记录数据用途、四轮研究结果和规则稳定性检查。

历史 431 区对照中，未经校准的路线 TV 为 51.34%，就业密度基线为 38.75%，真实数据直接训练为 26.27%，校准后生成训练为 25.89%。TV 越低，估计份额越接近观测份额。与真实训练仅相差 0.38 个百分点，现有证据不足以证明额外收益。数据没有时间戳，且空间留出区此前已被查看。它支持探索性空间评价，验证逐时预测和跨城市泛化需要新数据。

## 本地启动

在仓库根目录运行。需要 Python 3.11 或 3.12 和 Node.js。

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[research]'
npm --prefix web ci
npm --prefix web run build
ridecast setup-study --archive '/absolute/path/网约车选题 数据.zip'
ridecast present
```

打开 [本地研究工作台](http://127.0.0.1:8000)。`present` 启动 HTTP 服务和独立实验进程。老师数据、运行结果和模型细节都保存在 Git 忽略的 `var/` 下。首次成功复现后，首页可加载真实分区与逐区结果地图。前端的历史空间汇总可独立读取。

## 验证

```bash
pip install -e '.[research,ml,dev]'
ruff check .
pytest -q
pip check
npm --prefix web test
python scripts/verify_http.py
```

HTTP 验证需要已启动的本地工作台及配置好的老师数据，会提交一次完整的空间复现和两组参数配置。运行说明、接口和建模协议见下方。

- [本地运行与数据存放](docs/HANDOFF.md)
- [接口与字段说明](docs/CONTRACT.md)
- [建模、生成和留出区协议](docs/MODELING.md)
- [前端使用说明](web/README.md)
- [校准与稳定性报告](reports/calibrated-world.md)

早期时序实验的生成器、训练代码和报告仍可用于复核历史工作，入口见 [交接文档](docs/HANDOFF.md)。
