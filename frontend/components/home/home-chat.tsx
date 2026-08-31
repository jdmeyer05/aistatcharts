"use client";

/**
 * Ask questions about what is on this page.
 *
 * WHY THIS IS NOT THE INTERPRETATION PANEL WITH AN INPUT BOX. The panel answers
 * one fixed question in under 220 words and is graded on picking three things
 * out of twenty. This answers whatever is asked. The two fail in opposite
 * directions — the panel by surveying, a chat by answering anyway — so they get
 * different prompts and, deliberately, different payloads.
 *
 * THE PAYLOAD IS BROADER HERE, NOT SHARED. The panel prunes hard to fit ~13k of
 * a 20k budget, because everything it sends competes with the synthesis it has
 * to produce. A chat has a 400k-character ceiling and no idea which block the
 * next question is about, so breadth beats pruning: it sends whole blocks and
 * lets the model find the one that answers. Sharing one builder would force one
 * of the two surfaces to carry the other's tradeoff.
 *
 * THE SNAPSHOT IS FROZEN ON FIRST ASK. Every turn of one conversation sends the
 * same snapshot. Two reasons and both matter:
 *   - Coherence. The page refetches on its own cadence. Re-reading it per turn
 *     would let turn 3 answer off different numbers than turn 1, and nothing on
 *     screen would say so.
 *   - Cost. The server caches the prompt prefix (system + snapshot). A stable
 *     snapshot is the whole reason the second and later questions are cheap.
 *     Measured: the real payload serialises to 43,056 characters, roughly 11k
 *     input tokens, read back from cache on every follow-up.
 * When the underlying page has moved on, the header offers a reset rather than
 * silently swapping the ground under an open conversation.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { CardHeader } from "@/components/home/primitives";
import {
  askHomeChat,
  type EsBrief,
  type HomeChatTurn,
  type MarketDriverResponse,
  type VolLandscapeScan,
  type CalendarEvent,
  type HeatmapItem,
  type MacroPressureBoard,
  type SpValuation,
  type CtaFlowBoard,
  type SectorRrg,
} from "@/lib/api";

type Msg = HomeChatTurn & {
  grounding?: { unverified_count: number; unverified_tokens: string[] };
  truncated?: boolean;
};

/** A stored turn is only usable if it still has the shape the API requires.
 *  Anything else — an older schema, a half-written entry, a hand-edited key —
 *  gets sent back as history and rejected as a 422, which presents as "the chat
 *  is broken" with nothing to connect it to storage. */
function isMsg(v: unknown): v is Msg {
  const m = v as Msg | null;
  return !!m && (m.role === "user" || m.role === "assistant") && typeof m.content === "string";
}

/** Conversations survive a refresh.
 *
 *  THE SNAPSHOT IS STORED WITH THE MESSAGES, not separately and not omitted.
 *  Restoring the turns alone would leave every earlier answer referencing
 *  numbers the chat could no longer see, and the next reply would be reasoning
 *  from a different page than the one above it — the exact incoherence the
 *  freeze exists to prevent, reintroduced by the reload.
 *
 *  Written after mount, never during render: `localStorage` does not exist on
 *  the server, and reading it in a render pass is how you get a hydration
 *  mismatch on a streamed page. */
const STORE_KEY = "home-chat-v1";
const STORE_TTL_MS = 24 * 60 * 60 * 1000;

type Stored = { msgs: Msg[]; frozen: { data: unknown; asOf: string | null }; savedAt: number };

function load(): Stored | null {
  try {
    const raw = window.localStorage.getItem(STORE_KEY);
    if (!raw) return null;
    const v = JSON.parse(raw) as Stored;
    if (!v?.frozen || !Array.isArray(v.msgs) || !v.msgs.every(isMsg)) return null;
    // A day-old conversation about a day-old page is clutter, not continuity.
    if (!v.savedAt || Date.now() - v.savedAt > STORE_TTL_MS) return null;
    return v;
  } catch {
    return null;
  }
}

function save(v: Stored | null) {
  try {
    if (v) window.localStorage.setItem(STORE_KEY, JSON.stringify(v));
    else window.localStorage.removeItem(STORE_KEY);
  } catch {
    /* quota or private mode — the conversation still works, it just will not
       survive a refresh. Never let persistence failure break the chat. */
  }
}

const SUGGESTIONS = [
  "What is the single most unusual thing on this page right now?",
  "What does the pre-open prior say, and how much should I trust it?",
  "Which blocks are stale or unavailable?",
  "What do the base rates say about today's gap?",
];

export default function HomeChat() {
  const qc = useQueryClient();
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // The frozen snapshot. Null until the first question is asked.
  const frozen = useRef<{ data: unknown; asOf: string | null } | null>(null);
  const scroller = useRef<HTMLDivElement | null>(null);
  const [restored, setRestored] = useState(false);

  // Restore after mount, so the server and the first client render agree.
  useEffect(() => {
    const v = load();
    if (v) {
      frozen.current = v.frozen;
      setMsgs(v.msgs);
      setRestored(true);
    }
  }, []);

  /** Read whatever the page has already loaded. `getQueryData` never fetches —
   *  this is a pure observer, so opening the chat costs no requests and cannot
   *  make a card refresh out of turn. */
  const buildSnapshot = useCallback(() => {
    const brief = qc.getQueryData<EsBrief>(["es-brief"]);
    if (!brief?.available) return null;
    return {
      as_of: brief.asof,
      // Whole blocks, deliberately unpruned — see the note at the top.
      es_brief: brief,
      market_driver: qc.getQueryData<MarketDriverResponse>(["market-driver"]) ?? null,
      vol_landscape: qc.getQueryData<VolLandscapeScan>(["vol-landscape-home"]) ?? null,
      sectors: qc.getQueryData<{ group: string; items: HeatmapItem[] }>(["heatmap", "sectors"]) ?? null,
      calendar: qc.getQueryData<{ events: CalendarEvent[] }>(["events-home"]) ?? null,
      macro_pressure: qc.getQueryData<MacroPressureBoard>(["macro-pressure"]) ?? null,
      sp_valuation: qc.getQueryData<SpValuation>(["sp-valuation"]) ?? null,
      cta_flows: qc.getQueryData<CtaFlowBoard>(["cta-flows", "13874A"]) ?? null,
      // 8 weeks, matching the card and the server prefetch. Reading ["sector-rrg", 4]
      // here would observe a key nothing else writes, so it would sit empty forever.
      sector_rrg: qc.getQueryData<SectorRrg>(["sector-rrg", 8]) ?? null,
    };
  }, [qc]);

  /** Has the page moved on since the conversation started? Compared on the
   *  payload's own `asof`, not on when we fetched — a server-cached endpoint
   *  can hand back data far older than the request that got it. */
  const liveAsOf = qc.getQueryData<EsBrief>(["es-brief"])?.asof ?? null;
  const drifted = useMemo(
    () => !!frozen.current && !!liveAsOf && frozen.current.asOf !== liveAsOf,
    [liveAsOf, msgs.length],
  );

  const reset = () => {
    frozen.current = null;
    setMsgs([]);
    setErr(null);
    setRestored(false);
    save(null);
  };

  async function send(text: string) {
    const question = text.trim();
    if (!question || busy) return;

    if (!frozen.current) {
      const data = buildSnapshot();
      if (!data) {
        setErr("The ES briefing has not loaded yet — there is nothing to ask about.");
        return;
      }
      frozen.current = { data, asOf: (data as { as_of?: string }).as_of ?? null };
    }

    const history = msgs.map(({ role, content }) => ({ role, content }));
    setMsgs((m) => [...m, { role: "user", content: question }]);
    setQ("");
    setBusy(true);
    setErr(null);
    try {
      const r = await askHomeChat({ data: frozen.current.data, question, history });
      setMsgs((m) => {
        const next = [...m, { role: "assistant" as const, content: r.answer,
                              grounding: r.grounding, truncated: r.answer_truncated }];
        // Persist only completed exchanges. Saving the user turn before the
        // answer lands would restore a dangling question after a crash.
        if (frozen.current) save({ msgs: next, frozen: frozen.current, savedAt: Date.now() });
        return next;
      });
      requestAnimationFrame(() => {
        scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
      });
    } catch (e) {
      // Keep the question on screen. Dropping the user's own text on a failure
      // means retyping it, and the most common failure here is a timeout.
      setErr(e instanceof Error ? e.message : "Request failed");
      setQ(question);
      setMsgs((m) => m.slice(0, -1));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card space-y-3">
      <CardHeader
        title="Ask about this page"
        size="md"
        asOf={frozen.current?.asOf ?? liveAsOf}
        right={
          msgs.length > 0 ? (
            <button
              onClick={reset}
              className="text-[0.65rem] px-2 py-1 rounded border border-border hover:bg-surface-alt"
            >
              New chat
            </button>
          ) : null
        }
      />

      <p className="text-[0.6rem] text-text-muted leading-snug">
        Answers come only from the data on this page. It cannot see anything else, and it
        reports conditions rather than telling you what to trade.
        {frozen.current && " The snapshot is fixed for this conversation."}
        {/* A restored conversation must SAY it was restored. Presenting it as
            fresh would let the reader assume the answers above describe the
            page in front of them, when they describe the page as it stood when
            the conversation started. */}
        {restored && msgs.length > 0 && " Resumed from an earlier session."}
      </p>

      {drifted && (
        <div className="text-[0.6rem] text-amber-400 border border-amber-400/30 rounded px-2 py-1.5 leading-snug">
          The page has refreshed since this conversation started. Answers still reference the
          snapshot it began with — start a new chat to use the current one.
        </div>
      )}

      {msgs.length > 0 && (
        <div ref={scroller} className="max-h-[26rem] overflow-y-auto space-y-2.5 pr-1">
          {msgs.map((m, i) => (
            <div key={i} className={m.role === "user" ? "flex justify-end" : ""}>
              <div
                className={
                  m.role === "user"
                    ? "max-w-[85%] rounded px-2.5 py-1.5 text-[0.7rem] bg-surface-alt border border-border"
                    : "text-[0.72rem] leading-relaxed whitespace-pre-wrap"
                }
              >
                {m.content}
                {/* The same grounding check the interpretation panel runs. A
                    chat can invent a number as easily as a panel can, and a
                    silent unverified figure is the failure this surfaces. */}
                {/* An answer that ran out of budget is real but unfinished.
                    Serving it as complete is the same failure as reporting an
                    absence as a calm. */}
                {m.role === "assistant" && m.truncated && (
                  <div className="mt-1 text-[0.55rem] text-amber-400">
                    This answer hit its length limit and stops mid-thought — ask a narrower
                    question, or ask it to continue.
                  </div>
                )}
                {/* Wording matters here. The chat is allowed to draw on general
                    knowledge as long as it labels it, so a figure that is not in
                    the snapshot is often correct and expected — it just did not
                    come from this page. Calling that an error would train the
                    reader to ignore the one case that matters: a number
                    presented AS a page reading that the page does not contain. */}
                {m.role === "assistant" && (m.grounding?.unverified_count ?? 0) > 0 && (
                  <div className="mt-1 text-[0.55rem] text-text-muted">
                    Not from this page&apos;s data:{" "}
                    <span className="text-amber-400">
                      {m.grounding!.unverified_tokens.slice(0, 6).join(", ")}
                    </span>
                    {" — fine if it was general context, worth checking if it read as a page figure."}
                  </div>
                )}
              </div>
            </div>
          ))}
          {busy && <div className="text-[0.65rem] text-text-muted">Reading the page…</div>}
        </div>
      )}

      {msgs.length === 0 && !busy && (
        <div className="flex flex-wrap gap-1.5">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              onClick={() => send(s)}
              className="text-[0.6rem] text-left px-2 py-1 rounded border border-border text-text-muted hover:bg-surface-alt hover:text-text"
            >
              {s}
            </button>
          ))}
        </div>
      )}

      {err && <div className="text-[0.65rem] text-loss">{err}</div>}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          send(q);
        }}
        className="flex gap-2"
      >
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          disabled={busy}
          placeholder="Ask about anything on this page…"
          aria-label="Ask about this page"
          className="flex-1 min-w-0 bg-surface-alt border border-border rounded px-2.5 py-1.5 text-[0.7rem] focus:outline-none focus:ring-1 focus:ring-accent disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={busy || !q.trim()}
          className="text-[0.65rem] px-3 py-1.5 rounded border border-border hover:bg-surface-alt disabled:opacity-40"
        >
          {busy ? "…" : "Ask"}
        </button>
      </form>
    </div>
  );
}
