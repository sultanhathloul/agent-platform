import { AdminGate } from "@/components/AdminGate";
import { AuthGate } from "@/components/AuthGate";
import { SettingsAdmin } from "@/components/admin/SettingsAdmin";

export default function SettingsPage() {
  return (
    <AuthGate>
      <AdminGate>
        <SettingsAdmin />
      </AdminGate>
    </AuthGate>
  );
}
