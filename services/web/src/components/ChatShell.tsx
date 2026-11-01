"use client";

import { useReducer, useState } from "react";
import { CLOSED_PANEL, reportPanel } from "@/lib/reports";
import { Chat } from "./Chat";
import { ConversationSidebar } from "./ConversationSidebar";
import { ReportPanel } from "./ReportPanel";

/**
 * One conversation: the chat and, beside it, its report panel (reports PR, I7). The panel comes
 * after the chat in the row, so it sits on the left in Arabic and on the right in English. Keyed by
 * the conversation, so another conversation opens with the panel closed.
 */
function Conversation({ conversationId, onTurnDone }: { conversationId: string; onTurnDone: () => void }) {
  const [panel, dispatch] = useReducer(reportPanel, CLOSED_PANEL);
  return (
    <div className="flex h-full min-w-0 flex-1">
      <div className="min-w-0 flex-1">
        <Chat conversationId={conversationId} onTurnDone={onTurnDone} onReport={dispatch} />
      </div>
      <ReportPanel state={panel} onAction={dispatch} />
    </div>
  );
}

/** The conversations list beside the chat; the list reloads after each finished turn. */
export function ChatShell({ conversationId }: { conversationId: string }) {
  const [refreshKey, setRefreshKey] = useState(0);
  return (
    <div className="flex h-full">
      <ConversationSidebar currentId={conversationId} refreshKey={refreshKey} />
      <Conversation key={conversationId} conversationId={conversationId} onTurnDone={() => setRefreshKey((n) => n + 1)} />
    </div>
  );
}
