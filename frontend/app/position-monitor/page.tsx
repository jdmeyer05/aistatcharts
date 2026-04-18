import { redirect } from "next/navigation";

// Position Monitor is temporarily hidden — Robinhood creds aren't mounted on
// Cloud Run. Redirect any legacy /position-monitor links to Home.
export default function PositionMonitorRedirect() {
  redirect("/");
}
