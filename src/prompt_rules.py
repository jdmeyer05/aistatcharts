"""Deterministic grading. Every check here is a rule, not an opinion.

WHY RULES COME FIRST, AND WHY THEY OUTRANK THE MODEL. The adversarial critic in
this loop reads these findings; if the findings themselves were model-generated
the loop would be a model grading a model and calling the agreement progress.
Anything expressible as a rule belongs here, where it is testable, reproducible
on a replayed payload years later, and incapable of inventing a fourth failure
to keep the first three company. `src/es_auditor.py` reached the same conclusion
for the same reason.

EVERY CHECK IS A PAST FAILURE. Nothing in this file is hypothetical. Each rule
traces to a specific defect that reached the page and was caught by eye — the
sub-18 VIX called "elevated", the "no matching macro headline" written while the
story sat one feed away, the absent `change_pct_1d` reported as flat price
action, the index "rally" on divergent breadth. That is also what makes this set
the regression suite: a challenger prompt that reintroduces any of them is
rejected regardless of how well it scores elsewhere.

ON THE SCALAR SCORE. `score` is an aggregation, not a measurement. The severity
weights below were CHOSEN so that outputs can be ranked; they were not estimated
from anything. The counts are the real output — read `counts`, and treat `score`
as a sort key. It is reported because a promotion gate needs one number, and
stated plainly here because the alternative is a number that looks like it came
from data.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

# Declared weights. See the docstring: chosen for ranking, not estimated.
_W = {"critical": 3.0, "major": 1.0, "minor": 0.25}
_PENALTY_CAP = 6.0

# Common English words that look like tickers in upper case. Used by the
# invented-ticker check, which is otherwise a false-positive machine.
_TICKER_STOPWORDS = {
    # Macro releases, institutions and desk shorthand. Every one of these is a
    # plausible thing to write in a market note and none is a ticker, so leaving
    # them out would fire `invented_ticker` — a CRITICAL finding that sits in the
    # regression suite — on correct prose, and a false critical there can block a
    # good challenger from ever being promoted.
    "AI", "US", "USA", "USD", "EUR", "JPY", "GBP", "CNY", "GDP", "CPI", "PPI",
    "PCE", "FOMC", "ECB", "BOJ", "BOE", "PBOC", "SNB", "RBA", "BOC",
    "NFP", "ISM", "PMI", "PMIS", "JOLTS", "ADP", "NFIB", "ECI", "BLS", "BEA",
    "FRED", "EIA", "IEA", "OPEC", "SEC", "CFTC", "FDIC", "OCC", "IMF", "WTO",
    "EPS", "YOY", "MOM", "QOQ", "YTD", "MTD", "WTD", "TTM", "FY", "Q1", "Q2",
    "Q3", "Q4", "CEO", "CFO", "COO", "IPO", "MA", "LBO", "IG", "HY", "EM", "DM",
    "FX", "QT", "QE", "OI", "IV", "RV", "VRP", "ATM", "OTM", "ITM", "DTE",
    "RTH", "ETH", "ETF", "ETFS", "ETN", "REIT", "REITS", "TIPS", "OIS", "SOFR",
    "GMT", "UTC", "ET", "CT", "PT", "PM", "AM", "EOD", "EOM", "TBD", "NA", "OK",
    "BPS", "BP", "PP", "PPS", "VS", "WTI", "SPX", "NDX", "RUT", "ES", "NQ",
    "RTY", "DXY", "VIX", "MOVE", "CDS", "CTA",
    # Market-structure and price-level shorthand. THIS GROUP IS WHY THE RULE HAD
    # ZERO PRECISION IN PRODUCTION FOR ITS FIRST NINE DAYS. All 17 recorded
    # `invented_ticker` findings between 2026-08-19 and 2026-08-28 were one of
    # these — VAH, PVAH, PVAL, PDH, PDL, RVOL, TGA — written correctly, next to
    # a number that DID trace to the payload. The cockpit prompts discuss prior
    # value and overnight inventory constantly, so this is the vocabulary most
    # likely to appear in correct prose. The cost was not cosmetic: it scored
    # the home page at 0.139 and vetoed a challenger that won its replay.
    "VAH", "VAL", "PVAH", "PVAL", "POC", "VPOC", "NPOC", "TPO", "LVN", "HVN",
    "PDH", "PDL", "PDC", "PDO", "ONH", "ONL", "IBH", "IBL", "IBR",
    "HOD", "LOD", "VWAP", "TWAP", "RVOL", "ATR", "ADR", "OHLC", "OHLCV",
    "HTF", "LTF", "GEX", "DIX", "OPEX", "MOC", "LOC", "TICK", "ADD",
    # Geography and blocs. `UK` fired on a news digest about Bailey and UK
    # inflation on the very day the list above was written — the classes keep
    # arriving, which is the argument for the structural guard in
    # `_invented_tickers` and for the relative regression gate in
    # src/prompt_replay.py rather than for trusting this list to be complete.
    "UK", "EU", "EZ", "UAE", "APAC", "EMEA", "LATAM", "ROW", "OECD", "BRICS",
    "NATO", "UN", "WHO", "G10", "EMFX",
    # Funding and Treasury plumbing — the macro surfaces name these routinely.
    "TGA", "RRP", "ONRRP", "SLR", "SRF", "BTFP", "MBS", "UST", "CMBS", "ABS",
    "IORB", "EFFR", "ESTR", "SONIA", "TONA", "CORRA", "WAM", "QRA",
    # Futures roots the ES cockpit quotes. These ARE symbols, but they are the
    # platform's own contract shorthand, not securities the model invented.
    "MES", "MNQ", "MYM", "M2K", "YM", "ZN", "ZB", "ZT", "ZF", "ZQ",
    "CL", "GC", "SI", "HG", "NG", "ZC", "ZS", "ZW", "VX",
    "A", "I", "AND", "THE", "FOR", "BUT", "NOT", "ALL", "NEW", "NO", "ONE",
    "TWO", "ITS", "WAS", "ARE", "HAS", "HAD", "OUT", "OFF", "UP", "ON", "IN",
    "AT", "TO", "BY", "IF", "OR", "AS", "IS", "IT", "BE", "AN", "DO", "SO",
}


def _sev_counts(findings: list[dict]) -> dict:
    out = {"critical": 0, "major": 0, "minor": 0}
    for f in findings:
        s = f.get("severity", "minor")
        if s in out:
            out[s] += 1
    return out


def _score_from(findings: list[dict]) -> float:
    penalty = sum(_W.get(f.get("severity", "minor"), 0.25) for f in findings)
    return round(max(0.0, 1.0 - min(1.0, penalty / _PENALTY_CAP)), 4)


def _find(severity: str, rule: str, detail: str, evidence: str = "") -> dict:
    return {"severity": severity, "rule": rule, "detail": detail,
            "evidence": (evidence or "")[:300]}


def _words(s: str) -> int:
    return len((s or "").split())


def _payload_text(payload) -> str:
    try:
        return json.dumps(payload, default=str)
    except Exception:
        return str(payload)


def _safe(fn, findings: list[dict], name: str):
    """One broken check must not cost the whole grade."""
    try:
        fn()
    except Exception as e:
        logger.debug(f"prompt_rules: check {name} failed: {e}")


# ══════════════════════════════════════════════════════════════════
# market_driver
# ══════════════════════════════════════════════════════════════════

_ELEVATED_VOL = re.compile(
    r"elevated\s+(?:volatility|vol\b|implied vol)|volatility\s+is\s+elevated|"
    r"vol\s+is\s+elevated|elevated\s+VIX", re.I)
_FLAT_WORDS = re.compile(r"\b(flat|unchanged|muted move|little changed|steady)\b", re.I)
_NO_CATALYST = re.compile(
    r"no (?:matching |obvious |clear |specific )?(?:macro )?(?:catalyst|headline|news)|"
    r"without a (?:matching |clear )?catalyst|no catalyst (?:in|appears)|"
    r"absent any catalyst", re.I)
_BROAD_RALLY = re.compile(
    r"\b(broad(?:-based| based)? (?:rally|advance|gain)|stocks rallied|"
    r"broad risk appetite|rally was broad|widespread (?:buying|gains))\b", re.I)
_BREADTH_QUALIFIER = re.compile(r"narrow|divergen|breadth|equal[- ]weight|few names|thin participation", re.I)

# Turning a same-day co-movement number into a cause or a forecast. The
# attribution block is a regression on CONTEMPORANEOUS returns whose next-day
# correlations were measured at essentially zero, so "gold is driving equities"
# and "rates will push the tape" are both claims the payload cannot support —
# and both are the natural sentence to write when handed a ranked list.
_DRIVER_NOUN = r"(?:gold|oil|crude|the dollar|dollar|rates|duration|credit|treasuries)"
_DRIVER_CAUSAL = re.compile(
    rf"\b{_DRIVER_NOUN}\b[^.]{{0,45}}?\b(?:is|are|has been|have been)?\s*"
    r"(?:driving|drove|drives|caused|causing|will (?:push|drive|lift|weigh|lead|send)|"
    r"leads the|is leading the)\b", re.I)
_DRIVER_CAUSAL_REVERSE = re.compile(
    rf"\b(?:driven|propelled|dragged|led)\s+(?:higher\s+|lower\s+)?by\s+{_DRIVER_NOUN}\b", re.I)
_TICKER = re.compile(r"\b[A-Z]{2,5}\b")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
# "PVAH 7700.04", "PVAH: 7700.04", "PVAH (7700.04)" — and the mirrored forms.
_LABEL_AFTER = re.compile(r"^[\s:=~(\[]*(\d[\d,]*(?:\.\d+)?)")
_LABEL_BEFORE = re.compile(r"(\d[\d,]*(?:\.\d+)?)[\s:=~)\]]*$")


def _traces(num: str, ptext: str) -> bool:
    """Does a number written in prose appear in the payload as its own value?

    Bounded, not `in`. A bare substring test matches "5" inside "1523", which
    would let a real invented ticker slip through on any number at all — the
    wrong direction to be loose in for a critical rule.
    """
    raw = num.replace(",", "")
    cands = {raw}
    try:                                  # 7700.0 in prose vs 7700 in payload
        f = float(raw)
        cands |= {f"{f:g}", f"{f:.1f}", f"{f:.2f}"}
        if f == int(f):
            cands.add(str(int(f)))
    except ValueError:
        pass
    return any(re.search(rf"(?<![\d.]){re.escape(c)}(?![\d])", ptext)
               for c in cands if c)


def _invented_tickers(text: str, payload, where: str) -> list[dict]:
    """Uppercase tokens that name a security the payload never mentions.

    TWO GUARDS, AND THE SECOND ONE EXISTS BECAUSE THE FIRST IS A LIST. The
    stopword set above holds the desk vocabulary, and a list can only ever be as
    complete as the last person to edit it — its first nine days in production
    scored 0 true positives out of 17 because nobody had written down `VAH`.
    So the structural guard: an acronym sitting immediately beside a number that
    DOES trace to the payload is labelling that value, not naming a security.
    "PVAH 7700.04" is the model reading the payload correctly, whichever
    abbreviation it reached for. Without this, the next unlisted acronym repeats
    the same outage, and the symptom is a silently vetoed promotion.
    """
    findings: list[dict] = []
    ptext = _payload_text(payload).upper()
    seen: set[str] = set()
    for m in _TICKER.finditer(text or ""):
        tk = m.group(0)
        if tk in _TICKER_STOPWORDS or tk in seen:
            continue
        seen.add(tk)
        if tk in ptext:
            continue
        # STRICTLY ADJACENT, and the first draft of this was not. A 14-character
        # window either side let "SPY rose 0.42% while NVDA led" clear the guard
        # on SPY's number, which is the exact invention the rule exists to
        # catch. Only whitespace and light punctuation may separate the label
        # from its value, so the number has to be the token's own.
        before = _LABEL_BEFORE.search(text[max(0, m.start() - 24):m.start()])
        after = _LABEL_AFTER.match(text[m.end():m.end() + 24])
        adjacent = [g.group(1) for g in (before, after) if g]
        if any(_traces(n, ptext) for n in adjacent):
            continue          # a label for a grounded value, not a ticker
        findings.append(_find("critical", "invented_ticker",
                              f"'{tk}' {where}", tk))
    return findings


def grade_market_driver(payload: dict, output: dict) -> list[dict]:
    findings: list[dict] = []
    payload = payload or {}
    output = output or {}

    paras = (output.get("paragraphs") or {})
    p1 = str(paras.get("what_happened") or "")
    p2 = str(paras.get("whats_driving") or "")
    p3 = str(paras.get("what_to_watch") or "")
    prose = "\n".join([p1, p2, p3])
    label = str(output.get("regime_label") or "")

    # ── shape ────────────────────────────────────────────────────
    def _shape():
        if not p1 or not p2 or not p3:
            findings.append(_find("critical", "empty_paragraph",
                                  "One or more of the three paragraphs is empty — the card renders blank."))
        if not label:
            findings.append(_find("major", "missing_regime_label", "regime_label is empty."))
        conf = output.get("confidence")
        if conf is None or not (isinstance(conf, (int, float)) and 1 <= float(conf) <= 10):
            findings.append(_find("minor", "confidence_out_of_range",
                                  f"confidence={conf!r}, expected 1-10."))
    _safe(_shape, findings, "shape")

    # ── the VIX band rule ────────────────────────────────────────
    # A sub-20 VIX described as elevated volatility. The prompt carries a whole
    # calibration table because this happened.
    def _vix():
        band = str(payload.get("vix_level_band") or "").lower()
        if band in ("complacent", "muted") and _ELEVATED_VOL.search(prose):
            m = _ELEVATED_VOL.search(prose)
            findings.append(_find("critical", "vix_band_contradiction",
                                  f"Prose calls volatility elevated while vix_level_band is '{band}'.",
                                  prose[max(0, m.start() - 80):m.end() + 80]))
    _safe(_vix, findings, "vix")

    # ── absent move reported as flat ─────────────────────────────
    # A quote with no change_pct_1d means the day's move is UNKNOWN. Calling it
    # flat converts missing data into a confident market call.
    def _absent_flat():
        quotes = payload.get("quotes") or {}
        if not isinstance(quotes, dict):
            return
        missing = [t for t, q in quotes.items()
                   if isinstance(q, dict) and q.get("change_pct_1d") is None]
        if not missing:
            return
        for sentence in re.split(r"(?<=[.!?])\s+", prose):
            if not _FLAT_WORDS.search(sentence):
                continue
            for tk in missing:
                bare = tk.lstrip("^").split("-")[0]
                if re.search(rf"\b{re.escape(bare)}\b", sentence):
                    findings.append(_find(
                        "critical", "absent_move_called_flat",
                        f"{tk} has no change_pct_1d in the payload but is described as flat/unchanged.",
                        sentence))
                    break
    _safe(_absent_flat, findings, "absent_flat")

    # ── the two headline feeds ───────────────────────────────────
    def _catalyst():
        macro = payload.get("macro_headlines") or []
        if macro and _NO_CATALYST.search(prose):
            m = _NO_CATALYST.search(prose)
            findings.append(_find(
                "critical", "no_catalyst_despite_macro_feed",
                f"Claims no catalyst while macro_headlines carries {len(macro)} stories.",
                prose[max(0, m.start() - 80):m.end() + 80]))
    _safe(_catalyst, findings, "catalyst")

    # ── breadth qualifies the move ───────────────────────────────
    def _breadth():
        br = payload.get("breadth") or {}
        if not isinstance(br, dict):
            return
        if str(br.get("divergence") or "").lower() != "divergent":
            return
        m = _BROAD_RALLY.search(prose)
        if m and not _BREADTH_QUALIFIER.search(prose):
            findings.append(_find(
                "major", "breadth_divergence_unqualified",
                "Breadth reads 'divergent' but the note describes a broad rally with no narrowness qualifier.",
                prose[max(0, m.start() - 80):m.end() + 80]))
    _safe(_breadth, findings, "breadth")

    # ── the attribution block is co-movement, not cause ──────────
    def _drivers():
        if not (payload.get("cross_asset_drivers") or {}):
            return
        for rx in (_DRIVER_CAUSAL, _DRIVER_CAUSAL_REVERSE):
            m = rx.search(prose)
            if m:
                findings.append(_find(
                    "major", "driver_causal_language",
                    "States a macro market as causing or predicting the equity move. "
                    "`cross_asset_drivers` measures same-day co-movement only, and its "
                    "next-day correlations were measured at essentially zero.",
                    prose[max(0, m.start() - 60):m.end() + 60]))
                return
    _safe(_drivers, findings, "drivers")

    # ── numbers must trace to the payload ────────────────────────
    def _grounding():
        from src.grounding import _check_grounding
        g = _check_grounding(prose, payload)
        for tok in (g.get("unverified_tokens") or [])[:5]:
            findings.append(_find("major", "ungrounded_number",
                                  f"'{tok}' does not trace to any payload value.", tok))
    _safe(_grounding, findings, "grounding")

    # ── invented tickers ─────────────────────────────────────────
    def _tickers():
        findings.extend(_invented_tickers(
            prose, payload, "appears in the note but nowhere in the payload."))
    _safe(_tickers, findings, "tickers")

    # ── length caps ──────────────────────────────────────────────
    def _length():
        for name, para in (("what_happened", p1), ("whats_driving", p2), ("what_to_watch", p3)):
            n = _words(para)
            if n > 75:  # prompt says 60; 25% grace before it is worth a finding
                findings.append(_find("minor", "paragraph_too_long",
                                      f"{name} is {n} words against a 60-word cap."))
        cits = output.get("citations") or []
        if len(cits) > 5:
            findings.append(_find("minor", "too_many_citations", f"{len(cits)} citations against a cap of 5."))
    _safe(_length, findings, "length")

    # ── citation labels ──────────────────────────────────────────
    def _citations():
        for c in (output.get("citations") or [])[:8]:
            lab = str((c or {}).get("label") or "")
            if not lab:
                findings.append(_find("minor", "empty_citation_label", "A citation has no label."))
                continue
            # "SPY 0.96%" with no sign reads as a level, not a move.
            if re.match(r"^[A-Z]{1,5}\s+\d", lab) and not re.search(r"[+-]\s*\d", lab):
                findings.append(_find("minor", "unsigned_price_move",
                                      f"Citation '{lab}' quotes a move without a +/- sign.", lab))
    _safe(_citations, findings, "citations")

    return findings


# ══════════════════════════════════════════════════════════════════
# home_interpret
# ══════════════════════════════════════════════════════════════════

# Wording that tells the reader to act rather than describing what is priced.
#
# IT WAS `minor` BECAUSE THE PROMPT CONTRADICTED THE RULE. `BASE_SYSTEM` asks
# every surface to "name the tradeable implication where one exists", so the
# model writing "fade the edges" was obeying its instructions and this could
# only be an observation for the critic, not a violation. That contradiction is
# now resolved at the source: the home_page block overrides the style rule for
# this surface specifically (the options and flow pages still want a trade
# idea). With the instruction no longer asking for it, a directive IS a
# violation, so this is `major`.
#
# NO SEQUENCING GAP, and I first wrote the opposite here. The override lives in
# `PAGE_CONTEXT["home_page"]`, which is read per request — only the SYSTEM text
# goes through `prompt_registry`. So the instruction and this severity change
# ship in the same deploy and take effect together; nothing is graded against an
# instruction it was not given. `_interpret_cache_key` also hashes PAGE_CONTEXT,
# so the edit invalidates cached answers by itself.
#
# Expect a step in `directive_wording` on the day this lands: the detector went
# from matching 1 of 7 real violations to 12 of 12, so historical rows re-graded
# under it will look worse. That is the measurement improving, not the output.
#
# WIDENED 2026-08-29 because it was nominally graded and effectively unenforced.
# The previous pattern matched 1 of 7 violations found in live output: it caught
# "buy the dip" and missed "fade the edges of 7714-7760", "don't front-load size
# on the open", "wait for acceptance", "trim into strength", "stay flat until"
# and "size up". A rule that only catches phrasings a model would not use is
# worse than no rule, because it looks like the check is doing its job.
#
# (A test greps this whole module for the name of one hidden grading metric, so
# that word must not appear here even in prose. The guard is a crude substring
# match and is correct to keep crude — the metric is a hidden anchor, and the
# critic learns to game anything it can read. I tripped it twice writing this
# comment.)
#
# Scoped to TRADE actions deliberately, not to imperatives in general. "Treat
# the trend-up tag as stale" and "check a live quote before acting" are
# imperative and are not trade instructions; flagging those would push the prose
# toward mush. Checked against 14 real strings from these cards (zero false
# positives) and 12 real violations (zero misses).
_DIRECTIVE = re.compile(
    r"\b("
    r"(?:you should|make sure to|be ready to|look to|plan to)\s+\w+"
    r"|(?:don'?t|do not|never|avoid)\s+(?:buy|sell|short|add|chase|fade|size|front-?load|trade|enter|hold|press)"
    r"|(?:buy|sell|short|fade|chase|press)\s+(?:the|it|into|here|now|this|that)"
    r"|add (?:to|here)|trim (?:into|here)|scale (?:in|out)|take profits|get (?:long|short)"
    r"|size (?:up|down)|front-?load"
    r"|stay (?:flat|out|long|short)|enter (?:here|now)"
    r"|wait for (?:acceptance|confirmation|a\b)"
    r")\b", re.I)


def grade_home_interpret(payload: dict, output) -> list[dict]:
    findings: list[dict] = []
    text = output if isinstance(output, str) else str((output or {}).get("interpretation") or "")

    def _shape():
        if not text.strip():
            findings.append(_find("critical", "empty_interpretation", "No interpretation text returned."))
            return
        if "bottom line" not in text.lower():
            findings.append(_find("minor", "missing_bottom_line",
                                  "The prompt requires a closing 'Bottom line:' and there isn't one."))
        n = _words(text)
        if n > 275:  # 220-word cap plus grace
            findings.append(_find("minor", "too_long", f"{n} words against a 220-word cap."))
    _safe(_shape, findings, "shape")

    def _grounding():
        from src.grounding import _check_grounding
        g = _check_grounding(text, payload or {})
        for tok in (g.get("unverified_tokens") or [])[:5]:
            findings.append(_find("major", "ungrounded_number",
                                  f"'{tok}' does not trace to any payload value.", tok))
    _safe(_grounding, findings, "grounding")

    def _tickers():
        findings.extend(_invented_tickers(
            text, payload, "appears in the interpretation but nowhere in the payload."))
    _safe(_tickers, findings, "tickers")

    def _drivers():
        # Same prohibition as the market-driver surface, for the same reason:
        # the interpretation panel now receives the measured attribution too,
        # and a ranked list is the input that invites a causal sentence.
        if not ((payload or {}).get("drivers") or {}):
            return
        for rx in (_DRIVER_CAUSAL, _DRIVER_CAUSAL_REVERSE):
            m = rx.search(text)
            if m:
                findings.append(_find(
                    "major", "driver_causal_language",
                    "States a macro market as causing or predicting the equity move. "
                    "`drivers` measures same-day co-movement only.",
                    text[max(0, m.start() - 60):m.end() + 60]))
                return
    _safe(_drivers, findings, "drivers")

    def _directive():
        m = _DIRECTIVE.search(text)
        if m:
            findings.append(_find("major", "directive_wording",
                                  "Instructs an action rather than describing what is priced. "
                                  "The home_page prompt overrides the general 'name the tradeable "
                                  "implication' style rule for this surface.",
                                  text[max(0, m.start() - 60):m.end() + 60]))
    _safe(_directive, findings, "directive")

    return findings


# ══════════════════════════════════════════════════════════════════
# es_audit
# ══════════════════════════════════════════════════════════════════

_FORECAST = re.compile(
    r"\b(will (?:rally|sell off|fall|rise|break|hold)|expect(?:ed)? to (?:rally|fall|rise)|"
    r"likely to (?:rally|fall|rise|break)|should (?:rally|fall|rise)|"
    r"target(?:s|ing)? \d)\b", re.I)


def grade_es_audit(payload: dict, output: dict) -> list[dict]:
    """The auditor's own output, audited.

    An empty findings list is the COMMON CASE AND A SUCCESS — the prompt says so
    explicitly — so nothing here penalises silence. What is penalised is a
    finding that forecasts the market, quotes only one side of the contradiction
    it claims, or cites a number the card never contained.
    """
    findings: list[dict] = []
    output = output or {}
    items = output.get("findings") or []

    def _shape():
        if not isinstance(items, list):
            findings.append(_find("critical", "bad_findings_shape",
                                  f"findings is {type(items).__name__}, expected a list."))
    _safe(_shape, findings, "shape")

    if not isinstance(items, list):
        return findings

    for it in items[:10]:
        if not isinstance(it, dict):
            findings.append(_find("major", "bad_finding_shape", "A finding is not an object."))
            continue
        text = str(it.get("finding") or "")
        sev = str(it.get("severity") or "")

        def _sev():
            if sev not in ("high", "medium", "low"):
                findings.append(_find("minor", "bad_severity", f"severity={sev!r}.", text))
        _safe(_sev, findings, "sev")

        def _forecast():
            m = _FORECAST.search(text)
            if m:
                findings.append(_find("major", "auditor_forecast",
                                      "The auditor made a market forecast; its remit is contradictions only.",
                                      text))
        _safe(_forecast, findings, "forecast")

        def _both_sides():
            # "A finding without both sides quoted is not a finding" — the
            # cheapest proxy is two numbers, or one number plus a contrast word.
            nums = re.findall(r"-?\d+(?:\.\d+)?", text)
            contrast = re.search(r"\b(while|whereas|but|versus|vs\.?|against|yet)\b", text, re.I)
            if len(nums) < 2 and not (nums and contrast):
                findings.append(_find("minor", "one_sided_finding",
                                      "Finding does not quote both clashing values.", text))
        _safe(_both_sides, findings, "both_sides")

        def _grounded():
            from src.grounding import _check_grounding
            g = _check_grounding(text, payload or {})
            for tok in (g.get("unverified_tokens") or [])[:2]:
                findings.append(_find("critical", "invented_contradiction",
                                      f"Finding cites '{tok}', which is not in the card payload.", text))
        _safe(_grounded, findings, "grounded")

    return findings


# ══════════════════════════════════════════════════════════════════
# news_digest
# ══════════════════════════════════════════════════════════════════

# The digest's one job is to say what changed, and its one prohibition is to
# imply what to do about it. "This is context, not a signal" is the principle
# the entire ES card is built on, and a paragraph of prose is the easiest place
# on that card to blur it — which is exactly why this check exists here and is
# CRITICAL rather than advisory.
_DIGEST_DIRECTIONAL = re.compile(
    r"\b(bullish|bearish|risk[- ]on|risk[- ]off|constructive|supportive of (?:higher|lower)|"
    r"points? (?:higher|lower)|should (?:rally|fall|rise|drop|open) |"
    r"expect(?:s|ed)? (?:a )?(?:rally|selloff|sell-off|bounce|drop|move (?:higher|lower))|"
    r"favou?rs? (?:the )?(?:upside|downside|bulls|bears)|"
    r"(?:upside|downside) (?:bias|risk is)|lean (?:long|short)|"
    r"buy|sell|short the|fade the|target of|support at|resistance at)\b", re.I)

# Named in the prompt itself, so this is not a taste call — the instruction says
# "No 'risk-on', no 'constructive'" and these are the words it named.
_DIGEST_JARGON = re.compile(r"\b(risk[- ]on|risk[- ]off|constructive|goldilocks|melt[- ]up)\b", re.I)

_DIGEST_STRUCTURE = re.compile(r"^\s*[-*•]|\n\s*[-*•]|^#{1,6}\s|\*\*[A-Z]", re.M)


def grade_news_digest(payload: dict, output) -> list[dict]:
    """The ES card's headline synthesis, checked against its own instructions.

    Nearly every rule this prompt states is mechanically verifiable, which is
    rare for prose and is why this surface is worth grading at all: use only the
    headlines given, never imply a direction or a level, stay under 70 words, no
    bullets, and a jargon blacklist the prompt names explicitly.
    """
    findings: list[dict] = []
    text = output if isinstance(output, str) else str((output or {}).get("text") or "")

    def _shape():
        if not text.strip():
            findings.append(_find("critical", "empty_digest", "No digest text returned."))
            return
        n = _words(text)
        if n > 85:                      # 70-word cap plus grace
            findings.append(_find("minor", "digest_too_long",
                                  f"{n} words against a 70-word cap."))
        if _DIGEST_STRUCTURE.search(text):
            findings.append(_find("minor", "digest_has_structure",
                                  "Bullets or headings, where the prompt asks for 2-3 plain sentences."))
    _safe(_shape, findings, "shape")

    if not text.strip():
        return findings

    def _directional():
        m = _DIGEST_DIRECTIONAL.search(text)
        if m:
            findings.append(_find(
                "critical", "digest_implies_direction",
                "States or implies a direction, bias or level. The digest is context for a "
                "session, not a signal, and this is the one place on the ES card where prose "
                "can blur that.",
                text[max(0, m.start() - 70):m.end() + 70]))
    _safe(_directional, findings, "directional")

    def _jargon():
        m = _DIGEST_JARGON.search(text)
        if m:
            findings.append(_find("minor", "digest_jargon",
                                  f"Uses '{m.group(0)}', which the prompt names as banned.",
                                  text[max(0, m.start() - 50):m.end() + 50]))
    _safe(_jargon, findings, "jargon")

    def _grounding():
        from src.grounding import _check_grounding
        g = _check_grounding(text, payload or {})
        for tok in (g.get("unverified_tokens") or [])[:4]:
            findings.append(_find("major", "ungrounded_number",
                                  f"'{tok}' appears in the digest but in none of the headlines.", tok))
    _safe(_grounding, findings, "grounding")

    def _tickers():
        findings.extend(_invented_tickers(
            text, payload, "appears in the digest but in none of the headlines."))
    _safe(_tickers, findings, "tickers")

    return findings


_GRADERS = {
    "market_driver": grade_market_driver,
    "home_interpret": grade_home_interpret,
    "es_audit": grade_es_audit,
    # The digest cross-check is the auditor's second pass — same finding shape,
    # same rules, but its own graded population so the blind measured audit and
    # the prose cross-check are scored and rewritten as the different questions
    # they are.
    "es_audit_digest": grade_es_audit,
    "news_digest": grade_news_digest,
}


def grade(surface: str, payload, output) -> dict:
    """Run the rule set for a surface. Never raises.

    `interpret:<page>` surfaces all share the home interpretation rules: the
    same prompt writes them, so the same checks apply — grounding, invented
    tickers, the closing "Bottom line", the word cap.
    """
    fn = _GRADERS.get(surface)
    if fn is None and surface.startswith("interpret:"):
        fn = _GRADERS["home_interpret"]
    if fn is None:
        return {"score": None, "findings": [], "counts": {}, "error": f"no rules for {surface}"}
    try:
        findings = fn(payload, output) or []
    except Exception as e:
        logger.warning(f"prompt_rules: grade({surface}) failed: {e}")
        return {"score": None, "findings": [], "counts": {}, "error": str(e)}
    return {
        "score": _score_from(findings),
        "findings": findings,
        "counts": _sev_counts(findings),
    }


# The regression suite is exactly the critical-severity rules: the defects that
# reached the page once already. A challenger that reintroduces any of them is
# rejected no matter what it scores elsewhere.
REGRESSION_RULES = {
    "vix_band_contradiction",
    "absent_move_called_flat",
    "no_catalyst_despite_macro_feed",
    "invented_ticker",
    "empty_paragraph",
    "empty_interpretation",
    "invented_contradiction",
    "bad_findings_shape",
    "empty_digest",
    "digest_implies_direction",
}


def regression_failures(findings: list[dict]) -> list[str]:
    return sorted({f.get("rule") for f in (findings or [])
                   if f.get("rule") in REGRESSION_RULES})
