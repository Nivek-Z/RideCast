import { ArrowUpRight, Check, ChevronDown } from 'lucide-react';
import { useState } from 'react';
import type { ResearchSnapshot } from '../research/types';
import { number, tv } from './types';

const shares = [
  ['训练标签', '真实开发区', '拟合平滑规则、联合属性分布与噪声尺度'],
  ['生成标签', '静态虚拟区域', '按世界编号和种子可重复生成'],
  ['学生训练', '生成样本', '拟合学生系数；用生成验证世界选择正则'],
  ['最终对照', '真实留出区', '只用于评估行程空间份额'],
];
const report = 'research/reports/';

export function Evidence({ data }: { data?: ResearchSnapshot }) {
  const [open, setOpen] = useState<string>();
  return <div className="reveal">
    <header className="page-heading"><h1>研究记录</h1></header>
    <div className="evidence-top">
      <section className="surface panel">
        <div className="panel-title"><h2>真实数据</h2><span>Chicago TNP</span></div>
        <div className="evidence-stats"><div><strong>{data?.dataset.areas ?? '—'}</strong><span>普查区</span></div>
          <div><strong>364 / 431</strong><span>开发区 / 留出区</span></div></div>
        <p>文件提供每区的累计行程、人口、就业和建成环境属性。研究使用预先划好的 16 个空间块，其中 8 个用于开发。</p>
        <dl className="evidence-list"><div><dt>估计目标</dt><dd>累计 TripCount 的区域份额</dd></div><div><dt>统计起止与时间戳</dt><dd>未提供</dd></div>
          <div><dt>供给、取消及未服务请求</dt><dd>未观测</dd></div><div><dt>坐标参考</dt><dd>假设 EPSG:3435，原文件未声明</dd></div></dl>
      </section>
      <section className="surface panel">
        <div className="panel-title"><h2>每一步用了什么</h2></div>
        <ul className="lineage">{shares.map(([phase, source, explanation]) => <li key={phase}>
          <span><Check size={16} /></span><div><strong>{phase} · {source}</strong><p>{explanation}</p></div>
        </li>)}</ul>
      </section>
    </div>

    <section className="section">
      <div className="section-heading"><div><h2>稳定性证据</h2><p>比较数据重采样、样本增长和总量变化下的表现。</p></div></div>
      <div className="evidence-grid">
        <article className="surface panel"><h3>开发区重采样</h3>
          <strong className="evidence-value">{data?.stability.resampling.mean_tv_vs_full.toFixed(2) ?? '—'} <small>个百分点</small></strong>
          <p>五次按块抽取约 80% 开发样本，重训结果与全量拟合分布的平均 TV 差异。</p>
          <div className="spark-bars">{data?.stability.resampling.runs.map((run) => <div key={run.seed}><span>{run.seed}</span><meter min={0} max={5} value={run.tv_vs_full_percent} /><b>{run.tv_vs_full_percent.toFixed(2)}</b></div>)}</div>
        </article>
        <article className="surface panel"><h3>生成样本增长</h3>
          <strong className="evidence-value">{data ? number(data.stability.synthetic.training_regions) : '—'} <small>个拟合样本</small></strong>
          <p>固定正则强度，对比不同生成样本规模与完整学生模型的分布差异。</p>
          <div className="spark-bars">{data?.stability.growth.runs.map((run) => <div key={run.training_regions}><span>{number(run.training_regions)}</span><meter min={0} max={Math.max(1, ...data.stability.growth.runs.map(x => x.tv_vs_full_student_percent))} value={run.tv_vs_full_student_percent} /><b>{run.tv_vs_full_student_percent.toFixed(2)}</b></div>)}</div>
        </article>
        <article className="surface panel"><h3>可稳定复现的部分</h3>
          <strong className="evidence-value">{data?.stability.bootstrap ?? '—'} <small>次空间块重采样</small></strong>
          <p>早期幂次规则筛查方向和幅度。后续平滑模型采用有限样条结点与 L2 正则。</p>
          <p className="subtle">总行程量倍增检验、重采样与合成扩样检验均在当前数据和模型假设下成立。城市结构和供需变化需要新数据验证。</p>
        </article>
      </div>
    </section>

    <section className="section">
      <div className="section-heading"><div><h2>四个实验阶段</h2><p>展开查看方法和每轮结果。</p></div></div>
      <div className="surface evidence-history">
        {data?.studies.slice().reverse().map((study) => <article className="history-row" key={study.id}>
          <button className="history-trigger" type="button" onClick={() => setOpen(open === study.id ? undefined : study.id)} aria-expanded={open === study.id}>
            <div><strong>{study.title}</strong><span>{study.subtitle}</span></div><ChevronDown size={20} /></button>
          {open === study.id && <div className="history-detail">
            <p>{study.protocol}</p><p>{study.finding}</p>
            <div className="table-scroll"><table><thead><tr><th>方法</th><th>拟合来源</th><th>{study.developmentLabel} TV</th><th>留出区 TV</th></tr></thead>
              <tbody>{study.methods.map((method) => <tr key={method.id}><th>{method.label}</th><td>{method.train}</td>
                <td>{tv(method.development.tv)}</td><td>{tv(method.test.tv)}</td></tr>)}</tbody></table></div>
          </div>}
        </article>)}
      </div>
    </section>
    <section className="section surface panel limitations"><h2>结论适用范围</h2>
      <ul><li>同一个城市、同一份累计数据，空间留出区之前已被查看。本次是探索性研究。</li>
        <li>结果反映累计行程份额的空间差异，独立统计期和其他城市仍需验证。</li>
        <li>虚拟区域来自校准过的统计假设，提供可重复的模拟样本。真实供给、取消和时间动态仍未知。</li></ul>
      <div className="related-links">
        <a href={`${report}calibrated-world.md`} target="_blank" rel="noreferrer">校准实验报告 <ArrowUpRight size={15} /></a>
        <a href={`${report}teacher-data-transfer.md`} target="_blank" rel="noreferrer">未校准迁移报告 <ArrowUpRight size={15} /></a>
        <a href={`${report}RESEARCH_CALIBRATION.md`} target="_blank" rel="noreferrer">文献与方法 <ArrowUpRight size={15} /></a>
      </div>
    </section>
  </div>;
}
