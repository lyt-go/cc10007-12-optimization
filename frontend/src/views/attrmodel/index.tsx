import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/attrmodel'
const columns = [
  "模型编号",
  "模型名称",
  "归因逻辑",
  "触点权重分布",
  "衰减天数",
  "转化窗口",
  "直接贡献率",
  "间接贡献率"
]
const actions = [
  "提交模型评审",
  "发布模型",
  "下线模型"
]
const statuses = [
  "草稿",
  "模型评审中",
  "已发布",
  "已下线"
]
const stats = [
  {
    "label": "归因模型总数",
    "value": 0
  },
  {
    "label": "已发布模型",
    "value": 0
  },
  {
    "label": "评审中模型",
    "value": 0
  }
]

type Row = Record<string, string | number | null>

type RecalcState = {
  message?: string
  会话?: Record<string, string | number | null> | null
  受影响指标清单?: Row[]
  增量明细?: Row[]
  未生效分析?: Row[]
  历史验证?: Row[]
  贡献对比?: { 受影响指标?: Row[]; 未受影响指标?: Row[] }
}

export default function AttrmodelView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [recalc, setRecalc] = useState<RecalcState | null>(null)
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('归因模型列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '归因建模列表读取失败')
    }
  }, [filters])

  const reloadRecalc = useCallback(async () => {
    try {
      const response = await request(`${ENDPOINT}/refresh`)
      if (response.ok) {
        setRecalc(await response.json())
      }
    } catch {
      // 增量重算面板读取失败不打断主列表
    }
  }, [])

  useEffect(() => {
    void reload()
    void reloadRecalc()
  }, [reload, reloadRecalc])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      if (!response.ok) {
        throw new Error('归因建模动作未生效，请稍后重试')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '归因建模操作失败')
    }
  }

  async function runRecalc(path: string) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/refresh${path}`, { method: 'POST' })
      const payload = await response.json()
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.message ?? payload.detail ?? '增量重算操作未生效')
      }
      setRecalc(payload)
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '增量重算操作失败')
    }
  }

  return (
    <section className="page" data-module="attrmodel">
      <header className="page-head">
        <div>
          <h2>归因建模管理</h2>
          <p className="page-desc">管理多渠道归因建模，评估各触点的直接和间接贡献。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('归因模型登记入口尚未接入审批流')}>
            登记归因模型
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出归因建模清单
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
              <td className="empty-state" colSpan={columns.length + 1}>暂无归因建模数据，可先登记归因模型</td>
            </tr>
          )}
        </tbody>
      </table>

      <section className="recalc-panel">
        <header className="page-head">
          <div>
            <h3>触点权重增量重算</h3>
            <p className="page-desc">
              按归因模型与下钻路径识别受影响指标，只重算引用变化权重的路径；未受影响指标的当前根因与历史验证保持原样。
            </p>
          </div>
          <div className="page-actions">
            <button className="btn primary" type="button" onClick={() => void runRecalc('')}>
              归因建模刷新
            </button>
            <button className="btn" type="button" onClick={() => void runRecalc('/retry')}>
              失败重试
            </button>
            <button className="btn ghost" type="button" onClick={() => void runRecalc('/finish')}>
              结束操作
            </button>
          </div>
        </header>
        {recalc?.会话 && (
          <p className="recalc-session">
            会话 {recalc.会话['会话编号']}（{recalc.会话['status']}）· 批次 {recalc.会话['写入批次']} ·
            业务核验码 {String(recalc.会话['业务核验码'] ?? '')} · 刷新核验码 {String(recalc.会话['刷新核验码'] ?? '')}
            {recalc.会话['结束核验码'] ? ` · 结束核验码 ${String(recalc.会话['结束核验码'])}` : ''}
          </p>
        )}
        {recalc?.message && <p className="recalc-message">{recalc.message}</p>}

        <h4>受影响指标清单（与已完成验证结果分开放置）</h4>
        <table className="data-table">
          <thead>
            <tr><th>批次</th><th>分析编号</th><th>模型编号</th><th>受影响路径</th><th>权重版本</th><th>识别时间</th></tr>
          </thead>
          <tbody>
            {(recalc?.受影响指标清单 ?? []).map((row) => (
              <tr key={String(row.id)}>
                <td>{row['批次'] ?? '—'}</td>
                <td>{row['分析编号'] ?? '—'}</td>
                <td>{row['模型编号'] ?? '—'}</td>
                <td>{row['受影响路径'] ?? '—'}</td>
                <td>{row['权重版本'] ?? '—'}</td>
                <td>{row['识别时间'] ?? '—'}</td>
              </tr>
            ))}
            {(recalc?.受影响指标清单 ?? []).length === 0 && (
              <tr><td className="empty-state" colSpan={6}>暂无受影响指标识别记录</td></tr>
            )}
          </tbody>
        </table>

        <h4>归因增量明细数据集（按追溯号可追溯）</h4>
        <table className="data-table">
          <thead>
            <tr>
              <th>明细编号</th><th>分析编号</th><th>下钻路径</th><th>直接贡献率</th><th>间接贡献率</th>
              <th>排名</th><th>权重版本</th><th>重算窗口</th><th>应用状态</th><th>状态</th><th>追溯号</th>
            </tr>
          </thead>
          <tbody>
            {(recalc?.增量明细 ?? []).map((row) => (
              <tr key={String(row.id)}>
                <td>{row['明细编号'] ?? '—'}</td>
                <td>{row['分析编号'] ?? '—'}</td>
                <td>{row['维度下钻路径'] ?? '—'}</td>
                <td>{row['直接贡献率'] === '' ? '—' : row['直接贡献率'] ?? '—'}</td>
                <td>{row['间接贡献率'] === '' ? '—' : row['间接贡献率'] ?? '—'}</td>
                <td>{row['贡献度排名'] === '' ? '—' : row['贡献度排名'] ?? '—'}</td>
                <td>{row['权重版本'] ?? '—'}</td>
                <td>{row['重算窗口'] ?? '—'}</td>
                <td>{row['应用状态'] ?? '—'}</td>
                <td>{row['status'] ?? '—'}</td>
                <td>{row['追溯号'] ?? '—'}</td>
              </tr>
            ))}
            {(recalc?.增量明细 ?? []).length === 0 && (
              <tr><td className="empty-state" colSpan={11}>暂无归因增量明细，可先执行归因建模刷新</td></tr>
            )}
          </tbody>
        </table>

        <h4>受影响 / 未受影响指标贡献对比</h4>
        <table className="data-table">
          <thead>
            <tr><th>分组</th><th>分析编号</th><th>目标指标</th><th>直接贡献率</th><th>间接贡献率</th><th>贡献度排名</th><th>权重版本</th><th>来源</th></tr>
          </thead>
          <tbody>
            {(['受影响指标', '未受影响指标'] as const).flatMap((group) =>
              (recalc?.贡献对比?.[group] ?? []).map((row) => (
                <tr key={`${group}-${String(row['分析编号'])}`}>
                  <td>{group}</td>
                  <td>{row['分析编号'] ?? '—'}</td>
                  <td>{row['目标指标'] ?? '—'}</td>
                  <td>{row['直接贡献率'] ?? '—'}</td>
                  <td>{row['间接贡献率'] ?? '—'}</td>
                  <td>{row['贡献度排名'] ?? '—'}</td>
                  <td>{row['权重版本'] ?? '—'}</td>
                  <td>{row['来源'] ?? '—'}</td>
                </tr>
              )),
            )}
          </tbody>
        </table>

        <h4>已完成验证结果（历史验证只读）</h4>
        <table className="data-table">
          <thead>
            <tr><th>验证编号</th><th>分析编号</th><th>权重版本</th><th>直接贡献率</th><th>间接贡献率</th><th>验证结论</th><th>验证时间</th></tr>
          </thead>
          <tbody>
            {(recalc?.历史验证 ?? []).map((row) => (
              <tr key={String(row.id)}>
                <td>{row['验证编号'] ?? '—'}</td>
                <td>{row['分析编号'] ?? '—'}</td>
                <td>{row['权重版本'] ?? '—'}</td>
                <td>{row['直接贡献率'] ?? '—'}</td>
                <td>{row['间接贡献率'] ?? '—'}</td>
                <td>{row['验证结论'] ?? '—'}</td>
                <td>{row['验证时间'] ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <footer className="page-foot">
        <span>共 {total} 条归因建模记录</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
