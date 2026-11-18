import { Button, Checkbox, FormControl, MenuItem, Select, TextField } from '@mui/material';
import { ArrowDown, ChartNoAxesColumnIncreasing, ChevronsUpDown, Copy, FlaskConical, Layers3, Play, SlidersHorizontal, Timer } from 'lucide-react';
import { useMemo, useState } from 'react';
import { stop, submit, useArtifact } from './data';
import { MapLegend, TractMap, mapScales } from './Map';
import { active, number, seconds, stateText, tv, type ArenaMetrics, type Config, type MapData, type Run, type Status } from './types';

const worlds = [8, 16, 32, 64];
const alphas = [0.1, 0.3, 1, 3];
const defaultSeeds = [7, 42, 99];
const cohort = (seeds: number[]) => seeds.join(', ');
const configLabel = (config: Config) => `${config.worlds_per_seed} 世界/种子 · λ ${config.teacher_alpha}`;
const points = (value: number | null) => value == null ? '—' : `${value.toFixed(2)} pp`;
const arenaMetrics = (run?: Run) => run?.summary?.metrics as ArenaMetrics | undefined;
const date = (value: string) => new Date(value).toLocaleString('zh-CN', {
  month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
});

function Seeds({ value, update }: { value: string; update: (value: string) => void }) {
  return <div className="field">
    <label htmlFor="arena-seeds">随机种子</label>
    <TextField id="arena-seeds" size="small" value={value} onChange={(e) => update(e.target.value)}
      slotProps={{ htmlInput: { autoComplete: 'off', spellCheck: false, maxLength: 62 } }} fullWidth />
    <small>逗号分隔，填 1–5 个整数。同组种子可直接比较。</small>
  </div>;
}

function Choose({ options, selected, update, label }: { options: number[]; selected: number[]; update: (next: number[]) => void; label: string }) {
  return <div className="field"><span className="field-label">{label}</span>
    <div className="option-chips" role="group" aria-label={label}>{options.map((item) =>
      <button key={item} type="button" aria-pressed={selected.includes(item)}
        onClick={() => update(selected.includes(item) ? selected.filter((x) => x !== item) : [...selected, item].sort((a, b) => a - b))}>{item}</button>)}</div>
  </div>;
}

function ParameterForm({ status, refresh, selectCohort, draft, setDraft }: {
  status: Status | null; refresh: () => void; selectCohort: (value: string) => void;
  draft: Config; setDraft: (next: Config) => void;
}) {
  const [mode, setMode] = useState<'single' | 'grid'>('single');
  const [sizes, setSizes] = useState([16, 32]);
  const [regularization, setRegularization] = useState([0.1, 1]);
  const [seeds, setSeeds] = useState(cohort(draft.seeds));
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const count = mode === 'grid' ? sizes.length * regularization.length : 1;
  async function run() {
    setError(''); setNotice('');
    const pieces = seeds.trim().split(/[\s,，、]+/u);
    const parsed = pieces.map(Number).sort((a, b) => a - b);
    if (pieces.some((token) => !/^\d+$/u.test(token)) || parsed.some((seed) => seed > 2147483647)
      || new Set(parsed).size !== parsed.length || parsed.length > 5) {
      setError('请填写 1–5 个互不重复的非负整数种子，最大值为 2147483647。'); return;
    }
    const configurations = mode === 'single' ? [{ ...draft, seeds: parsed }]
      : sizes.flatMap((size) => regularization.map((alpha) => ({ worlds_per_seed: size, teacher_alpha: alpha, seeds: parsed })));
    setSending(true);
    try {
      await submit('arena', { configurations });
      selectCohort(cohort(parsed));
      refresh();
      setNotice(`已提交 ${configurations.length} 组实验`);
    } catch (error) { setError((error as Error).message); }
    finally { setSending(false); }
  }
  return <section className="surface form-panel">
    <div className="panel-title"><h2><SlidersHorizontal size={19} /> 实验配置</h2></div>
    <div className="segment" role="group" aria-label="运行方式">
      <button type="button" aria-pressed={mode === 'single'} onClick={() => setMode('single')}>单组实验</button>
      <button type="button" aria-pressed={mode === 'grid'} onClick={() => setMode('grid')}>批量组合</button>
    </div>
    {mode === 'single' ? <>
      <div className="field"><label id="world-label">每个种子的世界数</label>
        <FormControl fullWidth size="small"><Select labelId="world-label" value={draft.worlds_per_seed}
          onChange={(e) => setDraft({ ...draft, worlds_per_seed: Number(e.target.value) })} IconComponent={ChevronsUpDown}>
          {worlds.map((size) => <MenuItem key={size} value={size}>{size} 个世界</MenuItem>)}</Select></FormControl>
        <small>每个世界生成 64 区，其中 3/4 世界用于训练。</small></div>
      <div className="field"><label id="alpha-label">校准正则 λ</label>
        <FormControl fullWidth size="small"><Select labelId="alpha-label" value={draft.teacher_alpha}
          onChange={(e) => setDraft({ ...draft, teacher_alpha: Number(e.target.value) })} IconComponent={ChevronsUpDown}>
          {alphas.map((alpha) => <MenuItem key={alpha} value={alpha}>{alpha}</MenuItem>)}</Select></FormControl>
        <small>λ 越大，校准规则的拟合越保守。</small></div>
    </> : <>
      <Choose options={worlds} selected={sizes} update={setSizes} label="每个种子的世界数" />
      <Choose options={alphas} selected={regularization} update={setRegularization} label="校准正则 λ" />
      <p className="grid-count">{sizes.length} 种样本量 × {regularization.length} 个 λ = <strong>{count} 组</strong>。一次最多提交 12 组。</p>
    </>}
    <Seeds value={seeds} update={setSeeds} />
    <Button fullWidth variant="contained" onClick={run} endIcon={<Play size={16} />}
      disabled={sending || !status?.archive_ready || !status.worker_online || count < 1 || count > 12}>
      {sending ? '提交中…' : mode === 'grid' ? `运行 ${count} 组实验` : '运行参数组合'}
    </Button>
    {(!status?.archive_ready || !status.worker_online) && <small className="subtle">需要本地数据和实验进程。</small>}
    <div className="form-feedback" aria-live="polite">{error ? <p className="error-text">{error}</p> : notice && <p>{notice}</p>}</div>
  </section>;
}

function CompareDetail({ run, scale, map, error }: { run: Run; scale?: ReturnType<typeof mapScales>; map?: MapData; error?: string }) {
  const artifact = useArtifact(run.id);
  const metrics = arenaMetrics(run)!;
  const [foldsOpen, setFoldsOpen] = useState(false);
  return <article className="surface panel contender">
    <div className="panel-title"><h3>{configLabel(run.summary!.config)}</h3><span>{seconds(run.summary!.duration_seconds)}</span></div>
    <div className="contender-score">
      <div><span>交叉验证 TV</span><strong>{tv(metrics.mean_tv)}</strong></div>
      <div><span>折间标准差</span><strong>{points(metrics.fold_std)}</strong></div>
      <div><span>种子间标准差</span><strong>{points(metrics.seed_std)}</strong></div>
    </div>
    {map && scale ? <div className="contender-map">
      <TractMap data={map} layer="error" scale={scale} /><MapLegend layer="error" scale={scale} />
    </div> : <p className="subtle">{error ?? '读取逐区误差…'}</p>}
    <div className="seed-dots">{metrics.seed_means.map((item) => <div key={item.seed}><span>种子 {item.seed}</span><strong>{tv(item.tv)}</strong></div>)}</div>
    {artifact?.item?.diagnostics.folds && <div className="fold-detail">
      <Button size="small" aria-expanded={foldsOpen} onClick={() => setFoldsOpen(!foldsOpen)}>{foldsOpen ? '收起' : '查看'} 8 折明细</Button>
      {foldsOpen && <table><thead><tr><th>空间块</th><th>分区</th><th>生成 TV</th><th>真实 TV</th></tr></thead>
        <tbody>{artifact.item.diagnostics.folds.map((fold) => <tr key={fold.block}><th>{fold.block}</th><td>{fold.regions}</td>
          <td>{tv(fold.tv)}</td><td>{tv(fold.teacher_tv)}</td></tr>)}</tbody></table>}
    </div>}
  </article>;
}

function Compare({ runs }: { runs: Run[] }) {
  const left = useArtifact(runs[0]?.id);
  const right = useArtifact(runs[1]?.id);
  const third = useArtifact(runs[2]?.id);
  const artifacts = [left, right, third];
  const maps = artifacts.flatMap((item) => item?.item?.map ? [item.item.map] : []);
  const scale = maps.length ? mapScales(maps) : undefined;
  return <section className="section" aria-labelledby="compare-title">
    <div className="section-heading"><div><h2 id="compare-title">选中配置</h2><p>开发区的份额差异使用同一色阶。暖色高估，冷色低估。地图汇总各折对留出块的估计。</p></div></div>
    <div className={`contenders contenders-${runs.length}`}>
      {runs.map((run, index) => <CompareDetail key={run.id} run={run} scale={scale}
        map={artifacts[index]?.item?.map} error={artifacts[index]?.error} />)}
    </div>
  </section>;
}

export function Arena({ runs, status, refresh }: { runs: Run[]; status: Status | null; refresh: () => void }) {
  const [draft, setDraft] = useState<Config>({ worlds_per_seed: 16, teacher_alpha: 1, seeds: defaultSeeds });
  const [draftKey, setDraftKey] = useState(0);
  const [group, setGroup] = useState(cohort(defaultSeeds));
  const [chosen, setChosen] = useState<string[]>();
  const [cancelError, setCancelError] = useState('');
  const arenaRuns = runs.filter((run) => run.params.mode === 'arena');
  const groups = Array.from(new Set([cohort(defaultSeeds), group, ...arenaRuns.map((run) => cohort(run.params.config!.seeds))]));
  const members = arenaRuns.filter((run) => cohort(run.params.config!.seeds) === group);
  const ranked = useMemo(() => {
    const latest = new Map<string, Run>();
    for (const run of members) {
      if (run.state !== 'succeeded' || !run.summary) continue;
      const key = configLabel(run.summary.config);
      if (!latest.has(key)) latest.set(key, run);
    }
    return Array.from(latest.values()).sort((a, b) => arenaMetrics(a)!.mean_tv - arenaMetrics(b)!.mean_tv);
  }, [members]);
  const selections = chosen ?? ranked.slice(0, 2).map((run) => run.id);
  const compare = ranked.filter((run) => selections.includes(run.id));
  const waiting = members.filter((run) => run.state !== 'succeeded');
  const runningCount = waiting.filter(active).length;
  const baseline = arenaMetrics(ranked[0]);
  function copy(config: Config) {
    setDraft(config); setDraftKey((n) => n + 1);
    document.getElementById('arena-form')?.scrollIntoView({ behavior: 'instant', block: 'nearest' });
  }
  function selectGroup(value: string) { setGroup(value); setChosen(undefined); }
  return <div className="reveal">
    <header className="page-heading arena-heading">
      <div><h1>参数对比</h1><p>调整虚拟样本量和校准规则，比较同一空间验证协议下的结果。</p></div>
      <span className="protocol-tag"><Layers3 size={17} /> 364 个开发区 · 8 折</span>
    </header>
    <div className="arena-layout">
      <div id="arena-form"><ParameterForm key={draftKey} status={status} refresh={refresh} selectCohort={selectGroup} draft={draft} setDraft={setDraft} /></div>
      <section className="surface ranking">
        <div className="panel-title ranking-title"><div><h2><ChartNoAxesColumnIncreasing size={19} /> 配置排行</h2>
          <p className="subtle">按空间折平均 TV 排序，越低越好。</p></div>
          <FormControl size="small"><Select inputProps={{ 'aria-label': '排行随机种子组' }} value={group}
            onChange={(e) => selectGroup(e.target.value)}>{groups.map((value) => <MenuItem value={value} key={value}>种子 {value}</MenuItem>)}</Select></FormControl>
        </div>
        {baseline && <div className="benchmark-line">就业密度基线 <strong>{tv(baseline.employment_mean_tv)}</strong><span>勾选最多 3 个配置比较</span></div>}
        {ranked.length > 0 ? <div className="table-scroll"><table className="rank-table">
          <thead><tr><th>比较</th><th>配置</th><th>平均 TV <ArrowDown size={13} /></th><th>折间</th><th>种子间</th><th><Timer size={14} /> 用时</th><th>复制</th></tr></thead>
          <tbody>{ranked.map((run, index) => {
            const isSelected = selections.includes(run.id);
            const metrics = arenaMetrics(run)!;
            return <tr key={run.id} className={isSelected ? 'selected-row' : ''}>
              <td><Checkbox size="small" checked={isSelected} disabled={!isSelected && selections.length >= 3} onChange={() =>
                setChosen(isSelected ? selections.filter((id) => id !== run.id) : [...selections, run.id])}
                slotProps={{ input: { 'aria-label': `比较配置 ${configLabel(run.summary!.config)}` } }} /></td>
              <th><span className="rank-number">{index + 1}</span>{configLabel(run.summary!.config)}
                <small>{number(run.summary!.config.worlds_per_seed * 64)} 生成区/种子/折</small></th>
              <td className="rank-score">{tv(metrics.mean_tv)}</td><td>{points(metrics.fold_std)}</td><td>{points(metrics.seed_std)}</td>
              <td>{seconds(run.summary!.duration_seconds)}</td><td><button className="icon-button" type="button"
                aria-label={`复制配置 ${configLabel(run.summary!.config)}`} title="复制到单组表单" onClick={() => copy(run.summary!.config)}><Copy size={16} /></button></td>
            </tr>;
          })}</tbody></table></div> : <div className="empty-ranked">
          <span><FlaskConical size={25} /></span><h3>{runningCount ? '正在计算首批结果' : '这一组种子还没有成绩'}</h3>
          <p>{runningCount ? '每组完成后会自动进入排行。' : '从左侧提交一次完整的空间交叉验证。'}</p></div>}
        <div className="queue" aria-live="polite">
          <div className="queue-title"><strong>运行队列</strong><span>{runningCount ? `${runningCount} 组待完成` : '空闲'}</span></div>
          {waiting.slice(0, Math.max(8, runningCount)).map((run) => <div className="queue-item" key={run.id}>
            <div><strong>{configLabel(run.params.config!)}</strong><small>{run.error?.message ?? (active(run) && run.message ? run.message : date(run.created_at))}</small></div>
            <div><span className={`status-pill ${run.state}`}><i />{stateText[run.state]}</span>
              {active(run) && run.state !== 'cancelling' && <Button size="small" onClick={() => stop(run.id).then(refresh).catch((error) => setCancelError(error.message))}>停止</Button>}</div>
            {run.state === 'running' && <progress max={1} value={run.progress} aria-label="配置运行进度" />}
          </div>)}
          {cancelError && <p role="alert" className="error-text">{cancelError}</p>}
        </div>
      </section>
    </div>
    {compare.length > 0 && <Compare runs={compare} />}
    <div className="arena-footnote"><Layers3 size={17} /><p>每折重新拟合校准规则。折间波动反映空间差异，种子间波动反映模拟随机性。431 个历史留出区用于空间验证页的最终对照。</p></div>
  </div>;
}
