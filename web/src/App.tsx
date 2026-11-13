import {
  Activity, BrainCircuit, Building2, Database, FileUp, LayoutDashboard, Map as MapIcon, Monitor, Moon, Plus,
  ReceiptText, Settings, Sun, TrendingUp, ChevronRight, ArrowUpRight, Command, RefreshCw,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Dialogs } from './dialogs';
import { DashboardPage } from './pages/Dashboard';
import { ForecastPage } from './pages/Forecast';
import { JobsPage } from './pages/Jobs';
import { MapPage } from './pages/MapPage';
import { ModelsPage } from './pages/Models';
import { OrdersPage } from './pages/Orders';
import { SystemPage } from './pages/System';
import { Note } from './components/ui';
import { time } from './format';
import { useApp, useTheme, type Dialog, type Route } from './store';

const NAV: { route: Route; label: string; icon: typeof Activity }[] = [
  { route: 'dashboard', label: '总览', icon: LayoutDashboard },
  { route: 'map', label: '城市实况', icon: MapIcon },
  { route: 'orders', label: '订单', icon: ReceiptText },
  { route: 'models', label: '模型', icon: BrainCircuit },
  { route: 'forecast', label: '预测与调配', icon: TrendingUp },
  { route: 'jobs', label: '任务', icon: Activity },
];

function Logo() {
  return (
    <div className="logo" title="RideCast">
      <svg width="20" height="20" viewBox="0 0 32 32" fill="none">
        <path d="M6 22c4 0 5-11 10-11s6 11 10 11" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" />
        <circle cx="16" cy="11" r="2.4" fill="#fff" />
      </svg>
    </div>
  );
}

function CreateMenu() {
  const app = useApp();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const on = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    const escape = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      setOpen(false);
      ref.current?.querySelector('button')?.focus();
    };
    addEventListener('mousedown', on);
    addEventListener('keydown', escape);
    ref.current?.querySelector<HTMLButtonElement>('.menu button')?.focus();
    return () => { removeEventListener('mousedown', on); removeEventListener('keydown', escape); };
  }, [open]);
  const items: { dialog: Dialog; label: string; desc: string; icon: typeof Activity }[] = [
    { dialog: 'world', label: '虚拟城市', desc: '按种子生成确定性地图', icon: Building2 },
    { dialog: 'simulate', label: '仿真数据集', desc: '把一段演化物化为可训练数据', icon: Database },
    { dialog: 'import', label: '导入真实订单', desc: 'CSV 订单与可选上下文', icon: FileUp },
    { dialog: 'train', label: '训练任务', desc: 'GRU 多步需求预测', icon: BrainCircuit },
  ];
  return (
    <div style={{ position: 'relative' }} ref={ref}>
      <button className="btn primary" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>
        <Plus size={15} />
        新建资源
      </button>
      {open && (
        <div className="menu">
          {items.map((it) => (
            <button
              key={it.label}
              onClick={() => {
                setOpen(false);
                app.openDialog(it.dialog);
              }}
            >
              <it.icon size={16} className="dim" />
              <span>
                <div>{it.label}</div>
                <div className="d">{it.desc}</div>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function App() {
  const app = useApp();
  const [theme, setTheme] = useTheme();
  const active = app.jobs.filter((j) => j.state === 'queued' || j.state === 'running' || j.state === 'cancelling').length;
  const ThemeIcon = theme === 'auto' ? Monitor : theme === 'light' ? Sun : Moon;
  const nextTheme = theme === 'auto' ? 'light' : theme === 'light' ? 'dark' : 'auto';
  const workerOn = app.system?.worker_online;
  const title = app.route === 'system' ? '系统设置' : NAV.find((n) => n.route === app.route)?.label;

  useEffect(() => {
    document.querySelector('.main')?.scrollTo({ top: 0 });
  }, [app.route]);

  return (
    <div className="shell">
      <header className="workspace-bar">
        <button className="brand" onClick={() => app.go('dashboard')} aria-label="RideCast 首页"><Logo /><span>RideCast</span></button>
        <span className="workspace-slash">/</span>
        <span className="workspace-name">出行智能工作台</span>
        <span className="workspace-tag">LOCAL</span>
        <span className="grow" />
        <button className="btn ghost sm hide-mobile" onClick={app.refresh} title="刷新资源"><RefreshCw size={14} /> 刷新</button>
        <button className="connection-state" onClick={() => app.go('system')}>
          <i className={`dot ${!app.online ? 'red' : workerOn ? 'green' : 'amber'}`} />
          <span>{!app.online ? 'API 离线' : workerOn ? '系统已连接' : 'Worker 离线'}</span>
        </button>
        <button className="btn ghost icon" onClick={() => setTheme(nextTheme)} title={`主题：${{ auto: '跟随系统', light: '浅色', dark: '深色' }[theme]}`} aria-label="切换主题">
          <ThemeIcon size={17} strokeWidth={1.6} />
        </button>
        <span className="workspace-avatar" title="本地工作区"><Command size={16} /></span>
      </header>
      <nav className="rail" aria-label="主导航">
        {NAV.map((n) => (
          <button key={n.route} className={`rail-btn ${app.route === n.route ? 'active' : ''}`} onClick={() => app.go(n.route)} aria-label={n.label} aria-current={app.route === n.route ? 'page' : undefined}>
            <n.icon size={16} strokeWidth={1.6} />
            <span>{n.label}</span>
            {n.route === 'jobs' && active > 0 && <span className="nav-count">{active}</span>}
          </button>
        ))}
        <span className="spacer" />
        <button className={`rail-btn ${app.route === 'system' ? 'active' : ''}`} onClick={() => app.go('system')} aria-label="系统" aria-current={app.route === 'system' ? 'page' : undefined}>
          <Settings size={16} strokeWidth={1.6} /><span>系统设置</span>
        </button>
      </nav>

      <main className={`main route-${app.route}`} id="main-content">
        <div className="contextbar">
          <div className="site"><Building2 size={14} className="muted" />
          {app.worlds.length > 0 ? (
            <select className="select bare" value={app.world?.id ?? ''} onChange={(e) => app.setWorld(e.target.value)} aria-label="城市">
              {app.worlds.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name} · {w.metadata?.zones?.features?.length ?? 0} 区
                </option>
              ))}
            </select>
          ) : (
            <span className="muted" style={{ padding: '0 10px' }}>尚无城市</span>
          )}
          </div>
          <ChevronRight size={13} className="muted" />
          {app.worldDatasets.length > 0 && (
            <select className="select bare minor" value={app.dataset?.id ?? ''} onChange={(e) => app.setDataset(e.target.value)} aria-label="数据集">
              {app.worldDatasets.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.metadata?.source === 'imported' ? '导入' : '仿真'} · {d.id.slice(2, 10)} · {time.date(d.metadata?.observation_start, app.tz)} 起
                </option>
              ))}
            </select>
          )}
          <span className="grow" />
          <span className="context-hint mono hide-narrow">{app.tz} · {app.bucketMinutes} MIN / BUCKET</span>
        </div>
        <div className="page-intro">
          <h1>{title}</h1>
          <div className="intro-actions">
            {app.route === 'dashboard' && <button className="btn" onClick={() => app.go('forecast')}>需求预测 <ArrowUpRight size={14} /></button>}
            <CreateMenu />
          </div>
        </div>
        {!app.online && (
          <div style={{ padding: '20px 20px 0' }}>
            <Note tone="err">
              无法访问 RideCast API。请在后端目录运行 <span className="mono">ridecast serve</span> 与 <span className="mono">ridecast worker</span>，或到「系统」页检查连接设置。
            </Note>
          </div>
        )}
        {app.route === 'dashboard' && <DashboardPage />}
        {app.route === 'map' && <MapPage />}
        {app.route === 'orders' && <OrdersPage />}
        {app.route === 'models' && <ModelsPage />}
        {app.route === 'forecast' && <ForecastPage />}
        {app.route === 'jobs' && <JobsPage />}
        {app.route === 'system' && <SystemPage theme={theme} setTheme={setTheme} />}
      </main>
      <Dialogs />
    </div>
  );
}
