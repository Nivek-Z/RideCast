import { useMemo, useState, type ReactNode } from 'react';
import type { Any } from '../api';
import { useSize } from './charts';

// 本地 SVG 绘制虚构城市：浅灰区域块之间留出街道缝，区域中心是「名称 + 数值芯片」，
// 颜色只落在芯片里的小方块和细连线上。不依赖外部底图或地图密钥。坐标为 WGS84 [lon, lat]。

type LonLat = [number, number];
export type MapScale = 'seq' | 'div';

export interface MapArc {
  key: string;
  from: LonLat;
  to: LonLat;
  weight: number;
  label?: string;
}
export interface MapDot {
  key: string;
  lon: number;
  lat: number;
}
export interface MapPing extends MapDot {
  tone: 'ok' | 'bad';
  age: number; // 0 刚出现 → 1 消失
}

export function heatColor(t: number | null | undefined, scale: MapScale) {
  if (t === null || t === undefined || Number.isNaN(t)) return 'var(--heat-lo)';
  if (scale === 'seq') return `color-mix(in oklab, var(--heat-hi) ${(Math.max(0, Math.min(1, t)) * 100).toFixed(1)}%, var(--heat-lo))`;
  const a = Math.max(-1, Math.min(1, t));
  return `color-mix(in oklab, ${a >= 0 ? 'var(--red)' : 'var(--teal)'} ${(Math.abs(a) * 55).toFixed(1)}%, var(--heat-lo))`;
}

function ringCentroid(geometry: Any): LonLat {
  const ring: LonLat[] = geometry.type === 'Polygon' ? geometry.coordinates[0] : geometry.coordinates[0][0];
  let x = 0;
  let y = 0;
  const n = ring.length - 1 || 1;
  for (let i = 0; i < n; i++) {
    x += ring[i][0];
    y += ring[i][1];
  }
  return [x / n, y / n];
}

export function zoneCenters(zones: Any): Record<string, LonLat> {
  const out: Record<string, LonLat> = {};
  for (const f of zones?.features ?? []) out[f.properties.zone_id] = f.properties.center ?? ringCentroid(f.geometry);
  return out;
}

/** 估算芯片文字宽度：中日韩字符按全宽，其余按半宽 */
function textWidth(text: string, size: number) {
  let w = 0;
  for (const ch of text) w += ch.charCodeAt(0) > 0x2e7f ? size : size * 0.58;
  return w;
}

function Chip(props: { x: number; y: number; text: string; color?: string }) {
  const w = textWidth(props.text, 11.5) + 27;
  return (
    <g transform={`translate(${(props.x - w / 2).toFixed(1)},${props.y.toFixed(1)})`} pointerEvents="none">
      <rect className="chip-bg" width={w} height={20} rx={6} />
      <rect x={5} y={5} width={10} height={10} rx={3} fill={props.color ?? 'var(--bar)'} />
      <text className="chip-text" x={20} y={14.2}>
        {props.text}
      </text>
    </g>
  );
}

export function CityMap(props: {
  map: { zones: Any; roads: Any } | undefined;
  values?: Record<string, number | null | undefined>;
  scale?: MapScale;
  /** 色标上限；发散色标为对称幅度。缺省取当前数据最大值 */
  domain?: number;
  valueText?: (zoneId: string) => string | undefined;
  /** 芯片小方块的颜色，用来表达区域状态 */
  zoneTone?: (zoneId: string) => string | undefined;
  legend?: { title: string; lo: string; hi: string; mid?: string };
  selected?: string | null;
  onSelect?: (zoneId: string | null) => void;
  tooltip?: (zoneId: string) => ReactNode;
  flows?: MapArc[];
  dispatch?: MapArc[];
  vehicles?: MapDot[];
  pings?: MapPing[];
  eventZones?: string[];
  compact?: boolean;
  children?: ReactNode;
}) {
  const [ref, { width, height }] = useSize<HTMLDivElement>();
  const [hover, setHover] = useState<{ id: string; x: number; y: number } | null>(null);
  const scale = props.scale ?? 'seq';

  const projection = useMemo(() => {
    const features = props.map?.zones?.features ?? [];
    if (!features.length || !width || !height) return null;
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    const visit = (c: Any) => {
      if (typeof c[0] === 'number') {
        x0 = Math.min(x0, c[0]); x1 = Math.max(x1, c[0]);
        y0 = Math.min(y0, c[1]); y1 = Math.max(y1, c[1]);
      } else c.forEach(visit);
    };
    features.forEach((f: Any) => visit(f.geometry.coordinates));
    const k = Math.cos((((y0 + y1) / 2) * Math.PI) / 180);
    const pad = props.compact ? 8 : 24;
    const s = Math.min((width - pad * 2) / ((x1 - x0) * k || 1), (height - pad * 2) / (y1 - y0 || 1));
    const ox = (width - (x1 - x0) * k * s) / 2;
    const oy = (height - (y1 - y0) * s) / 2;
    return (p: LonLat): [number, number] => [ox + (p[0] - x0) * k * s, oy + (y1 - p[1]) * s];
  }, [props.map, width, height, props.compact]);

  const shapes = useMemo(() => {
    if (!projection || !props.map) return null;
    const ringPath = (ring: LonLat[]) => ring.map((p, i) => `${i ? 'L' : 'M'}${projection(p).map((v) => v.toFixed(1)).join(',')}`).join('') + 'Z';
    const zones = props.map.zones.features.map((f: Any) => {
      const polys: LonLat[][][] = f.geometry.type === 'Polygon' ? [f.geometry.coordinates] : f.geometry.coordinates;
      const center = projection(f.properties.center ?? ringCentroid(f.geometry));
      return { id: f.properties.zone_id as string, name: f.properties.name as string, d: polys.map((poly) => poly.map(ringPath).join('')).join(''), center };
    });
    const roads = (props.map.roads?.features ?? []).map((f: Any, i: number) => {
      const lines: LonLat[][] = f.geometry.type === 'LineString' ? [f.geometry.coordinates] : f.geometry.coordinates;
      return {
        key: f.properties?.road_id ?? String(i),
        d: lines.map((line) => line.map((p, j) => `${j ? 'L' : 'M'}${projection(p).map((v) => v.toFixed(1)).join(',')}`).join('')).join(''),
      };
    });
    return { zones, roads };
  }, [projection, props.map]);

  const domain = useMemo(() => {
    if (props.domain) return props.domain;
    const vals = Object.values(props.values ?? {}).filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
    return Math.max(1e-9, ...vals.map(Math.abs));
  }, [props.values, props.domain]);

  const arc = (a: MapArc, bend = 0.2) => {
    const [x1, y1] = projection!(a.from);
    const [x2, y2] = projection!(a.to);
    const dx = x2 - x1;
    const dy = y2 - y1;
    return { d: `M${x1.toFixed(1)},${y1.toFixed(1)}Q${((x1 + x2) / 2 - dy * bend).toFixed(1)},${((y1 + y2) / 2 + dx * bend).toFixed(1)} ${x2.toFixed(1)},${y2.toFixed(1)}`, len: Math.hypot(dx, dy) };
  };
  const flowMax = Math.max(1, ...(props.flows ?? []).map((f) => f.weight));
  const dispatchMax = Math.max(1, ...(props.dispatch ?? []).map((f) => f.weight));

  return (
    <div className="map" ref={ref}>
      {shapes && projection && (
        <svg width={width} height={height} onMouseLeave={() => setHover(null)} onClick={() => props.onSelect?.(null)}>
          <defs>
            <marker id="rc-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
              <path d="M0,0L10,5L0,10z" fill="var(--amber)" />
            </marker>
          </defs>
          {shapes.zones.map((z: Any) => {
            const v = props.values?.[z.id];
            const t = typeof v === 'number' ? v / domain : null;
            return (
              <path
                key={z.id}
                className="zone"
                d={z.d}
                style={props.compact ? { strokeWidth: 3 } : undefined}
                fill={props.values ? heatColor(t, scale) : 'var(--heat-lo)'}
                fillRule="evenodd"
                onMouseMove={(e) => {
                  const rect = e.currentTarget.ownerSVGElement!.getBoundingClientRect();
                  setHover({ id: z.id, x: e.clientX - rect.left, y: e.clientY - rect.top });
                }}
                onClick={(e) => {
                  e.stopPropagation();
                  props.onSelect?.(props.selected === z.id ? null : z.id);
                }}
              />
            );
          })}
          {!props.compact && (
            <g pointerEvents="none">
              {shapes.roads.map((r: Any) => (
                <path key={r.key} className="road" d={r.d} />
              ))}
            </g>
          )}
          {shapes.zones.filter((z: Any) => z.id === props.selected).map((z: Any) => (
            <path key={z.id} d={z.d} fill="none" stroke="var(--accent)" strokeWidth={2} strokeLinejoin="round" pointerEvents="none" />
          ))}
          {props.eventZones?.map((id) => {
            const z = shapes.zones.find((s: Any) => s.id === id);
            return z ? <circle key={id} className="event-ring" cx={z.center[0]} cy={z.center[1]} r={props.compact ? 14 : 40} /> : null;
          })}
          {props.flows?.map((f) => {
            const a = arc(f);
            if (a.len < 2) return null;
            return <path key={f.key} className="flow" d={a.d} strokeWidth={0.75 + (f.weight / flowMax) * 1.5} />;
          })}
          {props.dispatch?.map((f) => {
            const a = arc(f, -0.16);
            return <path key={f.key} className="dispatch" d={a.d} strokeWidth={1.25 + (f.weight / dispatchMax) * 1.5} markerEnd="url(#rc-arrow)" />;
          })}
          {props.pings?.map((p) => {
            const [x, y] = projection([p.lon, p.lat]);
            return <circle key={p.key} className="ping" cx={x} cy={y} r={2 + p.age * 9} stroke={p.tone === 'ok' ? 'var(--accent)' : 'var(--red)'} opacity={1 - p.age} />;
          })}
          {props.vehicles?.map((v) => {
            const [x, y] = projection([v.lon, v.lat]);
            return <circle key={v.key} className="vehicle" cx={x} cy={y} r={3} />;
          })}
          {!props.compact &&
            shapes.zones.map((z: Any) => {
              const text = props.valueText?.(z.id);
              return (
                <g key={z.id}>
                  <text className="zlabel" x={z.center[0]} y={z.center[1] - (text ? 6 : -4)}>
                    {z.name}
                  </text>
                  {text && <Chip x={z.center[0]} y={z.center[1] + 2} text={text} color={props.zoneTone?.(z.id)} />}
                </g>
              );
            })}
          {props.dispatch?.map((f) => {
            if (!f.label || props.compact) return null;
            const [x1, y1] = projection(f.from);
            const [x2, y2] = projection(f.to);
            const dx = x2 - x1, dy = y2 - y1;
            return <Chip key={f.key} x={(x1 + x2) / 2 + dy * 0.08} y={(y1 + y2) / 2 - dx * 0.08 - 10} text={f.label} color="var(--amber)" />;
          })}
        </svg>
      )}
      {hover && props.tooltip && (
        <div className="tooltip" style={{ top: Math.max(4, hover.y - 10), ...(hover.x > width * 0.6 ? { right: width - hover.x + 14 } : { left: hover.x + 14 }) }}>
          {props.tooltip(hover.id)}
        </div>
      )}
      {props.legend && !props.compact && (
        <div className="map-float map-legend">
          <span>{props.legend.title}</span>
          <div
            className="ramp"
            style={{
              background:
                scale === 'seq'
                  ? `linear-gradient(90deg in oklab, var(--heat-lo), var(--heat-hi))`
                  : `linear-gradient(90deg in oklab, ${heatColor(-1, 'div')}, var(--heat-lo), ${heatColor(1, 'div')})`,
              boxShadow: 'inset 0 0 0 0.75px var(--border-strong)',
            }}
          />
          <div className="row" style={{ justifyContent: 'space-between' }}>
            <span>{props.legend.lo}</span>
            {props.legend.mid && <span>{props.legend.mid}</span>}
            <span>{props.legend.hi}</span>
          </div>
        </div>
      )}
      {props.children}
    </div>
  );
}

export function TipRows(props: { title: ReactNode; sub?: ReactNode; rows: [string, ReactNode][] }) {
  return (
    <>
      <div style={{ fontWeight: 600 }}>{props.title}</div>
      {props.sub && <div className="tt">{props.sub}</div>}
      {props.rows.map(([k, v]) => (
        <div className="tr" key={k}>
          <span className="dim">{k}</span>
          <b>{v}</b>
        </div>
      ))}
    </>
  );
}
