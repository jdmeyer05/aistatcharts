import { redirect } from "next/navigation";

export default function OptionsLabRedirect() {
  redirect("/options-portfolio-analysis?view=lab");
}
