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

function formatDate(value: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value)
  if (!match) {
    return value
  }
  return `${match[3]}.${match[2]}.${match[1]}`
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

  const metrics = useMemo(() => {
    if (!report) {
      return null
    }

    const isInternalTransfer = (transaction: CategorizedTransaction): boolean => {
      const normalizedCategory = transaction.category_norm.toLowerCase()
      return transaction.is_internal_transfer || transaction.flow_type === 'internal_transfer' || normalizedCategory === 'internal_transfer'
    }

    const isIncome = (transaction: CategorizedTransaction): boolean => {
      const normalizedCategory = transaction.category_norm.toLowerCase()
      const normalizedLabel = transaction.category_label.toLowerCase()
      return normalizedCategory === 'income' || normalizedCategory === 'revenu' || normalizedLabel.includes('revenu') || normalizedLabel.includes('income')
    }

    let income = 0
    let expenses = 0
    let internalTransfersIn = 0
    let internalTransfersOut = 0
    let realBalanceVariation = 0

    const expenseByCategory = new Map<string, number>()

    for (const transaction of report.transactions) {
      realBalanceVariation += transaction.amount
      const internalTransfer = isInternalTransfer(transaction)

      if (internalTransfer) {
        if (transaction.amount > 0) internalTransfersIn += transaction.amount
        if (transaction.amount < 0) internalTransfersOut += Math.abs(transaction.amount)
        continue
      }

      if (transaction.amount > 0 && isIncome(transaction)) {
        income += transaction.amount
      } else if (transaction.amount < 0) {
        const expenseAmount = Math.abs(transaction.amount)
        expenses += expenseAmount
        const categoryName = transaction.category_label || 'À catégoriser'
        expenseByCategory.set(categoryName, (expenseByCategory.get(categoryName) ?? 0) + expenseAmount)
      }
    }

    const expenseCategories = [...expenseByCategory.entries()]
      .map(([name, amount]) => ({
        name,
        amount,
        percent: expenses > 0 ? (amount / expenses) * 100 : 0,
      }))
      .sort((left, right) => right.amount - left.amount)

    return {
      income,
      expenses,
      internalTransfersIn,
      internalTransfersOut,
      cashflow: income - expenses,
      realBalanceVariation,
      expenseCategories,
    }
  }, [report])

  const transactionGroups = useMemo(() => {
    if (!report) {
      return [] as Array<{ month: string; items: CategorizedTransaction[] }>
    }

    const grouped = new Map<string, CategorizedTransaction[]>()
    for (const transaction of report.transactions) {
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
  }, [report])

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
                <strong>{formatAmount(metrics?.income ?? 0, report.currency)}</strong>
              </div>
              <div>
                <span className="subtle-text">Dépenses</span>
                <strong>{formatAmount(metrics?.expenses ?? 0, report.currency)}</strong>
              </div>
              <div>
                <span className="subtle-text">Transferts internes entrants</span>
                <strong>{formatAmount(metrics?.internalTransfersIn ?? 0, report.currency)}</strong>
              </div>
              <div>
                <span className="subtle-text">Transferts internes sortants</span>
                <strong>{formatAmount(-(metrics?.internalTransfersOut ?? 0), report.currency)}</strong>
              </div>
              <div>
                <span className="subtle-text">Cashflow économique</span>
                <strong>{formatAmount(metrics?.cashflow ?? 0, report.currency)}</strong>
              </div>
              <div>
                <span className="subtle-text">Variation réelle du solde</span>
                <strong>{formatAmount(metrics?.realBalanceVariation ?? 0, report.currency)}</strong>
              </div>
            </div>
            <p className="subtle-text report-summary-note">
              Cashflow = revenus - dépenses (hors transferts internes). Variation = évolution réelle du solde (avec transferts internes).
            </p>
          </article>

          <article className="report-card">
            <h3>Répartition des dépenses</h3>
            {metrics?.expenseCategories.length === 0 ? <p>Aucune catégorie disponible.</p> : null}
            {metrics && metrics.expenseCategories.length > 0 ? (
              <div className="report-split-grid">
                <div className="report-chart-wrap" aria-label="Graphique de répartition des dépenses">
                  <ResponsiveContainer width="100%" height={220}>
                    <PieChart>
                      <Pie data={metrics.expenseCategories} dataKey="amount" nameKey="name" innerRadius={58} outerRadius={88} paddingAngle={2}>
                        {metrics.expenseCategories.map((category, index) => (
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
                  {metrics.expenseCategories.map((category) => (
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
            <h3>Transactions</h3>

            {transactionGroups.length === 0 ? <p className="subtle-text">Aucune transaction disponible.</p> : null}
            {transactionGroups.map((group) => (
              <div key={group.month} className="report-month-group">
                <h4>{group.month}</h4>
                <div className="report-transactions-list">
                  {group.items.map((transaction) => (
                    <article key={transaction.id} className="report-transaction-row">
                      <p className="report-transaction-date">{formatDate(transaction.date)}</p>
                      <div className="report-transaction-main">
                        <p className="report-transaction-merchant" title={transaction.merchant || transaction.label || 'Transaction'}>
                          {transaction.merchant || transaction.label || 'Transaction'}
                        </p>
                        <div className="report-transaction-meta">
                          <span className="report-category-badge">
                            {transaction.is_internal_transfer
                              ? 'Transfert interne'
                              : transaction.category_label || 'À catégoriser'}
                          </span>
                          {transaction.merchant && transaction.label && transaction.label !== transaction.merchant ? (
                            <span className="report-secondary-label" title={transaction.label}>{transaction.label}</span>
                          ) : null}
                        </div>
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
