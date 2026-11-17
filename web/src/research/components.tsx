import { Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControl, InputLabel, MenuItem, Select, Skeleton } from '@mui/material';
import { Info } from 'lucide-react';
import type { ReactNode } from 'react';
import { useResearch } from './context';
import type { MethodRoute, Metric } from './types';

export const routeLabels: Record<MethodRoute, string> = { real: '真实训练', synthetic: '生成训练', mixed: '混合训练', baseline: '基线' };
export const number = (value: number) => value.toLocaleString('zh-CN');
export const metricValue = (value: number | null | undefined, metric: Metric) => value == null ? '—' : metric === 'tv' ? `${value.toFixed(2)}%` : value.toFixed(4);
export const reportURL = (file: string) => `${import.meta.env.BASE_URL}research/reports/${file}`;

export function RouteChip({ route }: { route: MethodRoute }) {
  return <Chip label={routeLabels[route]} size="small" variant="outlined" className={`source-chip source-${route}`} />;
}

export function Section({ title, actions, children, className = '' }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={`research-section ${className}`}>
    {(title || actions) && <header className="research-section-head"><h2>{title}</h2>{actions && <div>{actions}</div>}</header>}
    {children}
  </section>;
}

export function StudySelector() {
  const { data, study, selectStudy } = useResearch();
  return <FormControl size="small" className="study-select"><InputLabel id="study-select-label">实验组</InputLabel>
    <Select labelId="study-select-label" label="实验组" value={study?.id ?? ''} onChange={(event) => selectStudy(event.target.value)}>
      {data?.studies.map((item) => <MenuItem key={item.id} value={item.id}>{item.title}</MenuItem>)}
    </Select>
  </FormControl>;
}

export function ResearchGate({ children }: { children: ReactNode }) {
  const { data, error, loading, reload } = useResearch();
  if (error) return <div className="research-empty"><Info size={28} /><h2>无法读取研究报告</h2><p>{error}</p><Button onClick={reload} variant="outlined">重新加载</Button></div>;
  if (!data || loading) return <div className="research-loading" aria-label="正在加载研究报告"><Skeleton width="38%" height={48} /><Skeleton variant="rounded" height={280} /><Skeleton variant="rounded" height={220} /></div>;
  return <>{children}</>;
}

export function MetricHelp({ open, onClose }: { open: boolean; onClose: () => void }) {
  return <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth aria-labelledby="metric-dialog-title">
    <DialogTitle id="metric-dialog-title">指标如何理解</DialogTitle>
    <DialogContent className="metric-help">
      <h3>TV · 空间分布差异</h3><p>比较行程量在区域之间的分配比例。0% 表示两种分布相同，100% 表示完全不重叠，越低越好。</p>
      <div className="formula">TV = 50 × Σ |真实份额 − 预测份额|</div>
      <p>例如真实分配为 80% / 20%，预测为 30% / 70%，TV 就是 50%：需要重新分配总量的 50%，才能与真实分布一致。</p>
      <p className="scope-notice">TV 不是逐小时订单量误差率，也不能换算为“100% − TV”的准确率。</p>
      <h3>Spearman · 排序相关</h3><p>衡量区域热度排序是否一致，范围为 −1 到 1，越接近 1 越好。均匀分配没有可比较的排序，显示为“—”。</p>
    </DialogContent>
    <DialogActions><Button onClick={onClose}>知道了</Button></DialogActions>
  </Dialog>;
}

export function EvidenceDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { data, study } = useResearch();
  return <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm" aria-labelledby="evidence-dialog-title">
    <DialogTitle id="evidence-dialog-title">实验范围与来源</DialogTitle>
    <DialogContent>
      <p className="dialog-lead">{study?.protocol}</p>
      <ul className="evidence-list">{data?.limits.map((limit) => <li key={limit}>{limit}</li>)}</ul>
      <dl className="research-kv"><dt>数据文件</dt><dd>{data?.dataset.filename}</dd><dt>目标口径</dt><dd>{data?.dataset.target}</dd><dt>坐标系</dt><dd>{data?.dataset.crs}</dd><dt>源文件摘要</dt><dd className="digest">{data?.dataset.sourceDigest}</dd></dl>
    </DialogContent>
    <DialogActions><Button component="a" href={reportURL('calibrated-world.json')} download>下载汇总 JSON</Button><Button onClick={onClose}>关闭</Button></DialogActions>
  </Dialog>;
}

export function downloadFile(name: string, content: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement('a'); link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function csvCell(value: string | number | null | undefined) {
  if (value == null) return '';
  const text = typeof value === 'string' && /^[=+\-@]/.test(value) ? `'${value}` : String(value);
  return `"${text.replace(/"/g, '""')}"`;
}
