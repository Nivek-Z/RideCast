// 展示格式化。后端时间一律 UTC ISO8601，展示按 world.timezone 转换。

export const KIND_LABEL: Record<string, string> = {
  residential: '居住',
  business: '商务',
  commercial: '商贸',
  transport: '枢纽',
  leisure: '文娱',
  mixed: '混合',
};
export const KIND_COLOR: Record<string, string> = {
  residential: 'var(--teal)',
  business: 'var(--accent)',
  commercial: 'var(--violet)',
  transport: 'var(--amber)',
  leisure: 'var(--green)',
  mixed: 'var(--text-3)',
};
export const JOB_KIND: Record<string, string> = { simulate: '场景仿真', import: '数据导入', train: '模型训练' };
export const JOB_STATE: Record<string, { label: string; tone: string }> = {
  queued: { label: '排队中', tone: '' },
  running: { label: '运行中', tone: 'blue' },
  cancelling: { label: '取消中', tone: 'amber' },
  succeeded: { label: '已完成', tone: 'green' },
  failed: { label: '失败', tone: 'red' },
  cancelled: { label: '已取消', tone: '' },
};
export const WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];

const nf0 = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1, minimumFractionDigits: 1 });
const nf2 = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2, minimumFractionDigits: 2 });

const missing = (v: unknown): v is null | undefined => v === null || v === undefined || Number.isNaN(v);

export const int = (v: number | null | undefined) => (missing(v) ? '—' : nf0.format(v));
export const fix1 = (v: number | null | undefined) => (missing(v) ? '—' : nf1.format(v));
export const fix2 = (v: number | null | undefined) => (missing(v) ? '—' : nf2.format(v));
/** 0–1 比例 → 百分比 */
export const ratio = (v: number | null | undefined, digits = 1) =>
  missing(v) ? '未知' : `${(v * 100).toFixed(digits)}%`;
/** 后端已乘 100 的百分数（WAPE/sMAPE/改进率） */
export const pct = (v: number | null | undefined, digits = 1) => (missing(v) ? '—' : `${v.toFixed(digits)}%`);
export const signed = (v: number | null | undefined) => (missing(v) ? '未知' : `${v > 0 ? '+' : ''}${nf0.format(v)}`);
export function compact(v: number | null | undefined) {
  if (missing(v)) return '—';
  if (Math.abs(v) >= 1e8) return `${(v / 1e8).toFixed(2)}亿`;
  if (Math.abs(v) >= 1e4) return `${(v / 1e4).toFixed(Math.abs(v) >= 1e5 ? 1 : 2)}万`;
  return nf0.format(v);
}

const cache = new Map<string, Intl.DateTimeFormat>();
function dtf(tz: string, key: string, opts: Intl.DateTimeFormatOptions) {
  const id = `${tz}|${key}`;
  let f = cache.get(id);
  if (!f) {
    try {
      f = new Intl.DateTimeFormat('zh-CN', { timeZone: tz, hourCycle: 'h23', ...opts });
    } catch {
      f = new Intl.DateTimeFormat('zh-CN', { hourCycle: 'h23', ...opts });
    }
    cache.set(id, f);
  }
  return f;
}
type T = string | number | Date | null | undefined;
const d = (t: T) => (t instanceof Date ? t : new Date(t as string | number));

export const time = {
  full: (t: T, tz: string) =>
    missing(t) ? '—' : dtf(tz, 'full', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(d(t)),
  md: (t: T, tz: string) => (missing(t) ? '—' : dtf(tz, 'md', { month: '2-digit', day: '2-digit' }).format(d(t))),
  mdhm: (t: T, tz: string) =>
    missing(t) ? '—' : dtf(tz, 'mdhm', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(d(t)),
  hm: (t: T, tz: string) => (missing(t) ? '—' : dtf(tz, 'hm', { hour: '2-digit', minute: '2-digit' }).format(d(t))),
  hms: (t: T, tz: string) =>
    missing(t) ? '—' : dtf(tz, 'hms', { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(d(t)),
  date: (t: T, tz: string) =>
    missing(t) ? '—' : dtf(tz, 'date', { year: 'numeric', month: '2-digit', day: '2-digit' }).format(d(t)),
  weekday: (t: T, tz: string) => (missing(t) ? '' : dtf(tz, 'wd', { weekday: 'short' }).format(d(t))),
};

/** 该时区在给定时刻相对 UTC 的偏移（分钟） */
export function tzOffsetMinutes(tz: string, at: Date) {
  const parts = dtf(tz, 'parts', {
    year: 'numeric', month: 'numeric', day: 'numeric', hour: 'numeric', minute: 'numeric', second: 'numeric',
  }).formatToParts(at);
  const p: Record<string, number> = {};
  for (const part of parts) if (part.type !== 'literal') p[part.type] = Number(part.value);
  const asUtc = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
  return Math.round((asUtc - Math.floor(at.getTime() / 1000) * 1000) / 60000);
}

/** 把 "YYYY-MM-DD" 或 "YYYY-MM-DDTHH:mm" 的当地墙上时间转成带偏移的 ISO 字符串 */
export function zonedISO(local: string, tz: string) {
  const [date, clock = '00:00'] = local.split('T');
  const guess = new Date(`${date}T${clock.slice(0, 5)}:00Z`);
  const offset = tzOffsetMinutes(tz, new Date(guess.getTime() - tzOffsetMinutes(tz, guess) * 60000));
  const sign = offset >= 0 ? '+' : '-';
  const abs = Math.abs(offset);
  const hh = String(Math.floor(abs / 60)).padStart(2, '0');
  const mm = String(abs % 60).padStart(2, '0');
  return `${date}T${clock.slice(0, 5)}:00${sign}${hh}:${mm}`;
}

/** 时刻在该时区的 "YYYY-MM-DDTHH:mm"，用于 datetime-local 输入框 */
export function localInput(t: T, tz: string) {
  const at = d(t);
  const shifted = new Date(at.getTime() + tzOffsetMinutes(tz, at) * 60000);
  return shifted.toISOString().slice(0, 16);
}

export function duration(seconds: number | null | undefined) {
  if (missing(seconds)) return '—';
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} 秒`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分 ${Math.round(seconds % 60)} 秒`;
  return `${Math.floor(seconds / 3600)} 时 ${Math.round((seconds % 3600) / 60)} 分`;
}
export function ago(t: T) {
  if (missing(t)) return '—';
  const s = (Date.now() - d(t).getTime()) / 1000;
  if (s < 5) return '刚刚';
  if (s < 60) return `${Math.floor(s)} 秒前`;
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  return `${Math.floor(s / 86400)} 天前`;
}
export const shortId = (id: string | null | undefined) => (id ? id.slice(0, 2) + id.slice(2, 10) : '—');
