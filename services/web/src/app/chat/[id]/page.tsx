import { redirect } from "next/navigation";
import { AuthGate } from "@/components/AuthGate";
import { Chat } from "@/components/Chat";
import { isUuid } from "@/lib/uuid";

export default async function ChatPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!isUuid(id)) redirect("/chat/new");

  return (
    <AuthGate>
      <Chat key={id} conversationId={id} />
    </AuthGate>
  );
}
