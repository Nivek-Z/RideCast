import { BookOpen, ChartNoAxesColumnIncreasing, Check, Layers, Map, Moon, Sun } from 'lucide-react';
import { IconButton } from '@mui/material';
import { useEffect, useState } from 'react';
import { Arena } from './study/Arena';
import { useWorkspace } from './study/data';
import { Defense } from './study/Defense';
import { Evidence } from './study/Evidence';
import { paletteIds, palettes, type ColorMode, type PaletteId } from './palettes';

const tabs = [
  { id: 'overview', title: '空间验证', icon: Map },
  { id: 'arena', title: '参数对比', icon: ChartNoAxesColumnIncreasing },
  { id: 'evidence', title: '研究记录', icon: BookOpen },
] as const;
const current = () => window.location.hash.slice(1);
const subtitle = '网约车空间分布研究';

export function App({ palette, selectPalette, mode, toggleMode }: {
  palette: PaletteId; selectPalette: (value: PaletteId) => void; mode: ColorMode; toggleMode: () => void;
}) {
  const [route, setRoute] = useState(current);
  const { runs, status, loading, snapshot, reportError, refresh } = useWorkspace();
  const page = route === 'arena' || route === 'evidence' ? route : 'overview';
  useEffect(() => {
    const change = () => setRoute(current());
    window.addEventListener('hashchange', change);
    return () => window.removeEventListener('hashchange', change);
  }, []);
  useEffect(() => {
    if (route === 'results') {
      const frame = requestAnimationFrame(() => document.getElementById('results')?.scrollIntoView());
      return () => cancelAnimationFrame(frame);
    }
    window.scrollTo({ top: 0, behavior: 'instant' });
  }, [route]);
  return <>
    <header className="shell-header">
      <div className="shell-inner">
        <a className="brand" href="#overview" aria-label="RideCast 空间验证">
          <span className="brand-mark"><Layers size={22} aria-hidden="true" /></span>
          <span><strong>RideCast</strong><small>{subtitle}</small></span>
        </a>
        <nav aria-label="研究导航">{tabs.map((tab) => <a href={`#${tab.id}`} aria-current={page === tab.id ? 'page' : undefined} key={tab.id}>
          <tab.icon size={17} /><span>{tab.title}</span></a>)}</nav>
        <div className="header-actions">
          <div className={`service ${status?.archive_ready && status.worker_online ? 'ready' : ''}`} role="status">
            <i /><span>{loading ? '连接中' : !status ? '仅报告' : !status.archive_ready ? '等待数据' : !status.worker_online ? '等待实验进程' : '实验就绪'}</span>
          </div>
          <div className="palette-switch" role="radiogroup" aria-label="界面配色">
            {paletteIds.map((id) => <label className="palette-option" title={palettes[id].name} key={id}>
              <input type="radio" name="palette" aria-label={palettes[id].name}
                checked={palette === id} onChange={() => selectPalette(id)} />
              <span className="palette-swatch" aria-hidden="true"
                style={{ backgroundColor: palettes[id][mode].colors.primary, color: palettes[id][mode].colors['on-primary'] }}>
                {palette === id && <Check size={12} strokeWidth={2.5} />}
              </span>
            </label>)}
          </div>
          <IconButton className="mode-toggle" aria-label="深色模式" aria-pressed={mode === 'dark'}
            title={mode === 'dark' ? '切换到浅色模式' : '切换到深色模式'} onClick={toggleMode}>
            {mode === 'dark' ? <Sun size={20} aria-hidden="true" /> : <Moon size={20} aria-hidden="true" />}
          </IconButton>
        </div>
      </div>
    </header>
    <main className="site-main">
      {reportError && <p className="error-text" role="alert">当前无法读取历史实验报告。</p>}
      {page === 'arena' ? <Arena runs={runs} status={status} refresh={refresh} />
        : page === 'evidence' ? <Evidence data={snapshot} />
        : <Defense snapshot={snapshot} runs={runs} status={status} refresh={refresh} />}
    </main>
    <footer className="shell-footer"><div><strong>RideCast</strong><span>{subtitle}</span>
      <a href="research/reports/calibrated-world.md" target="_blank" rel="noreferrer">实验报告</a></div></footer>
  </>;
}
