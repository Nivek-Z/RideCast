import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
export const researchSources = ['reports/calibrated-world.json', 'reports/teacher-data-transfer.json'];
const output = resolve(repositoryRoot, 'web/public/research');
const finite = (value) => typeof value === 'number' && Number.isFinite(value) ? value : null;
const meanFinite = (values) => { const present = values.filter((value) => finite(value) !== null); return present.length ? present.reduce((a, b) => a + b, 0) / present.length : null; };
export const metrics = (value) => ({ tv: finite(value?.total_variation_percent), spearman: finite(value?.spearman), relativeMae: finite(value?.relative_volume_mae) });

const labels = {
  uniform: '均匀分配', employment_density: '就业密度基线', original_simulator_transfer: '未经校准的虚拟世界',
  synthetic_ridge: '未经校准的虚拟世界', population: '人口权重', employment_mass_proxy: '就业密度 × 面积',
  population_density: '人口密度权重', calibrated_rule_direct: '稳健规则直接预测', real_only: '真实数据直接训练',
  calibrated_synthetic_only: '校准生成数据训练', real_plus_calibrated_synthetic: '真实与生成数据混合训练',
};
function row(id, label, route, train, test, development, config = [], selection = '') {
  return { id, label, route, train, test: metrics(test), development: metrics(development), config, selection };
}
function modelLabel(config) {
  if (config.family === 'poisson') return `Poisson · ${config.size} 属性 · α ${config.alpha}`;
  if (config.family === 'spline') return `平滑加性模型 · ${config.size} 属性 · α ${config.alpha}`;
  if (config.family === 'boost') return `受约束提升树 · ${config.loss === 'poisson' ? '计数' : '对数'} · 叶样本 ${config.min_samples_leaf}`;
  if (config.family === 'power') return `稳健幂次规则 · α ${config.alpha}`;
  if (config.family === 'student') return config.synthetic_fraction == null ? '真实数据 · 二次模型' : `生成数据权重 ${config.synthetic_fraction * 100}%`;
  return labels[config.name] ?? config.name;
}

export function buildSnapshot(report, original, digest) {
  const stable = report.stable_rule_stage, search = report.development_search_stage, distill = report.gam_generated_data_stage;
  const selected = search.selection.selected;
  const cv = search.cv[selected];
  const studies = [
    {
      id: 'calibrated-gam', title: '校准生成与真实对照', subtitle: '平滑规则校准后的生成训练', report: 'calibrated-world',
      stage: '研究 04', developmentLabel: '空间交叉验证', split: { development: search.split.development_regions, test: search.split.test_regions },
      protocol: '教师由真实开发区的空间交叉验证选出；学生只用生成标签拟合系数，正则强度按生成验证世界的保真度选择。',
      finding: '校准生成训练保留了教师的大部分能力；两者差距尚不足以证明生成数据提供额外精度。',
      methods: [
        row('original', '未经校准的虚拟世界', 'synthetic', '纯虚构城市', original.test.synthetic_ridge, null, [['生成规则', '原有随机规则'], ['真实标签拟合', '无']]),
        row('employment', '就业密度基线', 'baseline', '无参数拟合', original.test.employment_density, null, [['输入', '就业密度'], ['用途', '统计基线']]),
        row('teacher', '真实数据直接训练', 'real', `${search.split.development_regions} 个真实区域`, distill.test.teacher_direct,
          { total_variation_percent: cv.mean_fold_tv, spearman: meanFinite(cv.folds.map((fold) => fold.spearman)) }, [['模型', '平滑 Poisson 加性模型'], ['配置', selected], ['属性', search.protocol.features.length], ['选择依据', '开发区空间交叉验证']], '开发区选定'),
        row('student', '校准生成数据训练', 'synthetic', `${distill.synthetic.training_regions.toLocaleString('en-US')} 个生成区域`, distill.test.synthetic_only_student, null,
          [['模型', '生成数据拟合的 Poisson 学生'], ['真实标签', '用于教师与生成器校准'], ['学生系数', '仅用生成标签拟合'], ['正则强度', distill.selected_student_alpha], ['生成验证区域', distill.synthetic.validation_regions]], '生成验证集选定'),
      ],
    },
    {
      id: 'spatial-search', title: '空间模型搜索', subtitle: `${search.protocol.candidates.length} 个候选的空间分组验证`, report: 'calibrated-world',
      stage: '研究 03', developmentLabel: '空间交叉验证均值', split: { development: search.split.development_regions, test: search.split.test_regions },
      protocol: '开发区按八个空间块留一验证。以折平均 TV 加波动惩罚排序，并在开发区检查样本稳定性，再进行最终对照。',
      finding: '平滑加性模型由开发区选出。一个验证块仅有两个区域，交叉验证仍有小样本和空间依赖限制。',
      methods: search.protocol.candidates.map((config) => {
        const source = config.family === 'uniform' || config.family === 'employment' ? 'baseline' : config.family === 'student' && config.synthetic_fraction != null ? config.synthetic_fraction === 1 ? 'synthetic' : 'mixed' : 'real';
        const value = search.cv[config.name];
        return row(config.name, modelLabel(config), source, source === 'baseline' ? '无参数拟合' : source === 'synthetic' ? '校准生成数据' : source === 'mixed' ? '真实与生成数据' : `${search.split.development_regions} 个真实区域`,
          search.test[config.name], { total_variation_percent: value?.mean_fold_tv, spearman: meanFinite((value?.folds ?? []).map((fold) => fold.spearman)) },
          [['配置标识', config.name], ['属性数', config.size], ['折间标准差', finite(value?.std_fold_tv)?.toFixed(2) ?? '—'], ['最终对照', search.test[config.name] ? '已评价' : '未进入最终评价']], config.name === selected ? '开发区选定' : '');
      }),
    },
    {
      id: 'stable-rules', title: '稳健规则初探', subtitle: '真实训练区校准后的首次对照', report: 'calibrated-world', stage: '研究 02',
      developmentLabel: '验证区', split: { training: stable.split.train_regions, validation: stable.split.validation_regions, test: stable.split.test_regions },
      protocol: '从真实训练区提取平滑幂次规则，用空间块重采样保留相对稳定的项，再比较真实、生成与混合训练。',
      finding: '生成训练较原始仿真改善，但这一轮没有优于直接使用真实训练数据的对照。',
      methods: Object.keys(stable.selection.validation).map((key) => row(key, labels[key] ?? key,
        ['uniform', 'employment_density'].includes(key) ? 'baseline' : key.includes('synthetic') || key === 'original_simulator_transfer' ? key.startsWith('real_plus') ? 'mixed' : 'synthetic' : 'real',
        ['uniform', 'employment_density'].includes(key) ? '无参数拟合' : key === 'original_simulator_transfer' ? '纯虚构城市' : `${stable.split.train_regions} 个真实区校准`,
        stable.test[key], stable.selection.validation[key], [['规则', stable.selection.generator], ['选择', '真实验证区']], key === stable.selection.final_choice ? '验证区选定' : '')),
    },
    {
      id: 'uncalibrated', title: '未校准迁移', subtitle: '原有虚拟世界的直接迁移', report: 'teacher-data-transfer', stage: '研究 01',
      developmentLabel: '真实验证区', split: { validation: original.split.validation_tracts, test: original.split.test_tracts },
      protocol: '虚构城市的聚合完成量训练静态岭回归；真实标签用于验证与最终比较，没有拟合模型系数。',
      finding: '原有模拟规律的迁移接近均匀分配，没有显示有效的真实空间预测能力。',
      methods: original.protocol.candidates.map((key) => row(key, labels[key] ?? key, key === 'synthetic_ridge' ? 'synthetic' : 'baseline', key === 'synthetic_ridge' ? `${original.synthetic_training.train_zones} 个虚构区域` : '无参数拟合', original.test[key], original.validation[key], [['训练世界', original.synthetic_training.worlds], ['真实参数训练', '无']], key === original.selected_using_validation ? '验证区选定' : '')),
    },
  ];
  const source = original.source;
  return {
    schemaVersion: 1, date: report.date, digest,
    dataset: { name: 'Chicago TNP', filename: 'chicago_tnp.geojson', areas: source.feature_count, attributes: source.property_count,
      trips: source.trip_total, missing: source.missing_cells, sourceDigest: source.file_sha256, target: '区域累计完成行程量（口径待确认）',
      timezone: '未提供时间观测', crs: 'EPSG:3435（假设，待确认）', hasTime: false,
      inputs: search.protocol.features, excluded: ['区域编号', 'FALSE / TRUE', '拼车比例', '行程均价', '平均时长', '平均里程'],
      fields: [
        ['空间范围', `${source.feature_count} 个普查区`, '已提供'], ['人口与建成环境', `${source.property_count} 个静态属性`, '已提供'],
        ['区域行程量', '累计 TripCount', '待确认口径'], ['观测起止日期', '未提供', '缺失'], ['逐单时间戳', '未提供', '缺失'],
        ['取消与未服务请求', '未观测', '未知'], ['真实司机供给', '未观测', '未知'], ['原始坐标系', '文件未声明', '待确认'],
      ],
    },
    studies,
    stability: { model: selected, bootstrap: stable.rule.bootstraps, features: stable.rule.features, slopes: stable.rule.slopes,
      reference: stable.rule.reference, active: stable.rule.active, signSupport: stable.rule.sign_support,
      slopeLow: stable.rule.bootstrap_slope_q10, slopeHigh: stable.rule.bootstrap_slope_q90,
      resampling: search.selection.stability[selected], scale: search.scale_checks,
      growth: distill.synthetic_growth_check, conservativeGrowth: stable.stability.training_sample_growth,
      outlier: stable.stability.outlier, synthetic: distill.synthetic,
      support: distill.joint_feature_support, folds: cv.folds, cvMean: cv.mean_fold_tv, cvStd: cv.std_fold_tv,
      noiseAssumption: '训练残差尺度下的对数正态与 Poisson 假设',
    },
    limits: [
      '同一文件和对照区已被反复查看，本轮属于探索性研究。',
      '没有时间戳和观测统计期，结果不能用于评价 GRU 小时预测。',
      '生成样本保留了学习到的关系，没有增加独立真实观测。',
      '样本稳定性检查不能证明规则在未来概念变化下仍然有效。',
    ],
    verification: { tests: 101, http: report.verification.http.checks_passed, evidence: '本机验证' },
  };
}

export async function syncResearch() {
  const texts = await Promise.all(researchSources.map((file) => readFile(resolve(repositoryRoot, file), 'utf8')));
  const digest = createHash('sha256').update(texts.join('\n')).digest('hex');
  const snapshot = buildSnapshot(JSON.parse(texts[0]), JSON.parse(texts[1]), digest);
  await mkdir(resolve(output, 'reports'), { recursive: true });
  await writeFile(resolve(output, 'snapshot.json'), JSON.stringify(snapshot));
  for (const name of ['calibrated-world.md', 'teacher-data-transfer.md', 'calibrated-world.json']) {
    await writeFile(resolve(output, 'reports', name), await readFile(resolve(repositoryRoot, 'reports', name)));
  }
  for (const name of ['RESEARCH_CALIBRATION.md', 'CALIBRATED_WORLD.md']) {
    await writeFile(resolve(output, 'reports', name), await readFile(resolve(repositoryRoot, 'docs', name)));
  }
  return snapshot;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const snapshot = await syncResearch();
  console.log(`Research snapshot: ${snapshot.studies.length} studies · ${snapshot.dataset.areas} regions`);
}
