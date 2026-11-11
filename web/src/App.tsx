import {
  Activity, BrainCircuit, Building2, Database, FileUp, LayoutDashboard, Map as MapIcon, Monitor, Moon, Plus,
  ReceiptText, Settings, Sun, TrendingUp,
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
    addEventListener('mousedown', on);
    return () => removeEventListener('mousedown', on);
  }, [open]);
  const items: { dialog: Dialog; label: string; desc: string; icon: typeof Activity }[] = [
    { dialog: 'world', label: '虚拟城市', desc: '按种子生成确定性地图', icon: Building2 },
    { dialog: 'simulate', label: '仿真数据集', desc: '把一段演化物化为可训练数据', icon: Database },
    { dialog: 'import', label: '导入真实订单', desc: 'CSV 订单与可选上下文', icon: FileUp },
    { dialog: 'train', label: '训练任务', desc: 'GRU 多步需求预测', icon: BrainCircuit },
  ];
  return (
    <div style={{ position: 'relative' }} ref={ref}>
      <button className="btn primary" onClick={() => setOpen(!open)}>
        <Plus size={15} />
        新建
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
  const title = app.route === 'system' ? '系统' : NAV.find((n) => n.route === app.route)?.label;
  const ThemeIcon = theme === 'auto' ? Monitor : theme === 'light' ? Sun : Moon;
  const nextTheme = theme === 'auto' ? 'light' : theme === 'light' ? 'dark' : 'auto';
  const workerOn = app.system?.worker_online;
  const TitleIcon = app.route === 'system' ? Settings : (NAV.find((n) => n.route === app.route)?.icon ?? Settings);

  return (
    <div className="shell">
      <nav className="rail">
        <Logo />
        {NAV.map((n) => (
          <button key={n.route} className={`rail-btn ${app.route === n.route ? 'active' : ''}`} onClick={() => app.go(n.route)} aria-label={n.label}>
            <n.icon size={22} strokeWidth={1.5} />
            {n.route === 'jobs' && active > 0 && <span className="count">{active}</span>}
            <span className="tip">{n.label}</span>
          </button>
        ))}
        <span className="spacer" />
        <button className={`rail-btn ${app.route === 'system' ? 'active' : ''}`} onClick={() => app.go('system')} aria-label="系统">
          <Settings size={22} strokeWidth={1.5} />
          <span className="tip">系统</span>
        </button>
      </nav>

      <header className="topbar">
        <div className="site">
          <i className={`dot ${!app.online ? 'red' : workerOn ? 'green' : 'amber'}`} title={!app.online ? 'API 离线' : workerOn ? 'API 与 worker 在线' : 'worker 离线'} />
        {app.worlds.length > 0 ? (
          <select className="select bare" value={app.world?.id ?? ''} onChange={(e) => app.setWorld(e.target.value)} title="城市">
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
        <span className="tab-active">
          <TitleIcon size={19} strokeWidth={1.6} />
          {title}
        </span>
        {app.worldDatasets.length > 0 && (
          <select className="select bare minor hide-narrow" value={app.dataset?.id ?? ''} onChange={(e) => app.setDataset(e.target.value)} title="数据集">
            {app.worldDatasets.map((d) => (
              <option key={d.id} value={d.id}>
                {d.metadata?.source === 'imported' ? '导入' : '仿真'} · {d.id.slice(2, 10)} · {time.date(d.metadata?.observation_start, app.tz)} 起
              </option>
            ))}
          </select>
        )}
        <span className="grow" />
        <button className="btn ghost hide-narrow" onClick={() => app.go('system')} title="worker 执行仿真、导入与训练任务">
          {!app.online ? 'API 离线' : workerOn ? 'Worker 在线' : 'Worker 离线'}
        </button>
        <button className="btn ghost icon" onClick={() => setTheme(nextTheme)} title={`主题：${{ auto: '跟随系统', light: '浅色', dark: '深色' }[theme]}`} aria-label="切换主题">
          <ThemeIcon size={18} strokeWidth={1.6} />
        </button>
        <CreateMenu />
      </header>

      <main className="main">
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
