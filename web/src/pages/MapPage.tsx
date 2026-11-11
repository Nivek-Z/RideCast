import { CloudRain, LocateFixed, Pause, Play, SkipBack, SkipForward, Thermometer, Wind } from 'lucide-react';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { api, type Any } from '../api';
import { CityMap, TipRows, type MapArc, type MapDot, type MapPing, type MapScale } from '../components/CityMap';
import { LineChart, MiniBars } from '../components/charts';
import { Badge, Card, Empty, ErrorNote, KV, Loading, NumberInput, Seg, Switch } from '../components/ui';
import { fix1, fix2, int, KIND_LABEL, localInput, signed, time, zonedISO } from '../format';
import { useApp, useLocal, useQuery } from '../store';

type Mode = 'replay' | 'world';
type Layer = 'demand' | 'gap' | 'cancelled' | 'traffic' | 'rain';

const LAYERS: Record<Layer, { label: string; scale: MapScale; floor: number; legend: [string, string, string?] }> = {
  demand: { label: '需求', scale: 'seq', floor: 5, legend: ['0', '高'] },
  gap: { label: '供需计数差', scale: 'div', floor: 5, legend: ['司机多', '请求多', '0'] },
  cancelled: { label: '未服务', scale: 'seq', floor: 3, legend: ['0', '多'] },
  traffic: { label: '路况', scale: 'seq', floor: 1, legend: ['畅通', '拥堵'] },
  rain: { label: '降水', scale: 'seq', floor: 2, legend: ['0', '强'] },
};
const DAY = 86400000;

function Scrubber(props: { start: number; end: number; cursor: number; onChange: (t: number) => void; values?: number[]; tz: string }) {
  const progress = (props.cursor - props.start) / (props.end - props.start);
  const ref = useRef<HTMLDivElement>(null);
  const pick = (clientX: number) => {
    const rect = ref.current!.getBoundingClientRect();
    const f = Math.max(0, Math.min(0.99999, (clientX - rect.left) / rect.width));
    props.onChange(props.start + f * (props.end - props.start));
  };
  const span = props.end - props.start;
  // 整数天窗口按天打点，否则均分 5 段
  const parts = span % DAY === 0 && span / DAY <= 10 ? span / DAY : 5;
  const labels = Array.from({ length: parts + 1 }, (_, i) => props.start + (span * i) / parts);
  return (
    <div className="grow col" style={{ gap: 2 }}>
      <div
        className="scrub"
        ref={ref}
        onPointerDown={(e) => {
          e.currentTarget.setPointerCapture(e.pointerId);
          pick(e.clientX);
        }}
        onPointerMove={(e) => e.buttons === 1 && pick(e.clientX)}
      >
        {props.values?.length ? (
          <MiniBars values={props.values} progress={progress} />
        ) : (
          <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: 4, borderRadius: 2, background: `linear-gradient(90deg, color-mix(in srgb, var(--accent) 38%, var(--panel)) ${progress * 100}%, var(--tile) ${progress * 100}%)` }} />
        )}
        <div className="cursor" style={{ left: `${progress * 100}%` }}>
          <span className="pill">{time.mdhm(props.cursor, props.tz)}</span>
        </div>
      </div>
      <div className="row small muted" style={{ justifyContent: 'space-between' }}>
        {labels.map((t, i) => (
          <span key={i}>{span <= 2 * DAY ? time.mdhm(t, props.tz) : time.md(t, props.tz)}</span>
        ))}
      </div>
    </div>
  );
}

export function MapPage() {
  const app = useApp();
  const { world, dataset, tz } = app;
  const B = app.bucketMinutes * 60000;
  const [modePref, setMode] = useLocal<Mode>('rc.map.mode', 'replay');
  const mode: Mode = dataset ? modePref : 'world';
  const [layer, setLayer] = useLocal<Layer>('rc.map.layer', 'demand');
  const [showFlows, setShowFlows] = useLocal('rc.map.flows', false);
  const [showVehicles, setShowVehicles] = useLocal('rc.map.vehicles', true);
  const [speed, setSpeed] = useLocal<number>('rc.map.speed', 20); // 每秒推进的仿真分钟数
  const [seed, setSeed] = useState(42);
  const [playing, setPlaying] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [anchor, setAnchor] = useState(() => Math.floor(Date.now() / DAY) * DAY);

  const range = useMemo(() => {
    if (mode === 'replay' && dataset) return { start: Date.parse(dataset.metadata.observation_start), end: Date.parse(dataset.metadata.observation_end) };
    return { start: anchor - 3 * DAY, end: anchor + 4 * DAY };
  }, [mode, dataset, anchor]);

  const [cursor, setCursor] = useState(0);
  // 切换数据源时把游标放到合理位置：回放取最后一天开头，演化取此刻
  const scope = `${mode}:${dataset?.id}:${world?.id}`;
  useEffect(() => {
    setPlaying(false);
    setSelected(null);
    setCursor(mode === 'replay' ? Math.max(range.start, range.end - DAY) : Math.min(range.end - 1, Math.max(range.start, Date.now())));
  }, [scope]); // eslint-disable-line react-hooks/exhaustive-deps
  const clamped = Math.min(range.end - 1, Math.max(range.start, cursor || range.start));
  const bucket = range.start + Math.floor((clamped - range.start) / B) * B;
  // 演化模式的桶以 UTC 纪元对齐，与后端 floor 行为一致
  const worldBucket = Math.floor(clamped / B) * B;

  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    let last = performance.now();
    const tick = (now: number) => {
      const dt = now - last;
      if (dt >= 33) {
        last = now;
        setCursor((c) => Math.min(range.end - 1, c + (dt / 1000) * speed * 60000));
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, speed, range.end]);
  useEffect(() => {
    if (playing && clamped >= range.end - 1) setPlaying(false);
  }, [playing, clamped, range.end]);

  const map = useQuery(world ? `map:${world.id}` : null, () => api.worldMap(world!.id));
  const overview = useQuery(mode === 'replay' && dataset ? `overview:${dataset.id}` : null, () => api.overview(dataset!.id));
  const city = useQuery(mode === 'replay' && dataset ? `ts:${dataset.id}:all` : null, () => api.timeseries(dataset!.id, { limit: 10000 }));

  const replayKey = (b: number) => (mode === 'replay' && dataset && b >= range.start && b < range.end ? `replay:${dataset.id}:${b}` : null);
  const replayFn = (b: number) => (signal: AbortSignal) => api.replay(dataset!.id, new Date(b + B / 2).toISOString(), 1000, signal);
  const replay = useQuery(replayKey(bucket), replayFn(bucket));
  const previous = useQuery(replayKey(bucket - B), replayFn(bucket - B));
  useQuery(playing ? replayKey(bucket + B) : null, replayFn(bucket + B)); // 预取下一桶

  const state = useQuery(
    mode === 'world' && world ? `state:${world.id}:${seed}:${worldBucket}` : null,
    (signal) => api.worldState(world!.id, new Date(worldBucket).toISOString(), seed, signal),
  );

  const dayStart = range.start + Math.floor((bucket - range.start) / DAY) * DAY;
  const flows = useQuery(
    mode === 'replay' && dataset && showFlows ? `flows:${dataset.id}:${dayStart}` : null,
    (signal) => api.flows(dataset!.id, { start: new Date(dayStart).toISOString(), end: new Date(Math.min(range.end, dayStart + DAY)).toISOString(), limit: 100 }, signal),
  );
  const zoneSeries = useQuery(
    mode === 'replay' && dataset && selected ? `ts:${dataset.id}:${selected}:${dayStart}` : null,
    () => api.timeseries(dataset!.id, { zone_id: selected!, start: new Date(dayStart).toISOString(), end: new Date(dayStart + DAY).toISOString(), limit: 500 }),
  );

  const zoneProps = useMemo(() => {
    const out: Record<string, Any> = {};
    for (const f of map.data?.zones?.features ?? []) out[f.properties.zone_id] = f.properties;
    return out;
  }, [map.data]);

  // 每个区域当日的逐桶序列，用于右侧的微型时段条；区域过多时不请求
  const zoneCount = Object.keys(zoneProps).length;
  const strips = useQuery<Record<string, Any[]>>(
    mode === 'replay' && dataset && zoneCount > 0 && zoneCount <= 40 ? `zday:${dataset.id}:${dayStart}` : null,
    async () => {
      const out: Record<string, Any[]> = {};
      const window = { start: new Date(dayStart).toISOString(), end: new Date(dayStart + DAY).toISOString(), limit: 200 };
      await Promise.all(Object.keys(zoneProps).map(async (id) => {
        out[id] = (await api.timeseries(dataset!.id, { zone_id: id, ...window })).rows;
      }));
      return out;
    },
  );

  // 统一两种模式的区域行
  const snapshot: Any = mode === 'replay' ? replay.data : state.data;
  const zoneRows: Record<string, Any> = useMemo(() => {
    const out: Record<string, Any> = {};
    for (const z of snapshot?.zones ?? []) {
      out[z.zone_id] = mode === 'replay' ? z : { ...zoneProps[z.zone_id], ...z, supply_observed: true, supply_gap: z.demand - z.available_drivers, unserved_requests: z.cancelled };
    }
    return out;
  }, [snapshot, mode, zoneProps]);
  const supplyKnown = mode === 'world' || snapshot?.supply_observed !== false;
  const outcomesKnown = mode === 'world' || snapshot?.outcomes_observed !== false;

  const valueOf = (z: Any): number | null => {
    if (!z) return null;
    if (layer === 'demand') return z.demand;
    if (layer === 'gap') return z.supply_gap ?? null;
    if (layer === 'cancelled') return z.unserved_requests ?? null;
    if (layer === 'traffic') return z.traffic_index === null || z.traffic_index === undefined ? null : Math.max(0, z.traffic_index - 0.7);
    return z.precipitation;
  };
  const values = Object.fromEntries(Object.entries(zoneRows).map(([id, z]) => [id, valueOf(z)]));

  // 色标上限取已见最大值，播放时颜色不随单桶跳变
  const domains = useRef<Record<string, number>>({});
  const domainKey = `${scope}:${layer}`;
  const peak = Math.max(0, ...Object.values(values).map((v) => Math.abs(v ?? 0)));
  const known = layer === 'demand' && mode === 'replay' ? Math.max(0, ...(overview.data?.zones ?? []).map((z: Any) => z.peak_demand_per_bucket)) * 0.8 : 0;
  domains.current[domainKey] = Math.max(domains.current[domainKey] ?? 0, peak, known, LAYERS[layer].floor);
  const domain = layer === 'traffic' ? 1 : domains.current[domainKey];

  // 芯片小方块表达区域状态：红=本桶有未服务请求，橙=有活动，绿=有需求且全部服务，灰=无需求或未知
  const toneOf = (id: string) => {
    const z = zoneRows[id];
    if (!z) return 'var(--bar)';
    if (outcomesKnown && z.cancelled > 0) return 'var(--red)';
    if (z.event_intensity > 0) return 'var(--amber)';
    if (z.demand > 0) return outcomesKnown ? 'var(--green)' : 'var(--accent)';
    return 'var(--bar)';
  };
  const textOf = (id: string) => {
    const z = zoneRows[id];
    if (!z) return undefined;
    if (layer === 'demand') return `${int(z.demand)} 单`;
    if (layer === 'gap') return z.supply_gap === null || z.supply_gap === undefined ? '未知' : signed(z.supply_gap);
    if (layer === 'cancelled') return z.unserved_requests === null || z.unserved_requests === undefined ? '未知' : `${int(z.unserved_requests)} 单`;
    if (layer === 'traffic') return fix2(z.traffic_index);
    return `${fix1(z.precipitation)} mm/h`;
  };

  // 车辆与订单脉冲：按后端同一假设（行程自 requested_at 开始、起终点直线插值）在客户端逐帧计算
  const { vehicles, pings, appeared } = useMemo(() => {
    const vehicles: MapDot[] = [];
    const pings: MapPing[] = [];
    let appeared = 0;
    if (mode !== 'replay') return { vehicles, pings, appeared };
    const pingWindow = Math.max(60000, speed * 60000 * 0.9);
    const current: Any[] = replay.data?.bucket_start && Date.parse(replay.data.bucket_start) === bucket ? replay.data.orders : [];
    const before: Any[] = previous.data?.bucket_start && Date.parse(previous.data.bucket_start) === bucket - B ? previous.data.orders : [];
    for (const o of current) {
      const t0 = Date.parse(o.requested_at);
      if (t0 > clamped) continue;
      appeared += 1;
      const age = (clamped - t0) / pingWindow;
      if (age < 1 && o.pickup_lon !== null && o.pickup_lat !== null) pings.push({ key: o.order_id, lon: o.pickup_lon, lat: o.pickup_lat, tone: o.status === 'cancelled' ? 'bad' : 'ok', age });
    }
    if (showVehicles) {
      for (const o of [...before, ...current]) {
        if (!o.driver_id || !o.travel_minutes || o.pickup_lon === null || o.dropoff_lon === null || o.dropoff_lon === undefined) continue;
        const t0 = Date.parse(o.requested_at);
        const p = (clamped - t0) / (o.travel_minutes * 60000);
        if (p < 0 || p >= 1) continue;
        vehicles.push({ key: o.order_id, lon: o.pickup_lon + (o.dropoff_lon - o.pickup_lon) * p, lat: o.pickup_lat + (o.dropoff_lat - o.pickup_lat) * p });
      }
    }
    return { vehicles, pings, appeared };
  }, [mode, replay.data, previous.data, clamped, bucket, B, speed, showVehicles]);

  const arcs: MapArc[] = useMemo(() => {
    if (!showFlows || mode !== 'replay') return [];
    return (flows.data?.rows ?? [])
      .filter((r: Any) => r.dropoff_zone_id && r.pickup_zone_id !== r.dropoff_zone_id && r.origin && r.destination)
      .slice(0, 14)
      .map((r: Any) => ({ key: `${r.pickup_zone_id}>${r.dropoff_zone_id}`, from: r.origin, to: r.destination, weight: r.orders }));
  }, [flows.data, showFlows, mode]);

  if (!app.loaded) return <div className="page"><Loading height={300} /></div>;
  if (!world) {
    return (
      <div className="page">
        <Card>
          <Empty title="还没有城市" action={<button className="btn primary" onClick={() => app.openDialog('world')}>新建虚拟城市</button>}>
            创建一个种子确定的虚拟城市后，可以在任意时刻查看它的演化状态。
          </Empty>
        </Card>
      </div>
    );
  }

  const totals = sumTotals(zoneRows);
  const weather = mode === 'world' ? state.data?.weather : averageWeather(zoneRows);
  const eventZones = Object.values(zoneRows).filter((z) => z.event_intensity > 0).map((z) => z.zone_id);
  const sel = selected ? zoneRows[selected] ?? zoneProps[selected] : null;
  const ranked = Object.values(zoneRows).sort((a, b) => Math.abs(valueOf(b) ?? -1) - Math.abs(valueOf(a) ?? -1));
  const step = (dir: number) => {
    setPlaying(false);
    setCursor(Math.min(range.end - 1, Math.max(range.start, (mode === 'replay' ? bucket : worldBucket) + dir * B)));
  };
  const error = mode === 'replay' ? replay.error : state.error;
  const shownBucket = mode === 'replay' ? bucket : worldBucket;

  return (
    <div className="page fill">
      <div className="row" style={{ flexWrap: 'wrap', rowGap: 8 }}>
        <Seg
          value={mode}
          onChange={(m) => setMode(m)}
          options={[
            { value: 'replay', label: '历史回放' },
            { value: 'world', label: '世界演化' },
          ]}
        />
        <Seg value={layer} onChange={setLayer} options={(Object.keys(LAYERS) as Layer[]).map((l) => ({ value: l, label: LAYERS[l].label }))} />
        {mode === 'replay' && (
          <>
            <span style={{ width: 4 }} />
            <Switch checked={showVehicles} onChange={setShowVehicles}>车辆</Switch>
            <Switch checked={showFlows} onChange={setShowFlows}>OD 流向</Switch>
          </>
        )}
        <span className="grow" />
        {mode === 'world' && (
          <>
            <span className="label">演化种子</span>
            <NumberInput value={seed} min={0} onChange={(v) => setSeed(Number.isFinite(v) ? Math.max(0, Math.floor(v)) : 0)} style={{ width: 84 }} />
            <input
              className="input"
              type="datetime-local"
              style={{ width: 190 }}
              value={localInput(clamped, tz)}
              onChange={(e) => {
                if (!e.target.value) return;
                const t = Date.parse(zonedISO(e.target.value, tz));
                setAnchor(Math.floor(t / DAY) * DAY);
                setCursor(t);
              }}
            />
            <button
              className="btn"
              onClick={() => {
                setAnchor(Math.floor(Date.now() / DAY) * DAY);
                setCursor(Date.now());
              }}
            >
              <LocateFixed size={14} /> 此刻
            </button>
          </>
        )}
        {!dataset && <span className="small muted">当前城市没有数据集，仅可查看世界演化</span>}
      </div>

      <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 1fr) 360px', flex: 1, minHeight: 0 }}>
        <div className="card" style={{ overflow: 'hidden', minHeight: 320 }}>
          <CityMap
            map={map.data}
            values={values}
            scale={LAYERS[layer].scale}
            domain={domain}
            valueText={textOf}
            zoneTone={toneOf}
            legend={{ title: LAYERS[layer].label, lo: LAYERS[layer].legend[0], hi: LAYERS[layer].legend[1], mid: LAYERS[layer].legend[2] }}
            selected={selected}
            onSelect={setSelected}
            flows={arcs}
            vehicles={vehicles}
            pings={pings}
            eventZones={eventZones}
            tooltip={(id) => {
              const z = zoneRows[id] ?? zoneProps[id];
              if (!z) return null;
              return (
                <TipRows
                  title={z.name}
                  sub={KIND_LABEL[z.kind] ?? z.kind}
                  rows={[
                    ['需求', int(z.demand)],
                    ['完成 / 取消', outcomesKnown ? `${int(z.completed)} / ${int(z.cancelled)}` : '未知'],
                    ['可用司机', supplyKnown ? int(z.available_drivers) : '未知'],
                    ['路况指数', fix2(z.traffic_index)],
                    ['降水', `${fix1(z.precipitation)} mm/h`],
                    ...(z.event_intensity > 0 ? ([['活动强度', fix2(z.event_intensity)]] as [string, string][]) : []),
                  ]}
                />
              );
            }}
          >
            <div className="map-float" style={{ left: 14, top: 12, padding: '8px 12px' }}>
              <div className="row">
                <i className={`dot ${playing ? 'blue pulse' : ''}`} />
                <span style={{ fontSize: 15, fontWeight: 500 }}>{time.full(clamped, tz)}</span>
                <span className="muted">{time.weekday(clamped, tz)}</span>
              </div>
              <div className="small muted">{mode === 'replay' ? '历史回放 · 非实时 · 车辆为直线插值' : '合成观测 · 种子确定 · 可随机访问任意时刻'}</div>
            </div>
            {error && (
              <div className="map-float" style={{ left: 10, bottom: 10, maxWidth: 420 }}>
                <ErrorNote error={error} />
              </div>
            )}
          </CityMap>
        </div>

        <div className="col" style={{ gap: 12, minHeight: 0, overflow: 'auto' }}>
          <Card
            title="当前时间桶"
            sub={`${time.hm(shownBucket, tz)}–${time.hm(shownBucket + B, tz)}`}
            actions={mode === 'world' && state.data ? <Badge>合成观测</Badge> : mode === 'replay' && snapshot ? <Badge>{snapshot.source === 'synthetic' ? '合成' : '导入'}</Badge> : null}
          >
            <div className="grid" style={{ gridTemplateColumns: '1fr 1fr', gap: 10 }}>
              <Mini k="需求" v={int(totals.demand)} d={mode === 'replay' ? `已出现 ${appeared}` : undefined} />
              <Mini k="可用司机" v={supplyKnown ? int(totals.drivers) : '未知'} />
              <Mini k="完成" v={outcomesKnown ? int(totals.completed) : '未知'} />
              <Mini k="取消" v={outcomesKnown ? int(totals.cancelled) : '未知'} tone={totals.cancelled > 0 ? 'red' : undefined} />
            </div>
            {weather && (
              <div className="row small dim" style={{ marginTop: 10, gap: 12, flexWrap: 'wrap' }}>
                <span className="row" style={{ gap: 4 }}><Thermometer size={13} />{fix1(weather.temperature)} °C</span>
                <span className="row" style={{ gap: 4 }}><CloudRain size={13} />{fix1(weather.precipitation)} mm/h</span>
                <span className="row" style={{ gap: 4 }}><Wind size={13} />{fix1(weather.wind_speed)} m/s</span>
              </div>
            )}
            {eventZones.length > 0 && (
              <div className="row small" style={{ marginTop: 8, flexWrap: 'wrap' }}>
                {eventZones.map((id) => (
                  <Badge key={id} tone="amber">活动 · {zoneRows[id].name ?? app.zoneName(id)}</Badge>
                ))}
              </div>
            )}
            {mode === 'replay' && showVehicles && (
              <div className="small muted" style={{ marginTop: 8 }}>
                在途车辆 {vehicles.length} 辆{replay.data?.orders_truncated ? '（订单已按上限截断）' : ''}
              </div>
            )}
          </Card>

          {sel ? (
            <Card title={sel.name} sub={KIND_LABEL[sel.kind] ?? sel.kind} actions={<button className="btn ghost sm" onClick={() => setSelected(null)}>返回排行</button>}>
              <KV
                items={[
                  ['需求', int(sel.demand)],
                  ['完成 / 取消', outcomesKnown ? `${int(sel.completed)} / ${int(sel.cancelled)}` : '未知'],
                  ['可用司机', supplyKnown ? int(sel.available_drivers) : '未知'],
                  ['供需计数差', signed(sel.supply_gap)],
                  ...(mode === 'world' && sel.state ? ([['状态', <Badge tone={sel.state === 'undersupplied' ? 'red' : 'green'}>{sel.state === 'undersupplied' ? '供给不足' : '平衡'}</Badge>]] as [string, ReactNode][]) : []),
                  ['路况指数', fix2(sel.traffic_index)],
                  ['人口 / 岗位', `${int(sel.population)} / ${int(sel.jobs)}`],
                  ['基础需求 · 吸引力', `${fix1(sel.base_demand)} · ${fix2(sel.attractiveness)}`],
                ]}
              />
              {mode === 'replay' && zoneSeries.data && (
                <div style={{ marginTop: 10 }}>
                  <LineChart
                    height={120}
                    legend={false}
                    xFormat={(x) => time.hm(x, tz)}
                    tipFormat={(x) => time.mdhm(x, tz)}
                    valueFormat={(v) => int(v)}
                    markers={[{ x: bucket, label: '' }]}
                    series={[{ name: '需求', color: 'var(--accent)', area: true, points: zoneSeries.data.rows.map((r: Any) => ({ x: Date.parse(r.timestamp), y: r.demand })) }]}
                  />
                </div>
              )}
              {mode === 'world' && <div className="small muted" style={{ marginTop: 8 }}>状态依据该桶取消数判定；计数差不等于未满足请求数。</div>}
            </Card>
          ) : (
            <Card title="区域" sub={strips.data ? `${LAYERS[layer].label} · 方格为当日逐小时需求` : LAYERS[layer].label} flush>
              {ranked.length === 0 ? (
                <Loading height={120} />
              ) : (
                ranked.map((z) => {
                  const v = valueOf(z);
                  return (
                    <div key={z.zone_id} className="list-item" style={{ padding: '8px 18px' }} onClick={() => setSelected(z.zone_id)}>
                      <i className="sq" style={{ background: toneOf(z.zone_id) }} />
                      <span className="grow ellipsis">{z.name}</span>
                      {strips.data?.[z.zone_id] ? (
                        <DayStrip rows={strips.data[z.zone_id]} dayStart={dayStart} cursor={clamped} />
                      ) : (
                        <span className="bar-track" style={{ width: 64 }}>
                          <i style={{ display: 'block', height: 4, borderRadius: 2, width: `${Math.min(100, (Math.abs(v ?? 0) / domain) * 100)}%`, background: layer === 'gap' ? ((v ?? 0) > 0 ? 'var(--red)' : 'var(--teal)') : 'var(--bar)' }} />
                        </span>
                      )}
                      <span className="dim" style={{ width: 50, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{textOf(z.zone_id)}</span>
                    </div>
                  );
                })
              )}
            </Card>
          )}
        </div>
      </div>

      <div className="card">
        <div className="timeline">
          <div className="row" style={{ gap: 4 }}>
            <button className="btn ghost icon" onClick={() => step(-1)} aria-label="上一桶" title="上一桶">
              <SkipBack size={15} />
            </button>
            <button
              className="btn play"
              onClick={() => {
                if (!playing && clamped >= range.end - 2) setCursor(range.start);
                setPlaying(!playing);
              }}
              aria-label={playing ? '暂停' : '播放'}
            >
              {playing ? <Pause size={16} /> : <Play size={16} style={{ marginLeft: 2 }} />}
            </button>
            <button className="btn ghost icon" onClick={() => step(1)} aria-label="下一桶" title="下一桶">
              <SkipForward size={15} />
            </button>
          </div>
          <Seg value={speed} onChange={setSpeed} options={[{ value: 5, label: '5 分/秒' }, { value: 20, label: '20 分/秒' }, { value: 60, label: '1 时/秒' }]} />
          <Scrubber
            start={range.start}
            end={range.end}
            cursor={clamped}
            tz={tz}
            values={mode === 'replay' ? city.data?.rows?.map((r: Any) => r.demand) : undefined}
            onChange={(t) => setCursor(t)}
          />
        </div>
      </div>
    </div>
  );
}

/** 一天 24 格：格子深浅为该小时需求，红色表示该小时有未服务请求，游标之后的时段变淡 */
function DayStrip(props: { rows: Any[]; dayStart: number; cursor: number }) {
  const cells = Array.from({ length: 24 }, () => ({ demand: 0, cancelled: 0 }));
  for (const r of props.rows) {
    const i = Math.floor((Date.parse(r.timestamp) - props.dayStart) / 3600000);
    if (i >= 0 && i < 24) {
      cells[i].demand += r.demand ?? 0;
      cells[i].cancelled += r.cancelled ?? 0;
    }
  }
  const max = Math.max(1, ...cells.map((c) => c.demand));
  const now = Math.floor((props.cursor - props.dayStart) / 3600000);
  return (
    <span className="strip" aria-hidden="true">
      {cells.map((c, i) => (
        <i
          key={i}
          className={i === now ? 'now' : i > now ? 'future' : ''}
          style={{ background: c.cancelled > 0 ? 'var(--red)' : c.demand > 0 ? `color-mix(in srgb, var(--accent) ${Math.round(18 + (c.demand / max) * 82)}%, var(--tile))` : undefined }}
        />
      ))}
    </span>
  );
}

function Mini(props: { k: string; v: string; d?: string; tone?: string }) {
  return (
    <div>
      <div className="small dim">{props.k}</div>
      <div style={{ fontSize: 22, fontWeight: 500, letterSpacing: '-0.02em', fontVariantNumeric: 'tabular-nums', color: props.tone ? `var(--${props.tone})` : undefined }}>{props.v}</div>
      {props.d && <div className="small muted">{props.d}</div>}
    </div>
  );
}

function sumTotals(rows: Record<string, Any>) {
  let demand = 0, drivers = 0, completed = 0, cancelled = 0;
  for (const z of Object.values(rows)) {
    demand += z.demand ?? 0;
    drivers += z.available_drivers ?? 0;
    completed += z.completed ?? 0;
    cancelled += z.cancelled ?? 0;
  }
  return { demand, drivers, completed, cancelled };
}

function averageWeather(rows: Record<string, Any>) {
  const list = Object.values(rows);
  if (!list.length) return null;
  const mean = (key: string) => list.reduce((s, z) => s + (z[key] ?? 0), 0) / list.length;
  return { temperature: mean('temperature'), precipitation: mean('precipitation'), wind_speed: mean('wind_speed') };
}
