import { useEffect, useState } from 'react';
import { active, type Artifact, type Run, type Status } from './types';
import type { ResearchSnapshot } from '../research/types';

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...options, cache: 'no-store' });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const issue = typeof body?.detail === 'string' ? body.detail : body?.detail?.[0]?.msg;
    throw new Error(issue ?? `请求失败（${response.status}）`);
  }
  return response.json();
}

export const submit = (path: string, value?: object) => request<Run | Run[]>(`/api/research/${path}`, {
  method: 'POST',
  ...(value ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) } : {}),
});
export const stop = (id: string) => request<Run>(`/api/research/runs/${id}`, { method: 'DELETE' });

export function useWorkspace() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [status, setStatus] = useState<Status | null>(null);
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [snapshot, setSnapshot] = useState<ResearchSnapshot>();
  const [reportError, setReportError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    request<ResearchSnapshot>('research/snapshot.json', { signal: controller.signal })
      .then(setSnapshot).catch(() => { if (!controller.signal.aborted) setReportError(true); });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    let timeout: ReturnType<typeof setTimeout>;
    async function refresh() {
      let delay = 2600;
      try {
        const options = { signal: controller.signal };
        const [status, page] = await Promise.all([
          request<Status>('/api/research/status', options),
          request<{ items: Run[] }>('/api/research/runs', options),
        ]);
        if (controller.signal.aborted) return;
        setStatus(status);
        setRuns(page.items);
        if (page.items.some(active)) delay = 450;
      } catch {
        if (!controller.signal.aborted) setStatus(null);
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
          timeout = setTimeout(refresh, delay);
        }
      }
    }
    refresh();
    return () => { controller.abort(); clearTimeout(timeout); };
  }, [revision]);
  return { runs, status, loading, snapshot, reportError, refresh: () => setRevision((n) => n + 1) };
}

const artifacts = new Map<string, Artifact>();
export function useArtifact(id?: string) {
  const [result, setResult] = useState<{ id: string; item?: Artifact; error?: string }>();
  useEffect(() => {
    if (!id) return;
    const existing = artifacts.get(id);
    if (existing) { setResult({ id, item: existing }); return; }
    const controller = new AbortController();
    request<Artifact>(`/api/research/runs/${id}/result`, { signal: controller.signal })
      .then((item) => {
        if (controller.signal.aborted) return;
        if (artifacts.size >= 12) artifacts.delete(artifacts.keys().next().value!);
        artifacts.set(id, item);
        setResult({ id, item });
      })
      .catch((error: Error) => {
        if (!controller.signal.aborted) setResult({ id, error: error.message });
      });
    return () => controller.abort();
  }, [id]);
  return result?.id === id ? result : undefined;
}
