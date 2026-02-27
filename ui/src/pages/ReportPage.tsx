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

function formatMonthShort(monthKey: string): string {
  const shortLabels: Record<string, string> = {
    '01': 'J',
    '02': 'F',
    '03': 'M',
    '04': 'A',
    '05': 'M',
    '06': 'J',
    '07': 'J',
    '08': 'A',
    '09': 'S',
    '10': 'O',
    '11': 'N',
    '12': 'D',
  }

  const month = monthKey.slice(5, 7)
  return shortLabels[month] ?? monthKey
}

function isInternalTransfer(transaction: CategorizedTransaction): boolean {
  if (transaction.is_internal_transfer === true) {
    return true
  }

  const flowType = String(transaction.flow_type ?? '').toLowerCase()
  if (flowType.includes('internal')) {
    return true
  }

  const normalizedCategory = String(transaction.category_norm ?? '').toLowerCase()
  if (
    normalizedCategory === 'internal_transfer' ||
    normalizedCategory === 'internal_transfers' ||
    normalizedCategory === 'transfer_internal' ||
    normalizedCategory === 'transfert_interne' ||
    normalizedCategory === 'transferts_internes'
  ) {
    return true
  }

  const categoryLabel = String(transaction.category_label ?? '').toLowerCase()
  if (categoryLabel.includes('transfert interne') || categoryLabel.includes('transferts internes')) {
    return true
  }

  // Fallback faible mais utile: libellé/merchant mentionne explicitement un transfert interne.
  const fallbackSource = `${String(transaction.merchant ?? '')} ${String(transaction.label ?? '')}`.toLowerCase()
  return (
    fallbackSource.includes('transfert interne') ||
    fallbackSource.includes('internal transfer') ||
    fallbackSource.includes('virement interne') ||
    fallbackSource.includes('entre comptes')
  )
}

function computeReportMetrics(transactions: CategorizedTransaction[], selectedMonths: Set<string>) {
  const selectedTransactions = transactions.filter((transaction) => selectedMonths.has(getMonthKey(transaction.date)))

  const expensesOnly: CategorizedTransaction[] = []
  const transactionsByMonth = new Map<string, CategorizedTransaction[]>()
  const expenseByCategory = new Map<string, number>()

  let incomesTotal = 0
  let expensesTotal = 0
  let internalInTotal = 0
  let internalOutTotal = 0
  let balanceDelta = 0

  for (const transaction of selectedTransactions) {
    balanceDelta += transaction.amount
    const monthKey = getMonthKey(transaction.date)
    transactionsByMonth.set(monthKey, [...(transactionsByMonth.get(monthKey) ?? []), transaction])

    if (isInternalTransfer(transaction)) {
      if (transaction.amount > 0) {
        internalInTotal += transaction.amount
      }
      if (transaction.amount < 0) {
        internalOutTotal += Math.abs(transaction.amount)
      }
      continue
    }

    if (transaction.amount > 0) {
      incomesTotal += transaction.amount
      continue
    }

    if (transaction.amount < 0) {
      const expenseAmount = Math.abs(transaction.amount)
      expensesOnly.push(transaction)
      expensesTotal += expenseAmount
      const categoryName = transaction.category_label || 'À catégoriser'
      const normalizedCategoryName = categoryName.toLowerCase()

      // Mini test manuel: si category_label='Transferts internes' et amount<0,
      // la transaction ne doit pas apparaître dans categoryBreakdown.
      if (normalizedCategoryName === 'transferts internes' || normalizedCategoryName.includes('transfert interne')) {
        continue
      }

      expenseByCategory.set(categoryName, (expenseByCategory.get(categoryName) ?? 0) + expenseAmount)
    }
  }

  const categoryBreakdown = [...expenseByCategory.entries()]
    .map(([name, amount]) => ({
      name,
      amount,
      percent: expensesTotal > 0 ? (amount / expensesTotal) * 100 : 0,
    }))
    .sort((left, right) => right.amount - left.amount)

  return {
    incomesTotal,
    expensesTotal,
    internalInTotal,
    internalOutTotal,
    cashflow: incomesTotal - expensesTotal,
    balanceDelta,
    categoryBreakdown,
    transactionsByMonth,
    selectedTransactions,
    expensesOnly,
  }
}

export function ReportPage({ params }: ReportPageProps) {
  const [report, setReport] = useState<SpendingReport | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedMonths, setSelectedMonths] = useState<Set<string>>(new Set())
  const [activeYear, setActiveYear] = useState<string | null>(null)

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

  const { availableMonths, availableYears, monthsByYear } = useMemo(() => {
    if (!report) {
      return {
        availableMonths: [] as string[],
        availableYears: [] as string[],
        monthsByYear: {} as Record<string, string[]>,
      }
    }

    const availableMonthKeys = [...new Set(report.transactions.map((transaction) => getMonthKey(transaction.date)))].sort((left, right) => left.localeCompare(right))
    const groupedMonths = availableMonthKeys.reduce<Record<string, string[]>>((accumulator, monthKey) => {
      const year = monthKey.slice(0, 4)
      accumulator[year] = [...(accumulator[year] ?? []), monthKey]
      return accumulator
    }, {})

    const years = Object.keys(groupedMonths).sort((left, right) => right.localeCompare(left))

    return {
      availableMonths: availableMonthKeys,
      availableYears: years,
      monthsByYear: groupedMonths,
    }
  }, [report])

  useEffect(() => {
    if (availableYears.length === 0) {
      setActiveYear(null)
      setSelectedMonths(new Set())
      return
    }

    setActiveYear((previous) => {
      if (previous && availableYears.includes(previous)) {
        return previous
      }

      return availableYears[0]
    })
  }, [availableYears])

  useEffect(() => {
    if (!activeYear || availableMonths.length === 0) {
      return
    }

    const monthsInActiveYear = monthsByYear[activeYear] ?? []
    const availableMonthSet = new Set(availableMonths)

    setSelectedMonths((previous) => {
      if (previous.size > 0) {
        const kept = [...previous].filter((month) => availableMonthSet.has(month))
        if (kept.length > 0) {
          return new Set(kept)
        }
      }

      return new Set(monthsInActiveYear)
    })
  }, [activeYear, availableMonths, monthsByYear])

  const metrics = useMemo(() => {
    if (!report || selectedMonths.size === 0) {
      return null
    }

    return computeReportMetrics(report.transactions, selectedMonths)
  }, [report, selectedMonths])

  const transactionGroups = useMemo(() => {
    if (!metrics) {
      return [] as Array<{ month: string; items: CategorizedTransaction[] }>
    }

    return [...metrics.transactionsByMonth.entries()]
      .sort((left, right) => right[0].localeCompare(left[0]))
      .map(([month, items]) => ({
        month,
        items: [...items].sort((left, right) => right.date.localeCompare(left.date)),
      }))
  }, [metrics])

  const toggleMonth = (month: string): void => {
    setSelectedMonths((previous) => {
      const next = new Set(previous)
      if (next.has(month)) {
        next.delete(month)
      } else {
        next.add(month)
      }

      if (next.size === 0) {
        return new Set(activeYear ? monthsByYear[activeYear] ?? [] : availableMonths)
      }
      return next
    })
  }

  return (
    <section className="report-page" aria-label="Rapport de dépenses">
      <h2 className="report-title">Rapport financier</h2>
      {loading ? <p className="subtle-text">Chargement du rapport…</p> : null}
      {error ? <p className="error-text">{error}</p> : null}
      {report ? (
        <>
          {availableYears.length > 0 ? (
            <div className="report-period-selector">
              <div className="report-year-tabs" role="tablist" aria-label="Sélection de l'année du rapport">
                {availableYears.map((year) => (
                  <button
                    key={year}
                    type="button"
                    role="tab"
                    aria-selected={activeYear === year}
                    className={`year-tab ${activeYear === year ? 'active' : ''}`}
                    onClick={() => {
                      setActiveYear(year)
                      setSelectedMonths(new Set(monthsByYear[year] ?? []))
                    }}
                  >
                    {year}
                  </button>
                ))}
              </div>
              <div className="report-month-chips" role="group" aria-label="Sélection des mois du rapport">
                {(activeYear ? monthsByYear[activeYear] ?? [] : []).map((month) => (
                <button
                  key={month}
                  type="button"
                  className={`month-chip ${selectedMonths.has(month) ? 'active' : ''}`}
                  onClick={() => toggleMonth(month)}
                >
                  {formatMonthShort(month)}
                </button>
              ))}
              </div>
            </div>
          ) : null}

          <article className="report-card">
            <h3>Synthèse</h3>
            <div className="report-summary-grid">
              <div>
                <span className="subtle-text">Revenus</span>
                <strong className="amount-positive">{formatAmount(metrics?.incomesTotal ?? 0, report.currency)}</strong>
              </div>
              <div>
                <span className="subtle-text">Dépenses</span>
                <strong className="amount-negative">{formatAmount(metrics?.expensesTotal ?? 0, report.currency)}</strong>
              </div>
              <div>
                <span className="subtle-text">Transferts internes entrants</span>
                <strong className="amount-positive">{formatAmount(metrics?.internalInTotal ?? 0, report.currency)}</strong>
              </div>
              <div>
                <span className="subtle-text">Transferts internes sortants</span>
                <strong className="amount-negative">{formatAmount(-(metrics?.internalOutTotal ?? 0), report.currency)}</strong>
              </div>
              <div>
                <span className="subtle-text">Cashflow économique</span>
                <strong className={(metrics?.cashflow ?? 0) >= 0 ? 'amount-positive' : 'amount-negative'}>{formatAmount(metrics?.cashflow ?? 0, report.currency)}</strong>
              </div>
              <div>
                <span className="subtle-text">Variation réelle du solde</span>
                <strong className={(metrics?.balanceDelta ?? 0) >= 0 ? 'amount-positive' : 'amount-negative'}>{formatAmount(metrics?.balanceDelta ?? 0, report.currency)}</strong>
              </div>
            </div>
          </article>

          <article className="report-card">
            <h3>Répartition des dépenses</h3>
            {metrics?.categoryBreakdown.length === 0 ? <p>Aucune catégorie disponible.</p> : null}
            {metrics && metrics.categoryBreakdown.length > 0 ? (
              <div className="report-split-grid">
                <div className="report-chart-wrap" aria-label="Graphique de répartition des dépenses">
                  <ResponsiveContainer width="100%" height={220}>
                    <PieChart>
                      <Pie data={metrics.categoryBreakdown} dataKey="amount" nameKey="name" innerRadius={58} outerRadius={88} paddingAngle={2}>
                        {metrics.categoryBreakdown.map((category, index) => (
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
                  {metrics.categoryBreakdown.map((category) => (
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
                        <strong className={transaction.amount >= 0 ? 'amount-positive' : 'amount-negative'}>{formatAmount(transaction.amount, transaction.currency || report.currency)}</strong>
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
