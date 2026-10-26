import { AdminGate } from "@/components/AdminGate";
import { AuthGate } from "@/components/AuthGate";
import { KbDocumentsAdmin } from "@/components/admin/KbDocumentsAdmin";

export default async function KnowledgeBasePage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return (
    <AuthGate>
      <AdminGate>
        <KbDocumentsAdmin slug={slug} />
      </AdminGate>
    </AuthGate>
  );
}
