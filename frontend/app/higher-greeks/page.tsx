import { redirect } from "next/navigation";

export default function HigherGreeksRedirect() {
  redirect("/options-portfolio-analysis?view=higher-greeks");
}
