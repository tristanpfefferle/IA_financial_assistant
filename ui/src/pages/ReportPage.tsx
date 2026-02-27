import { useEffect, useMemo, useState } from 'react'
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts'

import { getSpendingReport, type SpendingReport, type SpendingReportParams } from '../api/agentApi'

type ReportPageProps = {
  params: SpendingReportParams
}

type CategorizedTransaction = SpendingReport['transactions'][number]

const CHART_COLORS = ['#38bdf8', '#818cf8', '#34d399', '#f59e0b', '#f472b6', '#f97316', '#a78bfa', '#10b981', '#06b6d4']

function formatAmount(value: number, currency: string): string {
  return new Intl.NumberFormat('fr-CH', { style: 'currency', currency }).format(value)
}

function getMonthKey(dateValue: string): string {
  if (!dateValue || dateValue.length < 7) {
    return 'Inconnu'
  }
  return dateValue.slice(0, 7)
}

export function ReportPage({ params }: ReportPageProps) {
  const [report, setReport] = useState<SpendingReport | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showInternalTransfers, setShowInternalTransfers] = useState(false)

  useEffect(() => {
    let active = true
    setLoading(true)
    setError(null)

    void getSpendingReport(params)
      .then((payload) => {
        if (!active) {
          return
        }
        setReport(payload)
      })
      .catch((err: unknown) => {
        if (!active) {
          return
        }
        setError(err instanceof Error ? err.message : 'Impossible de charger le rapport.')
      })
      .finally(() => {
        if (active) {
          setLoading(false)
        }
      })

    return () => {
      active = false
    }
  }, [params])

  const expenseCategories = useMemo(() => {
    if (!report) {
      return []
    }

    const totalExpenses = Math.abs(report.cashflow.total_expense)
    return [...report.categories]
      .map((category) => {
        const amount = Math.abs(category.amount)
        const percent = totalExpenses > 0 ? (amount / totalExpenses) * 100 : 0
        return { ...category, amount, percent }
      })
      .sort((left, right) => right.amount - left.amount)
  }, [report])

  const transactionGroups = useMemo(() => {
    if (!report) {
      return [] as Array<{ month: string; items: CategorizedTransaction[] }>
    }

    const filtered = report.transactions.filter((transaction) => {
      if (showInternalTransfers) {
        return true
      }
      return !transaction.is_internal_transfer
    })

    const grouped = new Map<string, CategorizedTransaction[]>()
    for (const transaction of filtered) {
      const monthKey = getMonthKey(transaction.date)
      const existing = grouped.get(monthKey) ?? []
      existing.push(transaction)
      grouped.set(monthKey, existing)
    }

    return [...grouped.entries()]
      .sort((left, right) => right[0].localeCompare(left[0]))
      .map(([month, items]) => ({
        month,
        items: [...items].sort((left, right) => right.date.localeCompare(left.date)),
      }))
  }, [report, showInternalTransfers])

  return (
    <section className="report-page" aria-label="Rapport de dépenses">
      <h2 className="report-title">Rapport financier</h2>
      {loading ? <p className="subtle-text">Chargement du rapport…</p> : null}
      {error ? <p className="error-text">{error}</p> : null}
      {report ? (
        <>
          <p className="subtle-text">Période : {report.period.label || `${report.period.start_date} → ${report.period.end_date}`}</p>

          <article className="report-card">
            <h3>Synthèse</h3>
            <div className="report-summary-grid">
              <div>
                <span className="subtle-text">Revenus</span>
                <strong>{formatAmount(Math.abs(report.cashflow.total_income), report.currency)}</strong>
              </div>
              <div>
                <span className="subtle-text">Dépenses</span>
                <strong>{formatAmount(Math.abs(report.cashflow.total_expense), report.currency)}</strong>
              </div>
              <div>
                <span className="subtle-text">Transferts internes</span>
                <strong>{formatAmount(Math.abs(report.cashflow.internal_transfers), report.currency)}</strong>
              </div>
              <div>
                <span className="subtle-text">Cashflow net</span>
                <strong>{formatAmount(report.cashflow.net_cashflow, report.currency)}</strong>
              </div>
            </div>
          </article>

          <article className="report-card">
            <h3>Répartition des dépenses</h3>
            {expenseCategories.length === 0 ? <p>Aucune catégorie disponible.</p> : null}
            {expenseCategories.length > 0 ? (
              <div className="report-split-grid">
                <div className="report-chart-wrap" aria-label="Graphique de répartition des dépenses">
                  <ResponsiveContainer width="100%" height={220}>
                    <PieChart>
                      <Pie data={expenseCategories} dataKey="amount" nameKey="name" innerRadius={58} outerRadius={88} paddingAngle={2}>
                        {expenseCategories.map((category, index) => (
                          <Cell key={category.name} fill={CHART_COLORS[index % CHART_COLORS.length]} />
                        ))}
                      </Pie>
                      <Tooltip formatter={(value: number) => formatAmount(Number(value), report.currency)} />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
                <div className="report-categories-table" role="table" aria-label="Détail par catégorie">
                  <div className="report-categories-head" role="row">
                    <span>Catégorie</span>
                    <span>Montant</span>
                    <span>%</span>
                  </div>
                  {expenseCategories.map((category) => (
                    <div key={category.name} className="report-categories-row" role="row">
                      <span>{category.name}</span>
                      <strong>{formatAmount(category.amount, report.currency)}</strong>
                      <span>{category.percent.toFixed(1)}%</span>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
          </article>

          <article className="report-card">
            <div className="report-transactions-head">
              <h3>Transactions</h3>
              <label className="report-toggle" htmlFor="report-show-transfers">
                <input
                  id="report-show-transfers"
                  type="checkbox"
                  checked={showInternalTransfers}
                  onChange={(event) => setShowInternalTransfers(event.target.checked)}
                />
                Afficher transferts internes
              </label>
            </div>

            {transactionGroups.length === 0 ? <p className="subtle-text">Aucune transaction disponible.</p> : null}
            {transactionGroups.map((group) => (
              <div key={group.month} className="report-month-group">
                <h4>{group.month}</h4>
                <div className="report-transactions-list">
                  {group.items.map((transaction) => (
                    <article key={transaction.id} className="report-transaction-row">
                      <div>
                        <p>{transaction.date}</p>
                        <p className="subtle-text">{transaction.merchant || transaction.label || 'Transaction'}</p>
                      </div>
                      <div>
                        <p>{transaction.category_label || 'À catégoriser'}</p>
                        {transaction.is_internal_transfer ? <p className="subtle-text">Transfert interne</p> : null}
                      </div>
                      <div className="report-transaction-amount">
                        <strong>{formatAmount(transaction.amount, transaction.currency || report.currency)}</strong>
                      </div>
                    </article>
                  ))}
                </div>
              </div>
            ))}
          </article>
        </>
      ) : null}
    </section>
  )
}
