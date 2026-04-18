import { redirect } from "next/navigation";

export default function PositionMonitorRedirect() {
  redirect("/?view=position-monitor");
}
