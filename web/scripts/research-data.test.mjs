import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import test from 'node:test';
import { buildSnapshot, metrics, repositoryRoot } from './sync-research.mjs';

const report = JSON.parse(await readFile(resolve(repositoryRoot, 'reports/calibrated-world.json'), 'utf8'));
const original = JSON.parse(await readFile(resolve(repositoryRoot, 'reports/teacher-data-transfer.json'), 'utf8'));
const snapshot = buildSnapshot(report, original, createHash('sha256').update('test').digest('hex'));

test('report scopes and source counts remain traceable', () => {
  assert.equal(snapshot.studies.length, 4);
  assert.equal(snapshot.dataset.areas, original.source.feature_count);
  assert.equal(snapshot.dataset.hasTime, false);
  for (const study of snapshot.studies) {
    assert.equal(Object.values(study.split).reduce((a, b) => a + b, 0), snapshot.dataset.areas);
    assert.equal(new Set(study.methods.map((method) => method.id)).size, study.methods.length);
  }
});

test('TV stays in percent, rank correlation stays a ratio, and missing values stay null', () => {
  const final = snapshot.studies[0];
  const student = final.methods.find((method) => method.id === 'student');
  assert.equal(student.test.tv, report.gam_generated_data_stage.test.synthetic_only_student.total_variation_percent);
  assert.equal(student.test.spearman, report.gam_generated_data_stage.test.synthetic_only_student.spearman);
  assert.equal(student.development.tv, null);
  assert.deepEqual(metrics({ total_variation_percent: 0, spearman: null }), { tv: 0, spearman: null, relativeMae: null });
  assert.equal(metrics({ total_variation_percent: Infinity }).tv, null);
});

test('cross-validation uses fold means and does not invent final test evaluations', () => {
  const study = snapshot.studies.find((item) => item.id === 'spatial-search');
  assert.equal(study.methods.length, report.development_search_stage.protocol.candidates.length);
  for (const method of study.methods) {
    const cv = report.development_search_stage.cv[method.id];
    assert.equal(method.development.tv, cv.mean_fold_tv);
    if (!(method.id in report.development_search_stage.test)) assert.equal(method.test.tv, null);
    const values = cv.folds.map((fold) => fold.spearman).filter((value) => typeof value === 'number');
    assert.equal(method.development.spearman, values.length ? values.reduce((a, b) => a + b, 0)/values.length : null);
  }
});

test('sensitivity values preserve their model stage and reference definition', () => {
  assert.equal(snapshot.stability.resampling.mean_tv_vs_full, report.development_search_stage.selection.stability[report.development_search_stage.selection.selected].mean_tv_vs_full);
  assert.equal(snapshot.stability.growth.runs.at(-1).tv_vs_full_student_percent, 0);
  assert.equal(snapshot.stability.outlier.prediction_share_tv_percent, report.stable_rule_stage.stability.outlier.prediction_share_tv_percent);
  assert.equal(snapshot.stability.active[2], false);
});
