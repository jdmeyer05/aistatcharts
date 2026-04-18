import { redirect } from "next/navigation";

export default function CalendarSpreadRedirect() {
  redirect("/spread-analysis?strategy=calendar");
}
