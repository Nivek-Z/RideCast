import { memo, useMemo, useState } from 'react';
import { MapPin } from 'lucide-react';
import type { MapData } from './types';

export type Layer = 'partition' | 'actual' | 'predicted' | 'error' | 'teacher';
const shareColors = Array.from({ length: 6 }, (_, i) => `var(--chart-tone-${i + 1})`);
const underColors = Array.from({ length: 4 }, (_, i) => `var(--under-tone-${i + 1})`);
const overColors = Array.from({ length: 4 }, (_, i) => `var(--over-tone-${i + 1})`);
const at = (palette: string[], fraction: number) =>
  palette[Math.min(palette.length - 1, Math.max(0, Math.floor(fraction * palette.length)))];
export const layers: Record<Layer, string> = {
  partition: '空间划分', actual: '观测份额', predicted: '生成模型估计', teacher: '真实模型估计', error: '份额差异',
};
export function mapScales(data: MapData[]) {
  return {
    share: Math.max(.001, ...data.flatMap((map) => [...map.actual, ...map.predicted, ...map.teacher])),
    error: Math.max(.001, ...data.flatMap((map) => map.predicted.map((value, index) => Math.abs(value - map.actual[index])))),
  };
}

export const TractMap = memo(function TractMap({ data, layer, selected, inspect, scale }: {
  data: MapData; layer: Layer; selected?: number; inspect?: boolean;
  scale: { share: number; error: number };
}) {
  const row = useMemo(() => new Map(data.regions.map((index, i) => [index, i])), [data]);
  return <svg aria-label={`芝加哥普查区 · ${layers[layer]}`} role="img" className="tract-map" viewBox="0 0 440 500">
    {data.paths.map((path, index) => {
      const i = row.get(index);
      let fill = 'var(--container-high)';
      if (layer === 'partition') fill = data.development[index] ? 'var(--map-development)' : 'var(--map-holdout)';
      else if (i != null && layer === 'error') {
        const error = data.predicted[i] - data.actual[i];
        fill = Math.abs(error) < scale.error * .024 ? 'var(--paper)' : at(error < 0 ? underColors : overColors, Math.abs(error) / scale.error);
      } else if (i != null && layer !== 'error') fill = at(shareColors, Math.sqrt(data[layer][i] / scale.share));
      return <path key={index} d={path} fill={fill} fillRule="evenodd"
        data-inspect={inspect ? index : undefined} className={i != null || layer === 'partition' ? 'tract in-scope' : 'tract'} />;
    })}
    {selected != null && <path className="tract-highlight" d={data.paths[selected]} fillRule="evenodd" fill="none" />}
  </svg>;
});

export function MapLegend({ layer, scale }: { layer: Layer; scale: { share: number; error: number } }) {
  if (layer === 'partition') return <div className="map-legend">
    <span><i className="development-swatch" />开发区</span><span><i className="holdout-swatch" />留出区</span>
  </div>;
  if (layer === 'error') return <div className="map-legend spectrum">
    <span>−{scale.error.toFixed(2)} pp</span><i className="error-scale" /><span>+{scale.error.toFixed(2)} pp</span>
  </div>;
  return <div className="map-legend spectrum">
    <span>0</span><i className="share-scale" /><span>{scale.share.toFixed(2)}% 行程份额</span>
  </div>;
}

export function MapAtlas({ data, preview = false }: { data: MapData; preview?: boolean }) {
  const [layer, setLayer] = useState<Layer>(preview ? 'partition' : 'actual');
  const [selected, setSelected] = useState<number>();
  const scale = useMemo(() => mapScales([data]), [data]);
  const shown: Layer[] = preview ? [layer] : [layer, 'predicted', 'error'];
  const index = selected == null ? -1 : data.regions.indexOf(selected);
  const toolLayers: Layer[] = preview ? ['partition', 'actual', 'predicted', 'error'] : ['actual', 'teacher'];
  return <>
    <div className="map-tools">
      <div className="map-switch" role="group" aria-label={preview ? '地图图层' : '左侧对照图层'}>
        {toolLayers.map((option) => <button key={option} type="button" aria-pressed={layer === option}
          onClick={() => { setLayer(option); setSelected(undefined); }}>{layers[option]}</button>)}
      </div>
    </div>
    <div className={preview ? 'map-single' : 'map-grid'}>
      {shown.map((value, i) => <div className="map-tile" key={i}>
        {!preview && <h3>{layers[value]}</h3>}
        <div onPointerOver={(event) => {
          if (event.pointerType === 'mouse' && event.target instanceof SVGPathElement && event.target.dataset.inspect) {
            setSelected(Number(event.target.dataset.inspect));
          }
        }} onClick={(event) => {
          if (event.target instanceof SVGPathElement && event.target.dataset.inspect) setSelected(Number(event.target.dataset.inspect));
        }}>
          <TractMap data={data} layer={value} scale={scale} selected={selected} inspect />
        </div>
        <MapLegend layer={value} scale={scale} />
      </div>)}
    </div>
    {!preview && <div className="map-inspect">
      <MapPin size={16} aria-hidden="true" />
      {selected == null ? <span>指向或轻触分区，查看该区的份额和误差</span> : <span>
        <strong>普查区 {data.ids[selected]}</strong> · 空间块 {data.blocks[selected]} · {data.development[selected] ? '开发区' : '留出区'}
        {index >= 0 && ` · 观测份额 ${data.actual[index].toFixed(3)}% · 生成模型估计 ${data.predicted[index].toFixed(3)}% · 差异 ${(data.predicted[index] - data.actual[index]).toFixed(3)} pp`}
      </span>}
    </div>}
  </>;
}
