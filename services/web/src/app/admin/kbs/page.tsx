import { AdminGate } from "@/components/AdminGate";
import { AuthGate } from "@/components/AuthGate";
import { KbAdmin } from "@/components/admin/KbAdmin";

export default function KnowledgeBasesPage() {
  return (
    <AuthGate>
      <AdminGate>
        <KbAdmin />
      </AdminGate>
    </AuthGate>
  );
}
