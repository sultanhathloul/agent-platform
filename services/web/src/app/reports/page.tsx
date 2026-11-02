import { AuthGate } from "@/components/AuthGate";
import { ReportLibrary } from "@/components/ReportLibrary";

export default function ReportsPage() {
  return (
    <AuthGate>
      <ReportLibrary />
    </AuthGate>
  );
}
