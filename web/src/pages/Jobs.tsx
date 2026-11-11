import { ArrowRight, Ban } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { api, streamJobEvents, type Job, type JobEvent } from '../api';
import { LearningCurves } from '../components/LearningCurves';
import { Badge, Card, Drawer, Empty, ErrorNote, KV, Loading, Note, Progress, Seg } from '../components/ui';
import { ago, duration, JOB_KIND, JOB_STATE, time } from '../format';
import { useApp, useQuery } from '../store';

const ACTIVE = new Set(['queued', 'running', 'cancelling']);
const localTz = Intl.DateTimeFormat().resolvedOptions().timeZone;

function StateBadge({ state }: { state: string }) {
  const s = JOB_STATE[state] ?? { label: state, tone: '' };
  return (
    <Badge tone={s.tone} dot>
      {s.label}
    </Badge>
  );
}
function elapsed(job: Job) {
  if (!job.started_at) return null;
  const end = job.finished_at ? Date.parse(job.finished_at) : Date.now();
  return (end - Date.parse(job.started_at)) / 1000;
}
function target(app: ReturnType<typeof useApp>, job: Job) {
  if (job.kind === 'train') return `数据集 ${String(job.params?.dataset_id ?? '').slice(0, 10)}`;
  const world = app.worlds.find((w) => w.id === job.params?.world_id);
  return `${world?.name ?? job.params?.world_id ?? ''}${job.kind === 'simulate' ? ` · ${job.params?.days} 天` : ''}`;
}

export function JobsPage() {
  const app = useApp();
  const [filter, setFilter] = useState<'all' | 'active' | 'done' | 'failed'>('all');
  const jobs = app.jobs.filter((j) => (filter === 'all' ? true : filter === 'active' ? ACTIVE.has(j.state) : filter === 'done' ? j.state === 'succeeded' : j.state === 'failed' || j.state === 'cancelled'));
  const counts = app.system?.job_counts ?? {};

  return (
    <div className="page">
      {app.system && app.system.worker_online === false && (
        <Note tone="warn">
          没有在线 worker：排队任务不会被执行。请在后端目录运行 <span className="mono">ridecast worker</span>。
        </Note>
      )}
      <Card
        title="任务"
        sub={`排队 ${counts.queued ?? 0} · 运行 ${counts.running ?? 0} · 完成 ${counts.succeeded ?? 0} · 失败 ${counts.failed ?? 0}`}
        flush
        actions={<Seg value={filter} onChange={setFilter} options={[{ value: 'all', label: '全部' }, { value: 'active', label: '进行中' }, { value: 'done', label: '已完成' }, { value: 'failed', label: '失败/取消' }]} />}
      >
        {!app.loaded ? (
          <Loading height={200} />
        ) : jobs.length === 0 ? (
          <Empty title="没有任务">仿真、导入与训练都会作为持久任务出现在这里，API 重启不会丢失排队任务。</Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>类型</th>
                  <th>状态</th>
                  <th style={{ width: 180 }}>进度</th>
                  <th>阶段</th>
                  <th>对象</th>
                  <th>提交</th>
                  <th className="num">耗时</th>
                  <th>任务号</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((j) => (
                  <tr key={j.id} className={`click ${app.routeArg === j.id ? 'sel' : ''}`} onClick={() => app.go('jobs', j.id)}>
                    <td>{JOB_KIND[j.kind] ?? j.kind}</td>
                    <td><StateBadge state={j.state} /></td>
                    <td>
                      <div className="row">
                        <div className="grow">
                          <Progress value={j.progress} tone={j.state === 'succeeded' ? 'done' : j.state === 'failed' ? 'red' : ''} indeterminate={j.state === 'queued'} />
                        </div>
                        <span className="small dim" style={{ width: 34, textAlign: 'right' }}>{Math.round(j.progress * 100)}%</span>
                      </div>
                    </td>
                    <td className="dim ellipsis" style={{ maxWidth: 260 }}>{j.state === 'failed' ? j.error?.message : j.message ?? j.stage ?? '—'}</td>
                    <td className="dim">{target(app, j)}</td>
                    <td className="dim">{ago(j.created_at)}</td>
                    <td className="num">{duration(elapsed(j))}</td>
                    <td className="mono muted">{j.id.slice(0, 10)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {app.routeArg && <JobDrawer key={app.routeArg} jobId={app.routeArg} onClose={() => app.go('jobs')} />}
    </div>
  );
}

function JobDrawer({ jobId, onClose }: { jobId: string; onClose: () => void }) {
  const app = useApp();
  const fetched = useQuery(`job:${jobId}`, () => api.job(jobId), { cache: false });
  const [patch, setPatch] = useState<Partial<Job>>({});
  const [events, setEvents] = useState<JobEvent[]>([]);
  const logRef = useRef<HTMLDivElement>(null);
  const job: Job | undefined = fetched.data ? { ...fetched.data, ...patch } : undefined;
  const live = job ? ACTIVE.has(job.state) : false;

  // 事件 payload 是局部补丁：合并而不是覆盖；终态后重新 GET 完整任务
  useEffect(() => {
    const stop = streamJobEvents(jobId, {
      event: (e) => {
        setEvents((list) => (list.some((x) => x.id === e.id) ? list : [...list, e]));
        if (e.event_type === 'progress' || e.event_type === 'state') setPatch((p) => ({ ...p, ...e.payload }));
        // state 补丁不含 started_at / worker_id 等字段，状态切换时重取完整任务
        if (e.event_type === 'state') fetched.reload();
      },
      done: () => {
        setPatch({});
        fetched.reload();
        app.refresh();
      },
    });
    return stop;
  }, [jobId]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [events.length]);

  const history = useQuery(job?.kind === 'train' ? `jobhist:${jobId}` : null, () => api.jobHistory(jobId), { cache: false, interval: live ? 1500 : undefined });
  useEffect(() => {
    if (!live) history.reload();
  }, [live]); // eslint-disable-line react-hooks/exhaustive-deps

  const [cancelling, setCancelling] = useState(false);
  const cancel = async () => {
    setCancelling(true);
    try {
      await api.cancelJob(jobId);
      fetched.reload();
      app.refresh();
    } catch (e) {
      app.toast((e as Error).message, 'err');
    } finally {
      setCancelling(false);
    }
  };

  return (
    <Drawer
      title={job ? `${JOB_KIND[job.kind]} · ${job.id.slice(0, 10)}` : '任务'}
      onClose={onClose}
      actions={
        job && live ? (
          <button className="btn sm danger" onClick={cancel} disabled={cancelling || job.state === 'cancelling'}>
            <Ban size={13} />
            {job.state === 'cancelling' ? '取消中' : '取消任务'}
          </button>
        ) : null
      }
    >
      {fetched.error ? (
        <ErrorNote error={fetched.error} />
      ) : !job ? (
        <Loading height={200} />
      ) : (
        <>
          <div className="col" style={{ gap: 6 }}>
            <div className="row">
              <StateBadge state={job.state} />
              <span className="dim ellipsis grow">{job.message ?? job.stage ?? (job.state === 'queued' ? '等待 worker 认领' : '')}</span>
              <b>{Math.round(job.progress * 100)}%</b>
            </div>
            <Progress value={job.progress} tone={job.state === 'succeeded' ? 'done' : job.state === 'failed' ? 'red' : ''} indeterminate={job.state === 'queued'} />
          </div>
          {job.state === 'queued' && app.system?.worker_online === false && <Note tone="warn">当前没有在线 worker，请启动 <span className="mono">ridecast worker</span>。</Note>}
          {job.state === 'cancelling' && <Note>取消是合作式的：会在下一个进度检查点停止，当前训练轮结束前可能仍在计算。</Note>}
          {job.error && (
            <Note tone="err">
              <div><b>{job.error.code ?? '错误'}</b> {job.error.type ? `· ${job.error.type}` : ''}</div>
              <div>{job.error.message}</div>
              <div className="small muted">终态不会自动重试，可用相同参数重新提交。</div>
            </Note>
          )}
          {job.result && (
            <button
              className="btn primary"
              style={{ alignSelf: 'flex-start' }}
              onClick={() => {
                if (job.result!.resource_type === 'model') app.go('models', job.result!.resource_id);
                else {
                  if (job.params?.world_id) app.setWorld(job.params.world_id);
                  app.setDataset(job.result!.resource_id);
                  app.go('dashboard');
                }
              }}
            >
              查看{job.result.resource_type === 'model' ? '模型' : '数据集'} <span className="mono">{job.result.resource_id.slice(0, 10)}</span> <ArrowRight size={14} />
            </button>
          )}

          {job.kind === 'train' && (
            <div className="col">
              <div className="label">学习曲线{live ? '（实时）' : ''}</div>
              {history.data ? <LearningCurves history={history.data} height={180} /> : <Loading height={120} />}
            </div>
          )}

          <KV
            items={[
              ['任务号', <span className="mono">{job.id}</span>],
              ['提交', time.full(job.created_at, localTz)],
              ['开始', time.full(job.started_at, localTz)],
              ['结束', time.full(job.finished_at, localTz)],
              ['耗时', duration(elapsed(job))],
              ['Worker', <span className="mono">{job.worker_id ?? '—'}</span>],
            ]}
          />

          <div className="col">
            <div className="label">事件日志（持久事件流{live ? ' · SSE 实时' : ''}）</div>
            <div className="log" ref={logRef}>
              {events.length === 0 && <span className="muted">暂无事件</span>}
              {events.map((e) => (
                <div key={e.id}>
                  <span className="ts">{time.hms(e.created_at, localTz)}</span>
                  {e.event_type === 'progress' ? (
                    <>
                      <span style={{ color: 'var(--accent)' }}>{String(Math.round((e.payload.progress ?? 0) * 100)).padStart(3, ' ')}%</span>{' '}
                      {e.payload.stage ? `[${e.payload.stage}] ` : ''}
                      {e.payload.message ?? ''}
                    </>
                  ) : (
                    <>
                      <span style={{ color: e.payload.state === 'failed' ? 'var(--red)' : 'var(--green)' }}>{e.event_type}</span>{' '}
                      {e.payload.state ?? ''} {e.payload.error?.message ?? e.payload.message ?? ''}
                    </>
                  )}
                </div>
              ))}
            </div>
          </div>

          <div className="col">
            <div className="label">参数</div>
            <pre className="log" style={{ margin: 0 }}>{JSON.stringify(job.params, null, 2)}</pre>
          </div>
        </>
      )}
    </Drawer>
  );
}
