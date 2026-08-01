"use client";

/**
 * One interpretation for the whole page, replacing the per-card panels.
 *
 * WHY ONE AND NOT SEVERAL. Three separate interpreters produced three separate
 * takes that never referenced each other — the CTA panel could call for one
 * lean while the macro panel implied another, and neither knew what the levels
 * or the session clock were doing. The reader was left doing the synthesis
 * that the model should have been doing. It also cost three Claude calls to
 * say less than one well-fed call says.
 *
 * READS THE CACHE, NEVER FETCHES. Every query below is declared with
 * `enabled: false`, so this subscribes to whatever the cards have already
 * loaded and re-renders when they update, without issuing a single request of
 * its own or changing any card's refetch cadence.
 *
 * THE PAYLOAD IS SUMMARISED, NOT DUMPED. The raw page state is far past the
 * server's prompt budget, and most of it is chart geometry the model cannot
 * use — 81-point gamma profiles, RRG tails, full option chains. Each block is
 * reduced to the handful of fields that carry the meaning. Less noise is also
 * simply a better prompt.
 */

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { AIInterpretation } from "@/components/ai-interpretation";
import {
  fetchEsBrief,
  fetchMarketDriver,
  fetchHeatmap,
  fetchEvents,
  fetchVolLandscape,
  fetchCtaFlows,
  fetchMacroPressure,
  fetchSectorRrg,
  fetchSpValuation,
  type EsBrief,
  type MarketDriverResponse,
  type HeatmapItem,
  type VolLandscapeScan,
  type CtaFlowBoard,
  type MacroPressureBoard,
  type SectorRrg,
  type SpValuation,
  type CalendarEvent,
} from "@/lib/api";

/** Subscribe to a cached query without ever triggering a fetch. */
function useCached<T>(queryKey: unknown[], queryFn: () => Promise<T>) {
  return useQuery<T>({ queryKey, queryFn, enabled: false }).data;
}

export default function PageInterpretation() {
  const brief = useCached<EsBrief>(["es-brief"], fetchEsBrief);
  const driver = useCached<MarketDriverResponse>(["market-driver"], fetchMarketDriver);
  const heatmap = useCached<{ group: string; items: HeatmapItem[] }>(
    ["heatmap", "sectors"], () => fetchHeatmap("sectors"));
  const vol = useCached<VolLandscapeScan>(["vol-landscape-home"], fetchVolLandscape);
  const events = useCached<{ events: CalendarEvent[] }>(["events-home"], fetchEvents);
  const cta = useCached<CtaFlowBoard>(["cta-flows", "13874A"], () => fetchCtaFlows("13874A"));
  const macro = useCached<MacroPressureBoard>(["macro-pressure"], fetchMacroPressure);
  const rrg = useCached<SectorRrg>(["sector-rrg", 4], () => fetchSectorRrg(4));
  const valuation = useCached<SpValuation>(["sp-valuation"], fetchSpValuation);

  const payload = useMemo(() => {
    // The ES briefing is the spine of this page; without it there is nothing
    // coherent to synthesise, so hold the button rather than send a fragment.
    if (!brief?.available) return null;

    const lv = brief.levels;
    const em = brief.expected_move;
    const gam = brief.gamma;
    const intra = brief.intraday;
    const br = brief.base_rates;

    const sectors = [...(heatmap?.items ?? [])].sort((a, b) => b.change - a.change);

    return {
      as_of: brief.asof,
      session: brief.session,
      session_day: brief.session_day,
      schedule_is_today: brief.schedule_is_today,

      conditions: brief.conditions,

      levels: lv?.available
        ? {
            last: lv.last, mode: lv.mode, stale: lv.stale, bar_age_min: lv.bar_age_min,
            rth_complete: lv.rth_complete, session_date: lv.session_date,
            profile_is_prior_session: lv.profile_is_prior_session,
            contract_roll_risk: lv.contract_roll_risk,
            nearest: lv.nearest ? { label: lv.nearest.label, value: lv.nearest.value, distance: lv.nearest.distance } : null,
            // `reach` is the difference between "the level is 40 handles away"
            // and "the level is half a session's travel away" — the second is
            // the one that decides whether it belongs in today's plan.
            levels: lv.levels?.map((l) => ({
              label: l.label, group: l.group, value: l.value, distance: l.distance,
              pct_of_expected_range: l.pct_of_expected_range, reach: l.reach,
            })),
          }
        : null,

      expected_move: em?.available
        ? {
            headline: em.headline?.source,
            sigma_handles: em.expected_handles,
            expected_range_handles: em.expected_range,
            band: [em.lower, em.upper],
            consumed: em.consumed,
            vol_regime: em.vol_regime,
            overnight: em.overnight,
            estimates: em.estimates?.map((e) => ({
              source: e.source, sigma: e.sigma_handles, quote_source: e.quote_source,
            })),
          }
        : null,

      // The 81-point profile and full strike ladder are chart geometry — the
      // regime, the flip and the walls are what carry the meaning.
      dealer_gamma: gam?.available
        ? {
            regime: gam.regime, flip_es: gam.flip_es, above_flip: gam.above_flip,
            distance_to_flip: gam.distance_to_flip,
            call_wall_es: gam.call_wall_es, put_wall_es: gam.put_wall_es,
            zero_dte_share_pct: gam.zero_dte_share,
          }
        : null,

      intraday_structure: intra?.available
        ? {
            day_type: intra.day_type,
            initial_balance: intra.opening_range?.ib,
            opening_range_30m: intra.opening_range?.or30,
            relative_volume: intra.relative_volume,
            overnight_inventory: intra.overnight_inventory,
            naked_pocs: (intra.naked_pocs ?? []).slice(0, 3),
            unfilled_gaps: (intra.unfilled_gaps ?? []).slice(0, 3),
            cross_asset: (intra.cross_asset?.rows ?? []).map((r) => ({
              label: r.label, change_pct: r.change_pct,
            })),
          }
        : null,

      base_rates: br?.available
        ? {
            source: br.source, sessions: br.sessions, window_years: br.window_years,
            todays_gap: br.gaps?.today,
            gap_fill_by_bucket: br.gaps?.buckets?.map((b) => ({ bucket: b.bucket, fill_rate: b.fill_rate, n: b.n })),
            typical_session: br.range,
            release_days_vs_normal: br.events?.events?.map((e) => ({
              name: e.name, range_vs_normal: e.range_vs_normal, n: e.n,
            })),
          }
        : null,

      // WHEN the session gets there, not how far. `live` is the intraday-critical
      // part — it says how much range is typically left from this hour — so it
      // goes first and survives truncation; the full hourly table is context.
      // Its window is its own and shorter than the daily rates above, which is
      // why the label travels with it rather than being inherited.
      session_path: br?.path?.available
        ? {
            where_we_are: br.path.live,
            source: br.path.source, sessions: br.path.sessions,
            first_hour: br.path.initial_balance,
            ib_break_follow_through: br.path.ib_breaks,
            ib_width_effect: br.path.ib_width,
            extreme_by_hour: br.path.extremes?.map((e) => ({
              slot: e.slot, high_pct: e.high_pct, low_pct: e.low_pct, minutes: e.minutes,
            })),
            range_covered_by_hour: br.path.progress?.map((p) => ({
              slot: p.slot, range_complete_pct: p.range_complete_pct, both_extremes_in_pct: p.both_in_pct,
            })),
            // The full caveat list is a UI disclosure; only the two that would
            // change how these numbers are READ are worth prompt budget.
            caveat:
              "Hourly buckets, cash RTH only. The 15:30 bucket is 30 minutes, half the " +
              "width of the others, so its share of the extremes understates the close.",
          }
        : null,

      // How many stocks are going with the index. Intraday-critical and cheap,
      // so it sits ahead of the schedule and the swing blocks.
      breadth: brief.breadth?.available
        ? {
            live: brief.breadth.live,
            session: brief.breadth.session,
            universe_n: brief.breadth.universe?.n,
            net_advancers_pct: brief.breadth.net_advancers_pct,
            advancers: brief.breadth.advancers,
            decliners: brief.breadth.decliners,
            ad_ratio: brief.breadth.ad_ratio,
            up_volume_pct: brief.breadth.up_volume_pct,
            trin: brief.breadth.trin,
            trin_band: brief.breadth.trin_band?.label,
            equal_vs_cap: brief.breadth.equal_vs_cap?.available
              ? {
                  spread_pct: brief.breadth.equal_vs_cap.spread_pct,
                  label: brief.breadth.equal_vs_cap.label,
                }
              : null,
            divergence: brief.breadth.divergence,
            caveat:
              "Reconstructed on a liquid US universe, not NYSE-listed issues — direction " +
              "and extremes carry, absolute counts will not match a terminal. NYSE TICK is " +
              "not available from any wired source and is absent, not approximated.",
          }
        : null,

      // What yesterday's bar says about tomorrow's RANGE. Geometry forecasts
      // range (IC 0.158, t=75) far better than direction (IC -0.016), so this
      // block is for sizing and target realism, never for a directional call.
      candle_context: brief.candles?.available
        ? {
            bar: brief.candles.bar,
            tomorrow_range: brief.candles.tomorrow_range
              ? {
                  p25: brief.candles.tomorrow_range.p25,
                  p50: brief.candles.tomorrow_range.p50,
                  p75: brief.candles.tomorrow_range.p75,
                  prob_exceeds_1_atr: brief.candles.tomorrow_range.prob_exceeds_1_atr,
                  n: brief.candles.tomorrow_range.n,
                }
              : null,
            measured_vs_implied: brief.candles.vs_implied,
            direction_tilt_is_negligible: true,
            caveat:
              "Cash index (^GSPC), not ES. Range is the predictable part; the close-location " +
              "tilt spans ~10bp and is context only, never a directional signal.",
          }
        : null,

      todays_schedule: brief.schedule,
      macro_news: (brief.news ?? []).slice(0, 6).map((n) => ({ source: n.source, title: n.title })),

      cta_positioning: cta?.available
        ? {
            bias_1w: cta.bias_1w, bias_1m: cta.bias_1m,
            current_exposure: cta.current_exposure,
            pivots: cta.pivots,
            note: "Exposure is model points, not dollars. Swing horizon, not intraday.",
          }
        : null,

      macro_backdrop: macro?.available
        ? {
            net_label: macro.net_label, net_score: macro.net_score, counts: macro.counts,
            biggest_headwind: macro.biggest_headwind?.label,
            biggest_support: macro.biggest_support?.label,
            top_movers: macro.rows
              ?.filter((r) => Math.abs(r.change_z ?? 0) >= 1)
              .slice(0, 5)
              .map((r) => ({ label: r.label, verdict: r.verdict, change_z: r.change_z })),
          }
        : null,

      // Guard the overlap: with fewer than six sectors, slice(0,3) and
      // slice(-3) share rows and the model would see the same name as both
      // leader and laggard.
      sectors_today: sectors.length >= 6
        ? {
            leaders: sectors.slice(0, 3).map((s) => ({ label: s.label, change: s.change })),
            laggards: sectors.slice(-3).map((s) => ({ label: s.label, change: s.change })),
          }
        : { all: sectors.map((s) => ({ label: s.label, change: s.change })) },

      sector_rotation_rrg: rrg?.available
        ? {
            counts: rrg.counts,
            leading: rrg.rows?.filter((r) => r.quadrant === "leading").map((r) => r.label),
            lagging: rrg.rows?.filter((r) => r.quadrant === "lagging").map((r) => r.label),
          }
        : null,

      vol_landscape: vol
        ? {
            regime: vol.regime, regime_action: vol.regime_action,
            summary: vol.summary,
            divergences: (vol.divergences ?? []).slice(0, 3),
          }
        : null,

      sp_valuation: valuation?.available
        ? { median_premium_pct: valuation.median_premium_pct }
        : null,

      upcoming_events_2w: (events?.events ?? []).slice(0, 6),

      // Only the regime label and citations from the driver card. Its own
      // paragraphs are another model's prose, and feeding them back would
      // produce an echo rather than an independent read.
      driver_regime: driver?.regime_label ?? null,
      driver_citations: (driver?.citations ?? []).map((c) => c.label).slice(0, 8),
    };
  }, [brief, driver, heatmap, vol, events, cta, macro, rrg, valuation]);

  return (
    <AIInterpretation
      page="home_page"
      buttonLabel="Interpret the whole page"
      data={payload ?? undefined}
      disabled={!payload}
    />
  );
}
