import type { Quotas, Usage } from "./types";

/** Stitch "Usage & Quotas" (Section 5.2). The mock also shows invoice
 * reconciliation and a spend-cap control -- `POST /billing/subscription` is a
 * documented `501` stub (no payment provider yet), so only the parts backed
 * by real data (usage, quotas) render here; nothing else is wired to an action. */
export function BillingPanel({ usage, quotas }: { usage: Usage; quotas: Quotas }) {
  return (
    <div>
      <h1 className="text-xl font-semibold text-text-primary">Usage & Quotas</h1>
      <p className="mt-1 text-sm text-text-secondary">
        {usage.start} to {usage.end}
      </p>

      <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
        <div className="border border-border bg-bg p-4">
          <p className="text-xs font-medium text-text-secondary">LLM tokens (today)</p>
          <p className="mt-1 text-2xl font-semibold text-text-primary">
            {quotas.llm_tokens.used.toLocaleString()}
            <span className="text-sm font-normal text-text-secondary">
              {" "}
              / {quotas.llm_tokens.limit.toLocaleString()}
            </span>
          </p>
          <p className="mt-1 text-xs text-text-secondary">
            {quotas.llm_tokens.remaining.toLocaleString()} remaining, resets{" "}
            {new Date(quotas.llm_tokens.resets_at).toLocaleTimeString()}
          </p>
        </div>
        <div className="border border-border bg-bg p-4">
          <p className="text-xs font-medium text-text-secondary">Query minutes (period)</p>
          <p className="mt-1 text-2xl font-semibold text-text-primary">
            {usage.query_minutes.toFixed(1)}
          </p>
        </div>
        <div className="border border-border bg-bg p-4">
          <p className="text-xs font-medium text-text-secondary">Seats</p>
          <p className="mt-1 text-2xl font-semibold text-text-primary">{usage.seats}</p>
        </div>
      </div>

      <div className="mt-6 border border-border bg-bg p-4">
        <p className="text-xs font-medium text-text-secondary">LLM token usage by stage</p>
        <table className="mt-2 w-full text-sm">
          <tbody>
            {Object.entries(usage.llm_tokens.by_stage).map(([stage, tokens]) => (
              <tr key={stage} className="border-b border-border last:border-b-0">
                <td className="py-1.5 text-text-primary">{stage}</td>
                <td className="py-1.5 text-right text-text-secondary">{tokens.toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-2 text-xs text-text-secondary">
          Input {usage.llm_tokens.input.toLocaleString()} · Output{" "}
          {usage.llm_tokens.output.toLocaleString()} · Total {usage.llm_tokens.total.toLocaleString()}
        </p>
      </div>
    </div>
  );
}
