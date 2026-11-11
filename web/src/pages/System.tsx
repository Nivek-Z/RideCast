import { Monitor, Moon, RefreshCw, Sun } from 'lucide-react';
import { useState } from 'react';
import { connection } from '../api';
import { Badge, Card, Empty, Field, KV, Note, Seg, Stat } from '../components/ui';
import { ago, int, time } from '../format';
import { useApp, type ThemeMode } from '../store';

const CAPABILITY: Record<string, string> = {
  geojson_layers: 'GeoJSON 图层', scenario_simulation: '场景仿真', csv_import: 'CSV 导入', quality_report: '质量报告', heatmap: '热力',
  flows: 'OD 流向', replay: '历史回放', job_sse: '任务事件流', gru_forecast: 'GRU 预测', model_comparison: '模型比较', dispatch_suggestions: '调配建议',
};
const UNIT: Record<string, string> = {
  demand: '需求', passengers: '乘客', temperature: '气温', precipitation: '降水', wind_speed: '风速', traffic_index: '路况指数',
  available_drivers: '可用司机', distance: '距离', travel_minutes: '行程时长',
};

export function SystemPage(props: { theme: ThemeMode; setTheme: (m: ThemeMode) => void }) {
  const app = useApp();
  const sys = app.system;
  const [base, setBase] = useState(connection.base);
  const [key, setKey] = useState(connection.key);
  const counts = sys?.job_counts ?? {};

  return (
    <div className="page">
      <div className="card">
        <div className="stat-strip">
          <Stat k="API" v={<span className="row"><i className={`dot ${app.online ? 'green' : 'red'}`} />{app.online ? '在线' : '离线'}</span>} d={sys ? `v${sys.version} · ${sys.api_version} · ${sys.storage}` : '无法连接'} />
          <Stat k="Worker" v={<span className="row"><i className={`dot ${sys?.worker_online ? 'green' : 'amber'}`} />{sys?.worker_online ? '在线' : '离线'}</span>} d={`${(sys?.workers ?? []).filter((w: { online: boolean }) => w.online).length} 个在线`} />
          <Stat k="TensorFlow" v={sys?.tensorflow_installed ? '已安装' : '未安装'} d={sys?.tensorflow_installed ? '可训练与预测' : "pip install -e '.[ml]'"} />
          <Stat k="资源" v={`${app.worlds.length} / ${app.datasets.length} / ${app.models.length}`} d="城市 / 数据集 / 模型" />
          <Stat k="任务" v={int(Object.values(counts as Record<string, number>).reduce((a, b) => a + b, 0))} d={`运行 ${counts.running ?? 0} · 排队 ${counts.queued ?? 0} · 失败 ${counts.failed ?? 0}`} />
        </div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))' }}>
        <Card title="外观">
          <div className="col" style={{ gap: 12 }}>
            <Seg
              value={props.theme}
              onChange={props.setTheme}
              options={[
                { value: 'auto', label: <><Monitor size={13} /> 跟随系统</> },
                { value: 'light', label: <><Sun size={13} /> 浅色</> },
                { value: 'dark', label: <><Moon size={13} /> 深色</> },
              ]}
            />
            <div className="small muted">主题保存在本机浏览器，地图与图表配色随主题切换。</div>
          </div>
        </Card>

        <Card title="连接">
          <div className="col" style={{ gap: 12 }}>
            <Field label="API 地址" hint="留空则通过开发服务器代理访问 127.0.0.1:8000（推荐）">
              <input className="input" placeholder="例如 http://127.0.0.1:8000" value={base} onChange={(e) => setBase(e.target.value)} />
            </Field>
            <Field label="X-API-Key" hint={sys?.api_key_required ? '后端已启用密钥校验' : '后端未启用密钥，可留空。密钥仅保存在本机浏览器，不会写入 URL'}>
              <input className="input" type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} />
            </Field>
            <div className="row">
              <button
                className="btn primary"
                onClick={() => {
                  connection.save(base, key);
                  location.reload();
                }}
              >
                保存并重新连接
              </button>
              <button className="btn" onClick={app.refresh}>
                <RefreshCw size={14} /> 刷新数据
              </button>
            </div>
          </div>
        </Card>

        <Card title="Worker" sub="每 5 秒心跳">
          {(sys?.workers ?? []).length === 0 ? (
            <Empty title="尚无 worker 记录">
              在后端目录运行 <span className="mono">ridecast worker</span>，仿真、导入与训练任务才会被执行。
            </Empty>
          ) : (
            <div className="col">
              {sys.workers.slice(0, 6).map((w: { id: string; online: boolean; heartbeat_at: string; current_job_id: string | null }) => (
                <div key={w.id} className="row">
                  <i className={`dot ${w.online ? 'green' : ''}`} />
                  <span className="mono grow ellipsis">{w.id}</span>
                  {w.current_job_id && w.online && <Badge tone="blue">执行 {w.current_job_id.slice(0, 10)}</Badge>}
                  <span className="small muted">{ago(w.heartbeat_at)}</span>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      {sys && (
        <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))' }}>
          <Card title="能力">
            <div className="row" style={{ flexWrap: 'wrap' }}>
              {sys.capabilities.map((c: string) => (
                <Badge key={c}>{CAPABILITY[c] ?? c}</Badge>
              ))}
            </div>
            <div style={{ marginTop: 12 }}>
              <KV
                items={[
                  ['单文件上传上限', `${Math.round(sys.limits.upload_bytes_per_file / 1048576)} MiB`],
                  ['面板行上限', int(sys.limits.max_panel_rows)],
                  ['列表单页上限', int(sys.limits.max_page_size)],
                  ['服务器时间', time.full(sys.server_time, Intl.DateTimeFormat().resolvedOptions().timeZone)],
                ]}
              />
            </div>
          </Card>
          <Card title="单位与时间约定">
            <KV items={Object.entries(sys.units as Record<string, string>).map(([k, v]) => [UNIT[k] ?? k, <span className="mono">{v}</span>])} />
            <div style={{ marginTop: 10 }}>
              <Note>{sys.time_convention}</Note>
            </div>
          </Card>
        </div>
      )}

      <Card title="城市" sub={`${app.worlds.length} 个`} flush actions={<button className="btn sm" onClick={() => app.openDialog('world')}>新建</button>}>
        {app.worlds.length === 0 ? (
          <Empty title="还没有城市" />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>名称</th>
                  <th>标识</th>
                  <th className="num">区域</th>
                  <th className="num">道路</th>
                  <th className="num">种子</th>
                  <th>时间桶</th>
                  <th>时区</th>
                  <th className="num">数据集</th>
                  <th className="num">模型</th>
                  <th>创建</th>
                </tr>
              </thead>
              <tbody>
                {app.worlds.map((w) => (
                  <tr key={w.id} className={`click ${w.id === app.world?.id ? 'sel' : ''}`} onClick={() => app.setWorld(w.id)}>
                    <td>{w.name}</td>
                    <td className="mono dim">{w.id}</td>
                    <td className="num">{w.metadata?.zones?.features?.length}</td>
                    <td className="num">{w.metadata?.roads?.features?.length}</td>
                    <td className="num">{w.metadata?.seed}</td>
                    <td>{w.metadata?.bucket_minutes} 分钟</td>
                    <td className="dim">{w.metadata?.timezone}</td>
                    <td className="num">{app.datasets.filter((d) => d.metadata?.world_id === w.id).length}</td>
                    <td className="num">{app.models.filter((m) => m.metadata?.world_id === w.id).length}</td>
                    <td className="dim">{ago(w.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="数据集" sub={`${app.datasets.length} 个`} flush actions={<button className="btn sm" onClick={() => app.openDialog('simulate')}>生成</button>}>
        {app.datasets.length === 0 ? (
          <Empty title="还没有数据集" />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>标识</th>
                  <th>城市</th>
                  <th>来源</th>
                  <th>口径</th>
                  <th>观测范围</th>
                  <th className="num">订单</th>
                  <th className="num">面板行</th>
                  <th>情景</th>
                  <th>创建</th>
                </tr>
              </thead>
              <tbody>
                {app.datasets.map((d) => {
                  const m = d.metadata ?? {};
                  const tz = app.worlds.find((w) => w.id === m.world_id)?.metadata?.timezone ?? 'Asia/Shanghai';
                  const sc = Object.entries(m.scenario ?? {}).filter(([, v]) => typeof v === 'number' && v !== 1);
                  return (
                    <tr
                      key={d.id}
                      className={`click ${d.id === app.dataset?.id ? 'sel' : ''}`}
                      onClick={() => {
                        app.setWorld(m.world_id);
                        app.go('dashboard');
                      }}
                    >
                      <td className="mono">{d.id}</td>
                      <td>{app.worlds.find((w) => w.id === m.world_id)?.name ?? m.world_id}</td>
                      <td className="dim">{m.source === 'synthetic' ? '合成' : '导入'}</td>
                      <td className="dim">{m.target_semantics}</td>
                      <td>{time.date(m.observation_start, tz)} — {time.date(m.observation_end, tz)}</td>
                      <td className="num">{int(m.order_count)}</td>
                      <td className="num">{int(m.row_count)}</td>
                      <td className="dim">{sc.length ? sc.map(([k, v]) => `${k.replace('_multiplier', '')}×${v}`).join(' ') : '默认'}</td>
                      <td className="dim">{ago(d.created_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
