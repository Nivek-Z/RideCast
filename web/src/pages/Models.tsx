import { ArrowRight, CheckCircle2 } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { api, type Any, type Resource } from '../api';
import { GroupedBars, LineChart } from '../components/charts';
import { LearningCurves } from '../components/LearningCurves';
import { Badge, Card, Empty, ErrorNote, KV, Loading, Note, Seg, Stat } from '../components/ui';
import { ago, duration, fix2, int, pct, ratio, time } from '../format';
import { useApp, useQuery } from '../store';

type Tab = 'backtest' | 'curves' | 'metrics' | 'config';
const BASELINES: Record<string, string> = { last_value: '上一桶', previous_day: '昨日同刻', previous_week: '上周同刻' };

function Improvement({ value }: { value: number | null | undefined }) {
  if (value === null || value === undefined) return <Badge>—</Badge>;
  return <Badge tone={value > 0 ? 'green' : 'red'}>{value > 0 ? '优于基线 ' : '不及基线 '}{pct(Math.abs(value))}</Badge>;
}

export function ModelsPage() {
  const app = useApp();
  const { world, worldModels } = app;
  const [tab, setTab] = useState<Tab>('backtest');
  const active = useQuery(world ? `active:${world.id}` : null, () => api.activeModel(world!.id), { cache: false });
  const model = worldModels.find((m) => m.id === app.routeArg) ?? worldModels[0];

  if (!app.loaded) return <div className="page"><Loading height={300} /></div>;
  if (!model) {
    return (
      <div className="page">
        <Card>
          <Empty title={world ? `「${world.name}」还没有模型` : '还没有模型'} action={<button className="btn primary" onClick={() => app.openDialog('train')}>发起训练</button>}>
            训练会在独立 worker 中进行，完成后在这里比较模型与基线、查看学习曲线和回测叠图。
          </Empty>
        </Card>
      </div>
    );
  }

  return (
    <div className="page">
      <div className="grid" style={{ gridTemplateColumns: '300px minmax(0, 1fr)', alignItems: 'start' }}>
        <Card title="模型版本" sub={`${worldModels.length} 个`} flush>
          {worldModels.map((m) => {
            const mt = m.metadata.metrics;
            return (
              <div key={m.id} className={`list-item ${m.id === model.id ? 'sel' : ''}`} onClick={() => app.go('models', m.id)}>
                <div className="grow col" style={{ gap: 2 }}>
                  <div className="row">
                    <span className="mono">{m.id.slice(0, 10)}</span>
                    {active.data?.model_id === m.id && <Badge tone="blue">已启用</Badge>}
                  </div>
                  <div className="small muted">
                    MAE {fix2(mt?.model?.mae)} · 基线 {fix2(mt?.baseline?.mae)} · {ago(m.created_at)}
                  </div>
                </div>
                <span className="small" style={{ color: (mt?.model_mae_improvement_percent ?? 0) > 0 ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}>
                  {mt?.model_mae_improvement_percent > 0 ? '+' : ''}
                  {pct(mt?.model_mae_improvement_percent)}
                </span>
              </div>
            );
          })}
        </Card>

        <ModelDetail key={model.id} model={model} tab={tab} setTab={setTab} isActive={active.data?.model_id === model.id} onActivated={active.reload} />
      </div>
    </div>
  );
}

function ModelDetail(props: { model: Resource; tab: Tab; setTab: (t: Tab) => void; isActive: boolean; onActivated: () => void }) {
  const app = useApp();
  const { model, tab } = props;
  const md = model.metadata;
  const mt = md.metrics;
  const tz = md.timezone ?? app.tz;
  const [busy, setBusy] = useState(false);
  const activate = async () => {
    setBusy(true);
    try {
      await api.activate(model.id);
      props.onActivated();
      app.toast('已设为该城市的启用模型（预测仍需显式选择模型）', 'ok');
    } catch (e) {
      app.toast((e as Error).message, 'err');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="col" style={{ gap: 12 }}>
      <div className="card">
        <div className="row" style={{ padding: '12px 14px 0', flexWrap: 'wrap' }}>
          <h3 className="mono" style={{ fontSize: 14 }}>{model.id}</h3>
          <Badge>{md.architecture ?? 'GRU'}</Badge>
          <Badge>{md.source === 'synthetic' ? '合成数据训练' : '导入数据训练'}</Badge>
          <Improvement value={mt?.model_mae_improvement_percent} />
          <span className="grow" />
          <button className="btn" onClick={activate} disabled={busy || props.isActive}>
            <CheckCircle2 size={14} />
            {props.isActive ? '已启用' : '设为启用'}
          </button>
          <button className="btn primary" onClick={() => app.go('forecast', model.id)}>
            用它预测 <ArrowRight size={14} />
          </button>
        </div>
        <div className="stat-strip" style={{ marginTop: 4 }}>
          <Stat k="测试 MAE" v={fix2(mt?.model?.mae)} unit="单" d={`基线（${BASELINES[mt?.baseline?.name] ?? mt?.baseline?.name}）${fix2(mt?.baseline?.mae)}`} />
          <Stat k="RMSE" v={fix2(mt?.model?.rmse)} unit="单" d={`基线 ${fix2(mt?.baseline?.rmse)}`} />
          <Stat k="WAPE" v={pct(mt?.model?.wape)} d={`基线 ${pct(mt?.baseline?.wape)}`} />
          <Stat k="sMAPE" v={pct(mt?.model?.smape)} d={`基线 ${pct(mt?.baseline?.smape)}`} />
          <Stat k="区间测试覆盖" v={ratio(mt?.interval?.test_coverage)} d={`名义 ${ratio(mt?.interval?.nominal_marginal_coverage, 0)} · 经验区间，不作保证`} />
        </div>
      </div>

      <Card
        title={<Seg value={tab} onChange={props.setTab} options={[{ value: 'backtest', label: '回测叠图' }, { value: 'curves', label: '学习曲线' }, { value: 'metrics', label: '指标对比' }, { value: 'config', label: '配置与划分' }]} />}
      >
        {tab === 'backtest' && <Backtest model={model} tz={tz} />}
        {tab === 'curves' && <Curves modelId={model.id} />}
        {tab === 'metrics' && <Metrics model={model} />}
        {tab === 'config' && (
          <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 20 }}>
            <div className="col">
              <div className="label">选中配置</div>
              <KV
                items={[
                  ['回看 / 预测步长', `${md.config.lookback} / ${md.config.horizon} 桶`],
                  ['GRU 单元', md.config.units],
                  ['学习率', md.config.learning_rate],
                  ['批大小', md.config.batch_size],
                  ['轮数上限', md.config.epochs],
                  ['随机种子', md.config.seed],
                  ['候选数 / 选中', `${md.candidates?.length ?? 1} / #${md.selected_candidate ?? 0}`],
                  ['训练耗时', duration(md.duration_seconds)],
                  ['特征版本', md.feature_schema?.version],
                  ['依赖', Object.entries(md.dependencies ?? {}).map(([k, v]) => `${k} ${v}`).join(' · ')],
                ]}
              />
            </div>
            <div className="col">
              <div className="label">时间划分（按时间顺序，测试集仅用于最终评估）</div>
              <table className="table" style={{ margin: '0 -14px', width: 'calc(100% + 28px)' }}>
                <thead>
                  <tr>
                    <th>分区</th>
                    <th>目标起</th>
                    <th>目标止（不含）</th>
                    <th className="num">起点数</th>
                  </tr>
                </thead>
                <tbody>
                  {(['train', 'validation', 'test'] as const).map((key) => (
                    <tr key={key}>
                      <td>{{ train: '训练', validation: '验证', test: '测试' }[key]}</td>
                      <td>{time.mdhm(md.split?.[key]?.target_start, tz)}</td>
                      <td>{time.mdhm(md.split?.[key]?.target_end_exclusive, tz)}</td>
                      <td className="num">{int(md.split?.[key]?.forecast_origin_count)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {(md.limitations ?? []).length > 0 && (
                <Note tone="warn">
                  {(md.limitations as string[]).map((l) => (
                    <div key={l}>{l}</div>
                  ))}
                </Note>
              )}
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}

function Curves({ modelId }: { modelId: string }) {
  const history = useQuery(`history:${modelId}`, () => api.modelHistory(modelId));
  if (history.error) return <ErrorNote error={history.error} />;
  if (!history.data) return <Loading height={220} />;
  return <LearningCurves history={history.data} height={260} />;
}

function Backtest({ model, tz }: { model: Resource; tz: string }) {
  const app = useApp();
  const horizonMax: number = model.metadata.config.horizon;
  const zoneIds: string[] = model.metadata.feature_schema?.zone_ids ?? [];
  const [zone, setZone] = useState('');
  const [horizon, setHorizon] = useState(1);
  useEffect(() => setHorizon((h) => Math.min(h, horizonMax)), [horizonMax]);

  // 回测明细不可变，按模型缓存；超过单页上限时继续翻页
  const evaluation = useQuery(`evaluation:${model.id}`, async () => {
    const first = await api.evaluation(model.id, 0, 10000);
    const rows: Any[] = first.rows;
    while (rows.length < first.total && rows.length < 60000) rows.push(...(await api.evaluation(model.id, rows.length, 10000)).rows);
    return { rows, total: first.total as number };
  });

  const chart = useMemo(() => {
    const rows: Any[] = evaluation.data?.rows ?? [];
    // 同一目标时刻有多个预测起点；固定 horizon 后每个时刻每区恰一行
    const picked = rows.filter((r) => r.horizon === horizon && (!zone || r.zone_id === zone));
    const byTime = new Map<number, { actual: number; predicted: number; baseline: number; lo: number; hi: number }>();
    for (const r of picked) {
      const t = Date.parse(r.timestamp);
      const cur = byTime.get(t) ?? { actual: 0, predicted: 0, baseline: 0, lo: 0, hi: 0 };
      cur.actual += r.actual_demand;
      cur.predicted += r.predicted_demand;
      cur.baseline += r.baseline_demand;
      cur.lo += r.lower;
      cur.hi += r.upper;
      byTime.set(t, cur);
    }
    return [...byTime.entries()].sort((a, b) => a[0] - b[0]);
  }, [evaluation.data, zone, horizon]);

  if (evaluation.error) return <ErrorNote error={evaluation.error} />;
  if (!evaluation.data) return <Loading height={280} />;
  return (
    <div className="col">
      <div className="row" style={{ flexWrap: 'wrap' }}>
        <select className="select" style={{ width: 150 }} value={zone} onChange={(e) => setZone(e.target.value)}>
          <option value="">全城（各区求和）</option>
          {zoneIds.map((id) => (
            <option key={id} value={id}>
              {app.zoneName(id)}
            </option>
          ))}
        </select>
        <span className="label">预测步</span>
        <Seg value={horizon} onChange={setHorizon} options={Array.from({ length: Math.min(horizonMax, 12) }, (_, i) => ({ value: i + 1, label: `+${i + 1}` }))} />
        <span className="grow" />
        <span className="small muted">测试集 {int(evaluation.data.total)} 行 · 提前 {horizon} 桶预测</span>
      </div>
      <LineChart
        height={300}
        xFormat={(x) => time.md(x, tz)}
        tipFormat={(x) => `${time.mdhm(x, tz)} ${time.weekday(x, tz)}`}
        valueFormat={(v) => v.toFixed(1)}
        band={zone ? { name: '经验区间', color: 'var(--accent)', points: chart.map(([x, v]) => ({ x, lo: v.lo, hi: v.hi })) } : undefined}
        series={[
          { name: '真实需求', color: 'var(--text)', points: chart.map(([x, v]) => ({ x, y: v.actual })), width: 1.4 },
          { name: '模型预测', color: 'var(--accent)', points: chart.map(([x, v]) => ({ x, y: v.predicted })) },
          { name: '基线', color: 'var(--text-3)', dashed: true, points: chart.map(([x, v]) => ({ x, y: v.baseline })), width: 1.2 },
        ]}
      />
      <div className="small muted">
        {zone ? '区间为验证残差的经验分位带，不保证覆盖率。' : '全城曲线为各区同一时刻预测值求和；区间不可直接相加，选择单个区域查看。'}
      </div>
    </div>
  );
}

function Metrics({ model }: { model: Resource }) {
  const app = useApp();
  const mt = model.metadata.metrics;
  const rows: [string, Any][] = [['GRU 模型', mt.model], ...Object.entries(mt.baselines ?? {}).map(([k, v]) => [`基线 · ${BASELINES[k] ?? k}`, v] as [string, Any])];
  return (
    <div className="col" style={{ gap: 16 }}>
      <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 20 }}>
        <div>
          <div className="label" style={{ marginBottom: 6 }}>分预测步 MAE（单）</div>
          <GroupedBars
            names={['模型', '基线']}
            colors={['var(--accent)', 'var(--border-strong)']}
            groups={(mt.by_horizon ?? []).map((h: Any) => ({ label: `+${h.horizon} 桶`, values: [h.model.mae, h.baseline.mae] }))}
          />
        </div>
        <div>
          <div className="label" style={{ marginBottom: 6 }}>分区域 MAE（单）</div>
          <GroupedBars
            names={['模型', '基线']}
            colors={['var(--accent)', 'var(--border-strong)']}
            groups={(mt.by_zone ?? []).map((z: Any) => ({ label: app.zoneName(z.zone_id), values: [z.model.mae, z.baseline.mae] }))}
          />
        </div>
      </div>
      <div className="table-wrap" style={{ margin: '0 -14px' }}>
        <table className="table">
          <thead>
            <tr>
              <th>测试集</th>
              <th className="num">MAE（单）</th>
              <th className="num">RMSE（单）</th>
              <th className="num">WAPE</th>
              <th className="num">sMAPE</th>
              <th className="num">样本</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([name, m], i) => (
              <tr key={name} style={i === 0 ? { fontWeight: 600 } : undefined}>
                <td>{name}</td>
                <td className="num">{fix2(m.mae)}</td>
                <td className="num">{fix2(m.rmse)}</td>
                <td className="num">{pct(m.wape)}</td>
                <td className="num">{pct(m.smape)}</td>
                <td className="num">{int(m.sample_count)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="small muted">
        验证集用于挑选超参与估计区间，测试集只做最终评估。WAPE 可超过 100%；真实总需求为零时为空。合成数据上的表现不代表真实业务精度。
      </div>
    </div>
  );
}
