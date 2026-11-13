import { AlertTriangle, Info, Loader2, X } from 'lucide-react';
import { useEffect, useRef, type CSSProperties, type ReactNode } from 'react';
import { ApiError } from '../api';

export function Card(props: {
  title?: ReactNode;
  sub?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
  flush?: boolean;
  style?: CSSProperties;
  className?: string;
}) {
  return (
    <section className={`card ${props.className ?? ''}`} style={props.style}>
      {(props.title || props.actions) && (
        <header className="card-head">
          <h3>{props.title}</h3>
          {props.sub && <span className="sub ellipsis">{props.sub}</span>}
          <span className="grow" />
          {props.actions}
        </header>
      )}
      <div className={`card-body ${props.flush ? 'flush' : ''}`}>{props.children}</div>
    </section>
  );
}

export function Stat(props: { k: ReactNode; v: ReactNode; unit?: string; d?: ReactNode; tone?: string; icon?: ReactNode }) {
  return (
    <div className="stat">
      <span className="k">{props.k}{props.icon && <span className="stat-icon">{props.icon}</span>}</span>
      <span className="v" style={props.tone ? { color: `var(--${props.tone})` } : undefined}>
        {props.v}
        {props.unit && <small>{props.unit}</small>}
      </span>
      {props.d && <span className="d">{props.d}</span>}
    </div>
  );
}

export function Seg<T extends string | number>(props: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: ReactNode }[];
}) {
  return (
    <div className="seg" role="tablist">
      {props.options.map((o) => (
        <button
          key={String(o.value)}
          role="tab"
          aria-selected={o.value === props.value}
          tabIndex={o.value === props.value ? 0 : -1}
          onKeyDown={(e) => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
            e.preventDefault();
            const i = props.options.findIndex((item) => item.value === props.value);
            const next = e.key === 'Home' ? 0 : e.key === 'End' ? props.options.length - 1 : (i + (e.key === 'ArrowRight' ? 1 : -1) + props.options.length) % props.options.length;
            props.onChange(props.options[next].value);
            (e.currentTarget.parentElement?.children[next] as HTMLButtonElement)?.focus();
          }}
          className={o.value === props.value ? 'on' : ''}
          onClick={() => props.onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Badge(props: { tone?: string; children: ReactNode; dot?: boolean }) {
  return (
    <span className={`badge ${props.tone ?? ''}`}>
      {props.dot && <i className="dot" style={{ width: 6, height: 6, background: 'currentColor', boxShadow: 'none' }} />}
      {props.children}
    </span>
  );
}

export function Field(props: { label: ReactNode; hint?: ReactNode; children: ReactNode; span2?: boolean; group?: boolean }) {
  // 默认用 <label> 包裹，使输入框自带可访问名称；内部已有 label 的控件组传 group
  const Tag = props.group ? 'div' : 'label';
  return (
    <Tag className={`field ${props.span2 ? 'span2' : ''}`}>
      <span className="label">{props.label}</span>
      {props.children}
      {props.hint && <span className="hint">{props.hint}</span>}
    </Tag>
  );
}

export function NumberInput(props: {
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
  style?: CSSProperties;
}) {
  return (
    <input
      className="input"
      type="number"
      style={props.style}
      value={Number.isFinite(props.value) ? props.value : ''}
      min={props.min}
      max={props.max}
      step={props.step ?? 1}
      onChange={(e) => props.onChange(e.target.value === '' ? NaN : Number(e.target.value))}
    />
  );
}

export function Empty(props: { title: ReactNode; children?: ReactNode; action?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="empty">
      {props.icon}
      <div className="t">{props.title}</div>
      {props.children && <div style={{ maxWidth: 420 }}>{props.children}</div>}
      {props.action}
    </div>
  );
}

export function Loading(props: { height?: number }) {
  return (
    <div className="empty" style={{ height: props.height }}>
      <Loader2 size={18} className="spin" />
    </div>
  );
}

export function Note(props: { tone?: 'warn' | 'err'; children: ReactNode }) {
  return (
    <div className={`note ${props.tone ?? ''}`}>
      {props.tone ? <AlertTriangle size={14} /> : <Info size={14} />}
      <div>{props.children}</div>
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const e = error as ApiError;
  const details = Array.isArray(e.details)
    ? (e.details as { loc: string[]; message: string }[]).map((d) => `${d.loc.slice(1).join('.')}: ${d.message}`).join('；')
    : null;
  return (
    <Note tone="err">
      <div>{e.message || '请求失败'}</div>
      {details && <div className="small">{details}</div>}
      {e.requestId && <div className="small muted mono">request_id {e.requestId}</div>}
    </Note>
  );
}

export function Progress(props: { value: number; tone?: string; indeterminate?: boolean }) {
  return (
    <div className={`progress ${props.tone ?? ''} ${props.indeterminate ? 'indet' : ''}`}>
      <i style={{ width: `${Math.max(0, Math.min(1, props.value)) * 100}%` }} />
    </div>
  );
}

function useEscape(onClose: () => void) {
  useEffect(() => {
    const on = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    addEventListener('keydown', on);
    return () => removeEventListener('keydown', on);
  }, [onClose]);
}

function useDialogFocus() {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const el = ref.current;
    const selector = 'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]';
    (el?.querySelector<HTMLElement>('input:not(:disabled), select:not(:disabled), textarea:not(:disabled)') ?? el?.querySelector<HTMLElement>(selector))?.focus();
    const trap = (e: KeyboardEvent) => {
      if (e.key !== 'Tab' || !el) return;
      const items = [...el.querySelectorAll<HTMLElement>(selector)].filter((item) => item.getClientRects().length > 0);
      const first = items[0], last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
    };
    el?.addEventListener('keydown', trap);
    return () => { el?.removeEventListener('keydown', trap); if (previous?.isConnected) previous.focus(); };
  }, []);
  return ref;
}

export function Modal(props: { title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; width?: number }) {
  useEscape(props.onClose);
  const ref = useDialogFocus();
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && props.onClose()}>
      <div ref={ref} className="modal" role="dialog" aria-modal="true" aria-label={typeof props.title === 'string' ? props.title : undefined} style={props.width ? { width: props.width } : undefined}>
        <header className="modal-head">
          <h2>{props.title}</h2>
          <button className="btn ghost icon sm" onClick={props.onClose} aria-label="关闭">
            <X size={15} />
          </button>
        </header>
        <div className="modal-body">{props.children}</div>
        {props.footer && <footer className="modal-foot">{props.footer}</footer>}
      </div>
    </div>
  );
}

export function Drawer(props: { title: ReactNode; onClose: () => void; children: ReactNode; actions?: ReactNode }) {
  useEscape(props.onClose);
  const ref = useDialogFocus();
  return (
    <>
      <div className="overlay" style={{ background: 'transparent' }} onMouseDown={props.onClose} />
      <aside ref={ref} className="drawer" role="dialog" aria-modal="true" aria-label={typeof props.title === 'string' ? props.title : undefined}>
        <header className="modal-head">
          <h2 className="ellipsis">{props.title}</h2>
          {props.actions}
          <button className="btn ghost icon sm" onClick={props.onClose} aria-label="关闭">
            <X size={15} />
          </button>
        </header>
        <div className="modal-body" style={{ flex: 1 }}>
          {props.children}
        </div>
      </aside>
    </>
  );
}

export function KV(props: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="kv">
      {props.items.map(([k, v], i) => (
        <div key={i} style={{ display: 'contents' }}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Switch(props: { checked: boolean; onChange: (v: boolean) => void; children: ReactNode }) {
  return (
    <label className="switch">
      <input type="checkbox" role="switch" checked={props.checked} onChange={(e) => props.onChange(e.target.checked)} />
      <span className="track" />
      {props.children}
    </label>
  );
}
