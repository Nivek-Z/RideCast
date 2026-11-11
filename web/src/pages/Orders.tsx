import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api, type Any } from '../api';
import { Badge, Card, Empty, ErrorNote, Loading, Seg } from '../components/ui';
import { fix1, int, time } from '../format';
import { useApp, useQuery } from '../store';

const PAGE = 50;

export function OrdersPage() {
  const app = useApp();
  const { dataset, world, tz } = app;
  const [zone, setZone] = useState('');
  const [status, setStatus] = useState<'' | 'completed' | 'cancelled'>('');
  const [offset, setOffset] = useState(0);
  useEffect(() => setOffset(0), [dataset?.id, zone, status]);
  useEffect(() => setZone(''), [world?.id]);

  const orders = useQuery(
    dataset ? `orders:${dataset.id}:${zone}:${status}:${offset}` : null,
    () => api.orders(dataset!.id, { zone_id: zone || undefined, status: status || undefined, offset, limit: PAGE }),
  );

  if (!dataset) {
    return (
      <div className="page">
        <Card>
          <Empty title="没有可浏览的订单" action={<button className="btn primary" onClick={() => app.openDialog('simulate')}>生成数据集</button>}>
            先为当前城市生成或导入一个数据集。
          </Empty>
        </Card>
      </div>
    );
  }
  const data: Any = orders.data;
  const rows: Any[] = data?.rows ?? [];
  const total = data?.total_count ?? 0;
  const zones: Any[] = world?.metadata?.zones?.features ?? [];

  return (
    <div className="page">
      <Card
        title="订单"
        sub={data ? `${int(total)} 条 · 按发起时间升序` : ''}
        flush
        actions={
          <>
            <select className="select" style={{ width: 150, height: 28 }} value={zone} onChange={(e) => setZone(e.target.value)}>
              <option value="">全部上车区域</option>
              {zones.map((f) => (
                <option key={f.properties.zone_id} value={f.properties.zone_id}>
                  {f.properties.name}
                </option>
              ))}
            </select>
            <Seg value={status} onChange={setStatus} options={[{ value: '', label: '全部' }, { value: 'completed', label: '已完成' }, { value: 'cancelled', label: '已取消' }]} />
          </>
        }
      >
        {orders.error ? (
          <div style={{ padding: 14 }}><ErrorNote error={orders.error} /></div>
        ) : !data ? (
          <Loading height={300} />
        ) : rows.length === 0 ? (
          <Empty title="没有符合条件的订单" />
        ) : (
          <div className="table-wrap" style={{ opacity: orders.loading ? 0.6 : 1, transition: 'opacity .15s' }}>
            <table className="table">
              <thead>
                <tr>
                  <th>发起时间</th>
                  <th>订单号</th>
                  <th>状态</th>
                  <th>上车区域</th>
                  <th>下车区域</th>
                  <th className="num">乘客</th>
                  <th className="num">行程</th>
                  <th>司机</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((o) => (
                  <tr key={o.order_id}>
                    <td>{time.date(o.requested_at, tz)} <span className="dim">{time.hms(o.requested_at, tz)}</span></td>
                    <td className="mono dim">{o.order_id}</td>
                    <td>
                      <Badge tone={o.status === 'completed' ? 'green' : 'red'} dot>
                        {o.status === 'completed' ? '已完成' : '已取消'}
                      </Badge>
                    </td>
                    <td>{app.zoneName(o.pickup_zone_id)}</td>
                    <td className={o.dropoff_zone_id ? '' : 'muted'}>{o.dropoff_zone_id ? app.zoneName(o.dropoff_zone_id) : '未知'}</td>
                    <td className="num">{int(o.passenger_count)}</td>
                    <td className="num">{o.travel_minutes === null || o.travel_minutes === undefined ? '—' : `${fix1(o.travel_minutes)} 分`}</td>
                    <td className="mono dim">{o.driver_id ?? <span className="muted">未服务</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="row" style={{ padding: '8px 14px', borderTop: '1px solid var(--border)' }}>
          <span className="small muted">订单状态为数据集中的最终结果。</span>
          <span className="grow" />
          <span className="small dim">
            {total ? `${int(offset + 1)}–${int(offset + rows.length)} / ${int(total)}` : ''}
          </span>
          <button className="btn sm icon" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))} aria-label="上一页">
            <ChevronLeft size={14} />
          </button>
          <button className="btn sm icon" disabled={data?.next_offset === null || data?.next_offset === undefined} onClick={() => setOffset(data.next_offset)} aria-label="下一页">
            <ChevronRight size={14} />
          </button>
        </div>
      </Card>
    </div>
  );
}
