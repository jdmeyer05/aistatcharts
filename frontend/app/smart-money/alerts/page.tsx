import { PendingSmartMoneyPage } from "../_shared/pending-page";

export default function AlertsPage() {
  return (
    <PendingSmartMoneyPage
      pageTitle="Smart Money Alerts"
      subtitle="Push notifications when tracked filings drop — before the news cycle picks them up."
      overview="Real edge in smart-money tracking is time. 13Fs go stale in weeks, activist filings move markets in hours. This page is the subscription layer: define what you care about (a fund, an activist, a politician, a ticker) and get notified the moment an EDGAR filing matches. Email, SMS (Twilio), or push (web push API)."
      features={[
        {
          title: "Fund Watchlist",
          description:
            "Subscribe to Berkshire, Pershing, Third Point, etc. Get notified on every 13F-HR, 13F-HR/A, 13D, and Form 4 from tracked filers.",
        },
        {
          title: "Ticker Watchlist",
          description:
            "Any ticker. Notifies on insider Form 4, new 13D targeting it, 8-K filings, buyback announcements.",
        },
        {
          title: "Politician Watchlist",
          description:
            "Individual House/Senate members. Get PTR filings within hours of publication to clerk.house.gov or efdsearch.senate.gov.",
        },
        {
          title: "Keyword Alerts",
          description:
            "Match on filing text (e.g., 'activist', 'strategic review', 'going concern'). Flags 13Ds with hostile intent language.",
        },
        {
          title: "Cluster Alerts",
          description:
            "Notify only when N+ smart-money signals align on a ticker within a window. Reduces noise vs single-signal spam.",
        },
        {
          title: "Delivery channels",
          description:
            "Email (SendGrid/SES), SMS (Twilio), browser push, Slack webhook. User picks per alert type.",
        },
        {
          title: "Daily Digest",
          description:
            "Optional end-of-day email summarizing all tracked filings — lower urgency, higher signal.",
        },
      ]}
      sources={[
        {
          name: "SEC EDGAR RSS",
          status: "free",
          note: "EDGAR publishes new filings as RSS at sec.gov/cgi-bin/browse-edgar?action=getcurrent. Poll every 5-15 min.",
        },
        {
          name: "Supabase subscriptions table",
          status: "internal",
          note: "Need user_alerts table: user_id, alert_type, target (cik/ticker/member), channels, frequency.",
        },
        {
          name: "SendGrid / AWS SES",
          status: "paid",
          note: "Transactional email delivery. Free tiers adequate for <100 users.",
        },
        {
          name: "Twilio",
          status: "paid",
          note: "SMS delivery — $0.0079/SMS domestic. Opt-in required.",
        },
        {
          name: "Web Push API",
          status: "free",
          note: "Browser-native push notifications via service workers. Free but requires HTTPS and user permission.",
        },
        {
          name: "Cloud Scheduler + Cloud Run worker",
          status: "internal",
          note: "Same pattern as OI history worker. Cron job polls EDGAR, dispatches matches. Already have the deployment pipeline.",
        },
      ]}
      backlinks={[
        { label: "Overview", href: "/smart-money" },
      ]}
    />
  );
}
