"use client";

import { useState } from "react";
import { Chat } from "./Chat";
import { ConversationSidebar } from "./ConversationSidebar";

/** The conversations list beside the chat; the list reloads after each finished turn. */
export function ChatShell({ conversationId }: { conversationId: string }) {
  const [refreshKey, setRefreshKey] = useState(0);
  return (
    <div className="flex h-full">
      <ConversationSidebar currentId={conversationId} refreshKey={refreshKey} />
      <div className="min-w-0 flex-1">
        <Chat key={conversationId} conversationId={conversationId} onTurnDone={() => setRefreshKey((n) => n + 1)} />
      </div>
    </div>
  );
}
