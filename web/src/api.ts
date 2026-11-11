// RideCast HTTP 客户端：REST + 基于 fetch 的 SSE（可携带 X-API-Key，EventSource 做不到）。
// 聚合接口是动态字典，这里只给稳定封装结构定类型，其余按 docs/FRONTEND.md 字段读取。

/* eslint-disable @typescript-eslint/no-explicit-any */
export type Any = any;

export interface Resource {
  id: string;
  kind: 'world' | 'dataset' | 'model';
  name: string;
  status: string;
  metadata: Any;
  created_at: string;
}
export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}
export type JobState = 'queued' | 'running' | 'cancelling' | 'succeeded' | 'failed' | 'cancelled';
export interface Job {
  id: string;
  kind: 'simulate' | 'import' | 'train';
  state: JobState;
  progress: number;
  stage: string | null;
  message: string | null;
  params: Any;
  result: { resource_type: string; resource_id: string } | null;
  error: { code?: string; message?: string; type?: string } | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  heartbeat_at: string | null;
  worker_id: string | null;
}
export interface JobEvent {
  id: number;
  job_id: string;
  event_type: string;
  payload: Any;
  created_at: string;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public code: string,
    public requestId?: string | null,
    public details?: unknown,
  ) {
    super(message);
  }
}

const LS_BASE = 'rc.apiBase';
const LS_KEY = 'rc.apiKey';

export const connection = {
  get base() {
    return (localStorage.getItem(LS_BASE) ?? '').replace(/\/+$/, '');
  },
  get key() {
    return localStorage.getItem(LS_KEY) ?? '';
  },
  save(base: string, key: string) {
    localStorage.setItem(LS_BASE, base.trim());
    if (key) localStorage.setItem(LS_KEY, key);
    else localStorage.removeItem(LS_KEY);
  },
};

type Query = Record<string, string | number | boolean | null | undefined>;

function url(path: string, query?: Query) {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(query ?? {})) if (v !== undefined && v !== null && v !== '') qs.set(k, String(v));
  const s = qs.toString();
  return `${connection.base}${path}${s ? `?${s}` : ''}`;
}

function headers(extra?: Record<string, string>) {
  const h: Record<string, string> = { ...extra };
  if (connection.key) h['X-API-Key'] = connection.key;
  return h;
}

async function request<T = Any>(
  method: string,
  path: string,
  opts: { query?: Query; body?: unknown; form?: FormData; signal?: AbortSignal } = {},
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url(path, opts.query), {
      method,
      signal: opts.signal,
      headers: headers(opts.body !== undefined ? { 'Content-Type': 'application/json' } : undefined),
      body: opts.form ?? (opts.body !== undefined ? JSON.stringify(opts.body) : undefined),
    });
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e;
    throw new ApiError('无法连接后端，请确认 ridecast serve 已启动', 0, 'network_error');
  }
  const requestId = response.headers.get('X-Request-ID');
  const text = await response.text();
  let data: Any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    /* 代理报错时可能返回非 JSON */
  }
  if (!response.ok) {
    const err = data?.error;
    const fallback = response.status >= 500 && !err ? '后端不可达或内部错误' : response.statusText;
    throw new ApiError(err?.message ?? fallback, response.status, err?.code ?? 'http_error', requestId, err?.details);
  }
  return data as T;
}

const get = <T = Any>(path: string, query?: Query, signal?: AbortSignal) => request<T>('GET', path, { query, signal });
const post = <T = Any>(path: string, body?: unknown) => request<T>('POST', path, { body: body ?? {} });
const v1 = '/api/v1';

export const api = {
  health: () => get('/health'),
  system: () => get(`${v1}/system`),

  worlds: () => get<Page<Resource>>(`${v1}/worlds`, { limit: 1000 }),
  createWorld: (body: Any) => post<Resource>(`${v1}/worlds`, body),
  worldMap: (id: string) => get(`${v1}/worlds/${id}/map`),
  worldState: (id: string, timestamp: string, seed: number, signal?: AbortSignal) =>
    get(`${v1}/worlds/${id}/state`, { timestamp, seed }, signal),
  activeModel: (worldId: string) => get(`${v1}/worlds/${worldId}/active-model`),

  simulate: (body: Any) => post<Job>(`${v1}/simulations`, body),
  importDataset: (form: FormData) => request<Job>('POST', `${v1}/datasets/import`, { form }),
  datasets: () => get<Page<Resource>>(`${v1}/datasets`, { limit: 1000 }),
  overview: (id: string) => get(`${v1}/datasets/${id}/overview`),
  timeseries: (id: string, q: Query = {}) => get(`${v1}/datasets/${id}/timeseries`, q),
  heatmap: (id: string, timestamp?: string) => get(`${v1}/datasets/${id}/heatmap`, { timestamp }),
  flows: (id: string, q: Query = {}, signal?: AbortSignal) => get(`${v1}/datasets/${id}/flows`, q, signal),
  replay: (id: string, timestamp: string, limit = 1000, signal?: AbortSignal) =>
    get(`${v1}/datasets/${id}/replay`, { timestamp, limit }, signal),
  orders: (id: string, q: Query = {}) => get(`${v1}/datasets/${id}/orders`, q),

  train: (body: Any) => post<Job>(`${v1}/training-jobs`, body),
  models: () => get<Page<Resource>>(`${v1}/models`, { limit: 1000 }),
  modelHistory: (id: string) => get(`${v1}/models/${id}/history`),
  evaluation: (id: string, offset = 0, limit = 10000) => get(`${v1}/models/${id}/evaluation`, { offset, limit }),
  activate: (id: string) => post(`${v1}/models/${id}/activate`),
  forecast: (body: Any) => post(`${v1}/forecasts`, body),
  dispatch: (body: Any) => post(`${v1}/dispatch-suggestions`, body),

  jobs: (limit = 200) => get<Page<Job>>(`${v1}/jobs`, { limit }),
  job: (id: string) => get<Job>(`${v1}/jobs/${id}`),
  cancelJob: (id: string) => request<Job>('DELETE', `${v1}/jobs/${id}`),
  jobHistory: (id: string) => get(`${v1}/jobs/${id}/history`),
};

/**
 * 读取持久任务事件流。终态收到 `done` 后主动结束，不自动重连；
 * 网络中断时用最后一个事件 id 通过 ?after= 续读。
 */
export function streamJobEvents(
  jobId: string,
  on: { event: (e: JobEvent) => void; done: (state: JobState) => void; error?: (e: unknown) => void },
): () => void {
  const controller = new AbortController();
  let cursor = 0;
  let finished = false;

  const run = async () => {
    while (!controller.signal.aborted && !finished) {
      try {
        const response = await fetch(url(`${v1}/jobs/${jobId}/events`, { after: cursor }), {
          headers: headers({ Accept: 'text/event-stream' }),
          signal: controller.signal,
        });
        if (!response.ok || !response.body) throw new ApiError('事件流不可用', response.status, 'sse_error');
        const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
        let buffer = '';
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += value.replace(/\r\n/g, '\n');
          let split: number;
          while ((split = buffer.indexOf('\n\n')) >= 0) {
            const block = buffer.slice(0, split);
            buffer = buffer.slice(split + 2);
            let type = 'message';
            let data = '';
            for (const line of block.split('\n')) {
              if (line.startsWith(':')) continue; // keepalive 注释
              if (line.startsWith('event:')) type = line.slice(6).trim();
              else if (line.startsWith('data:')) data += line.slice(5).trim();
              else if (line.startsWith('id:')) cursor = Number(line.slice(3).trim()) || cursor;
            }
            if (!data) continue;
            const parsed = JSON.parse(data);
            if (type === 'done') {
              finished = true;
              on.done(parsed.state);
              controller.abort();
              return;
            }
            on.event(parsed as JobEvent);
          }
        }
      } catch (e) {
        if (controller.signal.aborted) return;
        on.error?.(e);
      }
      await new Promise((r) => setTimeout(r, 1500));
    }
  };
  void run();
  return () => controller.abort();
}
