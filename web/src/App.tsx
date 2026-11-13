import {
  Activity, ArrowUpRight, BrainCircuit, Building2, ChevronRight, Database,
  FileUp, Plus, RefreshCw,
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
import { useApp, type Dialog, type Route } from './store';

const NAV: { route: Route; label: string }[] = [
  { route: 'dashboard', label: '总览' },
  { route: 'map', label: '城市实况' },
  { route: 'orders', label: '订单' },
  { route: 'models', label: '模型' },
  { route: 'forecast', label: '预测与调配' },
  { route: 'jobs', label: '任务' },
  { route: 'system', label: '系统设置' },
];

function Logo() {
  return (
    <div className="logo" title="RideCast">
      <svg width="20" height="20" viewBox="0 0 32 32" fill="none">
        <path d="M6 22c4 0 5-11 10-11s6 11 10 11" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" />
        <circle cx="16" cy="11" r="2.4" fill="currentColor" />
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
  const items: { dialog: Dialog; label: string; icon: typeof Activity }[] = [
    { dialog: 'world', label: '虚拟城市', icon: Building2 },
    { dialog: 'simulate', label: '仿真数据集', icon: Database },
    { dialog: 'import', label: '导入真实订单', icon: FileUp },
    { dialog: 'train', label: '训练任务', icon: BrainCircuit },
  ];
  return (
    <div style={{ position: 'relative' }} ref={ref}>
      <button className={app.route === 'dashboard' || app.route === 'jobs' ? 'btn primary' : 'btn'} aria-expanded={open} onClick={() => setOpen(!open)}>
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
              <span>{it.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function App() {
  const app = useApp();
  const active = app.jobs.filter((j) => ['queued', 'running', 'cancelling'].includes(j.state)).length;
  const title = NAV.find((n) => n.route === app.route)?.label;

  useEffect(() => {
    document.querySelector('.main')?.scrollTo({ top: 0 });
  }, [app.route]);

  return (
    <div className="shell">
      <header className="topbar">
        <button className="brand" onClick={() => app.go('dashboard')} aria-label="RideCast 首页">
          <Logo /><span>RideCast</span>
        </button>
        <nav className="topnav" aria-label="主导航">
          {NAV.map((n) => (
            <button key={n.route} className={`nav-link ${app.route === n.route ? 'active' : ''}`}
              onClick={() => app.go(n.route)} aria-label={n.route === 'system' ? '系统' : n.label}
              aria-current={app.route === n.route ? 'page' : undefined}>
              {n.label}
              {n.route === 'jobs' && active > 0 && <span className="nav-count">{active}</span>}
            </button>
          ))}
        </nav>
        <div className="header-actions">
          <button className="connection-state" onClick={() => app.go('system')} aria-label="连接状态">
            <i className={`dot ${app.online ? 'green' : 'red'}`} /><span>API {app.online ? '在线' : '离线'}</span>
          </button>
          <button className="btn ghost icon" onClick={app.refresh} aria-label="刷新资源" title="刷新资源"><RefreshCw size={15} strokeWidth={1.6} /></button>
        </div>
      </header>

      <div className="contextbar">
        <div className="site"><Building2 size={14} className="context-icon" />
          {app.worlds.length > 0 ? (
            <select className="select bare" value={app.world?.id ?? ''} onChange={(e) => app.setWorld(e.target.value)} aria-label="城市">
              {app.worlds.map((w) => <option key={w.id} value={w.id}>{w.name} · {w.metadata?.zones?.features?.length ?? 0} 区</option>)}
            </select>
          ) : <span className="muted small">尚无城市</span>}
        </div>
        <ChevronRight size={12} className="context-separator" />
        {app.worldDatasets.length > 0 && (
          <select className="select bare minor" value={app.dataset?.id ?? ''} onChange={(e) => app.setDataset(e.target.value)} aria-label="数据集">
            {app.worldDatasets.map((d) => <option key={d.id} value={d.id}>{d.metadata?.source === 'imported' ? '导入' : '仿真'} · {d.id.slice(2, 10)} · {time.date(d.metadata?.observation_start, app.tz)}</option>)}
          </select>
        )}
        <span className="grow" />
        <span className="context-hint hide-narrow">{app.tz} <span>·</span> {app.bucketMinutes} min</span>
        <span className="worker-state hide-mobile"><i className={`dot ${app.system?.worker_online ? 'green' : 'amber'}`} />Worker {app.system?.worker_online ? '在线' : '离线'}</span>
      </div>

      <main className={`main route-${app.route}`} id="main-content">
        <div className="page-intro">
          <h1>{title}</h1>
          <div className="intro-actions">
            {app.route === 'dashboard' && <button className="btn" onClick={() => app.go('forecast')}>需求预测 <ArrowUpRight size={14} /></button>}
            <CreateMenu />
          </div>
        </div>
        {!app.online && <div className="connection-error"><Note tone="err">无法访问 RideCast API。请启动后端，或在「系统设置」中检查连接。</Note></div>}
        {app.route === 'dashboard' && <DashboardPage />}
        {app.route === 'map' && <MapPage />}
        {app.route === 'orders' && <OrdersPage />}
        {app.route === 'models' && <ModelsPage />}
        {app.route === 'forecast' && <ForecastPage />}
        {app.route === 'jobs' && <JobsPage />}
        {app.route === 'system' && <SystemPage />}
      </main>
      <Dialogs />
    </div>
  );
}
