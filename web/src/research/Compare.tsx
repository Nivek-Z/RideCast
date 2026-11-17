import { Button, Checkbox, Drawer, FormControl, IconButton, InputAdornment, InputLabel, MenuItem, Select, Tab, Tabs, Table, TableBody, TableCell, TableContainer, TableHead, TablePagination, TableRow, TableSortLabel, TextField, ToggleButton, ToggleButtonGroup, Tooltip } from '@mui/material';
import { ArrowDownToLine, ArrowRight, ChevronRight, FileText, HelpCircle, Search, SlidersHorizontal, X } from 'lucide-react';
import { useMemo, useState } from 'react';
import { useApp } from '../store';
import { csvCell, downloadFile, EvidenceDialog, MetricHelp, metricValue, number, reportURL, RouteChip, routeLabels, Section, StudySelector } from './components';
import { useResearch } from './context';
import type { Method, MethodRoute, Metric, Scope, Study } from './types';

function initialSelection(study: Study) {
  const ordered = [...study.methods.filter((m) => m.selection), ...study.methods.filter((m) => m.route === 'baseline'), ...study.methods.filter((m) => m.test.tv != null)];
  return [...new Set(ordered.map((m) => m.id))].slice(0, 4);
}

export function ComparePage() {
  const { study } = useResearch();
  return study && <Comparison key={study.id} study={study} />;
}

function Comparison({ study }: { study: Study }) {
  const { data } = useResearch();
  const app = useApp();
  const [metric, setMetric] = useState<Metric>('tv');
  const [scope, setScope] = useState<Scope>('test');
  const [selection, setSelection] = useState(() => initialSelection(study));
  const [search, setSearch] = useState('');
  const [route, setRoute] = useState<MethodRoute | 'all'>('all');
  const [ascending, setAscending] = useState(true);
  const [sortMetric, setSortMetric] = useState<Metric>('tv');
  const [page, setPage] = useState(0);
  const [detail, setDetail] = useState<Method | null>(null);
  const [help, setHelp] = useState(false);
  const [evidence, setEvidence] = useState(false);
  const selected = study.methods.filter((m) => selection.includes(m.id));
  const plotted = selected.filter((m) => m[scope][metric] != null);
  const maximum = metric === 'tv' ? Math.max(20, Math.ceil(Math.max(...plotted.map((m) => m[scope].tv ?? 0), 0) / 20) * 20) : 1;
  const rows = useMemo(() => study.methods.filter((m) => (route === 'all' || m.route === route) && `${m.label} ${m.id}`.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => {
      const av = a[scope][sortMetric], bv = b[scope][sortMetric];
      if (av == null) return bv == null ? 0 : 1;
      if (bv == null) return -1;
      return (av - bv) * (ascending ? 1 : -1);
    }), [study, route, search, scope, sortMetric, ascending]);
  const toggle = (id: string) => setSelection((current) => current.includes(id) ? current.filter((value) => value !== id) : current.length < 8 ? [...current, id] : current);
  const exportCSV = () => {
    const content = [['实验组', '方案', '训练来源', '评价分区', 'TV (%)', 'Spearman', '选择依据'], ...selected.map((m) => [study.title, m.label, m.train, scope === 'test' ? '空间对照区' : study.developmentLabel, m[scope].tv, m[scope].spearman, m.selection])]
      .map((row) => row.map((cell) => csvCell(cell)).join(',')).join('\r\n');
    downloadFile(`ridecast-${study.id}-${scope}.csv`, `\uFEFF${content}`, 'text/csv;charset=utf-8');
    app.toast(`已导出 ${selected.length} 个方案的结果`, 'ok');
  };
  const sort = (key: Metric) => { setAscending(sortMetric === key ? !ascending : key === 'tv'); setSortMetric(key); };

  return <div className="research-page">
    <div className="research-page-title"><div><h1>实验对比</h1><p>{data?.dataset.name} · 区域累计行程量</p></div>
      <Button variant="outlined" startIcon={<ArrowDownToLine size={17} />} onClick={exportCSV} disabled={!selected.length}>导出结果</Button>
    </div>
    <div className="study-toolbar"><StudySelector /><span className="study-description">{study.subtitle}</span><span className="report-date">报告更新 {data?.date}</span></div>
    <div className="comparison-layout">
      <Section title="方法对比" className="method-chart-section" actions={<ToggleButtonGroup exclusive size="small" value={metric} onChange={(_, value: Metric | null) => value && setMetric(value)} aria-label="图表指标">
        <ToggleButton value="tv">TV 分布差异 ↓</ToggleButton><ToggleButton value="spearman">排序相关 ↑</ToggleButton>
      </ToggleButtonGroup>}>
        <div className="chart-subhead"><Tabs className="scope-tabs" value={scope} selectionFollowsFocus onChange={(_, value: Scope) => setScope(value)} aria-label="评价分区">
          <Tab value="test" label="空间对照区" /><Tab value="development" label={study.developmentLabel} />
        </Tabs><Tooltip title="指标说明"><IconButton aria-label="指标说明" size="small" onClick={() => setHelp(true)}><HelpCircle size={17} /></IconButton></Tooltip></div>
        <div className="comparison-chart" aria-label={metric === 'tv' ? '方案的空间分布差异比较，越低越好' : '方案的区域热度排序相关比较，越高越好'}>
          {plotted.length ? <>
            <div className="comparison-bars">{plotted.map((method) => {
              const value = method[scope][metric] ?? 0;
              const style = metric === 'tv' ? { width: `${value / maximum * 100}%` } : { left: `${value < 0 ? 50 + value * 50 : 50}%`, width: `${Math.abs(value) * 50}%` };
              return <button className={`comparison-bar-row ${method.route}`} key={method.id} onClick={() => setDetail(method)} aria-label={`查看${method.label}详情，${metricValue(value, metric)}`}>
                <span className="bar-method-label">{method.label}</span><span className={`bar-track ${metric === 'spearman' ? 'correlation-track' : ''}`}><span className="bar-fill" style={style} /></span><strong>{metricValue(value, metric)}</strong>
              </button>;
            })}</div>
            <div className="comparison-axis"><span /><div>{(metric === 'tv' ? [0, maximum / 4, maximum / 2, maximum * .75, maximum] : [-1, -.5, 0, .5, 1]).map((value) => <span key={value}>{value}{metric === 'tv' ? '%' : ''}</span>)}</div><span /></div>
          </> : <div className="chart-empty"><SlidersHorizontal size={24} /><p>{selected.length ? '所选方案没有这一分区的可比较指标' : '在下方勾选方案以查看对比'}</p></div>}
        </div>
        <div className="chart-footnote"><span>{metric === 'tv' ? '0% 表示分布一致；不是小时订单量误差率。' : '范围 −1 到 1；均匀分配不计算排序相关。'}</span>{selected.length > plotted.length && <span>{selected.length - plotted.length} 个方案无该指标</span>}</div>
      </Section>
      <aside className="study-aside"><div className="aside-heading"><h2>评估设置</h2><span className="evidence-tag">探索性</span></div>
        <dl className="research-kv"><dt>预测目标</dt><dd>区域行程份额</dd><dt>数据区域</dt><dd>{number(data?.dataset.areas ?? 0)} 个</dd>
          {study.split.training != null && <><dt>真实训练区</dt><dd>{study.split.training} 个</dd></>}
          {study.split.development != null && <><dt>真实开发区</dt><dd>{study.split.development} 个</dd></>}
          {study.split.validation != null && <><dt>真实验证区</dt><dd>{study.split.validation} 个</dd></>}
          <dt>空间对照区</dt><dd>{study.split.test} 个</dd><dt>划分方式</dt><dd>按空间块划分</dd>
        </dl>
        <div className="aside-divider" /><p>没有时间戳或统计期。本轮只评价静态空间分布。</p><p>对照区已被查看过，结果需要新数据进一步确认。</p>
        <Button onClick={() => setEvidence(true)} endIcon={<ArrowRight size={15} />}>实验范围与来源</Button>
        <Button variant="text" onClick={() => app.go('data')}>查看数据划分</Button>
      </aside>
    </div>
    <Section className="result-table-section" title={<><span>结果明细</span><span className="section-count">{study.methods.length} 个方案</span></>}
      actions={<Button size="small" component="a" href={reportURL(`${study.report}.md`)} download startIcon={<FileText size={15} />}>下载报告</Button>}>
      <div className="table-toolbar"><TextField size="small" placeholder="搜索方案" value={search} onChange={(e) => { setSearch(e.target.value); setPage(0); }} inputProps={{ 'aria-label': '搜索方案' }} InputProps={{ startAdornment: <InputAdornment position="start"><Search size={17} /></InputAdornment> }} />
        <FormControl size="small" className="source-filter"><InputLabel id="source-filter-label">训练来源</InputLabel><Select labelId="source-filter-label" label="训练来源" value={route} onChange={(e) => { setRoute(e.target.value as MethodRoute | 'all'); setPage(0); }}><MenuItem value="all">全部来源</MenuItem>{Object.entries(routeLabels).map(([value, label]) => <MenuItem key={value} value={value}>{label}</MenuItem>)}</Select></FormControl>
        <span className="selection-summary">已选 {selected.length} 项<span className="wide-only"> · 最多 8 项</span></span>
      </div>
      <TableContainer><Table aria-label="实验方案结果" className="research-results-table"><TableHead><TableRow>
        <TableCell padding="checkbox"><Checkbox inputProps={{ 'aria-label': '选择当前筛选的前八个方案' }} indeterminate={rows.slice(0, 8).some((m) => selection.includes(m.id)) && !rows.slice(0, 8).every((m) => selection.includes(m.id))} checked={rows.length > 0 && rows.slice(0, 8).every((m) => selection.includes(m.id))} onChange={(_, checked) => setSelection(checked ? rows.slice(0, 8).map((m) => m.id) : [])} /></TableCell>
        <TableCell>方案 / 训练来源</TableCell><TableCell>拟合数据</TableCell><TableCell align="right"><TableSortLabel active={sortMetric === 'tv'} direction={ascending ? 'asc' : 'desc'} onClick={() => sort('tv')}>TV ↓</TableSortLabel></TableCell><TableCell align="right"><TableSortLabel active={sortMetric === 'spearman'} direction={ascending ? 'asc' : 'desc'} onClick={() => sort('spearman')}>Spearman ↑</TableSortLabel></TableCell><TableCell align="right">详情</TableCell>
      </TableRow></TableHead><TableBody>
        {rows.slice(page * 10, page * 10 + 10).map((method) => <TableRow key={method.id} hover selected={selection.includes(method.id)}>
          <TableCell padding="checkbox"><Checkbox checked={selection.includes(method.id)} disabled={!selection.includes(method.id) && selected.length >= 8} onChange={() => toggle(method.id)} inputProps={{ 'aria-label': `选择${method.label}` }} /></TableCell>
          <TableCell><button className="method-name" onClick={() => setDetail(method)}>{method.label}</button><div className="method-meta"><RouteChip route={method.route} />{method.selection && <span>{method.selection}</span>}</div></TableCell><TableCell className="training-cell">{method.train}</TableCell><TableCell align="right" className="metric-cell">{metricValue(method[scope].tv, 'tv')}</TableCell><TableCell align="right" className="metric-cell secondary-metric">{metricValue(method[scope].spearman, 'spearman')}</TableCell><TableCell align="right"><IconButton aria-label={`查看${method.label}详情`} onClick={() => setDetail(method)}><ChevronRight size={18} /></IconButton></TableCell>
        </TableRow>)}
        {!rows.length && <TableRow><TableCell colSpan={6}><div className="no-results">没有匹配的方案。<Button onClick={() => { setSearch(''); setRoute('all'); }}>清除筛选</Button></div></TableCell></TableRow>}
      </TableBody></Table></TableContainer>
      {rows.length > 10 && <TablePagination component="div" count={rows.length} page={page} onPageChange={(_, value) => setPage(value)} rowsPerPage={10} rowsPerPageOptions={[10]} labelDisplayedRows={({ from, to, count }) => `${from}–${to} / ${count}`} getItemAriaLabel={(type) => type === 'next' ? '下一页方案' : '上一页方案'} />}
    </Section>
    <div className="research-page-foot"><span>当前显示：{scope === 'test' ? `${study.split.test} 区空间对照` : study.developmentLabel}</span><button onClick={() => setHelp(true)}><HelpCircle size={14} />指标定义</button></div>
    <Drawer anchor="right" open={!!detail} onClose={() => setDetail(null)} PaperProps={{ role: 'dialog', 'aria-modal': true, 'aria-labelledby': 'method-detail-title' }}><div className="research-drawer-head"><span>方案详情</span><IconButton aria-label="关闭方案详情" onClick={() => setDetail(null)}><X size={20} /></IconButton></div>{detail && <><h2 className="drawer-title" id="method-detail-title">{detail.label}</h2><RouteChip route={detail.route} /><dl className="research-kv detail-kv"><dt>拟合数据</dt><dd>{detail.train}</dd><dt>对照 TV</dt><dd>{metricValue(detail.test.tv, 'tv')}</dd><dt>排序相关</dt><dd>{metricValue(detail.test.spearman, 'spearman')}</dd><dt>选择依据</dt><dd>{detail.selection || '固定对照方案'}</dd>{detail.config.map(([key, value]) => <div className="kv-fragment" key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl><div className="drawer-protocol"><h3>实验协议</h3><p>{study.protocol}</p></div><p className="scope-notice">{study.finding}</p><Button component="a" href={reportURL(`${study.report}.md`)} download variant="outlined" startIcon={<ArrowDownToLine size={16} />}>下载完整报告</Button></>}</Drawer>
    <MetricHelp open={help} onClose={() => setHelp(false)} /><EvidenceDialog open={evidence} onClose={() => setEvidence(false)} />
  </div>;
}
