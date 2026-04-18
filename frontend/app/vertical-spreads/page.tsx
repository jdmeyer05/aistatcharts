import { redirect } from "next/navigation";

export default function VerticalSpreadsRedirect() {
  redirect("/spread-analysis?strategy=vertical");
}
