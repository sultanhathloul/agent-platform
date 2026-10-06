import { AuthGate } from "@/components/AuthGate";
import { PolicyAdmin } from "@/components/admin/PolicyAdmin";

export default function PoliciesPage() {
  return (
    <AuthGate>
      <PolicyAdmin />
    </AuthGate>
  );
}
