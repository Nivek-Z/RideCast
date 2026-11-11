import { ArrowRight, Check, CloudRain, PartyPopper, Thermometer, Wind } from 'lucide-react';
import { useMemo, useState } from 'react';
import { api, type Any } from '../api';
import { CityMap, TipRows } from '../components/CityMap';
import { BarChart, LineChart, Ring } from '../components/charts';
import { Badge, Card, Empty, ErrorNote, KV, Loading, Note, Seg, Stat } from '../components/ui';
import { compact, fix1, int, KIND_LABEL, ratio, signed, time, WEEKDAYS } from '../format';
import { useApp, useQuery } from '../store';

export function Onboarding() {
  const app = useApp();
  const steps = [
    { title: '创建虚拟城市', desc: '种子决定街区、用地、人口与岗位分布。', done: app.worlds.length > 0, action: () => app.openDialog('world'), label: '新建城市' },
    { title: '生成或导入数据', desc: '把一段演化物化为订单数据集，或上传真实订单 CSV。', done: app.datasets.length > 0, action: () => app.openDialog('simulate'), label: '生成数据集' },
    { title: '训练预测模型', desc: 'GRU 多步预测，按时间顺序留出验证与测试。', done: app.models.length > 0, action: () => app.openDialog('train'), label: '发起训练' },
    { title: '预测与调配建议', desc: '查看未来需求、区间与启发式司机调配。', done: false, action: () => app.go('forecast'), label: '去预测' },
  ];
  const current = steps.findIndex((s) => !s.done);
  return (
    <div className="steps">
      {steps.map((s, i) => (
        <div key={s.title} className={`card step ${s.done ? 'done' : ''}`}>
          <span className="n">{s.done ? <Check size={13} /> : i + 1}</span>
          <div style={{ fontWeight: 600 }}>{s.title}</div>
          <div className="small dim" style={{ flex: 1 }}>
            {s.desc}
          </div>
          <div>
            <button className={`btn sm ${i === current ? 'primary' : ''}`} onClick={s.action} disabled={i > current && i > 0 && !steps[i - 1].done}>
              {s.label}
              <ArrowRight size={13} />
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

export function DashboardPage() {
  const app = useApp();
  const { dataset, world, tz } = app;
  const [range, setRange] = useState<'2d' | '7d' | 'all'>('7d');
  const [zone, setZone] = useState('');

  const overview = useQuery(dataset ? `overview:${dataset.id}` : null, () => api.overview(dataset!.id));
  const map = useQuery(world ? `map:${world.id}` : null, () => api.worldMap(world!.id));
  const heat = useQuery(dataset ? `heatmap:${dataset.id}:latest` : null, () => api.heatmap(dataset!.id));
  const ov: Any = overview.data?.dataset_id === dataset?.id ? overview.data : undefined;

  const window = useMemo(() => {
    if (!ov) return null;
    const end = new Date(ov.range.end).getTime();
    const start = range === 'all' ? new Date(ov.range.start).getTime() : Math.max(new Date(ov.range.start).getTime(), end - (range === '2d' ? 2 : 7) * 86400000);
    return { start: new Date(start).toISOString(), end: new Date(end).toISOString() };
  }, [ov, range]);
  const series = useQuery(
    dataset && window ? `ts:${dataset.id}:${zone}:${window.start}:${window.end}` : null,
    () => api.timeseries(dataset!.id, { zone_id: zone || undefined, start: window!.start, end: window!.end, limit: 10000 }),
  );

  if (!app.loaded) return <div className="page"><Loading height={300} /></div>;
  if (!dataset) {
    return (
      <div className="page">
        <Card>
          <Empty title={world ? `「${world.name}」还没有数据集` : '欢迎使用 RideCast'}>
            RideCast 在种子确定的虚拟城市里演化网约车供需，并训练时空需求预测模型。按下面四步走通整条链路。
          </Empty>
        </Card>
        <Onboarding />
      </div>
    );
  }
  if (overview.error && !ov) return <div className="page"><ErrorNote error={overview.error} /></div>;
  if (!ov) return <div className="page"><Loading height={300} /></div>;

  const k = ov.kpis;
  const rows: Any[] = series.data?.rows ?? [];
  const supplyKnown = ov.supply_observed;
  const outcomes = ov.outcomes_observed;
  const maxZone = Math.max(1, ...ov.zones.map((z: Any) => z.demand));
  const heatRows: Record<string, Any> = Object.fromEntries((heat.data?.rows ?? []).map((r: Any) => [r.zone_id, r]));
  const days = (new Date(ov.range.end).getTime() - new Date(ov.range.start).getTime()) / 86400000;
  const xf = (x: number) => (range === '2d' ? time.hm(x, tz) : time.md(x, tz));

  return (
    <div className="page">
      <div className="row" style={{ flexWrap: 'wrap' }}>
        <Badge>{ov.source === 'synthetic' ? '合成数据' : '导入数据'}</Badge>
        <Badge>{ov.target_semantics === 'requests' ? '口径 · 发起订单' : '口径 · 完成行程'}</Badge>
        <span className="dim small">
          {time.full(ov.range.start, tz)} — {time.full(ov.range.end, tz)}（不含） · {fix1(days)} 天 · {ov.range.zone_count} 区 · {ov.bucket_minutes} 分钟桶 · {tz}
        </span>
      </div>

      <div className="card">
        <div className="stat-strip">
          <Stat k="需求订单" v={compact(k.demand)} d={`乘客 ${compact(k.passengers)} 人`} />
          <Stat k="完成率" v={ratio(k.completion_rate)} d={outcomes ? `完成 ${compact(k.completed)} 单` : '该口径无法观察取消'} />
          <Stat k="取消率" v={ratio(k.cancellation_rate, 2)} d={outcomes ? `取消 ${int(k.cancelled)} 单` : '未知，不按 0 绘制'} />
          <Stat k="峰值桶需求" v={int(k.peak_bucket_demand)} unit="单/桶" d={`均值 ${fix1(k.mean_bucket_demand)} 单/桶`} />
          <Stat k="最新可用司机" v={supplyKnown ? int(k.latest_available_drivers) : '未知'} d={supplyKnown ? `桶均 ${fix1(k.mean_available_drivers_per_bucket)} · 快照非去重人数` : '导入数据未提供供给'} />
        </div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 1.7fr) minmax(280px, 1fr)' }}>
        <Card
          title="需求曲线"
          sub={zone ? app.zoneName(zone) : '全城'}
          actions={
            <>
              <select className="select" style={{ width: 130, height: 28 }} value={zone} onChange={(e) => setZone(e.target.value)}>
                <option value="">全城</option>
                {ov.zones.map((z: Any) => (
                  <option key={z.zone_id} value={z.zone_id}>
                    {z.name}
                  </option>
                ))}
              </select>
              <Seg value={range} onChange={setRange} options={[{ value: '2d', label: '48 小时' }, { value: '7d', label: '7 天' }, { value: 'all', label: '全部' }]} />
            </>
          }
        >
          {series.error ? (
            <ErrorNote error={series.error} />
          ) : (
            <LineChart
              height={250}
              xFormat={xf}
              tipFormat={(x) => `${time.mdhm(x, tz)} ${time.weekday(x, tz)}`}
              valueFormat={(v) => int(v)}
              series={[
                { name: '需求', color: 'var(--accent)', area: true, points: rows.map((r) => ({ x: Date.parse(r.timestamp), y: r.demand })) },
                ...(supplyKnown ? [{ name: '可用司机', color: 'var(--text-3)', width: 1.2, points: rows.map((r) => ({ x: Date.parse(r.timestamp), y: r.available_drivers })) }] : []),
                ...(outcomes ? [{ name: '取消', color: 'var(--red)', width: 1.2, points: rows.map((r) => ({ x: Date.parse(r.timestamp), y: r.cancelled })) }] : []),
              ]}
            />
          )}
          {series.data?.truncated && <div className="small muted">仅显示最早 {rows.length} 个桶，共 {series.data.total_count} 个。</div>}
        </Card>

        <Card
          title="最新桶热力"
          sub={heat.data ? time.mdhm(heat.data.timestamp, tz) : ''}
          actions={
            <button className="btn ghost sm" onClick={() => app.go('map')}>
              城市实况 <ArrowRight size={13} />
            </button>
          }
          flush
        >
          <div style={{ height: 262, padding: '0 8px 8px' }}>
            <div style={{ height: '100%', borderRadius: 6, overflow: 'hidden' }}>
              <CityMap
                map={map.data}
                compact
                values={Object.fromEntries(Object.values(heatRows).map((r) => [r.zone_id, r.demand]))}
                tooltip={(id) => {
                  const r = heatRows[id];
                  return r ? (
                    <TipRows
                      title={r.name}
                      sub={KIND_LABEL[r.kind] ?? r.kind}
                      rows={[['需求', int(r.demand)], ['可用司机', r.supply_observed ? int(r.available_drivers) : '未知'], ['取消', r.unserved_requests === null ? '未知' : int(r.unserved_requests)]]}
                    />
                  ) : null;
                }}
              />
            </div>
          </div>
        </Card>
      </div>

      <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))' }}>
        <Card title="小时分布" sub="每桶全城平均需求">
          <BarChart
            name="平均需求"
            labelEvery={3}
            data={ov.distributions.hour.map((h: Any) => ({ label: String(h.hour).padStart(2, '0'), value: h.mean_city_demand_per_bucket, tip: `${String(h.hour).padStart(2, '0')}:00 · ${h.bucket_count} 个桶` }))}
          />
        </Card>
        <Card title="周内分布" sub="每桶全城平均需求">
          <BarChart
            name="平均需求"
            data={ov.distributions.weekday.map((w: Any) => ({ label: WEEKDAYS[w.weekday], value: w.mean_city_demand_per_bucket, tip: `${WEEKDAYS[w.weekday]} · ${w.bucket_count} 个桶` }))}
          />
        </Card>
        <Card title="最新桶环境" sub={time.mdhm(ov.weather.timestamp, tz)}>
          <div className="row" style={{ gap: 16, alignItems: 'flex-start' }}>
            <Ring value={outcomes ? k.completion_rate : null} color="var(--accent)" label={outcomes ? `${(k.completion_rate * 100).toFixed(0)}%` : '—'} size={76} />
            <div className="grow">
              <KV
                items={[
                  [<span className="row"><Thermometer size={13} />气温</span>, `${fix1(ov.weather.temperature)} °C`],
                  [<span className="row"><CloudRain size={13} />降水</span>, `${fix1(ov.weather.precipitation)} mm/h · ${ov.weather.raining_zones} 区有雨`],
                  [<span className="row"><Wind size={13} />风速</span>, `${fix1(ov.weather.wind_speed)} m/s`],
                  ['路况指数', fix1(ov.weather.traffic_index)],
                  [<span className="row"><PartyPopper size={13} />活动</span>, `${ov.weather.event_zones} 区`],
                  ['节假日', ov.weather.holiday ? '是' : '否'],
                ]}
              />
            </div>
          </div>
          <div className="small muted" style={{ marginTop: 8 }}>环形为全期完成率；天气为各区简单均值。</div>
        </Card>
      </div>

      <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 1.7fr) minmax(280px, 1fr)' }}>
        <Card title="区域" sub={`${ov.zones.length} 个`} flush>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>区域</th>
                  <th>用地</th>
                  <th className="num">累计需求</th>
                  <th className="num">桶均</th>
                  <th className="num">峰值</th>
                  <th className="num">取消率</th>
                  <th className="num">最新需求</th>
                  <th className="num">最新司机</th>
                  <th className="num" title="需求数 − 司机数的原始计数差，不等于未满足请求数">计数差</th>
                </tr>
              </thead>
              <tbody>
                {[...ov.zones].sort((a: Any, b: Any) => b.demand - a.demand).map((z: Any) => (
                  <tr key={z.zone_id} className="click" onClick={() => setZone(z.zone_id === zone ? '' : z.zone_id)}>
                    <td>
{z.name}
                    </td>
                    <td className="dim">{KIND_LABEL[z.kind] ?? z.kind}</td>
                    <td className="num">
                      <div className="bar-cell">
                        {int(z.demand)}
                        <span className="bar-track">
                          <i style={{ width: `${(z.demand / maxZone) * 100}%`, height: 4, display: 'block', background: 'var(--bar)', borderRadius: 2 }} />
                        </span>
                      </div>
                    </td>
                    <td className="num">{fix1(z.mean_demand_per_bucket)}</td>
                    <td className="num">{int(z.peak_demand_per_bucket)}</td>
                    <td className="num">{outcomes ? ratio(z.demand ? z.cancelled / z.demand : null, 2) : '未知'}</td>
                    <td className="num">{int(z.latest_demand)}</td>
                    <td className="num">{supplyKnown ? int(z.latest_available_drivers) : '未知'}</td>
                    <td className="num" style={{ color: z.latest_supply_gap > 0 ? 'var(--red)' : undefined }}>{signed(z.latest_supply_gap)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <Card title="数据质量">
          <div className="col" style={{ gap: 12 }}>
            <KV
              items={[
                ['输入行', int(ov.quality.input_rows)],
                ['接受行', int(ov.quality.accepted_rows)],
                ['拒绝 / 重复', `${int(ov.quality.rejected_rows)} / ${int(ov.quality.duplicate_rows)}`],
                ['零需求桶', `${int(ov.quality.zero_demand_buckets)} / ${int(ov.range.panel_rows)}`],
                ['面板缺失值', int(ov.quality.missing_panel_values)],
                ['覆盖声明', ov.quality.coverage_attested ? '已确认' : '未确认'],
              ]}
            />
            {(ov.quality.warnings ?? []).map((w: string) => (
              <Note key={w} tone="warn">
                {w}
              </Note>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}
