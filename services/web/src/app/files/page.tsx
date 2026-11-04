import { AuthGate } from "@/components/AuthGate";
import { MyFiles } from "@/components/MyFiles";

export default function FilesPage() {
  return (
    <AuthGate>
      <MyFiles />
    </AuthGate>
  );
}
