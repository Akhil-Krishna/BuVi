import { ChatPanel } from "@/features/chat/ChatPanel";
import { listDashboards } from "@/features/dashboards/actions";
import { listConversations } from "@/features/analytics/actions";
import { listDataSources } from "@/features/data-sources/actions";
import { getSession } from "@/lib/auth/session";

/** Stitch "AI Analytics Chat" (Section 5.2), Section 32's vertical-slice
 * journey: message -> SSE execution trace -> chart -> pin to dashboard.
 *
 * `?prompt=&dataSourceId=` seed SQL Lab's "Send to Chat/Chart" (Phase B4) --
 * optional, so a plain `/chat` visit is unaffected. */
export default async function ChatPage({
  searchParams,
}: {
  searchParams: Promise<{ prompt?: string; dataSourceId?: string }>;
}) {
  const session = await getSession();
  if (!session?.permissions.includes("chat:use")) {
    return (
      <div>
        <h1 className="text-xl font-semibold text-text-primary">Chat</h1>
        <p className="mt-2 text-sm text-text-secondary">
          Your role does not include access to the analytics chat.
        </p>
      </div>
    );
  }

  const { prompt, dataSourceId } = await searchParams;
  const [dashboards, conversations] = await Promise.all([listDashboards(), listConversations()]);

  // `GET /data-sources` needs `data:manage`, which a `client` does not hold -- so they get no
  // picker and rely on the run choosing a source from the question. Listing is a convenience for
  // whoever can, never something chat depends on: a failure here degrades to "auto only".
  const dataSources = session.permissions.includes("data:manage")
    ? await listDataSources()
        .then((all) =>
          all
            .filter((source) => source.status === "active")
            // Alphabetical, so the picker reads the same every visit rather than in creation
            // order. Plain comparison, not localeCompare: nothing here should vary by locale.
            .sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0))
        )
        .catch(() => [])
    : [];

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <ChatPanel
        dashboards={dashboards}
        dataSources={dataSources.map((s) => ({ id: s.id, name: s.name, engine: s.engine }))}
        initialConversations={conversations}
        initialPrompt={prompt}
        initialDataSourceId={dataSourceId}
      />
    </div>
  );
}
