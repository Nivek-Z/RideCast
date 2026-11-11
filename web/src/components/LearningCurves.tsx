import { useState } from 'react';
import type { Any } from '../api';
import { duration } from '../format';
import { LineChart } from './charts';
import { Badge, Empty, Seg } from './ui';

/** 学习曲线：读取结构化的 candidates[].epochs[]，不解析 message 字符串。 */
export function LearningCurves({ history, height = 220 }: { history: Any; height?: number }) {
  const candidates: Any[] = history?.candidates ?? [];
  const selected = history?.selected_candidate;
  const [pick, setPick] = useState<number | null>(null);
  // 训练中默认跟随最新候选；完成后默认展示被选中的候选
  const fallback = selected ?? history?.candidate ?? (candidates.length ? candidates[candidates.length - 1].candidate : 0);
  const current = candidates.find((c) => c.candidate === (pick ?? fallback)) ?? candidates[candidates.length - 1];
  const epochs: Any[] = current?.epochs ?? history?.epochs ?? [];

  if (!epochs.length) {
    return <Empty title="暂无曲线">{history?.status === 'starting' || history?.state === 'queued' || history?.state === 'running' ? '第一个 epoch 结束后出现。' : '该任务没有留下学习曲线快照。'}</Empty>;
  }
  const total = epochs.reduce((s, e) => s + (e.duration_seconds ?? 0), 0);
  return (
    <div className="col">
      <div className="row" style={{ flexWrap: 'wrap' }}>
        {candidates.length > 1 && (
          <Seg
            value={current?.candidate ?? 0}
            onChange={(v) => setPick(v)}
            options={candidates.map((c) => ({ value: c.candidate as number, label: `候选 ${c.candidate}${c.candidate === selected ? ' ✓' : ''}` }))}
          />
        )}
        {current?.config && (
          <span className="small muted">
            {current.config.units} 单元 · lr {current.config.learning_rate} · 回看 {current.config.lookback} · 批 {current.config.batch_size}
          </span>
        )}
        <span className="grow" />
        <Badge>{epochs.length} 轮 · {duration(total)}</Badge>
      </div>
      <LineChart
        height={height}
        zeroBased={false}
        xFormat={(x) => `${x}`}
        tipFormat={(x) => `第 ${x} 轮`}
        valueFormat={(v) => v.toFixed(4)}
        yFormat={(v) => v.toFixed(2)}
        series={[
          { name: '训练损失', color: 'var(--accent)', points: epochs.map((e) => ({ x: e.epoch, y: e.loss })), dots: epochs.length < 30 },
          { name: '验证损失', color: 'var(--text-3)', points: epochs.map((e) => ({ x: e.epoch, y: e.val_loss })), dots: epochs.length < 30 },
        ]}
      />
      <div className="small muted">损失单位：log1p(需求) 的均方误差，不是订单数误差。</div>
    </div>
  );
}
