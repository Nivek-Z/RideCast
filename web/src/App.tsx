import { Button, ButtonBase, IconButton, ListItemIcon, ListItemText, Menu, MenuItem, Tooltip } from '@mui/material';
import { Activity, ArrowLeftRight, Boxes, BrainCircuit, ChevronDown, CircleHelp, Database, FileClock, FileUp, FlaskConical, Layers3, Map, Plus, Settings2, SlidersHorizontal, TrendingUp } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Dialogs } from './dialogs';
import { Note } from './components/ui';
import { DashboardPage } from './pages/Dashboard';
import { ForecastPage } from './pages/Forecast';
import { JobsPage } from './pages/Jobs';
import { MapPage } from './pages/MapPage';
import { ModelsPage } from './pages/Models';
import { OrdersPage } from './pages/Orders';
import { SystemPage } from './pages/System';
import { ResearchGate, MetricHelp } from './research/components';
import { ComparePage } from './research/Compare';
import { useResearch } from './research/context';
import { DataPage, RecordsPage, StabilityPage } from './research/WorkspacePages';
import { useApp, type Dialog, type Route } from './store';

const PRIMARY = [
  { route: 'compare' as Route, label: '实验对比', icon: ArrowLeftRight },
  { route: 'data' as Route, label: '数据划分', icon: Database },
  { route: 'stability' as Route, label: '规则稳定性', icon: SlidersHorizontal },
  { route: 'records' as Route, label: '实验记录', icon: FileClock },
];
const TOOLS = [
  { route: 'dashboard' as Route, label: '时序数据总览', icon: Activity },
  { route: 'map' as Route, label: '城市演化与回放', icon: Map },
  { route: 'orders' as Route, label: '订单明细', icon: Layers3 },
  { route: 'models' as Route, label: 'GRU 模型与回测', icon: BrainCircuit },
  { route: 'forecast' as Route, label: '时序预测与调配', icon: TrendingUp },
  { route: 'jobs' as Route, label: '计算任务', icon: FileClock },
  { route: 'system' as Route, label: '连接与资源', icon: Settings2 },
];

export function App() {
  const app = useApp();
  const { data } = useResearch();
  const [toolAnchor, setToolAnchor] = useState<HTMLElement | null>(null);
  const [createAnchor, setCreateAnchor] = useState<HTMLElement | null>(null);
  const [help, setHelp] = useState(false);
  const isResearch = PRIMARY.some((item) => item.route === app.route);
  const title = [...PRIMARY, ...TOOLS, { route: 'system', label: '连接与资源' }].find((item) => item.route === app.route)?.label ?? '研究工作台';
  useEffect(() => { document.querySelector('.workspace-main')?.scrollTo({ top: 0 }); }, [app.route]);
  const open = (dialog: Dialog) => { setCreateAnchor(null); app.openDialog(dialog); };
  return <div className="workspace-shell">
    <a className="skip-link" href="#main-content" onClick={(event) => { event.preventDefault(); document.getElementById('main-content')?.focus(); }}>跳到主要内容</a>
    <aside className="navigation-rail"><ButtonBase className="rail-brand" aria-label="RideCast 研究首页" onClick={() => app.go('compare')}><FlaskConical size={26} strokeWidth={1.6} /></ButtonBase>
      <nav aria-label="研究导航">{PRIMARY.map((item) => <ButtonBase key={item.route} className={`rail-item ${app.route === item.route ? 'active' : ''}`} onClick={() => app.go(item.route)} aria-current={app.route === item.route ? 'page' : undefined}><span className="rail-icon"><item.icon size={21} strokeWidth={1.7} /></span><span>{item.label}</span></ButtonBase>)}</nav>
      <div className="rail-bottom"><Tooltip title="指标说明" placement="right"><IconButton aria-label="指标说明" onClick={() => setHelp(true)}><CircleHelp size={21} strokeWidth={1.6} /></IconButton></Tooltip><Tooltip title="连接与资源" placement="right"><IconButton aria-label="连接与资源" onClick={() => app.go('system')} className={app.route === 'system' ? 'rail-settings-active' : ''}><Settings2 size={21} strokeWidth={1.6} /></IconButton></Tooltip></div>
    </aside>
    <div className="workspace-body"><header className="workspace-header"><button className="workspace-brand" onClick={() => app.go('compare')}>RideCast<span>研究工作台</span></button>
      <div className="workspace-header-actions"><span className="snapshot-label"><i />报告快照{data?.date && <span>{data.date}</span>}</span>
        <Button onClick={(event) => setToolAnchor(event.currentTarget)} endIcon={<ChevronDown size={16} />} className={!isResearch && app.route !== 'system' ? 'tools-active' : ''}>时序工具</Button>
        <Menu anchorEl={toolAnchor} open={!!toolAnchor} onClose={() => setToolAnchor(null)}>{TOOLS.map((item) => <MenuItem key={item.route} selected={app.route === item.route} onClick={() => { app.go(item.route); setToolAnchor(null); }}><ListItemIcon><item.icon size={18} /></ListItemIcon><ListItemText>{item.label}</ListItemText></MenuItem>)}</Menu>
      </div></header>
      {!isResearch && app.route !== 'system' && <div className="lab-context"><span className="lab-context-label"><Boxes size={16} />时序实验</span><select aria-label="城市" className="select" value={app.world?.id ?? ''} onChange={(e) => app.setWorld(e.target.value)}>{!app.worlds.length && <option value="">尚无城市</option>}{app.worlds.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}</select><select aria-label="数据集" className="select" value={app.dataset?.id ?? ''} onChange={(e) => app.setDataset(e.target.value)}>{!app.worldDatasets.length && <option value="">尚无数据集</option>}{app.worldDatasets.map((d) => <option key={d.id} value={d.id}>{d.metadata?.source === 'imported' ? '导入' : '仿真'} · {d.id.slice(2, 10)}</option>)}</select><span className="lab-context-time">{app.tz} · {app.bucketMinutes} 分钟桶</span></div>}
      <main className={`workspace-main route-${app.route} ${isResearch ? 'research-main' : 'legacy-content'}`} id="main-content" tabIndex={-1}>
        {isResearch ? <ResearchGate>{app.route === 'compare' && <ComparePage />}{app.route === 'data' && <DataPage />}{app.route === 'stability' && <StabilityPage />}{app.route === 'records' && <RecordsPage />}</ResearchGate> : <>
          <div className="page-intro"><div><span className="tool-eyebrow">{app.route === 'system' ? '工作区设置' : '时序工具'}</span><h1>{title}</h1></div>{app.route !== 'system' && <Button variant="contained" startIcon={<Plus size={17} />} onClick={(event) => setCreateAnchor(event.currentTarget)}>创建资源</Button>}</div>
          {!app.online && app.loaded && <div className="connection-error"><Note tone="err">计算服务不可达。研究报告仍可查看；请从「时序工具 → 连接与资源」检查连接。</Note></div>}
          {app.route === 'dashboard' && <DashboardPage />}{app.route === 'map' && <MapPage />}{app.route === 'orders' && <OrdersPage />}{app.route === 'models' && <ModelsPage />}{app.route === 'forecast' && <ForecastPage />}{app.route === 'jobs' && <JobsPage />}{app.route === 'system' && <SystemPage />}
        </>}
      </main>
      <Menu anchorEl={createAnchor} open={!!createAnchor} onClose={() => setCreateAnchor(null)}>{[
        { dialog: 'world' as Dialog, label: '创建虚拟城市', icon: Boxes }, { dialog: 'simulate' as Dialog, label: '生成时序仿真数据', icon: Activity },
        { dialog: 'import' as Dialog, label: '导入真实订单', icon: FileUp }, { dialog: 'train' as Dialog, label: '提交 GRU 训练', icon: BrainCircuit },
      ].map((item) => <MenuItem key={item.dialog} onClick={() => open(item.dialog)}><ListItemIcon><item.icon size={18} /></ListItemIcon>{item.label}</MenuItem>)}</Menu>
    </div>
    <Dialogs /><MetricHelp open={help} onClose={() => setHelp(false)} />
  </div>;
}
