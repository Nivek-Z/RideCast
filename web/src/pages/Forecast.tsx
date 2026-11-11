import { ArrowRight, Loader2, Play, Route as RouteIcon } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { api, type Any } from '../api';
import { CityMap, TipRows, zoneCenters, type MapArc } from '../components/CityMap';
import { LineChart } from '../components/charts';
import { Badge, Card, Empty, ErrorNote, Field, Loading, Note, NumberInput, Seg, Stat } from '../components/ui';
import { fix1, fix2, int, KIND_LABEL, ratio, time } from '../format';
import { useApp, useQuery } from '../store';

const SCENARIO = { demand_multiplier: 1, rain_multiplier: 1, traffic_multiplier: 1, supply_multiplier: 1 };

export function ForecastPage() {
  const app = useApp();
  const { world, worldModels, worldDatasets, tz } = app;
  const active = useQuery(world ? `active:${world.id}` : null, () => api.activeModel(world!.id), { cache: false });

  const [modelId, setModelId] = useState('');
  const [datasetId, setDatasetId] = useState('');
  const [horizon, setHorizon] = useState(0);
  const [scenario, setScenario] = useState(SCENARIO);
  const [committed, setCommitted] = useState<Any>(null);
  const [step, setStep] = useState(1);
  const [zone, setZone] = useState<string | null>(null);

  const model = worldModels.find((m) => m.id === modelId);
  // 进入页面或切换城市时选默认模型：路由指定 > 已启用 > 最新
  useEffect(() => {
    if (model || !worldModels.length || active.loading) return;
    const pick = worldModels.find((m) => m.id === app.routeArg) ?? worldModels.find((m) => m.id === active.data?.model_id) ?? worldModels[0];
    setModelId(pick.id);
  }, [model, worldModels, active.loading, active.data, app.routeArg]);
  useEffect(() => {
    if (app.routeArg && worldModels.some((m) => m.id === app.routeArg)) setModelId(app.routeArg);
  }, [app.routeArg]); // eslint-disable-line react-hooks/exhaustive-deps
  // 换模型时重置数据集与步长，并立即给出一次默认预测
  useEffect(() => {
    if (!model) return;
    const ds = worldDatasets.find((d) => d.id === model.metadata.dataset_id) ?? worldDatasets[0];
    const h = model.metadata.config.horizon;
    setDatasetId(ds?.id ?? '');
    setHorizon(h);
    setScenario(SCENARIO);
    setStep(1);
    setCommitted(ds ? { model_id: model.id, dataset_id: ds.id, horizon: h } : null);
  }, [model?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const forecast = useQuery(committed ? `forecast:${JSON.stringify(committed)}` : null, () => api.forecast(committed));
  const fc: Any = committed && forecast.data?.model_id === committed.model_id && forecast.data?.dataset_id === committed.dataset_id ? forecast.data : undefined;
  const map = useQuery(world ? `map:${world.id}` : null, () => api.worldMap(world!.id));
  const B = (fc?.bucket_minutes ?? app.bucketMinutes) * 60000;
  const origin = fc ? Date.parse(fc.forecast_origin) : 0;
  const history = useQuery(
    fc ? `ts:${fc.dataset_id}:${zone ?? ''}:before:${origin}` : null,
    () => api.timeseries(fc.dataset_id, { zone_id: zone ?? undefined, start: new Date(origin - 48 * B).toISOString(), end: fc.forecast_origin, limit: 500 }),
  );

  // 调配建议
  const [supplyMode, setSupplyMode] = useState<'snapshot' | 'custom'>('snapshot');
  const [supply, setSupply] = useState<Record<string, number>>({});
  const [dispatch, setDispatch] = useState<{ data?: Any; error?: unknown; busy?: boolean }>({});
  useEffect(() => setDispatch({}), [committed]);
  const zoneIds: string[] = useMemo(() => (map.data?.zones?.features ?? []).map((f: Any) => f.properties.zone_id), [map.data]);
  const runDispatch = async () => {
    if (!fc) return;
    setDispatch({ busy: true, data: dispatch.data });
    try {
      const body: Any = { model_id: fc.model_id, dataset_id: fc.dataset_id };
      if (supplyMode === 'custom') body.supply = Object.fromEntries(zoneIds.map((id) => [id, Math.max(0, Math.round(supply[id] ?? 0))]));
      const data = await api.dispatch(body);
      setDispatch({ data });
      // 首次拿到快照后预填自定义供给，便于在此基础上微调
      if (supplyMode === 'snapshot' && data.supply_known) setSupply(Object.fromEntries(data.zones.map((z: Any) => [z.zone_id, z.available_before])));
    } catch (error) {
      setDispatch({ error });
    }
  };

  const centers = useMemo(() => zoneCenters(map.data?.zones), [map.data]);
  const zoneProps = useMemo(() => Object.fromEntries((map.data?.zones?.features ?? []).map((f: Any) => [f.properties.zone_id, f.properties])), [map.data]);

  if (!app.loaded) return <div className="page"><Loading height={300} /></div>;
  if (!worldModels.length) {
    return (
      <div className="page">
        <Card>
          <Empty title="预测需要先完成一次训练" action={<button className="btn primary" onClick={() => app.openDialog('train')}>发起训练</button>}>
            模型只从所选数据集的观测末尾向前预测，不会把仿真真值当作预测展示。
          </Empty>
        </Card>
      </div>
    );
  }

  const steps: Any[] = fc?.totals ?? [];
  const stepRows: Record<string, Any> = Object.fromEntries((fc?.rows ?? []).filter((r: Any) => r.horizon === step).map((r: Any) => [r.zone_id, r]));
  const peak = Math.max(1, ...(fc?.rows ?? []).map((r: Any) => r.predicted_demand));
  const arcs: MapArc[] = (dispatch.data?.recommendations ?? []).map((r: Any) => ({
    key: `${r.from_zone_id}>${r.to_zone_id}`,
    from: centers[r.from_zone_id],
    to: centers[r.to_zone_id],
    weight: r.drivers,
    label: `${r.drivers}`,
  }));
  const maxHorizon = model?.metadata.config.horizon ?? 1;
  const dirty = committed && (committed.dataset_id !== datasetId || committed.horizon !== horizon || JSON.stringify(committed.scenario ?? {}) !== JSON.stringify(cleanScenario(scenario)));

  const histRows: Any[] = history.data?.rows ?? [];
  const futurePoints = zone
    ? (fc?.rows ?? []).filter((r: Any) => r.zone_id === zone).map((r: Any) => ({ x: Date.parse(r.timestamp), y: r.predicted_demand, lo: r.lower, hi: r.upper }))
    : steps.map((t) => ({ x: Date.parse(t.timestamp), y: t.predicted_demand, lo: 0, hi: 0 }));
  const lastHist = histRows[histRows.length - 1];
  const dd = dispatch.data;

  return (
    <div className="page">
      <Card>
        <div className="row" style={{ alignItems: 'flex-end', flexWrap: 'wrap', gap: 12 }}>
          <div style={{ width: 230 }}>
            <Field label="模型">
              <select className="select" value={modelId} onChange={(e) => setModelId(e.target.value)}>
                {worldModels.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.id.slice(0, 10)} · MAE {fix2(m.metadata.metrics?.model?.mae)}
                    {active.data?.model_id === m.id ? ' · 已启用' : ''}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <div style={{ width: 230 }}>
            <Field label="历史数据集（从其观测末尾向前预测）">
              <select className="select" value={datasetId} onChange={(e) => setDatasetId(e.target.value)}>
                {worldDatasets.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.id.slice(0, 10)} · 止于 {time.mdhm(d.metadata.observation_end, tz)}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <div style={{ width: 90 }}>
            <Field label={`步长 ≤ ${maxHorizon}`}>
              <NumberInput value={horizon} min={1} max={maxHorizon} onChange={(v) => setHorizon(Math.max(1, Math.min(maxHorizon, v || 1)))} />
            </Field>
          </div>
          {([['demand_multiplier', '需求倍数'], ['rain_multiplier', '降水倍数'], ['traffic_multiplier', '拥堵倍数'], ['supply_multiplier', '供给倍数']] as const).map(([key, label]) => (
            <div key={key} style={{ width: 84 }}>
              <Field label={label}>
                <NumberInput value={scenario[key]} min={0} max={5} step={0.1} onChange={(v) => setScenario({ ...scenario, [key]: v })} />
              </Field>
            </div>
          ))}
          <span className="grow" />
          <button
            className={`btn ${dirty ? 'primary' : ''}`}
            disabled={!datasetId || forecast.loading}
            onClick={() => {
              const sc = cleanScenario(scenario);
              setCommitted({ model_id: modelId, dataset_id: datasetId, horizon, ...(Object.keys(sc).length ? { scenario: sc } : {}) });
              forecast.reload();
            }}
          >
            {forecast.loading ? <Loader2 size={14} className="spin" /> : <Play size={14} />}
            运行预测
          </button>
        </div>
        {fc && Object.keys(fc.scenario ?? {}).length > 0 && (
          <div className="small muted" style={{ marginTop: 8 }}>情景覆盖是调用方给定的假设；需求倍数是对模型输出的显式缩放，不是估计出的因果效应。</div>
        )}
      </Card>

      {forecast.error && <ErrorNote error={forecast.error} />}
      {!fc && !forecast.error && <Card><Loading height={240} /></Card>}

      {fc && (
        <>
          <div className="card">
            <div className="stat-strip">
              <Stat k="预测起点" v={time.mdhm(fc.forecast_origin, tz)} d={`数据集观测末尾 · ${time.weekday(fc.forecast_origin, tz)}`} />
              {steps.map((t) => (
                <Stat key={t.horizon} k={`+${t.horizon} 桶 · ${time.hm(t.timestamp, tz)}`} v={fix1(t.predicted_demand)} unit="单" d="全城期望需求" />
              ))}
            </div>
          </div>

          <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 1.25fr) minmax(320px, 1fr)' }}>
            <Card
              title="预测热力"
              sub={stepRows[zoneIds[0]] ? `${time.mdhm(stepRows[zoneIds[0]].timestamp, tz)} 起的一个桶` : ''}
              actions={<Seg value={step} onChange={setStep} options={steps.map((t) => ({ value: t.horizon as number, label: `+${t.horizon}` }))} />}
              flush
            >
              <div style={{ height: 420, padding: '0 8px 8px' }}>
                <div style={{ height: '100%', borderRadius: 6, overflow: 'hidden' }}>
                  <CityMap
                    map={map.data}
                    values={Object.fromEntries(Object.entries(stepRows).map(([id, r]) => [id, r.predicted_demand]))}
                    domain={peak}
                    valueText={(id) => (stepRows[id] ? `${fix1(stepRows[id].predicted_demand)} 单` : undefined)}
                    legend={{ title: '预测需求（期望值）', lo: '0', hi: fix1(peak) }}
                    zoneTone={(id) => {
                      const z = dd?.supply_known ? dd.zones.find((x: Any) => x.zone_id === id) : null;
                      return z ? (z.shortage_after > 0 ? 'var(--red)' : z.available_after !== z.available_before ? 'var(--amber)' : 'var(--green)') : 'var(--accent)';
                    }}
                    selected={zone}
                    onSelect={setZone}
                    dispatch={arcs}
                    tooltip={(id) => {
                      const r = stepRows[id];
                      const z = dd?.zones?.find((x: Any) => x.zone_id === id);
                      return r ? (
                        <TipRows
                          title={zoneProps[id]?.name ?? id}
                          sub={KIND_LABEL[zoneProps[id]?.kind] ?? ''}
                          rows={[
                            ['预测需求', fix2(r.predicted_demand)],
                            ['经验区间', `${fix1(r.lower)} – ${fix1(r.upper)}`],
                            ...(z && dd.supply_known ? ([['司机 调配前→后', `${z.available_before} → ${z.available_after}`], ['需要司机', String(z.required_drivers)]] as [string, string][]) : []),
                          ]}
                        />
                      ) : null;
                    }}
                  />
                </div>
              </div>
            </Card>

            <div className="col" style={{ gap: 12 }}>
              <Card title="历史与预测" sub={zone ? app.zoneName(zone) : '全城'} actions={zone ? <button className="btn ghost sm" onClick={() => setZone(null)}>看全城</button> : null}>
                <LineChart
                  height={200}
                  xFormat={(x) => time.mdhm(x, tz)}
                  tipFormat={(x) => `${time.mdhm(x, tz)} ${time.weekday(x, tz)}`}
                  valueFormat={(v) => fix1(v)}
                  markers={[{ x: origin, label: '预测起点' }]}
                  band={zone ? { name: '经验区间', color: 'var(--accent)', points: futurePoints.map((p: Any) => ({ x: p.x, lo: p.lo, hi: p.hi })) } : undefined}
                  series={[
                    { name: '历史需求', color: 'var(--text-2)', area: true, points: histRows.map((r) => ({ x: Date.parse(r.timestamp), y: r.demand })) },
                    {
                      name: '预测',
                      color: 'var(--accent)',
                      dashed: true,
                      dots: true,
                      width: 2,
                      // 从最后一个历史桶连到首个预测点，仅为视觉衔接
                      points: [...(lastHist ? [{ x: Date.parse(lastHist.timestamp), y: lastHist.demand }] : []), ...futurePoints.map((p: Any) => ({ x: p.x, y: p.y }))],
                    },
                  ]}
                />
                <div className="small muted" style={{ marginTop: 6 }}>
                  区间：验证残差经验分位，名义边际覆盖 {ratio(fc.interval?.nominal_marginal_coverage, 0)}，不作保证。点击地图区域查看单区区间。
                </div>
              </Card>
              <Card title="分区预测" sub={`+${step} 桶`} flush>
                <div className="table-wrap" style={{ maxHeight: 190 }}>
                  <table className="table">
                    <thead>
                      <tr>
                        <th>区域</th>
                        <th className="num">预测</th>
                        <th className="num">下界</th>
                        <th className="num">上界</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.values(stepRows).sort((a, b) => b.predicted_demand - a.predicted_demand).map((r) => (
                        <tr key={r.zone_id} className={`click ${zone === r.zone_id ? 'sel' : ''}`} onClick={() => setZone(zone === r.zone_id ? null : r.zone_id)}>
                          <td>{app.zoneName(r.zone_id)}</td>
                          <td className="num">
                            <div className="bar-cell">
                              {fix2(r.predicted_demand)}
                              <span className="bar-track"><i style={{ display: 'block', height: 4, borderRadius: 2, background: 'var(--bar)', width: `${(r.predicted_demand / peak) * 100}%` }} /></span>
                            </div>
                          </td>
                          <td className="num dim">{fix1(r.lower)}</td>
                          <td className="num dim">{fix1(r.upper)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            </div>
          </div>

          <Card
            title="调配建议"
            sub="仅针对首个预测桶 · 贪心就近调剂"
            actions={
              <>
                <Seg value={supplyMode} onChange={setSupplyMode} options={[{ value: 'snapshot', label: '最新桶供给快照' }, { value: 'custom', label: '自定义供给' }]} />
                <button className="btn primary" onClick={runDispatch} disabled={dispatch.busy}>
                  {dispatch.busy ? <Loader2 size={14} className="spin" /> : <RouteIcon size={14} />}
                  生成建议
                </button>
              </>
            }
          >
            <div className="col" style={{ gap: 12 }}>
              {supplyMode === 'custom' && (
                <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))', gap: 8 }}>
                  {zoneIds.map((id) => (
                    <Field key={id} label={app.zoneName(id)}>
                      <NumberInput value={supply[id] ?? 0} min={0} onChange={(v) => setSupply({ ...supply, [id]: Number.isFinite(v) ? v : 0 })} />
                    </Field>
                  ))}
                </div>
              )}
              {dispatch.error ? <ErrorNote error={dispatch.error} /> : null}
              {!dd && !dispatch.error && <div className="muted small">按「每位可用司机在下一桶服务一单」估算各区所需司机，从富余区向最近的缺口区调剂。点击「生成建议」查看。</div>}
              {dd && !dd.supply_known && <Note tone="warn">该数据集没有完整的供给观测，无法给出调配建议。可切换到「自定义供给」填写各区司机数。</Note>}
              {dd && dd.supply_known && (
                <>
                  <div className="row" style={{ flexWrap: 'wrap' }}>
                    <Badge tone="amber">启发式建议 · 未执行</Badge>
                    <Badge>{dd.supply_source === 'caller' ? '供给：自定义' : `供给快照：${time.mdhm(dd.supply_snapshot_timestamp, tz)}`}</Badge>
                    <Badge tone={dd.conservation?.preserved ? '' : 'red'}>司机守恒 {dd.conservation?.before} → {dd.conservation?.after}</Badge>
                    <span className="small dim">调动 {int(dd.total_relocated_drivers)} 人 · {fix1(dd.total_driver_km)} 司机·公里 · 剩余缺口 {int(dd.remaining_shortage)}</span>
                  </div>
                  <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1.2fr)', gap: 20 }}>
                    <div>
                      <div className="label" style={{ marginBottom: 6 }}>调剂路线（地图橙色箭头）</div>
                      {dd.recommendations.length === 0 ? (
                        <div className="note">各区现有司机已能覆盖首桶预测需求，无需调剂。</div>
                      ) : (
                        <div className="col" style={{ gap: 6 }}>
                          {dd.recommendations.map((r: Any) => (
                            <div key={`${r.from_zone_id}${r.to_zone_id}`} className="row note" style={{ alignItems: 'center' }}>
                              <span>{app.zoneName(r.from_zone_id)}</span>
                              <ArrowRight size={13} style={{ color: 'var(--amber)', margin: 0 }} />
                              <span>{app.zoneName(r.to_zone_id)}</span>
                              <span className="grow" />
                              <b>{r.drivers} 人</b>
                              <span className="muted">{fix1(r.distance_km)} km 直线</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                    <div className="table-wrap">
                      <table className="table">
                        <thead>
                          <tr>
                            <th>区域</th>
                            <th className="num">预测需求</th>
                            <th className="num">需要司机</th>
                            <th className="num">调配前</th>
                            <th className="num">调配后</th>
                            <th className="num">缺口 前→后</th>
                          </tr>
                        </thead>
                        <tbody>
                          {dd.zones.map((z: Any) => (
                            <tr key={z.zone_id}>
                              <td>{app.zoneName(z.zone_id)}</td>
                              <td className="num">{fix2(z.predicted_demand)}</td>
                              <td className="num">{z.required_drivers}</td>
                              <td className="num">{z.available_before}</td>
                              <td className="num" style={z.available_after !== z.available_before ? { color: 'var(--amber)', fontWeight: 600 } : undefined}>{z.available_after}</td>
                              <td className="num" style={z.shortage_after > 0 ? { color: 'var(--red)' } : undefined}>
                                {z.shortage_before} → {z.shortage_after}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                  <div className="small muted">距离为区域质心球面直线距离，未考虑道路、到达时间与服务时长；执行前须人工核对。</div>
                </>
              )}
            </div>
          </Card>
        </>
      )}
    </div>
  );
}

function cleanScenario(s: typeof SCENARIO) {
  return Object.fromEntries(Object.entries(s).filter(([, v]) => Number.isFinite(v) && v !== 1));
}
