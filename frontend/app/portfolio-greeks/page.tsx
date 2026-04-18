import { redirect } from "next/navigation";

export default function PortfolioGreeksRedirect() {
  redirect("/options-portfolio-analysis?view=portfolio");
}
