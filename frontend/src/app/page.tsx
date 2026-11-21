import AppShell from "@/components/AppShell";
import AuthGate from "@/components/AuthGate";

export default function Home() {
  return (
    <AuthGate>
      <AppShell />
    </AuthGate>
  );
}
