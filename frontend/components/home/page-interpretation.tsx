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
import { minutesSince, useMinuteClock } from "@/components/home/primitives";
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

/** Subscribe to a cached query without ever triggering a fetch.
 *
 *  `dataUpdatedAt` comes back too, and it is not decoration. The horizon bands
 *  collapse, and collapsing one unmounts its cards — which stops their refetch
 *  intervals while a cache-only observer like this keeps the entry alive.
 *  Without an age, a board that stopped updating an hour ago is indistinguish-
 *  able here from one that just refreshed and did not move, and this panel
 *  would describe the older reading in the present tense. */
function useCached<T>(queryKey: unknown[], queryFn: () => Promise<T>) {
  const q = useQuery<T>({ queryKey, queryFn, enabled: false });
  return { data: q.data, updatedAt: q.dataUpdatedAt };
}


export default function PageInterpretation() {
  const briefQ = useCached<EsBrief>(["es-brief"], fetchEsBrief);
  const driverQ = useCached<MarketDriverResponse>(["market-driver"], fetchMarketDriver);
  const heatmapQ = useCached<{ group: string; items: HeatmapItem[] }>(
    ["heatmap", "sectors"], () => fetchHeatmap("sectors"));
  const volQ = useCached<VolLandscapeScan>(["vol-landscape-home"], fetchVolLandscape);
  const eventsQ = useCached<{ events: CalendarEvent[] }>(["events-home"], fetchEvents);
  const ctaQ = useCached<CtaFlowBoard>(["cta-flows", "13874A"], () => fetchCtaFlows("13874A"));
  const macroQ = useCached<MacroPressureBoard>(["macro-pressure"], fetchMacroPressure);
  // 8, matching the card and the server prefetch. This read ["sector-rrg", 4]
  // while the card wrote ["sector-rrg", 8], so the two looked at different
  // tails of the same board — and once the seeded 4-week payload aged, nothing
  // refreshed it: this query is `enabled: false` and no other consumer of that
  // key existed.
  const rrgQ = useCached<SectorRrg>(["sector-rrg", 8], () => fetchSectorRrg(8));
  const valuationQ = useCached<SpValuation>(["sp-valuation"], fetchSpValuation);

  const brief = briefQ.data;
  const driver = driverQ.data;
  const heatmap = heatmapQ.data;
  const vol = volQ.data;
  const events = eventsQ.data;
  const cta = ctaQ.data;
  const macro = macroQ.data;
  const rrg = rrgQ.data;
  const valuation = valuationQ.data;

  /** Blocks whose data is materially older than the cadence they claim.
   *
   *  The threshold is each card's own refetch interval doubled — one missed
   *  cycle is noise, two means the card is not running. Reported by name and
   *  age so the write-up can say "the CTA board has not refreshed in 90
   *  minutes" instead of describing a ninety-minute-old number in the present
   *  tense. */
  const nowMin = useMinuteClock();
  const stale = useMemo(() => {
    const checks: Array<[string, number, number]> = [
      ["market_driver", driverQ.updatedAt, 10],
      ["sector_heatmap", heatmapQ.updatedAt, 5],
      ["vol_landscape", volQ.updatedAt, 15],
      ["macro_calendar", eventsQ.updatedAt, 30],
      ["cta_flows", ctaQ.updatedAt, 90],
      ["macro_pressure", macroQ.updatedAt, 90],
      ["sector_rotation", rrgQ.updatedAt, 90],
      ["sp_valuation", valuationQ.updatedAt, 180],
    ];
    return checks
      .map(([name, at, limit]) => [name, minutesSince(at, nowMin), limit] as const)
      .filter(([, age, limit]) => age != null && age > limit)
      .map(([name, age]) => ({ block: name, minutes_old: age as number }));
  }, [
    driverQ.updatedAt, heatmapQ.updatedAt, volQ.updatedAt, eventsQ.updatedAt,
    ctaQ.updatedAt, macroQ.updatedAt, rrgQ.updatedAt, valuationQ.updatedAt, nowMin,
  ]);

  /** THE BLOCKS THIS PANEL COULD NOT READ.
   *
   *  Every field below degrades to `null` when its card has not loaded — which
   *  is correct, and was also the bug: a `null` from "this board reports
   *  unavailable today" is indistinguishable from a `null` because the server
   *  prefetch hit its 8-second timeout and `page.tsx` (rightly) declined to
   *  seed a bad payload. The panel then auto-ran over the hole and said nothing
   *  about it — an absence rendered as a calm, which is the failure mode this
   *  project keeps finding in new places.
   *
   *  Naming the gap is cheap and it changes what the model can honestly write.
   *  Sent as part of the payload so the prompt can refuse to characterise a
   *  board it was never given. */
  const missing = useMemo(() => {
    const checks: Array<[string, boolean]> = [
      ["market_driver", driver == null],
      ["sector_heatmap", heatmap == null],
      ["vol_landscape", vol == null],
      ["macro_calendar", events == null],
      ["cta_flows", cta == null || cta.available === false],
      ["macro_pressure", macro == null || macro.available === false],
      ["sector_rotation", rrg == null || rrg.available === false],
      ["sp_valuation", valuation == null || valuation.available === false],
    ];
    return checks.filter(([, isMissing]) => isMissing).map(([name]) => name);
  }, [driver, heatmap, vol, events, cta, macro, rrg, valuation]);

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

      // The shape axis. Handed over with its forward null attached, because a
      // narrator given only "likely choppy" writes a sentence about the
      // afternoon — the exact reading the measurement does not support.
      session_shape: brief?.chop_trend?.available
        ? {
            label: brief.chop_trend.label,
            as_of_mark: brief.chop_trend.mark,
            efficiency: brief.chop_trend.efficiency,
            efficiency_pctile_at_this_mark: brief.chop_trend.pctile,
            median_efficiency_at_this_mark: brief.chop_trend.median_at_mark,
            pct_of_such_sessions_finishing_choppy: brief.chop_trend.p_finish_choppy_pct,
            pct_of_such_sessions_finishing_trendy: brief.chop_trend.p_finish_trendy_pct,
            base_rate_choppy_pct: brief.chop_trend.base_choppy_pct,
            base_rate_trendy_pct: brief.chop_trend.base_trendy_pct,
            n: brief.chop_trend.n_band,
            describes: "the session so far, never the hours ahead",
            forward_test: brief.chop_trend.forward,
          }
        : null,

      base_rates: br?.available
        ? {
            source: br.source, sessions: br.sessions, window_years: br.window_years,
            todays_gap: br.gaps?.today,
            gap_fill_by_bucket: br.gaps?.buckets?.map((b) => ({ bucket: b.bucket, fill_rate: b.fill_rate, n: b.n })),
            // The unconditional rate above is a whole-session frequency. This is
            // the live one, and the narrator was quoting the former as though it
            // were the latter — they diverge by 30 points or more by midday.
            gap_fill_still_open: br.gap_fill_live?.available
              ? {
                  state: br.gap_fill_live.state, as_of: br.gap_fill_live.as_of,
                  fill_rate: br.gap_fill_live.fill_rate, n: br.gap_fill_live.n,
                  conditioned_on: br.gap_fill_live.conditioned_on,
                  distance: br.gap_fill_live.distance,
                  unconditional: br.gap_fill_live.unconditional,
                  instrument: br.gap_fill_live.instrument,
                  sessions: br.gap_fill_live.sessions,
                }
              : null,
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

      // After-the-bell risk, kept as its own block rather than left to be found
      // inside `todays_schedule`. The distinction it encodes — an event that
      // cannot touch this session's range but owns the decision to hold through
      // it — is the one the model was previously unable to make at all, because
      // the card carried no single-name earnings and the prompt forbids
      // inventing events that are not in the payload.
      after_close: (brief.after_close ?? []).length
        ? {
            events: (brief.after_close ?? []).map((e) => ({
              name: e.name, symbol: e.symbol, time_et: e.time_et,
              time_approx: e.time_approx, impact: e.impact,
              market_cap: e.market_cap, affects: e.affects,
              also_reporting: e.also_reporting ?? undefined,
            })),
            premium: brief.event_premium?.available
              ? {
                  segment_handles: brief.event_premium.segment_handles,
                  vs_session: brief.event_premium.vs_session ?? null,
                  vs_session_withheld: brief.event_premium.vs_session_withheld,
                  // The two straddles are deliberately NOT passed once the
                  // multiple is withheld: handed both, the model reconstructs
                  // the ratio itself and prints the inflated number the
                  // withholding exists to prevent.
                  ...(brief.event_premium.vs_session != null
                    ? {
                        this_session_straddle: brief.event_premium.this_session_straddle,
                        next_session_straddle: brief.event_premium.next_session_straddle,
                      }
                    : {}),
                  quote_source: brief.event_premium.quote_source,
                }
              : null,
            premium_reason: brief.event_premium?.available
              ? null
              : (brief.event_premium?.reason ?? "not measured"),
            note:
              "`market_cap` selects the name and ranks nothing else — there is no index-weight " +
              "feed here, so it must never be converted into index points or a share of the " +
              "expected move. `vs_session` is the measured price of the event: ordinary sessions " +
              "of movement priced into the one overnight that contains it, from two SPX " +
              "straddles. 1.0 is a normal night.",
          }
        : null,

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

      // High-impact prints FIRST, then nearest. The old date-ordered slice(0,6)
      // cut Nonfarm payrolls and CPI out of the payload entirely, which is why
      // the model asserted "no other events in the two-week window" while the
      // calendar card on the same page listed both.
      //
      // TWO AXES, BOTH SENT. `impact` is an assigned TIMING label; `measured`
      // is the measured range expansion over 3,677 sessions. Sending only the
      // first told the model CPI was "high impact" when the platform's own
      // study puts it at 1.06x and 12th of 23 — so it could write "CPI could
      // widen the range" in perfect good faith, contradicted by a measurement
      // sitting one card below. Selection honours both axes for the same
      // reason the card's does.
      upcoming_events_2w: (() => {
        const all = events?.events ?? [];
        const priority = all.filter(
          (e) => e.impact === "high" || (e.measured != null && e.measured.band !== "none")
        );
        const prioritySet = new Set(priority);
        const rest = all.filter((e) => !prioritySet.has(e));
        return [...priority, ...rest.slice(0, Math.max(0, 10 - priority.length))].map((e) => ({
          name: e.name,
          date: e.date,
          days_away: e.days_away,
          scheduled_discontinuity: e.impact === "high",
          measured_range_multiplier: e.measured?.multiplier ?? null,
          measured_survives_correction: e.measured?.survives_fdr ?? null,
          measured_rank: e.measured ? `${e.measured.rank} of ${e.measured.of}` : null,
          // Explicitly distinguished from "measured, and ordinary".
          never_measured: e.measured == null,
        }));
      })(),
      event_measurement_note:
        "measured_range_multiplier is median |close-to-close| on the print over that " +
        "session's own trailing 60-session median — 1.00 is an ordinary day, magnitude " +
        "only, no direction. 23 events were tested and only Nonfarm payrolls survives the " +
        "multiple-comparison correction, so treat any other multiplier as unestablished. " +
        "Every event's next-session multiplier is near 1.0: nothing carries past the print.",

      // Only the regime label and citations from the driver card. Its own
      // paragraphs are another model's prose, and feeding them back would
      // produce an echo rather than an independent read.
      driver_regime: driver?.regime_label ?? null,
      driver_citations: (driver?.citations ?? []).map((c) => c.label).slice(0, 8),

      // The measured attribution, passed through rather than re-derived. It
      // rides on the driver card's response because this panel deliberately
      // fetches nothing of its own — and unlike that card's paragraphs, this is
      // a measurement rather than another model's prose, so feeding it back is
      // not an echo.
      drivers: driver?.drivers ?? null,

      // WHAT IS MISSING, SAID OUT LOUD. Without this the model cannot tell a
      // board that reported "unavailable today" from one whose fetch timed out,
      // and it has no way to know it is looking at nine cards instead of
      // eleven. Named blocks, not a count, so the write-up can say which.
      blocks_unavailable: missing,
      // Present but not current. A collapsed horizon band unmounts its cards
      // and stops their refetch, so "loaded" and "up to date" are separate
      // facts and both have to travel.
      blocks_stale: stale,
      coverage_note:
        (missing.length === 0
          ? "Every block on the page loaded."
          : `${missing.length} of 9 swing blocks did not load and are absent from this payload: ` +
            `${missing.join(", ")}. Do not characterise them, and do not describe the page as ` +
            `quiet or aligned on the strength of what is here — their absence is a fetch outcome, ` +
            `not a reading.`) +
        (stale.length > 0
          ? ` ${stale.length} block${stale.length === 1 ? " is" : "s are"} present but not current: ` +
            `${stale.map((s) => `${s.block} (${s.minutes_old}m old)`).join(", ")}. Their numbers are ` +
            `real but describe an earlier moment — say so rather than writing them in the present tense.`
          : ""),
    };
  }, [brief, driver, heatmap, vol, events, cta, macro, rrg, valuation, missing, stale]);

  return (
    <AIInterpretation
      page="home_page"
      buttonLabel="Interpret the whole page"
      data={payload ?? undefined}
      disabled={!payload}
      // The only surface with autoRun. This is the prompt the loop is meant to
      // rewrite, and on clicks alone it gathered 7 records in nine days — short
      // of the 10 its critic needs and the 8 its replay needs, so it was the
      // one prompt that could never be improved from evidence. Firing once per
      // page load feeds it at the rate the site is actually used.
      autoRun
    />
  );
}
