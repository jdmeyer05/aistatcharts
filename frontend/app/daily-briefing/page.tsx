import { redirect } from "next/navigation";

export default function MarketScanRedirect() {
  redirect("/?view=market-scan");
}
