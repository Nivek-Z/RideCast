import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { useLocal } from '../store';
import type { ResearchSnapshot, Study } from './types';

interface ResearchState {
  data: ResearchSnapshot | null; error: string | null; loading: boolean;
  study: Study | null; selectStudy: (id: string) => void; reload: () => void;
}
const Context = createContext<ResearchState>(null as unknown as ResearchState);
export const useResearch = () => useContext(Context);

export function ResearchProvider({ children }: { children: ReactNode }) {
  const [data, setData] = useState<ResearchSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [studyId, selectStudy] = useLocal('rc.research.study', 'calibrated-gam');
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    fetch(`${import.meta.env.BASE_URL}research/snapshot.json`, { signal: controller.signal, cache: 'no-cache' })
      .then(async (response) => {
        if (!response.ok) throw new Error('研究报告暂不可用，请重新加载。');
        const value = await response.json();
        if (value.schemaVersion !== 1 || !Array.isArray(value.studies) || !value.studies.length) throw new Error('研究报告格式不兼容。');
        setData(value as ResearchSnapshot); setError(null);
      })
      .catch((e) => { if (e.name !== 'AbortError') setError(e.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [tick]);
  const study = data?.studies.find((item) => item.id === studyId) ?? data?.studies[0] ?? null;
  return <Context.Provider value={{ data, error, loading, study, selectStudy, reload: () => setTick((n) => n + 1) }}>{children}</Context.Provider>;
}
