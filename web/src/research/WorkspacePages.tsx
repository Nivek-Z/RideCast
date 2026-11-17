import { Button, Chip, IconButton, Tab, Tabs, Table, TableBody, TableCell, TableContainer, TableHead, TableRow } from '@mui/material';
import { ArrowDownToLine, ArrowRight, BookOpen, ChevronRight, FileJson2, FileText, Layers3 } from 'lucide-react';
import { useState } from 'react';
import { JobsPage } from '../pages/Jobs';
import { useApp } from '../store';
import { EvidenceDialog, number, reportURL, Section, StudySelector } from './components';
import { useResearch } from './context';

const fieldLabels: Record<string, string> = {
  population: '人口', employment_density: '就业密度', area_m2: '区域面积', network_density: '路网密度', income: '家庭收入',
  no_vehicle_share: '无车家庭占比', education_share: '学历占比', young_adult_share: '青年占比', household_size: '户均人数',
  job_entropy: '岗位混合度', transit_distance: '公共交通距离', employment_near_transit: '公共交通附近就业',
  transit_frequency: '公共交通频次', unemployment_percent: '失业比例', commute_minutes: '通勤时间',
};

export function DataPage() {
  const { data, study } = useResearch();
  const [tab, setTab] = useState('coverage');
  const [evidence, setEvidence] = useState(false);
  if (!data || !study) return null;
  const source = data.dataset;
  const splits = [
    ...(study.split.training != null ? [{ label: '真实训练区', value: study.split.training, cls: 'training' }] : []),
    ...(study.split.development != null ? [{ label: '真实开发区', value: study.split.development, cls: 'development' }] : []),
    ...(study.split.validation != null ? [{ label: '真实验证区', value: study.split.validation, cls: 'validation' }] : []),
    { label: '空间对照区', value: study.split.test, cls: 'test' },
  ];
  return <div className="research-page">
    <div className="research-page-title"><div><h1>数据与划分</h1><p>观测范围、属性来源与实验分区</p></div><Button variant="outlined" onClick={() => setEvidence(true)}>来源详情</Button></div>
    <Section className="dataset-source"><div className="file-symbol"><FileJson2 size={26} strokeWidth={1.5} /></div><div className="dataset-source-main"><h2>{source.filename}</h2><p>{source.name} · {number(source.areas)} 个区域 · {source.attributes} 个属性</p></div><Chip label="真实区域汇总" variant="outlined" /><div className="dataset-trip-count"><strong>{number(source.trips)}</strong><span>文件累计行程量 · 统计期未提供</span></div></Section>
    <div className="study-toolbar"><StudySelector /><span className="study-description">划分只使用几何位置，不使用行程标签。</span></div>
    <Section title="空间分区" actions={<span className="section-meta">{number(source.areas)} 个区域</span>}>
      <div className="partition-strip">{splits.map((part) => <div key={part.label} className={part.cls} style={{ flexGrow: part.value }}><span>{part.label}</span><strong>{part.value}<small>区</small></strong></div>)}</div>
      <div className="partition-caption"><p>{study.protocol}</p><span>按空间块划分</span></div>
    </Section>
    <Section title="数据说明"><Tabs value={tab} onChange={(_, value: string) => setTab(value)} aria-label="数据说明视图" className="section-tabs"><Tab value="coverage" label="观测覆盖" /><Tab value="features" label="输入与排除项" /><Tab value="lineage" label="生成数据谱系" /></Tabs>
      {tab === 'coverage' && <TableContainer><Table aria-label="数据观测覆盖"><TableHead><TableRow><TableCell>项目</TableCell><TableCell>文件提供内容</TableCell><TableCell>状态</TableCell></TableRow></TableHead><TableBody>{source.fields.map(([key, value, status]) => <TableRow key={key}><TableCell>{key}</TableCell><TableCell>{value}</TableCell><TableCell><span className={`coverage-status ${status === '已提供' ? 'provided' : ''}`}>{status}</span></TableCell></TableRow>)}</TableBody></Table></TableContainer>}
      {tab === 'features' && <div className="feature-sections"><div><h3>用于空间模型的属性</h3><p>每折的参考量、缩放与样条结点仅由该折训练区拟合。</p><div className="feature-list">{source.inputs.map((field) => <Chip key={field} label={fieldLabels[field] ?? field} variant="outlined" />)}</div></div><div><h3>排除的预测输入</h3><p>避免区域身份记忆与目标相关字段泄漏。FALSE + TRUE 与 TripCount 完全相等。</p><div className="feature-list excluded">{source.excluded.map((field) => <Chip key={field} label={field} variant="outlined" />)}</div></div></div>}
      {tab === 'lineage' && <div className="lineage-content"><p className="lineage-source">谱系来源：校准生成与真实对照</p><div className="lineage-flow">{[
        ['真实开发数据', `${data.studies[0].split.development} 个区域`, '拟合教师、协变量分布与残差尺度'],
        ['校准生成器', `${data.stability.synthetic.worlds} 个虚拟世界`, '静态区域属性与行程量均值规则'],
        ['生成训练数据', `${number(data.stability.synthetic.training_regions)} 个区域`, '只用生成标签拟合学生系数'],
        ['生成验证数据', `${number(data.stability.synthetic.validation_regions)} 个区域`, '按教师均值保真度选择正则强度'],
      ].map(([title, count, description], i) => <div className="lineage-node" key={title}><span className="lineage-step">{i + 1}</span><h3>{title}</h3><strong>{count}</strong><p>{description}</p>{i < 3 && <ArrowRight size={18} className="lineage-arrow" />}</div>)}</div><p className="scope-notice">真实对照区的标签不参与上述拟合。生成样本不是新增的独立真实观测，也没有恢复真实小时曲线、供给或取消过程。</p></div>}
    </Section>
    <div className="research-page-foot"><span>目标：{source.target}</span><Button size="small" component="a" href={reportURL('CALIBRATED_WORLD.md')} download startIcon={<ArrowDownToLine size={14} />}>下载实验协议</Button></div>
    <EvidenceDialog open={evidence} onClose={() => setEvidence(false)} />
  </div>;
}

function GrowthChart({ points }: { points: { x: number; y: number }[] }) {
  const width = 560, height = 228, left = 44, right = 28, top = 22, bottom = 42;
  const xMin = Math.min(...points.map((p) => p.x)), xMax = Math.max(...points.map((p) => p.x));
  const yMax = Math.max(5, Math.ceil(Math.max(...points.map((p) => p.y)) / 5) * 5);
  const x = (value: number) => left + (value-xMin) / Math.max(1, xMax-xMin) * (width-left-right);
  const y = (value: number) => height-bottom - value/yMax*(height-top-bottom);
  return <svg className="growth-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`生成样本扩充后的分布变化：${points.map((p) => `${p.x}个样本，${p.y.toFixed(2)}个百分点`).join('；')}`}>
    {[0, yMax/2, yMax].map((value) => <g key={value}><line x1={left} x2={width-right} y1={y(value)} y2={y(value)} className="plot-grid" /><text x={left-12} y={y(value)+4} textAnchor="end" className="plot-label">{value}</text></g>)}
    <polyline points={points.map((p) => `${x(p.x)},${y(p.y)}`).join(' ')} fill="none" className="plot-line" />
    {points.map((point) => <g key={point.x}><circle cx={x(point.x)} cy={y(point.y)} r={4} className="plot-point" /><text x={x(point.x)} y={y(point.y)-12} textAnchor="middle" className="plot-value">{point.y.toFixed(2)}</text><text x={x(point.x)} y={height-15} textAnchor="middle" className="plot-label">{number(point.x)}</text></g>)}
  </svg>;
}

export function StabilityPage() {
  const { data } = useResearch();
  const [tab, setTab] = useState('current');
  if (!data) return null;
  const value = data.stability;
  return <div className="research-page">
    <div className="research-page-title"><div><h1>规则与稳定性</h1><p>检查关系是否依赖少量样本，以及生成数据扩充后的变化</p></div><Button component="a" href={reportURL('RESEARCH_CALIBRATION.md')} download variant="outlined" startIcon={<BookOpen size={16} />}>文献笔记</Button></div>
    <Tabs value={tab} onChange={(_, selected: string) => setTab(selected)} aria-label="规则研究阶段" className="page-tabs"><Tab value="current" label="平滑模型与生成训练" /><Tab value="conservative" label="早期稳健规则" /></Tabs>
    {tab === 'current' ? <>
      <div className="model-rule-summary"><div className="model-rule-icon"><Layers3 size={23} /></div><div><h2>平滑 Poisson 加性模型</h2><p>{value.model}</p></div><div className="model-rule-facts"><span>{data.dataset.inputs.length} 个属性</span><span>{value.folds.length} 个空间折</span><span>L2 正则</span></div></div>
      <div className="stability-grid"><Section title="真实样本重采样" actions={<span className="evidence-tag">开发区检查</span>}>
        <p className="section-description">每个空间块抽取 80% 样本，按 5 个固定种子重训。比较重训模型与全量模型的预测份额。</p>
        <div className="stability-numbers"><div><span>平均变化</span><strong>{value.resampling.mean_tv_vs_full.toFixed(2)}<small>pp</small></strong></div><div><span>最大变化</span><strong>{value.resampling.max_tv_vs_full.toFixed(2)}<small>pp</small></strong></div><span className="plain-status">{value.resampling.passes ? '满足本轮筛选条件' : '未满足筛选条件'}</span></div>
        <div className="resample-bars">{value.resampling.runs.map((run) => <div key={run.seed}><span>种子 {run.seed}</span><div><i style={{ width: `${run.tv_vs_full_percent / 5 * 100}%` }} /></div><strong>{run.tv_vs_full_percent.toFixed(2)}</strong></div>)}</div>
        <p className="panel-caption">筛选条件：平均变化 ≤ 5 pp，最大变化 ≤ 10 pp。</p>
      </Section><Section title="生成样本扩充"><p className="section-description">相对 9,216 个生成样本拟合的学生，比较不同生成样本量下的预测分布变化。</p>
        <GrowthChart points={value.growth.runs.map((run) => ({ x: run.training_regions, y: run.tv_vs_full_student_percent }))} />
        <p className="panel-caption">纵轴为 TV 变化（pp），不是预测准确率。全量点为 0 是参照定义。</p>
      </Section></div>
      <Section title="规模与适用边界"><div className="stability-check-list">{value.scale.map((check) => <div key={check.factor}><span>训练行程量整体乘 {check.factor}</span><strong>{check.share_tv_percent < .0001 ? '< 0.0001' : check.share_tv_percent.toFixed(4)} pp</strong><p>份额变化接近浮点精度；不代表未来行为变化已经验证。</p></div>)}
        <div><span>联合特征支持提示</span><strong>{value.support.outside_count} / {value.support.rows} 区</strong><p>触发简化近邻距离提示；其余区域也不保证可靠。</p></div><div><span>生成分批与扩充复现</span><strong>{value.synthetic.partition_repeatable ? '逐行一致' : '未通过'}</strong><p>固定种子与区域编号的重复生成结果保持一致。</p></div>
      </div></Section>
      <div className="research-boundary"><span>解释范围</span><p>这些是当前静态文件上的敏感性检查。只有一个城市、一次空间划分，不能证明规则长期不变，也没有验证时间漂移或未来总需求。</p></div>
    </> : <>
      <Section title="训练区提取的幂次关联"><div className="conservative-rule"><div className="formula">w ∝ (1 + 人口 / {Math.round(value.reference[0])})<sup>{value.slopes[0].toFixed(3)}</sup> × (1 + 就业密度 / {value.reference[1].toFixed(4)})<sup>{value.slopes[1].toFixed(3)}</sup></div><p>此式来自早期稳健规则实验，不是当前平滑模型的完整函数。只表示条件关联，不表示因果关系。</p></div>
        <TableContainer><Table aria-label="早期规则稳定性"><TableHead><TableRow><TableCell>属性</TableCell><TableCell align="right">拟合系数</TableCell><TableCell align="right">同号比例</TableCell><TableCell align="right">Bootstrap 10%–90%</TableCell><TableCell>保留情况</TableCell></TableRow></TableHead><TableBody>{value.features.map((name, index) => <TableRow key={name}><TableCell>{fieldLabels[name] ?? name}</TableCell><TableCell align="right">{value.slopes[index].toFixed(3)}</TableCell><TableCell align="right">{(value.signSupport[index]*100).toFixed(0)}%</TableCell><TableCell align="right">{value.slopeLow[index].toFixed(2)}–{value.slopeHigh[index].toFixed(2)}</TableCell><TableCell>{value.active[index] ? '保留' : '未保留'}</TableCell></TableRow>)}</TableBody></Table></TableContainer>
      </Section><Section title="早期规则的异常值敏感性"><div className="outlier-check"><span>固定随机选取 {value.outlier.corrupted_training_rows} 条训练记录，标签放大 {value.outlier.multiplier} 倍</span><strong>{value.outlier.prediction_share_tv_percent.toFixed(2)}<small>pp 分布变化</small></strong></div><p className="panel-caption">结果仅适用于这一早期稳健规则，不能套用到后续平滑模型。</p></Section>
    </>}
  </div>;
}

export function RecordsPage() {
  const { data, selectStudy } = useResearch();
  const app = useApp();
  const [tab, setTab] = useState('reports');
  if (!data) return null;
  return <div className="research-page">
    <div className="research-page-title"><div><h1>实验记录</h1><p>已完成的研究报告与本机计算任务</p></div>{tab === 'jobs' && <Button variant="contained" onClick={() => app.openDialog('train')} disabled={!app.online}>提交 GRU 训练</Button>}</div>
    <Tabs value={tab} onChange={(_, selected: string) => setTab(selected)} aria-label="记录类型" className="page-tabs"><Tab value="reports" label="研究报告" /><Tab value="jobs" label="计算任务" /></Tabs>
    {tab === 'reports' ? <>
      <Section className="research-records">{data.studies.map((study) => <div className="research-record" key={study.id}><div className="record-document"><FileText size={22} strokeWidth={1.5} /></div><button className="record-main" onClick={() => { selectStudy(study.id); app.go('compare'); }}><span className="record-stage">{study.stage} · {data.date}</span><h2>{study.title}</h2><p>{study.finding}</p></button><span className="record-status">已有报告</span><IconButton component="a" href={reportURL(`${study.report}.md`)} download aria-label={`下载${study.title}报告`}><ArrowDownToLine size={18} /></IconButton><IconButton aria-label={`打开${study.title}`} onClick={() => { selectStudy(study.id); app.go('compare'); }}><ChevronRight size={18} /></IconButton></div>)}</Section>
      <div className="record-references"><div><h2>方法与文献</h2><p>原始论文的阅读范围、采用的思想和未复现部分。</p></div><Button component="a" href={reportURL('RESEARCH_CALIBRATION.md')} download startIcon={<BookOpen size={16} />}>下载文献笔记</Button><Button component="a" href={reportURL('calibrated-world.json')} download startIcon={<ArrowDownToLine size={16} />}>汇总 JSON</Button></div>
    </> : <div className="embedded-jobs"><p className="scope-notice">这里显示本机 API 的仿真、导入与 GRU 任务；静态空间研究报告与这些任务分别记录。</p>{!app.online && app.loaded ? <Section title="计算服务不可达"><p className="section-description">连接服务后才能读取任务状态。研究报告仍可查看。</p><Button onClick={() => app.go('system')}>检查连接与资源</Button></Section> : <JobsPage />}</div>}
  </div>;
}
