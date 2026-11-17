import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/metricdrill'
const columns = [
  "分析编号",
  "目标指标",
  "波动方向",
  "波动幅度",
  "维度下钻路径",
  "贡献度排名",
  "根因假设",
  "验证结果"
]
const actions = [
  "发起分析",
  "确认结论",
  "归档分析"
]
const statuses = [
  "草稿",
  "分析中",
  "已确认",
  "已归档"
]
const stats = [
  {
    "label": "归因分析总数",
    "value": 0
  },
  {
    "label": "已确认分析",
    "value": 0
  },
  {
    "label": "分析中分析",
    "value": 0
  }
]

type Row = Record<string, string | number | null>

type PendingItem = {
  分析编号?: string
  目标指标?: string
  status?: string
  贡献度排名?: string
  当前权重版本?: string
  关联批次?: string
  增量明细?: Row[]
}

export default function MetricdrillView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [listCode, setListCode] = useState('')
  const [pendingItems, setPendingItems] = useState<PendingItem[]>([])
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('归因分析列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
      setListCode(payload['清单核验码'] ?? '')
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '指标归因列表读取失败')
    }
  }, [filters])

  const reloadPending = useCallback(async () => {
    try {
      const response = await request(`${ENDPOINT}/pending-details`)
      if (response.ok) {
        const payload = await response.json()
        setPendingItems(payload.items ?? [])
      }
    } catch {
      // 待处理明细读取失败不打断主列表
    }
  }, [])

  useEffect(() => {
    void reload()
    void reloadPending()
  }, [reload, reloadPending])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      if (!response.ok) {
        throw new Error('指标归因动作未生效，请稍后重试')
      }
      await reload()
      await reloadPending()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '指标归因操作失败')
    }
  }

  return (
    <section className="page" data-module="metricdrill">
      <header className="page-head">
        <div>
          <h2>指标归因管理</h2>
          <p className="page-desc">对指标异常波动进行多维度归因分析，下钻定位波动根因。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('归因分析登记入口尚未接入审批流')}>
            登记归因分析
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出指标归因清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td className="row-actions">
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无指标归因数据，可先登记归因分析</td>
            </tr>
          )}
        </tbody>
      </table>

      <section className="recalc-panel">
        <h3>指标下钻待处理明细</h3>
        <p className="page-desc">
          归因增量明细数据集已投递、尚未确认的归因分析；仅引用变化权重的下钻路径被更新，未受影响指标保持当前根因。
        </p>
        {pendingItems.map((item) => (
          <article key={String(item.分析编号)} className="pending-item">
            <header>
              <strong>{item.分析编号}</strong> · {item.目标指标} · 状态 {item.status} ·
              当前权重版本 {item.当前权重版本 ?? '—'} · 关联批次 {item.关联批次 ?? '—'}
            </header>
            <table className="data-table">
              <thead>
                <tr>
                  <th>明细编号</th><th>下钻路径</th><th>直接贡献率</th><th>间接贡献率</th>
                  <th>排名</th><th>权重版本</th><th>重算窗口</th><th>应用状态</th><th>追溯号</th>
                </tr>
              </thead>
              <tbody>
                {(item.增量明细 ?? []).map((row) => (
                  <tr key={String(row.id)}>
                    <td>{row['明细编号'] ?? '—'}</td>
                    <td>{row['维度下钻路径'] ?? '—'}</td>
                    <td>{row['直接贡献率'] === '' ? '—' : row['直接贡献率'] ?? '—'}</td>
                    <td>{row['间接贡献率'] === '' ? '—' : row['间接贡献率'] ?? '—'}</td>
                    <td>{row['贡献度排名'] === '' ? '—' : row['贡献度排名'] ?? '—'}</td>
                    <td>{row['权重版本'] ?? '—'}</td>
                    <td>{row['重算窗口'] ?? '—'}</td>
                    <td>{row['应用状态'] ?? '—'}</td>
                    <td>{row['追溯号'] ?? '—'}</td>
                  </tr>
                ))}
                {(item.增量明细 ?? []).length === 0 && (
                  <tr><td className="empty-state" colSpan={9}>暂无关联的归因增量明细</td></tr>
                )}
              </tbody>
            </table>
          </article>
        ))}
        {pendingItems.length === 0 && <p className="page-desc">暂无待处理明细</p>}
      </section>

      <footer className="page-foot">
        <span>共 {total} 条指标归因记录</span>
        {listCode && <span>清单核验码 {listCode}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
