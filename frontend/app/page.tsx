import { DashboardContent } from "./_home/dashboard";

export default function HomePage() {
  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Home</h1>
        <p className="text-text-secondary text-sm mt-1">
          Market pulse, cross-asset vol, intelligence, and top ideas — one screen.
        </p>
      </div>
      <DashboardContent />
    </div>
  );
}
