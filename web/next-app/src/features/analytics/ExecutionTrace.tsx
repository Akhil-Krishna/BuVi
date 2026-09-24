import { TRACE_STEPS, type AnalyticsRunEvent, type RunStage } from "./types";

type StepState = "pending" | "active" | "done" | "failed";

function stateFor(stages: readonly RunStage[], events: AnalyticsRunEvent[]): StepState {
  const relevant = events.filter((event) => stages.includes(event.stage));
  if (relevant.some((event) => event.status === "failed" || event.status === "cancelled")) {
    return "failed";
  }
  if (
    stages.every((stage) => relevant.some((e) => e.stage === stage && e.status === "completed"))
  ) {
    return "done";
  }
  if (relevant.length > 0) return "active";
  return "pending";
}

const DOT_CLASS: Record<StepState, string> = {
  pending: "bg-border",
  active: "bg-accent animate-pulse",
  done: "bg-success",
  failed: "bg-danger",
};

const TEXT_CLASS: Record<StepState, string> = {
  pending: "text-text-secondary",
  active: "text-text-primary",
  done: "text-text-primary",
  failed: "text-danger",
};

/** Stitch "AI Analytics Chat": a collapsed-by-default execution trace, never
 * the model's own reasoning (Section 11) -- just these 6 user-safe steps. */
export function ExecutionTrace({ events }: { events: AnalyticsRunEvent[] }) {
  const latest = events[events.length - 1];
  return (
    <div className="border border-border bg-bg p-4">
      <p className="text-xs font-semibold tracking-wide text-text-secondary uppercase">
        Execution trace
      </p>
      <ol className="mt-3 space-y-2">
        {TRACE_STEPS.map((step) => {
          const state = stateFor(step.stages, events);
          return (
            <li key={step.label} className="flex items-center gap-2 text-sm">
              <span className={`h-2 w-2 shrink-0 rounded-full ${DOT_CLASS[state]}`} aria-hidden />
              <span className={TEXT_CLASS[state]}>{step.label}</span>
            </li>
          );
        })}
      </ol>
      {latest && <p className="mt-3 text-xs text-text-secondary">{latest.message}</p>}
    </div>
  );
}
