"use client";

import { useCallback, useEffect, useRef, useState, useTransition } from "react";
import {
  cancelRun,
  getArtifact,
  getArtifactData,
  sendChatMessage,
} from "@/features/analytics/actions";
import { ExecutionTrace } from "@/features/analytics/ExecutionTrace";
import type {
  AnalyticsRunEvent,
  ArtifactDataResponse,
  ArtifactResponse,
} from "@/features/analytics/types";
import { useRunStream } from "@/features/analytics/useRunStream";
import { ChartRenderer } from "@/features/charts/ChartRenderer";
import { parseChartSpec } from "@/features/charts/types";
import type { Dashboard } from "@/features/dashboards/types";
import { PinToDashboardDialog } from "./PinToDashboardDialog";

type Turn = {
  runId: string;
  prompt: string;
  status: "completed" | "failed" | "cancelled";
  events: AnalyticsRunEvent[];
  artifactId: string | null;
  artifact: ArtifactResponse | null;
  data: ArtifactDataResponse | null;
  errorMessage: string | null;
  pinnedTo: string | null;
};

/** Stitch "AI Analytics Chat" (Section 5.2). Owns the whole chat surface --
 * composer, live execution trace, chart render, and pin-to-dashboard -- as
 * one stateful client component rather than five components passing a run's
 * state through props, since exactly one run streams at a time.
 *
 * `turns` holds only *finished* runs. The currently streaming run is never
 * copied into React state frame by frame -- it renders straight from
 * `useRunStream`'s own live return value, and is folded into `turns` exactly
 * once, when its `run.*` terminal event arrives (Section 11: the typed
 * status there, not string-matching its message, is what tells a real
 * failure from a user-initiated cancellation). */
export function ChatPanel({ dashboards }: { dashboards: Dashboard[] }) {
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [activePrompt, setActivePrompt] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [sendError, setSendError] = useState<string | null>(null);
  const [pinningRunId, setPinningRunId] = useState<string | null>(null);
  const [isSending, startSendTransition] = useTransition();
  const fetchedArtifactFor = useRef<Set<string>>(new Set());

  // Fires once, from inside the stream's own frame handler, on the run's
  // terminal `run.*` event -- an "external system" callback, not a
  // derived-state effect (Section 11: the typed `event.status` here, never
  // its message text, is what tells a real failure from a cancellation).
  const handleFinal = useCallback(
    (event: AnalyticsRunEvent, events: AnalyticsRunEvent[]) => {
      const status: Turn["status"] =
        event.status === "completed" || event.status === "cancelled" ? event.status : "failed";
      // Section 11: `artifactId` rides the `artifact.completed` event, not
      // the terminal `run.*` one -- it must be read out of the full stream,
      // not off the one event that closed it.
      const artifactId = [...events].reverse().find((e) => e.artifactId)?.artifactId ?? null;
      setTurns((current) => [
        ...current,
        {
          runId: event.runId,
          prompt: activePrompt,
          status,
          events,
          artifactId,
          artifact: null,
          data: null,
          errorMessage: status === "failed" ? event.message : null,
          pinnedTo: null,
        },
      ]);
      setActiveRunId(null);
    },
    [activePrompt]
  );
  const stream = useRunStream(activeRunId, handleFinal);

  useEffect(() => {
    const pending = turns.find(
      (turn) =>
        turn.status === "completed" &&
        turn.artifactId &&
        !fetchedArtifactFor.current.has(turn.runId)
    );
    if (!pending) return;
    fetchedArtifactFor.current.add(pending.runId);
    (async () => {
      const [artifact, data] = await Promise.all([
        getArtifact(pending.artifactId!),
        getArtifactData(pending.artifactId!),
      ]);
      setTurns((current) =>
        current.map((turn) =>
          turn.runId === pending.runId
            ? {
                ...turn,
                artifact: artifact.ok ? artifact.data : null,
                data: data.ok ? data.data : null,
                errorMessage: artifact.ok ? null : artifact.message,
              }
            : turn
        )
      );
    })();
  }, [turns]);

  function submit() {
    const content = input.trim();
    if (!content || isSending) return;
    setSendError(null);
    startSendTransition(async () => {
      const result = await sendChatMessage(conversationId, content, null);
      if (!result.ok) {
        setSendError(result.message);
        return;
      }
      setConversationId(result.data.conversationId);
      setActivePrompt(content);
      setActiveRunId(result.data.runId);
      setInput("");
    });
  }

  function requestCancel() {
    if (!activeRunId) return;
    startSendTransition(async () => {
      await cancelRun(activeRunId);
    });
  }

  return (
    <div className="flex flex-1 flex-col gap-6">
      {turns.length === 0 && !activeRunId && (
        <p className="text-sm text-text-secondary">
          Ask a question about your data, e.g. &ldquo;Create a sales dashboard for Q2&rdquo;.
        </p>
      )}

      {turns.map((turn) => (
        <div key={turn.runId} className="border border-border bg-bg p-4">
          <p className="text-sm font-medium text-text-primary">{turn.prompt}</p>

          {turn.status === "cancelled" && (
            <p className="mt-3 text-sm text-text-secondary">Run cancelled.</p>
          )}

          {turn.status === "failed" && (
            <p className="mt-3 text-sm text-danger">{turn.errorMessage ?? "The run failed."}</p>
          )}

          {turn.status === "completed" && turn.artifact && turn.data && (
            <div className="mt-3">
              <p className="text-sm text-text-secondary">{turn.artifact.summary}</p>
              {(() => {
                const spec = parseChartSpec(turn.artifact!.chart_spec);
                return spec ? (
                  <div className="mt-3">
                    <ChartRenderer
                      spec={spec}
                      columns={turn.data!.columns}
                      rows={turn.data!.rows}
                    />
                  </div>
                ) : (
                  <p className="mt-3 text-sm text-text-secondary">
                    This chart could not be rendered.
                  </p>
                );
              })()}

              {turn.pinnedTo ? (
                <p className="mt-3 text-sm text-success">Pinned to dashboard.</p>
              ) : turn.artifact.can_pin ? (
                pinningRunId === turn.runId ? (
                  <PinToDashboardDialog
                    artifactId={turn.artifact.artifact_id}
                    dashboards={dashboards}
                    onClose={() => setPinningRunId(null)}
                    onPinned={(dashboardId) => {
                      setPinningRunId(null);
                      setTurns((current) =>
                        current.map((t) =>
                          t.runId === turn.runId ? { ...t, pinnedTo: dashboardId } : t
                        )
                      );
                    }}
                  />
                ) : (
                  <button
                    type="button"
                    onClick={() => setPinningRunId(turn.runId)}
                    className="mt-3 rounded-sm border border-border px-3 py-1.5 text-sm text-text-primary hover:bg-bg-subtle"
                  >
                    Pin to Dashboard
                  </button>
                )
              ) : null}
            </div>
          )}

          {turn.status === "completed" && turn.errorMessage && (
            <p className="mt-3 text-sm text-danger">{turn.errorMessage}</p>
          )}
        </div>
      ))}

      {activeRunId && (
        <div className="border border-border bg-bg p-4">
          <p className="text-sm font-medium text-text-primary">{activePrompt}</p>
          <div className="mt-3 space-y-3">
            <ExecutionTrace events={stream.events} />
            {stream.connectionError && (
              <p className="text-xs text-warning">
                Reconnecting to the run&rsquo;s progress stream...
              </p>
            )}
            <button
              type="button"
              onClick={requestCancel}
              className="rounded-sm border border-border px-3 py-1 text-xs text-text-secondary hover:bg-bg-subtle"
            >
              Cancel run
            </button>
          </div>
        </div>
      )}

      <div className="sticky bottom-0 border border-border bg-bg p-3">
        <textarea
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
          placeholder="Ask a follow-up question or specify a slice..."
          rows={2}
          className="w-full resize-none border-0 text-sm text-text-primary outline-none"
        />
        <div className="flex items-center justify-between">
          <p className="text-xs text-text-secondary">
            Press Enter to submit, Shift+Enter for a new line
          </p>
          <button
            type="button"
            onClick={submit}
            disabled={isSending || Boolean(activeRunId) || input.trim().length === 0}
            className="rounded-sm bg-primary px-4 py-1.5 text-sm font-medium text-white hover:bg-primary-hover disabled:opacity-50"
          >
            Send
          </button>
        </div>
        {sendError && <p className="mt-2 text-sm text-danger">{sendError}</p>}
      </div>
    </div>
  );
}
