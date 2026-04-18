import { redirect } from "next/navigation";

export default function VolSurfaceRedirect() {
  redirect("/options-portfolio-analysis?view=vol-surface");
}
