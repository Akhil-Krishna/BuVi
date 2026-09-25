import { ChatPanel } from "@/features/chat/ChatPanel";
import { listDashboards } from "@/features/dashboards/actions";
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
  const dashboards = await listDashboards();

  return (
    <div className="flex flex-1 flex-col">
      <h1 className="text-xl font-semibold text-text-primary">Chat</h1>
      <div className="mt-4 flex flex-1 flex-col">
        <ChatPanel
          dashboards={dashboards}
          initialPrompt={prompt}
          initialDataSourceId={dataSourceId}
        />
      </div>
    </div>
  );
}
