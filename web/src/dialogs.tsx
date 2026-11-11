import { Loader2 } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { api, type Job } from './api';
import { ErrorNote, Field, Modal, Note, NumberInput } from './components/ui';
import { time, zonedISO } from './format';
import { useApp } from './store';

function useSubmit() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, run };
}

function Footer(props: { onClose: () => void; busy: boolean; onSubmit: () => void; label: string; disabled?: boolean }) {
  return (
    <>
      <button className="btn" onClick={props.onClose}>
        取消
      </button>
      <button className="btn primary" disabled={props.busy || props.disabled} onClick={props.onSubmit}>
        {props.busy && <Loader2 size={14} className="spin" />}
        {props.label}
      </button>
    </>
  );
}

function WorkerHint() {
  const app = useApp();
  if (app.system?.worker_online !== false) return null;
  return (
    <Note tone="warn">
      当前没有在线 worker，任务会停留在排队状态。请在后端目录运行 <span className="mono">ridecast worker</span>。
    </Note>
  );
}

function WorldSelect(props: { value: string; onChange: (v: string) => void }) {
  const app = useApp();
  return (
    <select className="select" value={props.value} onChange={(e) => props.onChange(e.target.value)}>
      {app.worlds.map((w) => (
        <option key={w.id} value={w.id}>
          {w.name} · {w.metadata?.zones?.features?.length} 区 · {w.metadata?.bucket_minutes} 分钟桶
        </option>
      ))}
    </select>
  );
}

function WorldDialog({ onClose }: { onClose: () => void }) {
  const app = useApp();
  const s = useSubmit();
  const [f, setF] = useState({ name: '霁川市', rows: 3, cols: 3, seed: 42, bucket_minutes: 60, timezone: 'Asia/Shanghai' });
  const set = (patch: Partial<typeof f>) => setF({ ...f, ...patch });
  const submit = () =>
    s.run(async () => {
      const world = await api.createWorld(f);
      app.refresh();
      app.setWorld(world.id);
      app.toast(`城市「${world.name}」已就绪`, 'ok');
      onClose();
    });
  return (
    <Modal title="新建虚拟城市" onClose={onClose} footer={<Footer onClose={onClose} busy={s.busy} onSubmit={submit} label="创建城市" />}>
      <Note>地图为种子确定的虚构城市，不对应任何真实城市。相同参数会复用同一个确定性世界。</Note>
      <div className="form-grid">
        <Field label="名称" span2>
          <input className="input" value={f.name} maxLength={100} onChange={(e) => set({ name: e.target.value })} />
        </Field>
        <Field label="网格行数" hint="1–8">
          <NumberInput value={f.rows} min={1} max={8} onChange={(rows) => set({ rows })} />
        </Field>
        <Field label="网格列数" hint="1–8">
          <NumberInput value={f.cols} min={1} max={8} onChange={(cols) => set({ cols })} />
        </Field>
        <Field label="世界种子" hint="决定街区划分、用地、人口与岗位">
          <NumberInput value={f.seed} min={0} onChange={(seed) => set({ seed })} />
        </Field>
        <Field label="时间桶">
          <select className="select" value={f.bucket_minutes} onChange={(e) => set({ bucket_minutes: Number(e.target.value) })}>
            <option value={15}>15 分钟</option>
            <option value={30}>30 分钟</option>
            <option value={60}>60 分钟</option>
          </select>
        </Field>
        <Field label="时区（IANA）" span2>
          <input className="input" value={f.timezone} onChange={(e) => set({ timezone: e.target.value })} />
        </Field>
      </div>
      <ErrorNote error={s.error} />
    </Modal>
  );
}

function afterEnqueue(app: ReturnType<typeof useApp>, job: Job, onClose: () => void) {
  app.refresh();
  app.toast('任务已提交，正在排队');
  onClose();
  app.go('jobs', job.id);
}

function SimulateDialog({ onClose }: { onClose: () => void }) {
  const app = useApp();
  const s = useSubmit();
  const [worldId, setWorldId] = useState(app.world?.id ?? '');
  const [date, setDate] = useState('2026-01-01');
  const [days, setDays] = useState(45);
  const [seed, setSeed] = useState(42);
  const [sc, setSc] = useState({ demand_multiplier: 1, rain_multiplier: 1, traffic_multiplier: 1, supply_multiplier: 1 });
  const world = app.worlds.find((w) => w.id === worldId);
  const tz = world?.metadata?.timezone ?? 'Asia/Shanghai';
  const rows = days * (1440 / (world?.metadata?.bucket_minutes ?? 60)) * (world?.metadata?.zones?.features?.length ?? 0);
  const submit = () =>
    s.run(async () => {
      // 仅提交偏离默认值的倍数，保持 scenario 记录干净
      const scenario = Object.fromEntries(Object.entries(sc).filter(([, v]) => v !== 1));
      const job = await api.simulate({ world_id: worldId, start: zonedISO(date, tz), days, seed, scenario });
      afterEnqueue(app, job, onClose);
    });
  const mult = (key: keyof typeof sc, label: string, max: number) => (
    <Field label={label} hint={`0–${max}，1 为不调整`}>
      <NumberInput value={sc[key]} min={0} max={max} step={0.1} onChange={(v) => setSc({ ...sc, [key]: v })} />
    </Field>
  );
  return (
    <Modal
      title="生成仿真数据集"
      onClose={onClose}
      footer={<Footer onClose={onClose} busy={s.busy} onSubmit={submit} label="提交仿真" disabled={!worldId || rows > 150000} />}
    >
      {app.worlds.length === 0 ? (
        <Note tone="warn">请先创建一个虚拟城市。</Note>
      ) : (
        <>
          <WorkerHint />
          <div className="form-grid">
            <Field label="城市" span2>
              <WorldSelect value={worldId} onChange={setWorldId} />
            </Field>
            <Field label="起始日期" hint={`${tz} 当地 00:00`}>
              <input className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
            </Field>
            <Field label="天数" hint="2–180">
              <NumberInput value={days} min={2} max={180} onChange={setDays} />
            </Field>
            <Field label="演化种子" hint="与世界种子、时间范围共同定义一次确定性实验" span2>
              <NumberInput value={seed} min={0} onChange={setSeed} />
            </Field>
            {mult('demand_multiplier', '需求倍数', 5)}
            {mult('rain_multiplier', '降水倍数', 5)}
            {mult('traffic_multiplier', '拥堵倍数', 3)}
            {mult('supply_multiplier', '供给倍数', 3)}
          </div>
          <div className={`small ${rows > 150000 ? '' : 'muted'}`} style={rows > 150000 ? { color: 'var(--red)' } : undefined}>
            面板规模 {rows.toLocaleString()} 行（上限 150,000）
          </div>
        </>
      )}
      <ErrorNote error={s.error} />
    </Modal>
  );
}

function ImportDialog({ onClose }: { onClose: () => void }) {
  const app = useApp();
  const s = useSubmit();
  const [worldId, setWorldId] = useState(app.world?.id ?? '');
  const world = app.worlds.find((w) => w.id === worldId);
  const [orders, setOrders] = useState<File | null>(null);
  const [context, setContext] = useState<File | null>(null);
  const [o, setO] = useState({
    start: '2026-01-01',
    end: '2026-01-08',
    target_semantics: 'requests',
    invalid_policy: 'reject',
    coverage_attested: false,
    column_map: '',
  });
  const tz = world?.metadata?.timezone ?? 'Asia/Shanghai';
  const submit = () =>
    s.run(async () => {
      const options = {
        observation_start: zonedISO(o.start, tz),
        observation_end: zonedISO(o.end, tz),
        coverage_attested: o.coverage_attested,
        target_semantics: o.target_semantics,
        timezone: tz,
        invalid_policy: o.invalid_policy,
        column_map: o.column_map.trim() ? JSON.parse(o.column_map) : {},
      };
      const form = new FormData();
      form.set('world_id', worldId);
      form.set('options', JSON.stringify(options));
      form.set('orders', orders!);
      if (context) form.set('context', context);
      afterEnqueue(app, await api.importDataset(form), onClose);
    });
  const mb = Math.round((app.system?.limits?.upload_bytes_per_file ?? 0) / 1048576);
  return (
    <Modal
      title="导入真实订单"
      onClose={onClose}
      footer={<Footer onClose={onClose} busy={s.busy} onSubmit={submit} label="上传并导入" disabled={!orders || !worldId || !o.coverage_attested} />}
    >
      <WorkerHint />
      <div className="form-grid">
        <Field label="目标城市（区域与时间桶以此为准）" span2>
          <WorldSelect value={worldId} onChange={setWorldId} />
        </Field>
        <Field label="订单 CSV" hint={mb ? `单文件上限 ${mb} MiB` : undefined}>
          <input className="input" type="file" accept=".csv,text/csv" onChange={(e) => setOrders(e.target.files?.[0] ?? null)} style={{ padding: 4 }} />
        </Field>
        <Field label="上下文 CSV（可选）" hint="天气、路况、供给等">
          <input className="input" type="file" accept=".csv,text/csv" onChange={(e) => setContext(e.target.files?.[0] ?? null)} style={{ padding: 4 }} />
        </Field>
        <Field label="观测起始（含）" hint={`${tz} 当地 00:00`}>
          <input className="input" type="date" value={o.start} onChange={(e) => setO({ ...o, start: e.target.value })} />
        </Field>
        <Field label="观测结束（不含）">
          <input className="input" type="date" value={o.end} onChange={(e) => setO({ ...o, end: e.target.value })} />
        </Field>
        <Field label="标签口径">
          <select className="select" value={o.target_semantics} onChange={(e) => setO({ ...o, target_semantics: e.target.value })}>
            <option value="requests">requests · 发起订单</option>
            <option value="completed_trips">completed_trips · 仅完成行程</option>
          </select>
        </Field>
        <Field label="无效行策略">
          <select className="select" value={o.invalid_policy} onChange={(e) => setO({ ...o, invalid_policy: e.target.value })}>
            <option value="reject">reject · 整体拒绝</option>
            <option value="drop">drop · 丢弃并记录</option>
          </select>
        </Field>
        <Field label="列映射 JSON（可选）" hint="规范列名 → 源文件列名" span2>
          <textarea className="input" rows={3} placeholder='{"requested_at": "pickup_datetime"}' value={o.column_map} onChange={(e) => setO({ ...o, column_map: e.target.value })} />
        </Field>
      </div>
      <label className="check">
        <input type="checkbox" checked={o.coverage_attested} onChange={(e) => setO({ ...o, coverage_attested: e.target.checked })} />
        我确认该时间范围内的订单覆盖完整，没有订单的桶可记为零需求
      </label>
      <ErrorNote error={s.error} />
    </Modal>
  );
}

function TrainDialog({ onClose }: { onClose: () => void }) {
  const app = useApp();
  const s = useSubmit();
  const [datasetId, setDatasetId] = useState(app.dataset?.id ?? app.datasets[0]?.id ?? '');
  const [c, setC] = useState({ lookback: 24, horizon: 3, epochs: 30, units: 32, batch_size: 128, learning_rate: 0.001, seed: 42, tune: true });
  const set = (patch: Partial<typeof c>) => setC({ ...c, ...patch });
  const dataset = app.datasets.find((d) => d.id === datasetId);
  const bucket = dataset?.metadata?.bucket_minutes ?? 60;
  const hours = (n: number) => `${((n * bucket) / 60).toFixed(bucket === 60 ? 0 : 1)} 小时`;
  const submit = () => s.run(async () => afterEnqueue(app, await api.train({ dataset_id: datasetId, config: c }), onClose));
  const num = (key: Exclude<keyof typeof c, 'tune'>, label: string, min: number, max: number, hint?: ReactNode, step = 1) => (
    <Field label={label} hint={hint ?? `${min}–${max}`}>
      <NumberInput value={c[key]} min={min} max={max} step={step} onChange={(v) => set({ [key]: v } as Partial<typeof c>)} />
    </Field>
  );
  return (
    <Modal title="发起训练" onClose={onClose} footer={<Footer onClose={onClose} busy={s.busy} onSubmit={submit} label="提交训练" disabled={!datasetId} />}>
      {app.datasets.length === 0 ? (
        <Note tone="warn">请先生成或导入一个数据集。</Note>
      ) : (
        <>
          <WorkerHint />
          {app.system?.tensorflow_installed === false && <Note tone="warn">后端未安装 TensorFlow，训练任务会失败。请用 <span className="mono">pip install -e '.[ml]'</span> 安装。</Note>}
          <div className="form-grid">
            <Field label="数据集" span2>
              <select className="select" value={datasetId} onChange={(e) => setDatasetId(e.target.value)}>
                {app.datasets.map((d) => (
                  <option key={d.id} value={d.id}>
                    {app.worlds.find((w) => w.id === d.metadata?.world_id)?.name ?? d.metadata?.world_id} · {d.metadata?.source === 'imported' ? '导入' : '仿真'} ·{' '}
                    {time.date(d.metadata?.observation_start, d.metadata?.timezone ?? app.tz)} 起 · {d.id.slice(2, 10)}
                  </option>
                ))}
              </select>
            </Field>
            {num('lookback', '回看窗口（桶）', 2, 168, `2–168，当前约 ${hours(c.lookback)}`)}
            {num('horizon', '预测步长（桶）', 1, 24, `1–24，当前约 ${hours(c.horizon)}`)}
            {num('epochs', '训练轮数', 1, 100)}
            {num('units', 'GRU 单元数', 8, 128)}
            {num('batch_size', '批大小', 16, 1024)}
            {num('learning_rate', '学习率', 0.0001, 0.1, '0–0.1', 0.0005)}
            {num('seed', '随机种子', 0, 2147483647, '可复现实验')}
            <Field label="超参搜索" group>
              <label className="check" style={{ height: 30 }}>
                <input type="checkbox" checked={c.tune} onChange={(e) => set({ tune: e.target.checked })} />
                用验证集比较多个候选
              </label>
            </Field>
          </div>
        </>
      )}
      <ErrorNote error={s.error} />
    </Modal>
  );
}

export function Dialogs() {
  const app = useApp();
  const close = () => app.openDialog(null);
  if (app.dialog === 'world') return <WorldDialog onClose={close} />;
  if (app.dialog === 'simulate') return <SimulateDialog onClose={close} />;
  if (app.dialog === 'import') return <ImportDialog onClose={close} />;
  if (app.dialog === 'train') return <TrainDialog onClose={close} />;
  return null;
}
