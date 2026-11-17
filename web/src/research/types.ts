export type Metric = 'tv' | 'spearman';
export type Scope = 'test' | 'development';
export type MethodRoute = 'synthetic' | 'real' | 'mixed' | 'baseline';
export interface Metrics { tv: number | null; spearman: number | null; relativeMae: number | null }
export interface Method {
  id: string; label: string; route: MethodRoute; train: string;
  test: Metrics; development: Metrics; config: [string, string | number][]; selection: string;
}
export interface Study {
  id: string; title: string; subtitle: string; report: string; stage: string; developmentLabel: string;
  split: { training?: number; validation?: number; development?: number; test: number };
  protocol: string; finding: string; methods: Method[];
}
export interface ResearchSnapshot {
  schemaVersion: 1; date: string; digest: string;
  dataset: { name: string; filename: string; areas: number; attributes: number; trips: number; missing: number;
    sourceDigest: string; target: string; timezone: string; crs: string; hasTime: boolean;
    inputs: string[]; excluded: string[]; fields: [string, string, string][] };
  studies: Study[];
  stability: { model: string; bootstrap: number; features: string[]; slopes: number[]; reference: number[];
    active: boolean[]; signSupport: number[]; slopeLow: number[]; slopeHigh: number[];
    resampling: { runs: { seed: number; training_regions: number; tv_vs_full_percent: number }[]; mean_tv_vs_full: number; max_tv_vs_full: number; passes: boolean };
    scale: { factor: number; share_tv_percent: number }[];
    growth: { runs: { training_regions: number; converged: boolean; tv_vs_full_student_percent: number; fidelity_to_teacher_mean_tv_percent: number }[] };
    conservativeGrowth: { fraction: number; mean_tv_vs_full_percent: number; max_tv_vs_full_percent: number }[];
    outlier: { corrupted_training_rows: number; multiplier: number; prediction_share_tv_percent: number };
    synthetic: { worlds: number; regions: number; training_regions: number; validation_regions: number; residual_log_sigma: number; partition_repeatable: boolean };
    support: { threshold_training_fold_q95: number; outside_count: number; rows: number };
    folds: { block: number; regions: number; total_variation_percent: number }[];
    cvMean: number; cvStd: number; noiseAssumption: string;
  };
  limits: string[]; verification: { tests: number; http: number; evidence: string };
}
