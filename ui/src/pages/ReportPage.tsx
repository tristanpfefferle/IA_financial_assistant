import { useEffect, useMemo, useState } from 'react'

import { getSpendingReport, type SpendingReport, type SpendingReportParams } from '../api/agentApi'

type ReportPageProps = {
  params: SpendingReportParams
  onConfirmViewed: () => void
  isSending?: boolean
}

function formatAmount(value: number, currency: string): string {
  return new Intl.NumberFormat('fr-CH', { style: 'currency', currency }).format(value)
}

export function ReportPage({ params, onConfirmViewed, isSending = false }: ReportPageProps) {
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

  const topCategories = useMemo(() => (report?.categories ?? []).slice(0, 8), [report])

  return (
    <section className="report-page" aria-label="Rapport de dépenses">
      <h2 className="report-title">Rapport de dépenses</h2>
      {loading ? <p className="subtle-text">Chargement du rapport…</p> : null}
      {error ? <p className="error-text">{error}</p> : null}
      {report ? (
        <>
          <p className="subtle-text">Période : {report.period.label || `${report.period.start_date} → ${report.period.end_date}`}</p>
          <div className="report-grid">
            <article className="report-card">
              <h3>Totaux</h3>
              <p>Entrées : {formatAmount(report.cashflow.total_income, report.currency)}</p>
              <p>Sorties : {formatAmount(report.cashflow.total_expense, report.currency)}</p>
              <p>Solde : {formatAmount(report.cashflow.net_cashflow, report.currency)}</p>
            </article>
            <article className="report-card">
              <h3>Répartition catégories</h3>
              {topCategories.length === 0 ? <p>Aucune catégorie disponible.</p> : null}
              <ul className="report-categories">
                {topCategories.map((category) => (
                  <li key={category.name}>
                    <span>{category.name}</span>
                    <strong>{formatAmount(category.amount, report.currency)}</strong>
                  </li>
                ))}
              </ul>
            </article>
          </div>
        </>
      ) : null}

      <button
        type="button"
        className="report-cta"
        disabled={isSending || loading || Boolean(error)}
        onClick={onConfirmViewed}
      >
        J’ai consulté mon rapport.
      </button>
    </section>
  )
}
