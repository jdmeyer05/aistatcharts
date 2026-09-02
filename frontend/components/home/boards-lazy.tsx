"use client";

/**
 * The home-only boards that are no longer cards on the page.
 *
 * WHY THEY LIVE IN THEIR OWN MODULE. Measured across four production builds:
 * deleting the entire swing half of the home page — its render AND its import —
 * changed the route's JavaScript by zero bytes. `HomeFast` and `HomeSwing` are
 * two exports of `home-client.tsx`, and that module statically imports every
 * board, so importing either export drags all of them. Tree-shaking does not
 * cross the "use client" boundary to drop the unused one.
 *
 * So collapsing a band never saved a byte and neither would a route split. The
 * only thing that moves the bundle is the import graph, which is why these
 * two moved out of `home-client.tsx` and are reached exclusively through
 * `next/dynamic` in `board-roster.tsx`. They are downloaded when a reader opens
 * the row, and not before.
 *
 * Moved verbatim from `home-client.tsx` — the behaviour, the cadences and the
 * query keys are unchanged, so the roster's shadow queries and the page
 * interpretation still read the same cache entries they always did.
 */

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  fetchHeatmap,
  fetchVolLandscape,
} from "@/lib/api";
import { ordinal } from "@/lib/home-constants";
import { CardHeader, Takeaway } from "@/components/home/primitives";

/** Local copy of the tone helper — `home-client.tsx` keeps its own for the
 *  cards that stayed there, and duplicating four lines is cheaper than a shared
 *  import that would put this module back on the initial graph.
 *
 *  NOT quite the original, and deliberately so. That version reads
 *  `if (n == null || n === 0) muted; return n > 0 ? gain : loss`, so a NaN
 *  falls through `NaN > 0` — which is false — and paints RED. A change that
 *  could not be computed rendered as a loss, which is this project's most
 *  repeated bug wearing a colour. This copy tests `Number.isNaN` explicitly and
 *  returns the neutral tone. Everything else in this module is a verbatim move,
 *  so the note at the top holds for all of it but this. */
function pctClass(n: number | undefined | null): string {
  if (n == null || Number.isNaN(n)) return "text-text-muted";
  return n > 0 ? "text-gain" : n < 0 ? "text-loss" : "text-text-muted";
}

/* ─── Sector Relative ─────────────────────────────────────────── */

function SectorRelative() {
  const q = useQuery({
    queryKey: ["heatmap", "sectors"],
    queryFn: () => fetchHeatmap("sectors"),
    refetchInterval: 60_000,
    staleTime: 45_000,
  });
  // Fall back inside useMemo so an undefined `q.data` doesn't churn the
  // `[]` reference every render and re-trigger the sort.
  const sorted = useMemo(
    () => [...(q.data?.items ?? [])].sort((a, b) => b.change - a.change),
    [q.data?.items]
  );
  const maxAbs = Math.max(0.5, ...sorted.map((s) => Math.abs(s.change || 0)));

  // The spread between best and worst is what says whether today was a sector
  // day or an index day, and nothing computed it.
  const read = useMemo(() => {
    if (sorted.length < 2) return null;
    const top = sorted[0];
    const bottom = sorted[sorted.length - 1];
    const spread = (top.change ?? 0) - (bottom.change ?? 0);
    const up = sorted.filter((s) => (s.change ?? 0) > 0).length;
    return { top, bottom, spread, up, n: sorted.length };
  }, [sorted]);

  return (
    <div className="card card-compact space-y-2">
      <CardHeader
        title="Sector Relative"
        href="/sector-analysis"
        asOf={q.dataUpdatedAt || null}
        staleAfterMin={15}
      />
      {q.isLoading && <div className="text-xs text-text-muted">Loading…</div>}
      {/* Without this the card rendered a header over an empty box whenever the
          rows were missing, which reads as "still loading" forever rather than
          as a fault. Say which it is. */}
      {!q.isLoading && sorted.length === 0 && (
        <div className="py-2 flex items-baseline gap-2 flex-wrap">
          <p className="text-xs text-text-muted">
            {q.isError ? "Couldn't load sector performance." : "No sector data returned."}
          </p>
          {q.isError && (
            <button
              type="button"
              onClick={() => q.refetch()}
              disabled={q.isFetching}
              className="text-[0.65rem] text-accent hover:underline disabled:opacity-50"
            >
              {q.isFetching ? "Retrying…" : "Retry"}
            </button>
          )}
        </div>
      )}
      <div className="space-y-1">
        {sorted.map((s) => {
          const pct = s.change || 0;
          const width = Math.abs(pct) / maxAbs * 50;
          const isUp = pct >= 0;
          return (
            <div key={s.symbol} className="flex items-center gap-2 text-xs tabular-nums">
              <div className="w-16 truncate text-text-muted" title={s.label}>{s.label}</div>
              <div className="flex-1 flex h-4 items-center relative">
                <div className="absolute left-1/2 top-0 bottom-0 w-px bg-border" />
                {isUp ? (
                  <div
                    className="absolute left-1/2 top-0.5 bottom-0.5 bg-gain/70 rounded-r"
                    style={{ width: `${width}%` }}
                  />
                ) : (
                  <div
                    className="absolute right-1/2 top-0.5 bottom-0.5 bg-loss/70 rounded-l"
                    style={{ width: `${width}%` }}
                  />
                )}
              </div>
              <div className={`w-14 text-right ${pctClass(pct)}`}>
                {pct > 0 ? "+" : ""}{pct.toFixed(2)}%
              </div>
            </div>
          );
        })}
      </div>
      {read && (
        <Takeaway
          headline={
            `${read.up} of ${read.n} sectors green, and the spread from ${read.top.label} to ` +
            `${read.bottom.label} is ${read.spread.toFixed(2)} points.`
          }
          detail={
            `A wide spread means the index move is not the whole story and there is something to ` +
            `pick between sectors; a narrow one means everything moved together. This is today's ` +
            `dispersion in isolation — where it sits against its own history is on the rotation ` +
            `board, which is the card that keeps a reference set for it.`
          }
        />
      )}
    </div>
  );
}

/* ─── Vol Landscape Snapshot ──────────────────────────────────── */

/** "Relative to what" for a single measure.
 *
 *  Renders the percentile against the measure's own recorded history — never a
 *  placeholder and never a middle value, because a stand-in reads as a real
 *  reading.
 *
 *  `gap` handles the MIXED case. `percentiles()` counts history per measure, so
 *  a measure added to TRACKED later carries fewer rows than its neighbours; once
 *  the older ones clear the 60-row floor and it has not, this row would show
 *  percentiles on three stats and a silently bare number on the fourth — which
 *  is the exact ambiguity this whole change exists to remove. When some measures
 *  can be placed and this one cannot, say so in place. When NONE can, the row
 *  note says it once instead (see `refNote`) rather than repeating a dash. */
function Ref({ h, gap }: { h?: { pctile: number | null; n_history: number }; gap?: boolean }) {
  if (!h || h.pctile == null) {
    if (!gap || !h) return null;
    return (
      <span
        className="ml-1 text-text-muted/50"
        title={`No reference for this measure yet — ${h.n_history} recorded sessions, and 60 are needed. The other stats in this row have enough history; this one does not.`}
      >
        —
      </span>
    );
  }
  const p = Math.round(h.pctile);
  // Only the tails are worth colouring. Everything between is the normal state
  // and colouring it would manufacture significance out of an ordinary reading.
  const tone = p >= 80 ? "text-loss" : p <= 20 ? "text-gain" : "text-text-muted/70";
  // `ordinal` rather than an inline suffix: the inline version is where "1th
  // pctile" came from, and it was already fixed once at three other sites.
  return (
    <span className={`ml-1 ${tone}`} title={`Percentile against its own last ${h.n_history} recorded sessions.`}>
      {ordinal(p)}
    </span>
  );
}

function VolLandscapeSnapshot() {
  const q = useQuery({
    queryKey: ["vol-landscape-home"],
    queryFn: fetchVolLandscape,
    refetchInterval: 5 * 60_000,
    staleTime: 4 * 60_000,
  });
  const d = q.data;

  // This card read `top_dislocations` / `rows` / `items`, and the endpoint
  // returns none of them — it returns `metrics`, `divergences`, `summary`,
  // `regime` and `regime_action`. Every one of those lookups resolved to
  // undefined, so the fallback chain always produced an empty array and the
  // card permanently displayed "No dislocations surfaced right now." It had
  // never shown data. `divergences` is the field that actually carries the
  // dislocations the card was written to show.
  const divergences = useMemo(() => (d?.divergences ?? []).slice(0, 5), [d]);
  const s = d?.summary;

  // One honest sentence when the reference set is too thin, instead of a
  // per-stat "n/a". `n_history` is the same for every measure (they are
  // recorded as one row per session), so take it from whichever is present.
  const refNote = useMemo(() => {
    const hist = d?.history;
    if (!hist) return null;
    const entries = Object.values(hist);
    if (entries.length === 0) return null;
    if (entries.some((e) => e.pctile != null)) return null;
    const n = Math.max(...entries.map((e) => e.n_history));
    return `No historical reference yet — ${n} session${n === 1 ? "" : "s"} recorded, and the percentiles above need 60. Until then these are levels, not readings: nothing here says whether they are high or low.`;
  }, [d]);

  // True only in the mixed state: at least one measure placed, at least one not.
  // Drives the in-place dash so no number is ever silently uncontextualised.
  const refPartial = useMemo(() => {
    const entries = Object.values(d?.history ?? {});
    return entries.some((e) => e.pctile != null) && entries.some((e) => e.pctile == null);
  }, [d]);

  // Cuts that cannot discriminate, named rather than left in the payload.
  const nearMedianCuts = useMemo(() => {
    const t = d?.thresholds;
    if (!t) return [];
    return Object.entries(t)
      // `pctile_in_universe != null` is not redundant with `near_median`.
      // threshold_report omits `near_median` entirely when it cannot compute a
      // percentile, so the pair is unreachable today — but the type permits it,
      // and the previous `?? 0` would have printed "0th percentile", inventing
      // the exact statistic this sentence exists to report. Filter, never
      // default: a fabricated number is worse than a missing line.
      .filter(([, v]) => v.near_median && v.pctile_in_universe != null)
      .map(([k, v]) => {
        const where = `the ${k.replace(/_/g, " ")} cut of ${v.cut} sits at the ${ordinal(v.pctile_in_universe as number)} percentile`;
        return v.n ? `${where} of today's ${v.n} names` : where;
      });
  }, [d]);

  return (
    <div className="card card-compact space-y-2">
      <CardHeader
        title="Vol Landscape"
        href="/vol-landscape"
        asOf={q.dataUpdatedAt || null}
        staleAfterMin={30}
      />

      {q.isLoading && <div className="text-xs text-text-muted">Loading…</div>}

      {!q.isLoading && !d && (
        <div className="text-xs text-text-muted">Vol landscape unavailable.</div>
      )}

      {d && (
        <>
          {d.regime && (
            <div className="flex items-baseline gap-2 flex-wrap">
              <span className="text-[0.65rem] font-bold px-1.5 py-0.5 rounded bg-accent/15 text-accent">
                {d.regime}
              </span>
              {d.regime_action && (
                <span className="text-[0.65rem] text-text">{d.regime_action}</span>
              )}
            </div>
          )}

          {s && (
            <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[0.62rem] text-text-muted tabular-nums">
              <span title="Average front-month implied vol across the scanned universe.">
                avg IV <span className="text-text">{s.avg_iv?.toFixed(1)}</span>
                <Ref h={d.history?.avg_iv} gap={refPartial} />
              </span>
              <span title="Implied over realised. Above 1 means options are pricing more movement than has been delivered.">
                IV/HV <span className="text-text">{s.avg_ivhv?.toFixed(2)}</span>
                <Ref h={d.history?.avg_ivhv} gap={refPartial} />
              </span>
              <span title="Names whose term structure is inverted — front vol above back vol, which prices near-term event risk.">
                <span className="text-text">{s.n_inverted}</span> inverted
                <span className="text-text-muted/70"> of {s.n_tickers}</span>
                <Ref h={d.history?.n_inverted} gap={refPartial} />
              </span>
              {/* Separate denominators on purpose. Skew is counted only over
                  chains that pass put-call parity, so a shared "of 20" would
                  overstate it — the two numbers are no longer out of the same
                  pool and cannot share a label. */}
              <span title="Names with unusually steep put skew. Counted only over chains whose ATM put and ATM call agree to within put-call parity — a chain quoting stale wings gets no vote.">
                <span className="text-text">{s.n_steep_skew}</span> steep skew
                <span className="text-text-muted/70"> of {s.n_skew_rated ?? s.n_tickers}</span>
                <Ref h={d.history?.n_steep_skew} gap={refPartial} />
              </span>
            </div>
          )}

          {/* RELATIVE TO WHAT. A bare "avg IV 20.7" reads as a fact about the
              market; without a reference set it is a fact about nothing. The
              percentiles above are computed and typed already — they were just
              never rendered, so the card printed raw levels and the reader had
              no way to tell whether 20.7 was calm, ordinary or extreme.

              When the reference does not exist yet, say so ONCE here rather
              than stamping "n/a" on every stat. Silence would be worse than
              either: an uncontextualised number looks identical to a
              contextualised one that happens to be normal. */}
          {refNote && (
            <p className="text-[0.58rem] text-text-muted/80 leading-snug">
              {refNote}
            </p>
          )}

          {/* A cut sitting at the median of the cross section splits the
              universe in half, so a count taken against it cannot separate a
              regime from its opposite — "10 of 17 have steep skew" is then
              close to "10 of 17 are above average". The backend already
              discloses this in `thresholds`; nothing displayed it. Shown only
              when it is true, because a cut that DOES discriminate is not news. */}
          {nearMedianCuts.length > 0 && (
            <p className="text-[0.58rem] text-amber-400/80 leading-snug">
              {nearMedianCuts.join("; ")} — that count separates less than it
              appears to.
            </p>
          )}

          {/* What the scan above means for the instrument actually being traded.
              Everything else on this card describes the vol universe; this is
              the only part that answers "so what for ES". Each row is the
              measured value on the left and the reading beside it, because a
              reader who disagrees with the reading still needs the number. */}
          {(d.es_read?.reads?.length ?? 0) > 0 && (
            <div className="space-y-1 border-t border-border pt-1.5">
              <h4 className="text-[0.6rem] font-bold uppercase tracking-wider text-text-muted">
                What this says for ES
              </h4>
              {d.es_read!.reads!.map((r, i) => (
                <div key={i} className="text-[0.65rem] leading-snug">
                  <div className="flex items-baseline gap-2">
                    <span className="text-text-muted shrink-0 w-[8.5rem] truncate" title={r.label}>
                      {r.label}
                    </span>
                    <span className="text-text font-medium tabular-nums">{r.value}</span>
                  </div>
                  <p className="text-text-muted pl-[9.25rem] leading-snug">{r.note}</p>
                  {/* Rendered, not tucked into a tooltip. A caveat that only
                      appears on hover is a caveat the reader will act without. */}
                  {r.caveat && (
                    <p className="text-[0.55rem] text-text-muted/70 pl-[9.25rem] leading-snug italic">
                      {r.caveat}
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}

          {divergences.length === 0 ? (
            <div className="text-xs text-text-muted">No cross-asset dislocations right now.</div>
          ) : (
            <div className="space-y-1 text-[0.65rem]">
              {divergences.map((x, i) => (
                <div key={i} className="flex items-start gap-2" title={x.description}>
                  <span className="font-bold shrink-0 w-[4.5rem] truncate">{x.pair}</span>
                  <span className="text-text-muted shrink-0 w-[3.5rem] truncate">{x.metric}</span>
                  <span className="text-text flex-1 min-w-0 leading-snug">{x.signal}</span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

export { SectorRelative, VolLandscapeSnapshot };
