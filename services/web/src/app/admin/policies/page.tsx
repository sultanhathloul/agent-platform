import { AdminGate } from "@/components/AdminGate";
import { AuthGate } from "@/components/AuthGate";
import { PolicyAdmin } from "@/components/admin/PolicyAdmin";

export default function PoliciesPage() {
  return (
    <AuthGate>
      <AdminGate>
        <PolicyAdmin />
      </AdminGate>
    </AuthGate>
  );
}
