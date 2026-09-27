"use client";

import { useCallback, useEffect, useRef, useState, useTransition } from "react";
import {
  cancelRun,
  getArtifact,
  getArtifactData,
  getConversationMessages,
  listConversations,
  sendChatMessage,
} from "@/features/analytics/actions";
import { ExecutionTrace } from "@/features/analytics/ExecutionTrace";
import type {
  AnalyticsRunEvent,
  ArtifactDataResponse,
  ArtifactResponse,
  Conversation,
} from "@/features/analytics/types";
import { useRunStream } from "@/features/analytics/useRunStream";
import { ChartRenderer } from "@/features/charts/ChartRenderer";
import { parseChartSpec } from "@/features/charts/types";
import type { Dashboard } from "@/features/dashboards/types";
import { ConversationSidebar } from "./ConversationSidebar";
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
  /** A run the assistant answered in conversation ("hi", "what can you do") completes with no
   * artifact; its reply travels as the closing `run.completed` event's message. */
  reply: string | null;
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
export type DataSourceOption = { id: string; name: string; engine: string };

export function ChatPanel({
  dashboards,
  dataSources = [],
  initialConversations,
  initialPrompt,
  initialDataSourceId,
}: {
  dashboards: Dashboard[];
  initialConversations: Conversation[];
  /** Active sources this user may list; empty for a role that cannot (then only auto-detect). */
  dataSources?: DataSourceOption[];
  /** SQL Lab's "Send to Chat/Chart" (Section 31 Phase B4) arrives here: the
   * query-gateway result itself has no chat endpoint to land in (chat only
   * ever takes free text), so the honest bridge is seeding the composer with
   * the same data source and a natural-language prompt, then letting the
   * real chat pipeline run from there -- not a second, fake execution path. */
  initialPrompt?: string;
  initialDataSourceId?: string | null;
}) {
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversations, setConversations] = useState(initialConversations);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [activePrompt, setActivePrompt] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState(initialPrompt ?? "");
  // "" is auto-detect: the run reads the question and picks among the tenant's active sources.
  // A source named in the URL (SQL Lab's handoff) is kept as-is when the list is empty -- the role
  // cannot list, and the server still validates it -- but dropped when the list is known and does
  // not contain it, so the <select> never holds a value it has no option for.
  const [dataSourceId, setDataSourceId] = useState<string>(
    initialDataSourceId &&
      (dataSources.length === 0 || dataSources.some((s) => s.id === initialDataSourceId))
      ? initialDataSourceId
      : ""
  );
  const [sendError, setSendError] = useState<string | null>(null);
  const [pinningRunId, setPinningRunId] = useState<string | null>(null);
  const [isSending, startSendTransition] = useTransition();
  const fetchedArtifactFor = useRef<Set<string>>(new Set());
  const scrollAreaRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to newest content when turns or active run changes.
  useEffect(() => {
    scrollAreaRef.current?.scrollTo({
      top: scrollAreaRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [turns, activeRunId]);

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
          reply: status === "completed" && !artifactId ? event.message : null,
          pinnedTo: null,
        },
      ]);
      setActiveRunId(null);
      if (!conversations.some((c) => c.id === conversationId)) {
        listConversations().then(setConversations);
      }
    },
    [activePrompt, conversationId, conversations]
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
      const result = await sendChatMessage(conversationId, content, dataSourceId || null);
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

  function selectConversation(id: string) {
    if (id === conversationId || activeRunId) return;
    setConversationId(id);
    setIsLoadingHistory(true);
    startSendTransition(async () => {
      const messages = await getConversationMessages(id);
      const loaded: Turn[] = [];
      for (const m of messages) {
        if (m.role === "user") {
          loaded.push({
            runId: m.run_id ?? m.id,
            prompt: m.content,
            status: "completed",
            events: [],
            artifactId: null,
            artifact: null,
            data: null,
            errorMessage: null,
            reply: null,
            pinnedTo: null,
          });
        } else if (m.role === "assistant" && loaded.length > 0) {
          // The assistant's stored content is the run's summary or conversational reply either
          // way (Section 32's persist_artifact / ADR 0025 both store it there); shown as text.
          // Historical charts are not re-fetched here -- this is chat history, not artifact replay.
          loaded[loaded.length - 1].reply = m.content;
        }
      }
      setTurns(loaded);
      setIsLoadingHistory(false);
    });
  }

  function newConversation() {
    if (activeRunId) return;
    setConversationId(null);
    setTurns([]);
    setSendError(null);
  }

  function requestCancel() {
    if (!activeRunId) return;
    startSendTransition(async () => {
      await cancelRun(activeRunId);
    });
  }

  const isEmpty = turns.length === 0 && !activeRunId && !isLoadingHistory;

  return (
    <div className="flex flex-1 overflow-hidden">
      <ConversationSidebar
        conversations={conversations}
        activeId={conversationId}
        onSelect={selectConversation}
        onNew={newConversation}
      />
      <div className="flex flex-1 flex-col overflow-hidden">
      {/* Scrollable message area — takes all remaining height, pushes
          the composer to the bottom. */}
      <div ref={scrollAreaRef} className="flex-1 overflow-y-auto px-1 pb-4">
        {isEmpty && (
          <div className="flex flex-1 flex-col items-center justify-center py-24">
            <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-primary-muted">
              <svg
                className="h-6 w-6 text-primary"
                fill="none"
                viewBox="0 0 24 24"
                strokeWidth={1.5}
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M7.5 8.25h9m-9 3H12m-9.75 1.51c0 1.6 1.123 2.994 2.707 3.227 1.087.16 2.185.283 3.293.369V21l4.076-4.076a1.526 1.526 0 0 1 1.037-.443 48.282 48.282 0 0 0 5.68-.494c1.584-.233 2.707-1.626 2.707-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0 0 12 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018Z"
                />
              </svg>
            </div>
            <p className="mt-4 text-sm text-text-secondary">
              Ask a question about your data to get started.
            </p>
            <p className="mt-1 text-xs text-text-muted">
              e.g. &ldquo;Show me monthly revenue for Q2&rdquo; or &ldquo;Create a sales
              dashboard&rdquo;
            </p>
          </div>
        )}

        {/* Completed turns */}
        <div className="mx-auto w-full max-w-3xl space-y-4 pt-4" data-testid="chat-thread">
          {turns.map((turn) => (
            <div key={turn.runId} className="space-y-3">
              {/* User message bubble */}
              <div className="flex justify-end">
                <div className="max-w-[80%] rounded-xl rounded-br-sm bg-primary px-4 py-2.5 text-sm text-white">
                  {turn.prompt}
                </div>
              </div>

              {/* Assistant response */}
              <div className="flex justify-start">
                <div className="max-w-[90%] rounded-xl rounded-bl-sm border border-border-subtle bg-bg-elevated px-4 py-3">
                  {turn.status === "cancelled" && (
                    <p className="text-sm text-text-muted">Run cancelled.</p>
                  )}

                  {turn.status === "completed" && turn.reply && (
                    <p className="text-sm text-text-primary">{turn.reply}</p>
                  )}

                  {turn.status === "failed" && (
                    <p className="text-sm text-danger">{turn.errorMessage ?? "The run failed."}</p>
                  )}

                  {turn.status === "completed" && turn.artifact && turn.data && (
                    <div>
                      <p className="text-sm text-text-secondary">{turn.artifact.summary}</p>
                      {(() => {
                        const spec = parseChartSpec(turn.artifact!.chart_spec);
                        return spec ? (
                          <div className="mt-3 overflow-hidden rounded-lg border border-border-subtle">
                            <ChartRenderer
                              spec={spec}
                              columns={turn.data!.columns}
                              rows={turn.data!.rows}
                            />
                          </div>
                        ) : (
                          <p className="mt-3 text-sm text-text-muted">
                            This chart could not be rendered.
                          </p>
                        );
                      })()}

                      {turn.pinnedTo ? (
                        <p className="mt-3 text-xs font-medium text-success">
                          Pinned to dashboard.
                        </p>
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
                            className="mt-3 rounded-md border border-border px-3 py-1.5 text-xs font-medium text-text-secondary transition-colors hover:border-primary hover:text-primary"
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
              </div>
            </div>
          ))}

          {/* Currently streaming run */}
          {activeRunId && (
            <div className="space-y-3">
              {/* User message */}
              <div className="flex justify-end">
                <div className="max-w-[80%] rounded-xl rounded-br-sm bg-primary px-4 py-2.5 text-sm text-white">
                  {activePrompt}
                </div>
              </div>

              {/* Live execution trace */}
              <div className="flex justify-start">
                <div className="max-w-[90%] space-y-3 rounded-xl rounded-bl-sm border border-border-subtle bg-bg-elevated px-4 py-3">
                  <ExecutionTrace events={stream.events} />
                  {stream.connectionError && (
                    <p className="text-xs text-warning">
                      Reconnecting to the run&rsquo;s progress stream...
                    </p>
                  )}
                  <button
                    type="button"
                    onClick={requestCancel}
                    className="rounded-md border border-border px-3 py-1 text-xs text-text-muted transition-colors hover:border-danger hover:text-danger"
                  >
                    Cancel run
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Composer — pinned to bottom */}
      <div className="shrink-0 border-t border-border-subtle px-4 pb-4 pt-3">
        <div className="mx-auto w-full max-w-3xl">
          <div className="rounded-xl border border-border bg-bg-elevated transition-colors focus-within:border-primary">
            <textarea
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  submit();
                }
              }}
              placeholder="Ask a question about your data..."
              rows={2}
              className="w-full resize-none bg-transparent px-4 pt-3 pb-2 text-sm text-text-primary outline-none placeholder:text-text-muted"
            />
            <div className="flex items-center justify-between gap-3 px-4 pb-3">
              <div className="flex min-w-0 items-center gap-3">
                {dataSources.length > 0 && (
                  <label className="flex min-w-0 items-center gap-1.5 text-xs text-text-muted">
                    <span className="shrink-0">Data source</span>
                    <select
                      aria-label="Data source"
                      value={dataSourceId}
                      onChange={(event) => setDataSourceId(event.target.value)}
                      disabled={Boolean(activeRunId)}
                      className="min-w-0 max-w-[16rem] truncate rounded-md border border-border bg-bg-elevated px-2 py-1 text-xs text-text-secondary outline-none transition-colors focus:border-primary disabled:opacity-50"
                    >
                      <option value="">Auto-detect from my question</option>
                      {dataSources.map((source) => (
                        <option key={source.id} value={source.id}>
                          {source.name} · {source.engine}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <p className="hidden text-xs text-text-muted sm:block">
                  Enter to send, Shift+Enter for newline
                </p>
              </div>
              <button
                type="button"
                onClick={submit}
                disabled={
                  isSending || Boolean(activeRunId) || isLoadingHistory || input.trim().length === 0
                }
                className="rounded-lg bg-primary px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-primary-hover disabled:opacity-40"
              >
                Send
              </button>
            </div>
          </div>
          {sendError && <p className="mt-2 text-sm text-danger">{sendError}</p>}
        </div>
      </div>
      </div>
    </div>
  );
}
