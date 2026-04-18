"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  fetchAlerts,
  createAlert,
  deleteAlert,
  patchAlert,
  type AlertType,
  type AlertChannel,
} from "@/lib/api";

const TYPE_OPTIONS: { value: AlertType; label: string; hint: string }[] = [
  { value: "fund", label: "Fund", hint: "CIK of a hedge fund (e.g., 0001067983 = Berkshire). Fires on new 13F-HR or 13D filings." },
  { value: "ticker", label: "Ticker", hint: "Ticker symbol. Fires on insider Form 4, new 13D targeting it, 8-K, or buyback announcements." },
  { value: "politician", label: "Politician", hint: "Exact House/Senate member name. Fires on new PTR disclosure." },
  { value: "activist", label: "Activist", hint: "Activist firm name (substring match). Fires on any 13D they file." },
  { value: "keyword", label: "Keyword", hint: "Phrase to match in filing text (e.g., 'going concern', 'strategic review')." },
];

const CHANNEL_OPTIONS: { value: AlertChannel; label: string; tag: string }[] = [
  { value: "email", label: "Email", tag: "live" },
  { value: "sms", label: "SMS", tag: "soon" },
  { value: "push", label: "Browser push", tag: "soon" },
];

export default function AlertsPage() {
  const qc = useQueryClient();
  const alertsQ = useQuery({ queryKey: ["alerts"], queryFn: fetchAlerts });

  const [type, setType] = useState<AlertType>("ticker");
  const [target, setTarget] = useState("");
  const [label, setLabel] = useState("");
  const [channels, setChannels] = useState<AlertChannel[]>(["email"]);

  const addAlert = useMutation({
    mutationFn: () =>
      createAlert({
        alert_type: type,
        target: target.trim(),
        label: label.trim() || undefined,
        channels,
      }),
    onSuccess: () => {
      setTarget("");
      setLabel("");
      qc.invalidateQueries({ queryKey: ["alerts"] });
    },
  });

  const removeAlert = useMutation({
    mutationFn: (id: string) => deleteAlert(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alerts"] }),
  });

  const toggleAlert = useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) => patchAlert(id, { active }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alerts"] }),
  });

  const data = alertsQ.data?.data ?? [];
  const setupRequired = alertsQ.data?.setup_required;
  const selectedTypeHint = TYPE_OPTIONS.find((o) => o.value === type)?.hint;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Smart Money Alerts</h1>
        <p className="text-text-secondary text-sm mt-1">
          Notifications when tracked filings drop. Define what you care about here — an EDGAR polling worker
          dispatches matches to your configured channels.
        </p>
      </div>

      {setupRequired && (
        <div className="card border-l-4 border-l-warn">
          <div className="text-xs font-bold uppercase tracking-wider text-warn mb-1">One-time setup needed</div>
          <p className="text-sm mb-2">
            The <code>user_alerts</code> table doesn&apos;t exist yet in Supabase. Run the schema once:
          </p>
          <pre className="text-[11px] bg-surface-alt p-2 rounded border border-border overflow-x-auto">
supabase_alerts_schema.sql  (run in Supabase SQL editor, or via CLI)
          </pre>
          <p className="text-xs text-text-muted mt-2">
            The SQL creates the table, indexes, and RLS policies so users can only see their own alerts. Safe to
            re-run — everything is <code>IF NOT EXISTS</code> / <code>OR REPLACE</code>.
          </p>
        </div>
      )}

      <div className="card border-l-4 border-l-accent">
        <div className="text-xs font-bold uppercase tracking-wider text-accent mb-1">Worker status</div>
        <p className="text-sm">
          Subscriptions persist here today. Email delivery requires the EDGAR-polling Cloud Scheduler worker —
          pattern is identical to the OI history worker we built. SMS (Twilio) and browser push are gated on
          provider setup. Unchecked channels still create a valid subscription — they just won&apos;t fire until
          the worker is live.
        </p>
      </div>

      {/* Add alert form */}
      <div className="card">
        <div className="text-sm font-semibold mb-3">Add alert</div>
        <div className="space-y-3">
          <div className="flex flex-wrap gap-3">
            <div className="flex-1 min-w-[200px]">
              <label className="metric-label">Type</label>
              <select
                value={type}
                onChange={(e) => setType(e.target.value as AlertType)}
                className="mt-0.5 w-full px-2 py-1.5 border border-border rounded text-sm bg-surface"
              >
                {TYPE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex-[2] min-w-[240px]">
              <label className="metric-label">Target</label>
              <input
                value={target}
                onChange={(e) => setTarget(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key !== "Enter") return;
                  if (!target.trim() || addAlert.isPending || channels.length === 0) return;
                  addAlert.mutate();
                }}
                placeholder={
                  type === "fund"
                    ? "0001067983"
                    : type === "ticker"
                      ? "NVDA"
                      : type === "politician"
                        ? "Hon. Nancy Pelosi"
                        : type === "activist"
                          ? "PERSHING SQUARE"
                          : "strategic review"
                }
                className="mt-0.5 w-full px-2 py-1.5 border border-border rounded text-sm bg-surface font-data"
              />
            </div>
            <div className="flex-1 min-w-[200px]">
              <label className="metric-label">Label (optional)</label>
              <input
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="My NVDA watch"
                className="mt-0.5 w-full px-2 py-1.5 border border-border rounded text-sm bg-surface"
              />
            </div>
          </div>

          {selectedTypeHint && (
            <div className="text-[11px] text-text-muted italic">{selectedTypeHint}</div>
          )}

          <div>
            <label className="metric-label mb-1">Channels</label>
            <div className="flex gap-2 flex-wrap">
              {CHANNEL_OPTIONS.map((c) => {
                const on = channels.includes(c.value);
                return (
                  <button
                    key={c.value}
                    type="button"
                    onClick={() =>
                      setChannels((prev) =>
                        prev.includes(c.value) ? prev.filter((x) => x !== c.value) : [...prev, c.value],
                      )
                    }
                    className={`px-3 py-1 text-xs rounded border transition-colors ${
                      on ? "bg-accent text-white border-accent" : "border-border text-text-muted hover:bg-surface-alt"
                    }`}
                  >
                    {c.label}
                    <span
                      className={`ml-1.5 text-[9px] font-bold uppercase ${
                        c.tag === "live" ? "text-gain" : "text-warn"
                      }`}
                    >
                      {c.tag}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={() => addAlert.mutate()}
              disabled={!target.trim() || addAlert.isPending || channels.length === 0}
              className="px-5 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm"
            >
              {addAlert.isPending ? "Saving…" : "Add alert"}
            </button>
            {addAlert.isError && (
              <span className="text-xs text-loss">
                {(addAlert.error as Error)?.message ?? "Failed — check that the alerts table is set up."}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Existing alerts */}
      <div className="card">
        <div className="flex items-baseline justify-between mb-2">
          <div className="text-sm font-semibold">Your alerts</div>
          <div className="text-xs text-text-muted">{data.length} active</div>
        </div>

        {alertsQ.isPending && <div className="text-xs text-text-muted py-4">Loading…</div>}

        {alertsQ.isError && !setupRequired && (
          <div className="text-sm text-loss py-4">
            Failed to load: {(alertsQ.error as Error)?.message ?? "unknown error"}
          </div>
        )}

        {alertsQ.isSuccess && data.length === 0 && !setupRequired && (
          <div className="text-sm text-text-muted py-6">
            No alerts yet. Add one above — good starting points: your most-held ticker, Pershing Square, Hon. Nancy
            Pelosi.
          </div>
        )}

        {data.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs font-data">
              <thead className="border-b border-border text-text-muted">
                <tr>
                  <th className="text-left py-1.5 px-2">Type</th>
                  <th className="text-left py-1.5 px-2">Target</th>
                  <th className="text-left py-1.5 px-2">Label</th>
                  <th className="text-left py-1.5 px-2">Channels</th>
                  <th className="text-left py-1.5 px-2">Created</th>
                  <th className="text-left py-1.5 px-2">Active</th>
                  <th className="text-left py-1.5 px-2"></th>
                </tr>
              </thead>
              <tbody>
                {data.map((a) => (
                  <tr key={a.id} className="border-b border-border/50 hover:bg-surface-alt">
                    <td className="py-1 px-2 uppercase text-[10px] font-bold">{a.alert_type}</td>
                    <td className="py-1 px-2 font-semibold">{a.target}</td>
                    <td className="py-1 px-2 text-text-muted">{a.label ?? "—"}</td>
                    <td className="py-1 px-2 text-text-muted">{a.channels.join(", ")}</td>
                    <td className="py-1 px-2 text-text-muted">{a.created_at.slice(0, 10)}</td>
                    <td className="py-1 px-2">
                      <label className="flex items-center gap-1 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={a.active}
                          onChange={() => toggleAlert.mutate({ id: a.id, active: !a.active })}
                          className="accent-accent"
                        />
                        <span className={a.active ? "text-gain" : "text-text-muted"}>
                          {a.active ? "on" : "off"}
                        </span>
                      </label>
                    </td>
                    <td className="py-1 px-2">
                      <button
                        onClick={() => removeAlert.mutate(a.id)}
                        disabled={removeAlert.isPending}
                        className="text-[10px] text-text-muted hover:text-loss"
                        title="Delete alert"
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card card-compact text-[11px] text-text-muted">
        <strong>Under the hood:</strong> subscriptions persist in Supabase table <code>public.user_alerts</code> with
        row-level security (each user sees only their own). An EDGAR RSS poller (Cloud Run + Scheduler cron, every
        10 min) will evaluate new filings against active alerts and dispatch matches. Poller is the next deploy; the
        subscription CRUD you see here works today.
      </div>
    </div>
  );
}
