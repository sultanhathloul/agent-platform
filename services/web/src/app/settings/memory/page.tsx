import { AuthGate } from "@/components/AuthGate";
import { MemorySettings } from "@/components/MemorySettings";

export default function MemorySettingsPage() {
  return (
    <AuthGate>
      <MemorySettings />
    </AuthGate>
  );
}
