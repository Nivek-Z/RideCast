import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

export function useSize<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  useEffect(() => {
    if (!ref.current) return;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setSize((s) => (Math.abs(s.width - width) < 0.5 && Math.abs(s.height - height) < 0.5 ? s : { width, height }));
    });
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return [ref, size] as const;
}

function niceTicks(min: number, max: number, count = 4) {
  if (!(max > min)) max = min + 1;
  const raw = (max - min) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const start = Math.floor(min / step) * step;
  const ticks: number[] = [];
  for (let v = start; v <= max + step * 0.999; v += step) ticks.push(Number(v.toPrecision(12)));
  return ticks;
}

export interface Series {
  name: string;
  color: string;
  points: { x: number; y: number | null | undefined }[];
  dashed?: boolean;
  area?: boolean;
  width?: number;
  dots?: boolean;
}
export interface Band {
  name: string;
  color: string;
  points: { x: number; lo: number; hi: number }[];
}

const fmtDefault = (v: number) => (Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(v % 1000 ? 1 : 0)}k` : String(Number(v.toFixed(2))));

export function LineChart(props: {
  series: Series[];
  band?: Band;
  height?: number;
  xFormat: (x: number) => string;
  tipFormat?: (x: number) => string;
  yFormat?: (v: number) => string;
  valueFormat?: (v: number) => string;
  zeroBased?: boolean;
  markers?: { x: number; label: string }[];
  legend?: boolean;
  xTicks?: number[];
}) {
  const height = props.height ?? 220;
  const [ref, { width }] = useSize<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const m = { l: 38, r: 10, t: 10, b: 22 };
  const yFormat = props.yFormat ?? fmtDefault;
  const valueFormat = props.valueFormat ?? ((v: number) => String(Number(v.toFixed(2))));

  const model = useMemo(() => {
    const xs = new Set<number>();
    let lo = Infinity;
    let hi = -Infinity;
    for (const s of props.series)
      for (const p of s.points) {
        xs.add(p.x);
        if (p.y !== null && p.y !== undefined && Number.isFinite(p.y)) {
          lo = Math.min(lo, p.y);
          hi = Math.max(hi, p.y);
        }
      }
    for (const p of props.band?.points ?? []) {
      xs.add(p.x);
      lo = Math.min(lo, p.lo);
      hi = Math.max(hi, p.hi);
    }
    const sorted = [...xs].sort((a, b) => a - b);
    if (!Number.isFinite(lo)) {
      lo = 0;
      hi = 1;
    }
    if (props.zeroBased !== false) lo = Math.min(0, lo);
    const ticks = niceTicks(lo, hi);
    return { xs: sorted, x0: sorted[0] ?? 0, x1: sorted[sorted.length - 1] ?? 1, y0: ticks[0], y1: ticks[ticks.length - 1], ticks };
  }, [props.series, props.band, props.zeroBased]);

  const iw = Math.max(10, width - m.l - m.r);
  const ih = height - m.t - m.b;
  const sx = (x: number) => m.l + (model.x1 === model.x0 ? iw / 2 : ((x - model.x0) / (model.x1 - model.x0)) * iw);
  const sy = (y: number) => m.t + ih - ((y - model.y0) / (model.y1 - model.y0 || 1)) * ih;

  const paths = useMemo(
    () =>
      props.series.map((s) => {
        let d = '';
        let pen = false;
        let first: number | null = null;
        let last: number | null = null;
        for (const p of s.points) {
          if (p.y === null || p.y === undefined || !Number.isFinite(p.y)) {
            pen = false;
            continue;
          }
          d += `${pen ? 'L' : 'M'}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`;
          pen = true;
          first ??= p.x;
          last = p.x;
        }
        const area = s.area && first !== null && last !== null ? `${d}L${sx(last).toFixed(1)},${sy(model.y0)}L${sx(first).toFixed(1)},${sy(model.y0)}Z` : null;
        return { d, area };
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [props.series, model, iw, ih],
  );
  const bandPath = useMemo(() => {
    const pts = props.band?.points;
    if (!pts?.length) return null;
    const up = pts.map((p, i) => `${i ? 'L' : 'M'}${sx(p.x).toFixed(1)},${sy(p.hi).toFixed(1)}`).join('');
    const down = [...pts].reverse().map((p) => `L${sx(p.x).toFixed(1)},${sy(p.lo).toFixed(1)}`).join('');
    return `${up}${down}Z`;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.band, model, iw, ih]);

  const xTicks = useMemo(() => {
    if (props.xTicks) return props.xTicks;
    const n = Math.max(2, Math.min(8, Math.floor(iw / 90)));
    if (model.xs.length <= n) return model.xs;
    return Array.from({ length: n }, (_, i) => model.xs[Math.round((i * (model.xs.length - 1)) / (n - 1))]);
  }, [model.xs, iw, props.xTicks]);

  const onMove = (e: React.MouseEvent) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = model.x0 + ((e.clientX - rect.left - m.l) / iw) * (model.x1 - model.x0);
    const xs = model.xs;
    if (!xs.length) return;
    let a = 0;
    let b = xs.length - 1;
    while (b - a > 1) {
      const mid = (a + b) >> 1;
      if (xs[mid] < x) a = mid;
      else b = mid;
    }
    setHover(Math.abs(xs[a] - x) <= Math.abs(xs[b] - x) ? xs[a] : xs[b]);
  };

  const hoverRows = useMemo(() => {
    if (hover === null) return [];
    const rows: { name: string; color: string; value: string }[] = [];
    for (const s of props.series) {
      const p = s.points.find((q) => q.x === hover);
      if (p && p.y !== null && p.y !== undefined) rows.push({ name: s.name, color: s.color, value: valueFormat(p.y) });
    }
    const bp = props.band?.points.find((q) => q.x === hover);
    if (bp && props.band) rows.push({ name: props.band.name, color: props.band.color, value: `${valueFormat(bp.lo)} – ${valueFormat(bp.hi)}` });
    return rows;
  }, [hover, props.series, props.band, valueFormat]);

  return (
    <div>
      {props.legend !== false && props.series.length + (props.band ? 1 : 0) > 1 && (
        <div className="legend" style={{ marginBottom: 6 }}>
          {props.series.map((s) => (
            <span key={s.name}>
              <i className="swatch" style={{ background: s.color, height: s.dashed ? 2 : 8 }} />
              {s.name}
            </span>
          ))}
          {props.band && (
            <span>
              <i className="swatch" style={{ background: props.band.color, opacity: 0.3 }} />
              {props.band.name}
            </span>
          )}
        </div>
      )}
      <div className="chart" ref={ref} style={{ height }} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {width > 0 && (
          <svg width={width} height={height}>
            {model.ticks.map((t) => (
              <g key={t}>
                <line className="gridline" x1={m.l} x2={width - m.r} y1={sy(t)} y2={sy(t)} />
                <text className="axis" x={m.l - 6} y={sy(t) + 3.5} textAnchor="end">
                  {yFormat(t)}
                </text>
              </g>
            ))}
            {xTicks.map((t, i) =>
              // 相邻刻度文字相同时只保留第一个（例如按天格式化的小时级数据）
              i > 0 && props.xFormat(t) === props.xFormat(xTicks[i - 1]) ? null : (
                <text key={t} className="axis" x={sx(t)} y={height - 5} textAnchor={i === 0 ? 'start' : i === xTicks.length - 1 ? 'end' : 'middle'}>
                  {props.xFormat(t)}
                </text>
              ),
            )}
            {props.markers?.map((mk) => (
              <g key={mk.label}>
                <line x1={sx(mk.x)} x2={sx(mk.x)} y1={m.t} y2={m.t + ih} stroke="var(--text-3)" strokeDasharray="3 3" />
                <text className="axis" x={sx(mk.x) + 4} y={m.t + 9}>
                  {mk.label}
                </text>
              </g>
            ))}
            {bandPath && <path d={bandPath} fill={props.band!.color} opacity={0.16} />}
            {props.series.map((s, i) => (
              <g key={s.name}>
                {paths[i].area && <path d={paths[i].area!} fill={s.color} opacity={0.07} />}
                <path d={paths[i].d} fill="none" stroke={s.color} strokeWidth={s.width ?? 1.5} strokeDasharray={s.dashed ? '4 3' : undefined} strokeLinejoin="round" strokeLinecap="round" />
                {s.dots && s.points.map((p) => (p.y === null || p.y === undefined ? null : <circle key={p.x} cx={sx(p.x)} cy={sy(p.y)} r={2.5} fill={s.color} />))}
              </g>
            ))}
            {hover !== null && (
              <g pointerEvents="none">
                <line x1={sx(hover)} x2={sx(hover)} y1={m.t} y2={m.t + ih} stroke="var(--border-strong)" />
                {props.series.map((s) => {
                  const p = s.points.find((q) => q.x === hover);
                  return p && p.y !== null && p.y !== undefined ? (
                    <circle key={s.name} cx={sx(hover)} cy={sy(p.y)} r={3.5} fill={s.color} stroke="var(--panel)" strokeWidth={1.5} />
                  ) : null;
                })}
              </g>
            )}
          </svg>
        )}
        {hover !== null && hoverRows.length > 0 && (
          <Tooltip x={sx(hover)} y={m.t} width={width} title={(props.tipFormat ?? props.xFormat)(hover)} rows={hoverRows} />
        )}
      </div>
    </div>
  );
}

function Tooltip(props: { x: number; y: number; width: number; title: ReactNode; rows: { name: string; color: string; value: string }[] }) {
  const flip = props.x > props.width * 0.6;
  return (
    <div className="tooltip" style={{ top: props.y, ...(flip ? { right: props.width - props.x + 10 } : { left: props.x + 10 }) }}>
      <div className="tt">{props.title}</div>
      {props.rows.map((r) => (
        <div className="tr" key={r.name}>
          <i className="swatch" style={{ background: r.color }} />
          {r.name}
          <b>{r.value}</b>
        </div>
      ))}
    </div>
  );
}

export function BarChart(props: {
  data: { label: string; value: number; tip?: string }[];
  height?: number;
  color?: string;
  name: string;
  valueFormat?: (v: number) => string;
  labelEvery?: number;
}) {
  const height = props.height ?? 150;
  const [ref, { width }] = useSize<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const m = { l: 34, r: 4, t: 8, b: 20 };
  const max = Math.max(1, ...props.data.map((d) => d.value));
  const ticks = niceTicks(0, max, 3);
  const top = ticks[ticks.length - 1];
  const iw = Math.max(10, width - m.l - m.r);
  const ih = height - m.t - m.b;
  const step = iw / Math.max(1, props.data.length);
  const bw = Math.max(2, Math.min(10, step * 0.5));
  const color = props.color ?? 'var(--accent)';
  const vf = props.valueFormat ?? ((v: number) => String(Number(v.toFixed(1))));
  return (
    <div className="chart" ref={ref} style={{ height }} onMouseLeave={() => setHover(null)}>
      {width > 0 && (
        <svg width={width} height={height}>
          {ticks.map((t) => (
            <g key={t}>
              <line className="gridline" x1={m.l} x2={width - m.r} y1={m.t + ih - (t / top) * ih} y2={m.t + ih - (t / top) * ih} />
              <text className="axis" x={m.l - 6} y={m.t + ih - (t / top) * ih + 3.5} textAnchor="end">
                {fmtDefault(t)}
              </text>
            </g>
          ))}
          {props.data.map((d, i) => {
            const h = (d.value / top) * ih;
            const cx = m.l + step * (i + 0.5);
            return (
              <g key={d.label} onMouseEnter={() => setHover(i)}>
                <rect x={m.l + step * i} y={m.t} width={step} height={ih} fill="transparent" />
                <rect x={cx - bw / 2} y={m.t + ih - h} width={bw} height={Math.max(1, h)} rx={Math.min(2, bw / 2)} fill={color} opacity={hover === null || hover === i ? 1 : 0.45} />
                {i % (props.labelEvery ?? 1) === 0 && (
                  <text className="axis" x={cx} y={height - 5} textAnchor="middle">
                    {d.label}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
      )}
      {hover !== null && props.data[hover] && (
        <Tooltip
          x={m.l + step * (hover + 0.5)}
          y={m.t}
          width={width}
          title={props.data[hover].tip ?? props.data[hover].label}
          rows={[{ name: props.name, color, value: vf(props.data[hover].value) }]}
        />
      )}
    </div>
  );
}

/** 两组并排柱：模型 vs 基线 */
export function GroupedBars(props: {
  groups: { label: string; values: (number | null)[] }[];
  names: string[];
  colors: string[];
  height?: number;
  valueFormat?: (v: number) => string;
}) {
  const height = props.height ?? 180;
  const [ref, { width }] = useSize<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const m = { l: 34, r: 4, t: 8, b: 20 };
  const max = Math.max(1e-9, ...props.groups.flatMap((g) => g.values.map((v) => v ?? 0)));
  const ticks = niceTicks(0, max, 3);
  const top = ticks[ticks.length - 1];
  const iw = Math.max(10, width - m.l - m.r);
  const ih = height - m.t - m.b;
  const step = iw / Math.max(1, props.groups.length);
  const n = props.names.length;
  const bw = Math.max(2, Math.min(10, (step * 0.6) / n));
  const vf = props.valueFormat ?? ((v: number) => v.toFixed(2));
  return (
    <div>
      <div className="legend" style={{ marginBottom: 6 }}>
        {props.names.map((name, i) => (
          <span key={name}>
            <i className="swatch" style={{ background: props.colors[i] }} />
            {name}
          </span>
        ))}
      </div>
      <div className="chart" ref={ref} style={{ height }} onMouseLeave={() => setHover(null)}>
        {width > 0 && (
          <svg width={width} height={height}>
            {ticks.map((t) => (
              <g key={t}>
                <line className="gridline" x1={m.l} x2={width - m.r} y1={m.t + ih - (t / top) * ih} y2={m.t + ih - (t / top) * ih} />
                <text className="axis" x={m.l - 6} y={m.t + ih - (t / top) * ih + 3.5} textAnchor="end">
                  {fmtDefault(t)}
                </text>
              </g>
            ))}
            {props.groups.map((g, gi) => {
              const cx = m.l + step * (gi + 0.5);
              return (
                <g key={g.label} onMouseEnter={() => setHover(gi)} opacity={hover === null || hover === gi ? 1 : 0.5}>
                  <rect x={m.l + step * gi} y={m.t} width={step} height={ih} fill="transparent" />
                  {g.values.map((v, vi) => {
                    const h = ((v ?? 0) / top) * ih;
                    return <rect key={vi} x={cx - (n * bw + (n - 1) * 2) / 2 + vi * (bw + 2)} y={m.t + ih - h} width={bw} height={Math.max(1, h)} rx={2} fill={props.colors[vi]} />;
                  })}
                  <text className="axis" x={cx} y={height - 5} textAnchor="middle">
                    {g.label}
                  </text>
                </g>
              );
            })}
          </svg>
        )}
        {hover !== null && props.groups[hover] && (
          <Tooltip
            x={m.l + step * (hover + 0.5)}
            y={m.t}
            width={width}
            title={props.groups[hover].label}
            rows={props.groups[hover].values.map((v, i) => ({ name: props.names[i], color: props.colors[i], value: v === null ? '—' : vf(v) }))}
          />
        )}
      </div>
    </div>
  );
}

export function Ring(props: { value: number | null | undefined; size?: number; color?: string; label?: ReactNode }) {
  const size = props.size ?? 64;
  const r = size / 2 - 5;
  const c = 2 * Math.PI * r;
  const v = props.value ?? 0;
  return (
    <svg width={size} height={size} role="img">
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--hover)" strokeWidth={5} />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke={props.color ?? 'var(--accent)'}
        strokeWidth={5}
        strokeLinecap="round"
        strokeDasharray={`${c * Math.max(0, Math.min(1, v))} ${c}`}
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
      <text x="50%" y="50%" dominantBaseline="central" textAnchor="middle" fontSize={size * 0.2} fontWeight={600} fill="var(--text)">
        {props.label}
      </text>
    </svg>
  );
}

/** 时间轴底图：按像素列聚合后的迷你柱 */
export function MiniBars(props: { values: number[]; color?: string; progress?: number }) {
  const [ref, { width, height }] = useSize<HTMLDivElement>();
  const bars = useMemo(() => {
    const n = Math.max(1, Math.min(props.values.length, Math.floor(width / 3)));
    const out: number[] = [];
    for (let i = 0; i < n; i++) {
      const a = Math.floor((i * props.values.length) / n);
      const b = Math.max(a + 1, Math.floor(((i + 1) * props.values.length) / n));
      let peak = 0;
      for (let j = a; j < b; j++) peak = Math.max(peak, props.values[j] ?? 0);
      out.push(peak);
    }
    return out;
  }, [props.values, width]);
  const max = Math.max(1, ...bars);
  const w = width / Math.max(1, bars.length);
  return (
    <div ref={ref} style={{ position: 'absolute', inset: 0 }}>
      <svg width={width} height={height}>
        {bars.map((v, i) => {
          const h = Math.max(1, (v / max) * (height - 4));
          // 游标左侧（已回放的部分）着浅蓝，右侧保持中性
          const played = props.progress !== undefined && (i + 0.5) / bars.length <= props.progress;
          return <rect key={i} x={i * w + 0.5} y={height - h} width={Math.max(1, w - 1)} height={h} rx={1} fill={played ? 'color-mix(in srgb, var(--accent) 38%, var(--panel))' : (props.color ?? 'var(--border-strong)')} />;
        })}
      </svg>
    </div>
  );
}
