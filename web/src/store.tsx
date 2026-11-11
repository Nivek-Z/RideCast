import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { api, type Any, type Job, type Resource } from './api';

/* ---------- 轻量数据获取 ---------- */
const memo = new Map<string, unknown>();

export interface QueryResult<T> {
  data: T | undefined;
  error: Error | null;
  loading: boolean;
  reload: () => void;
}

/**
 * key 为 null 时不请求。相同 key 的结果进内存缓存（后端资源版本不可变，可按 ID 缓存）；
 * key 变化期间保留上一份数据，避免图表闪烁。
 */
export function useQuery<T = Any>(
  key: string | null,
  fn: (signal: AbortSignal) => Promise<T>,
  opts: { interval?: number; cache?: boolean } = {},
): QueryResult<T> {
  const useCache = opts.cache !== false;
  const [state, setState] = useState<{ key: string | null; data: T | undefined; error: Error | null; loading: boolean }>(
    () => ({ key, data: key && useCache ? (memo.get(key) as T | undefined) : undefined, error: null, loading: !!key }),
  );
  const [tick, setTick] = useState(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    if (!key) {
      setState({ key, data: undefined, error: null, loading: false });
      return;
    }
    const controller = new AbortController();
    const cached = useCache ? (memo.get(key) as T | undefined) : undefined;
    setState((s) => ({ key, data: cached ?? s.data, error: null, loading: true }));
    fnRef.current(controller.signal).then(
      (data) => {
        if (controller.signal.aborted) return;
        if (useCache) {
          memo.delete(key);
          memo.set(key, data);
          // 回放逐桶缓存会持续增长，按插入顺序淘汰最旧条目
          if (memo.size > 400) memo.delete(memo.keys().next().value as string);
        }
        setState({ key, data, error: null, loading: false });
      },
      (error) => {
        if (controller.signal.aborted || error?.name === 'AbortError') return;
        setState((s) => ({ key, data: s.key === key ? s.data : undefined, error, loading: false }));
      },
    );
    return () => controller.abort();
  }, [key, tick, useCache]);

  useEffect(() => {
    if (!opts.interval || !key) return;
    const id = setInterval(() => !document.hidden && setTick((t) => t + 1), opts.interval);
    return () => clearInterval(id);
  }, [opts.interval, key]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data: state.data, error: state.error, loading: state.loading, reload };
}

export function useLocal<T>(key: string, initial: T): [T, (v: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw === null ? initial : (JSON.parse(raw) as T);
    } catch {
      return initial;
    }
  });
  const set = useCallback(
    (v: T) => {
      setValue(v);
      try {
        localStorage.setItem(key, JSON.stringify(v));
      } catch {
        /* 隐私模式下忽略 */
      }
    },
    [key],
  );
  return [value, set];
}

/* ---------- 主题 ---------- */
export type ThemeMode = 'auto' | 'light' | 'dark';
export function useTheme(): [ThemeMode, (m: ThemeMode) => void] {
  const [mode, setMode] = useState<ThemeMode>(() => (localStorage.getItem('rc.theme') as ThemeMode) || 'auto');
  useEffect(() => {
    const media = matchMedia('(prefers-color-scheme: dark)');
    const apply = () => {
      const dark = mode === 'dark' || (mode === 'auto' && media.matches);
      document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    };
    apply();
    localStorage.setItem('rc.theme', mode);
    media.addEventListener('change', apply);
    return () => media.removeEventListener('change', apply);
  }, [mode]);
  return [mode, setMode];
}

/* ---------- 全局应用状态 ---------- */
export type Route = 'dashboard' | 'map' | 'orders' | 'models' | 'forecast' | 'jobs' | 'system';
export type Dialog = null | 'world' | 'simulate' | 'import' | 'train';
interface Toast {
  id: number;
  text: string;
  tone: 'info' | 'ok' | 'err';
}

interface AppState {
  system: Any | undefined;
  online: boolean; // API 可达
  worlds: Resource[];
  datasets: Resource[]; // 全部数据集
  models: Resource[];
  jobs: Job[];
  loaded: boolean;
  world: Resource | undefined;
  dataset: Resource | undefined; // 当前世界下选中的数据集
  worldDatasets: Resource[];
  worldModels: Resource[];
  tz: string;
  bucketMinutes: number;
  setWorld: (id: string) => void;
  setDataset: (id: string) => void;
  refresh: () => void;
  route: Route;
  routeArg: string | undefined;
  go: (route: Route, arg?: string) => void;
  dialog: Dialog;
  openDialog: (d: Dialog) => void;
  toast: (text: string, tone?: Toast['tone']) => void;
  zoneName: (zoneId: string | null | undefined) => string;
}

const Ctx = createContext<AppState>(null as unknown as AppState);
export const useApp = () => useContext(Ctx);

const ROUTES: Route[] = ['dashboard', 'map', 'orders', 'models', 'forecast', 'jobs', 'system'];
function parseHash(): { route: Route; arg?: string } {
  const [, route, arg] = location.hash.split('/');
  return { route: ROUTES.includes(route as Route) ? (route as Route) : 'dashboard', arg };
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [nav, setNav] = useState(parseHash);
  useEffect(() => {
    const on = () => setNav(parseHash());
    addEventListener('hashchange', on);
    return () => removeEventListener('hashchange', on);
  }, []);
  const go = useCallback((route: Route, arg?: string) => {
    location.hash = `#/${route}${arg ? `/${arg}` : ''}`;
  }, []);

  const system = useQuery('system', () => api.system(), { interval: 5000 });
  const worlds = useQuery('worlds', () => api.worlds(), { interval: 30000 });
  const datasets = useQuery('datasets', () => api.datasets(), { interval: 30000 });
  const models = useQuery('models', () => api.models(), { interval: 30000 });
  const jobs = useQuery('jobs', () => api.jobs(), { interval: 2500 });

  const [worldId, setWorldId] = useLocal<string>('rc.world', '');
  const [datasetByWorld, setDatasetByWorld] = useLocal<Record<string, string>>('rc.datasets', {});
  const [dialog, openDialog] = useState<Dialog>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);

  const toast = useCallback((text: string, tone: Toast['tone'] = 'info') => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t.slice(-3), { id, text, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 5000);
  }, []);

  const refresh = useCallback(() => {
    worlds.reload();
    datasets.reload();
    models.reload();
    jobs.reload();
    system.reload();
  }, [worlds, datasets, models, jobs, system]);

  // 任务进入终态时刷新资源并提示
  const seen = useRef<Map<string, string> | null>(null);
  useEffect(() => {
    const items = jobs.data?.items;
    if (!items) return;
    if (!seen.current) {
      seen.current = new Map(items.map((j) => [j.id, j.state]));
      return;
    }
    let changed = false;
    for (const job of items) {
      const before = seen.current.get(job.id);
      if (before !== job.state) {
        seen.current.set(job.id, job.state);
        const label = { simulate: '场景仿真', import: '数据导入', train: '模型训练' }[job.kind];
        if (job.state === 'succeeded') {
          toast(`${label}已完成`, 'ok');
          changed = true;
        } else if (job.state === 'failed') toast(`${label}失败：${job.error?.message ?? '未知错误'}`, 'err');
        else if (job.state === 'cancelled' && before) toast(`${label}已取消`);
      }
    }
    if (changed) {
      worlds.reload();
      datasets.reload();
      models.reload();
    }
  }, [jobs.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const value = useMemo<AppState>(() => {
    const allWorlds = worlds.data?.items ?? [];
    const allDatasets = datasets.data?.items ?? [];
    const allModels = models.data?.items ?? [];
    // 默认选中有数据集的最新世界
    const fallback = allWorlds.find((w) => allDatasets.some((d) => d.metadata?.world_id === w.id)) ?? allWorlds[0];
    const world = allWorlds.find((w) => w.id === worldId) ?? fallback;
    const worldDatasets = allDatasets.filter((d) => d.metadata?.world_id === world?.id);
    const worldModels = allModels.filter((m) => m.metadata?.world_id === world?.id);
    const dataset = worldDatasets.find((d) => d.id === datasetByWorld[world?.id ?? '']) ?? worldDatasets[0];
    const zoneNames = new Map<string, string>(
      (world?.metadata?.zones?.features ?? []).map((f: Any) => [f.properties.zone_id, f.properties.name]),
    );
    return {
      system: system.data,
      online: !system.error,
      worlds: allWorlds,
      datasets: allDatasets,
      models: allModels,
      jobs: jobs.data?.items ?? [],
      loaded: !!(worlds.data && datasets.data && models.data) || !!system.error,
      world,
      dataset,
      worldDatasets,
      worldModels,
      tz: world?.metadata?.timezone ?? 'Asia/Shanghai',
      bucketMinutes: world?.metadata?.bucket_minutes ?? 60,
      setWorld: setWorldId,
      setDataset: (id: string) => world && setDatasetByWorld({ ...datasetByWorld, [world.id]: id }),
      refresh,
      route: nav.route,
      routeArg: nav.arg,
      go,
      dialog,
      openDialog,
      toast,
      zoneName: (id) => (id ? (zoneNames.get(id) ?? id) : '未知'),
    };
  }, [system.data, system.error, worlds.data, datasets.data, models.data, jobs.data, worldId, datasetByWorld, nav, dialog,
      go, refresh, toast, setWorldId, setDatasetByWorld]);

  return (
    <Ctx.Provider value={value}>
      {children}
      <div className="toasts">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.tone === 'ok' ? 'ok' : t.tone === 'err' ? 'err' : ''}`}>
            {t.text}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}
