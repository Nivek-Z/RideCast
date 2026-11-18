import { ArrowDown, ArrowUpRight, Check, Database, FlaskConical, Layers3, Play, Route, ScanLine } from 'lucide-react';
import { Button, LinearProgress } from '@mui/material';
import { useState } from 'react';
import type { ResearchSnapshot, Study } from '../research/types';
import { stop, submit, useArtifact } from './data';
import { MapAtlas } from './Map';
import { active, number, seconds, stateText, tv, type FormalMetrics, type Run, type Status } from './types';

const steps = [
  { id: 'data', name: '划分真实数据', evidence: '364 区开发 · 431 区对照', icon: Database },
  { id: 'calibration', name: '校准虚拟世界', evidence: '15 项区域属性 · 平滑关系', icon: Route },
  { id: 'generation', name: '生成虚拟区域', evidence: '192 个世界 · 12,288 个区', icon: Layers3 },
  { id: 'training', name: '训练学生模型', evidence: '9,216 个生成区用于拟合', icon: FlaskConical },
  { id: 'evaluation', name: '评估空间分布', evidence: '431 个区 · 空间 TV', icon: ScanLine },
];

const resultNotes: Record<string, string> = {
  original: '虚构城市的原始生成规则',
  employment: '按就业密度分配',
  teacher: '364 个真实开发区拟合',
  student: '9,216 个生成区域拟合',
};

export function ComparisonBars({ study, compact = false }: { study: Study; compact?: boolean }) {
  const order = compact ? ['original', 'student'] : ['original', 'employment', 'teacher', 'student'];
  const max = 60;
  return <div className={`bars ${compact ? 'bars-compact' : ''}`}>
    {order.map((id) => {
      const row = study.methods.find((entry) => entry.id === id);
      if (!row || row.test.tv == null) return null;
      return <div className={`bar-row bar-${id}`} key={id}>
        <div className="bar-heading"><div><strong>{row.label}</strong>{!compact && <small>{resultNotes[id]}</small>}</div>
          <span>{tv(row.test.tv)}</span></div>
        <div className="bar-track"><i style={{ width: `${row.test.tv / max * 100}%` }} /></div>
      </div>;
    })}
    {!compact && <div className="bar-axis">{[0, 15, 30, 45, 60].map((tick) => <span key={tick}>{tick}%</span>)}</div>}
  </div>;
}

function RunStages({ run }: { run?: Run }) {
  const current = run?.stage === 'saving' ? steps.length : steps.findIndex((step) => step.id === run?.stage);
  const stageTimings = run?.summary?.steps ?? [];
  return <ol className="pipeline">
    {steps.map((step, i) => {
      const done = run?.state === 'succeeded' || current > i;
      const running = run?.state === 'running' && current === i;
      const start = stageTimings.find((item) => item.stage === step.id);
      const end = stageTimings.find((item) => item.stage === steps[i + 1]?.id);
      const duration = start && (end?.elapsed_seconds ?? run?.summary?.duration_seconds);
      return <li key={step.id} className={done ? 'step done' : running ? 'step running' : 'step'}>
        <div className="step-rail"><span className="step-icon">{done ? <Check size={19} /> : <step.icon size={20} />}</span></div>
        <strong>{step.name}</strong><small>{step.evidence}</small>
        {duration != null && start && <time>{seconds(Math.max(0, duration - start.elapsed_seconds))}</time>}
      </li>;
    })}
  </ol>;
}

export function Defense({ snapshot, runs, status, refresh }: {
  snapshot?: ResearchSnapshot; runs: Run[]; status: Status | null; refresh: () => void;
}) {
  const [pending, setPending] = useState(false);
  const [actionError, setActionError] = useState('');
  const [submitted, setSubmitted] = useState<Run>();
  const [hideAtlas, setHideAtlas] = useState(false);
  const formal = runs.filter((run) => run.params.mode === 'reproduction');
  const latest = formal[0] ?? submitted;
  const live = submitted && !formal.some((run) => run.id === submitted.id) ? submitted : latest;
  const completed = formal.find((run) => run.state === 'succeeded');
  const artifact = useArtifact(completed?.id);
  const actual = completed?.summary?.metrics as FormalMetrics | undefined;
  const study = snapshot?.studies[0];
  const original = study?.methods.find((method) => method.id === 'original')?.test.tv;
  const before = study?.methods.find((method) => method.id === 'student')?.test.tv;
  const teacher = study?.methods.find((method) => method.id === 'teacher')?.test.tv;

  async function launch() {
    setActionError(''); setPending(true);
    try { setSubmitted(await submit('reproduce') as Run); refresh(); }
    catch (error) { setActionError((error as Error).message); }
    finally { setPending(false); }
  }

  return <>
    <section className="hero reveal">
      <div className="hero-copy">
        <h1>芝加哥累计行程份额估计</h1>
        <p className="hero-description">Chicago TNP · 795 个普查区 · 统计期和时间戳未提供</p>
        <div className="hero-outcome">
          <div><span>生成数据训练</span><b className="accent">{tv(before)}</b></div>
          <div><span>真实数据直接训练</span><b>{tv(teacher)}</b></div>
        </div>
        <p className="subtle metric-note">空间分布差异（TV），越低越好。两种方法均在相同的 431 个区域评分。</p>
        <div className="hero-actions">
          <Button variant="contained" endIcon={<Play size={17} />} onClick={launch}
            disabled={!status?.worker_online || !status.archive_ready || pending || !!(live && active(live))}>
            {pending || (live && active(live)) ? '实验运行中' : '一键运行实验'}
          </Button>
          <Button href="#results" variant="text" endIcon={<ArrowDown size={17} />}>查看对比</Button>
        </div>
        {!status?.archive_ready && <p className="subtle service-note">{status ? '配置本地 Chicago TNP ZIP 后即可运行。' : '连接本地研究服务后即可运行。'}</p>}
        {status?.archive_ready && !status.worker_online && <p className="subtle service-note">等待本地实验进程连接。</p>}
        {actionError && <p className="error-text" role="alert">{actionError}</p>}
      </div>
      <div className="hero-map">
        <div className="visual-title"><h2>普查区地图</h2></div>
        {artifact?.item ? <MapAtlas data={artifact.item.map} preview />
          : <div className="hero-fallback">
            <div className="split-strip"><i /><i /></div>
            <div className="split-caption"><span>364 个开发区</span><span>431 个留出区</span></div>
            {study && <ComparisonBars study={study} compact />}
            <p>{artifact?.error ?? (completed ? '正在读取真实分区几何…' : '完成一次本地实验后，可查看真实分区地图。')}</p>
          </div>}
      </div>
    </section>

    <section className="section reveal delay-one" aria-labelledby="pipeline-title">
      <div className="section-heading">
        <div><h2 id="pipeline-title">实验流程</h2><p>真实数据用于拟合生成规则，学生模型的系数从生成数据中学习。</p></div>
        {live && <div className={`status-pill ${live.state}`} role="status"><i />{stateText[live.state]}
          {live.state === 'succeeded' && live.summary && ` · ${seconds(live.summary.duration_seconds)}`}</div>}
      </div>
      <div className="surface pipeline-panel">
        <RunStages run={live} />
        <div className="run-line" aria-live="polite">
          {live ? <>
            {active(live) && <LinearProgress variant="determinate" value={live.progress * 100} aria-label="实验进度" />}
            <div><span>{live.error?.message ?? (live.state === 'succeeded' ? '本次运行的地图和评分已保存。' :
              live.state === 'cancelled' ? '本次实验已停止。' : (live.message || '实验已进入队列。'))}</span>
              {active(live) && live.state !== 'cancelling' && <Button size="small" onClick={() => stop(live.id).then(refresh).catch((error) => setActionError(error.message))}>停止</Button>}</div>
          </> : <span>使用固定空间划分和种子 7、42、99。本地运行从读取 Chicago TNP 原始 ZIP 开始。</span>}
        </div>
      </div>
    </section>

    <section className="section reveal delay-two" id="results" aria-labelledby="results-title">
      <div className="section-heading"><div><h2 id="results-title">留出区结果</h2><p>431 个空间留出区 · 累计行程份额 · 探索性对照</p></div>
        <Button href="#evidence" endIcon={<ArrowUpRight size={17} />}>研究记录</Button></div>
      <div className="results-layout">
        <div className="surface score-panel">
          <div className="panel-title"><h3>四种方法的空间 TV</h3><span>历史实验</span></div>
          {study && <ComparisonBars study={study} />}
        </div>
        <div className="surface finding">
          <h3>校准改善了模拟</h3>
          {original != null && before != null && <div className="finding-value">{(original-before).toFixed(2)}<small> 个百分点</small></div>}
          <p>这是相对于原始未校准路线的空间 TV 降幅，两条路线的模型和生成方式也不同。</p>
          <hr />
          <h3>与真实数据直接训练接近</h3>
          {teacher != null && before != null && <p>生成训练与直接用真实数据训练相差 <strong>{Math.abs(teacher-before).toFixed(2)} 个百分点</strong>，现有证据不足以证明生成训练提高了份额估计的精度。</p>}
          <p className="subtle">观测份额由留出区原始累计 TripCount 计算。</p>
        </div>
      </div>
      {actual && completed?.summary && <div key={completed.id} className="surface live-result" aria-label="最近一次复现">
        <div className="panel-title"><div><h3>最近一次复现</h3><p className="subtle">种子 {completed.summary.config.seeds.join(' / ')} · 拟合 {number(actual.train_regions)} 区 · 合成验证 {number(actual.validation_regions)} 区</p></div>
          <span>{seconds(completed.summary.duration_seconds)}</span></div>
        <div className="run-metrics">
          <div><small>生成训练 TV</small><strong>{tv(actual.student.total_variation_percent)}</strong></div>
          <div><small>真实训练 TV</small><strong>{tv(actual.teacher.total_variation_percent)}</strong></div>
          <div><small>就业密度 TV</small><strong>{tv(actual.employment.total_variation_percent)}</strong></div>
          <div><small>历史生成训练 TV</small><strong>{tv(before)}</strong></div>
        </div>
        {artifact?.item && <div className="map-details">
          <Button onClick={() => setHideAtlas(!hideAtlas)} aria-expanded={!hideAtlas}>{hideAtlas ? '展开逐区地图' : '收起逐区地图'}</Button>
          {!hideAtlas && <MapAtlas data={artifact.item.map} />}
        </div>}
      </div>}
    </section>
  </>;
}
