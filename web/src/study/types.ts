export interface Config { worlds_per_seed: number; teacher_alpha: number; seeds: number[] }
export type Mode = 'reproduction' | 'arena';
export type State = 'queued' | 'running' | 'cancelling' | 'succeeded' | 'failed' | 'cancelled';
export interface Score { total_variation_percent: number; spearman: number | null; relative_volume_mae: number }
export interface FormalMetrics {
  student: Score; teacher: Score; employment: Score; train_regions: number; validation_regions: number;
  student_alpha: number; synthetic_fidelity_tv: Record<string, number>; residual_log_sigma: number;
}
export interface ArenaMetrics {
  mean_tv: number; fold_std: number; seed_std: number | null; seed_means: { seed: number; tv: number }[];
  teacher_mean_tv: number; employment_mean_tv: number;
}
export interface Summary {
  protocol: string; mode: Mode; source_digest: string; config: Config;
  development_regions: number; holdout_regions: number; duration_seconds: number;
  metrics: FormalMetrics | ArenaMetrics; steps: { stage: string; elapsed_seconds: number }[];
}
export interface Run {
  id: string; state: State; progress: number; stage: string; message: string; summary?: Summary;
  error: { message: string } | null; created_at: string; started_at: string | null; finished_at: string | null;
  params: { mode: Mode; config?: Config; batch_id?: string };
}
export interface MapData {
  ids: string[]; paths: string[]; development: boolean[]; blocks: number[];
  regions: number[]; actual: number[]; predicted: number[]; teacher: number[];
}
export interface Fold { block: number; regions: number; tv: number; teacher_tv: number; employment_tv: number }
export interface Artifact { map: MapData; diagnostics: { folds?: Fold[] } }
export interface Status { archive_ready: boolean; worker_online: boolean }

export const active = (run: Run) => ['queued', 'running', 'cancelling'].includes(run.state);
export const number = (value: number) => value.toLocaleString('zh-CN');
export const tv = (value: number | null | undefined) => value == null ? '—' : `${value.toFixed(2)}%`;
export const seconds = (value: number) => `${value.toFixed(2)} 秒`;
export const stateText: Record<State, string> = {
  queued: '排队中', running: '运行中', cancelling: '正在停止', succeeded: '已完成', failed: '失败', cancelled: '已停止',
};
