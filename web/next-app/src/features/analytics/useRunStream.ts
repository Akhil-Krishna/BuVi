"use client";

import { useEffect, useRef, useState } from "react";
import type { AnalyticsRunEvent, RunEventStatus, RunStage } from "./types";

const STAGES: readonly RunStage[] = [
  "intent",
  "schema",
  "semantic",
  "sql",
  "validation",
  "execution",
  "visualization",
  "artifact",
  "run",
];
const STATUSES: readonly RunEventStatus[] = ["started", "completed", "failed", "cancelled"];

export type RunStreamState = {
  events: AnalyticsRunEvent[];
  /** Set once a `run.*` event arrives (Section 11: `stage === "run"` is
   * always the terminal event, regardless of which status it carries). */
  finalStatus: RunEventStatus | null;
  artifactId: string | null;
  /** The stream itself failed to connect/stay open -- distinct from the run
   * finishing with `failed`, which is a normal, well-formed outcome. */
  connectionError: boolean;
};

/**
 * `GET /runs/{id}/events` (Section 11) through the BFF proxy, which streams
 * the upstream `text/event-stream` body through unmodified
 * (`app/api/[...path]/route.ts`). Native `EventSource` gives us
 * `Last-Event-ID` reconnection and heartbeat-comment handling for free; the
 * cost is registering a listener per `<stage>.<status>` pair up front, since
 * every frame here carries an explicit `event:` name and so never reaches
 * `onmessage`.
 */
const EMPTY_STATE: RunStreamState = {
  events: [],
  finalStatus: null,
  artifactId: null,
  connectionError: false,
};

/**
 * @param onFinal Fired exactly once, from inside the stream's own frame
 * handler, when the run's terminal `run.*` event arrives -- an "external
 * system" callback (react-hooks `set-state-in-effect`'s own carve-out), not a
 * derived-state effect, so the caller can safely `setState` in it without a
 * cascading-render lint violation.
 */
export function useRunStream(
  runId: string | null,
  onFinal?: (event: AnalyticsRunEvent, events: AnalyticsRunEvent[]) => void
): RunStreamState {
  // Reset synchronously during render when `runId` changes, rather than in an
  // effect -- the React-recommended pattern for "clear state when a prop
  // changes" (react.dev "You Might Not Need an Effect"), and it keeps a stale
  // previous run's events from flashing before the effect below re-runs.
  const [trackedRunId, setTrackedRunId] = useState(runId);
  const [state, setState] = useState<RunStreamState>(EMPTY_STATE);
  if (runId !== trackedRunId) {
    setTrackedRunId(runId);
    setState(EMPTY_STATE);
  }

  const seenSeqs = useRef<Set<number>>(new Set());
  // The authoritative accumulated event list, updated synchronously in
  // `handleFrame` below. `setState`'s updater form is *not* guaranteed to run
  // synchronously with the `setState` call (React may defer it to the render
  // phase), so a local variable assigned inside that updater cannot be read
  // right after -- `onFinal` needs the full list the instant the terminal
  // event arrives, not on React's own schedule.
  const eventsRef = useRef<AnalyticsRunEvent[]>([]);
  // Always the latest `onFinal` without resubscribing the stream below on
  // every render -- refs may not be written during render itself, only from
  // an effect or event handler (react-hooks/refs), so this keeps it current
  // just after each commit instead.
  const onFinalRef = useRef(onFinal);
  useEffect(() => {
    onFinalRef.current = onFinal;
  });

  useEffect(() => {
    seenSeqs.current = new Set();
    eventsRef.current = [];
    if (!runId) return;

    const source = new EventSource(`/api/runs/${runId}/events`);

    function handleFrame(raw: MessageEvent<string>) {
      let event: AnalyticsRunEvent;
      try {
        event = JSON.parse(raw.data) as AnalyticsRunEvent;
      } catch {
        return;
      }
      if (seenSeqs.current.has(event.seq)) return; // a resync can redeliver
      seenSeqs.current.add(event.seq);
      eventsRef.current = [...eventsRef.current, event].sort((a, b) => a.seq - b.seq);
      const nextEvents = eventsRef.current;
      setState((current) => ({
        events: nextEvents,
        finalStatus: event.stage === "run" ? event.status : current.finalStatus,
        artifactId: event.artifactId ?? current.artifactId,
        connectionError: false,
      }));
      if (event.stage === "run") {
        source.close();
        onFinalRef.current?.(event, nextEvents);
      }
    }

    for (const stage of STAGES) {
      for (const status of STATUSES) {
        source.addEventListener(`${stage}.${status}`, handleFrame as EventListener);
      }
    }
    source.onerror = () => {
      // EventSource retries transient drops on its own; a `finalStatus` means
      // the run is already over and this is just the source closing.
      setState((current) =>
        current.finalStatus ? current : { ...current, connectionError: true }
      );
    };

    return () => source.close();
  }, [runId]);

  return state;
}
