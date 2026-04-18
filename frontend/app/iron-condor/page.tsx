import { redirect } from "next/navigation";

export default function IronCondorRedirect() {
  redirect("/spread-analysis?strategy=iron-condor");
}
