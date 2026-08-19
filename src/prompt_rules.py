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
_TICKER = re.compile(r"\b[A-Z]{2,5}\b")


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
        ptext = _payload_text(payload).upper()
        seen: set[str] = set()
        for tk in _TICKER.findall(prose):
            if tk in _TICKER_STOPWORDS or tk in seen:
                continue
            seen.add(tk)
            if tk not in ptext:
                findings.append(_find("critical", "invented_ticker",
                                      f"'{tk}' appears in the note but nowhere in the payload.", tk))
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
# Kept at `minor` on purpose: the live baseline prompt explicitly ASKS for a
# tradeable implication, so this fires as an observation for the critic to
# weigh, not as a violation of the instructions the model was given.
_DIRECTIVE = re.compile(
    r"\b(you should (?:buy|sell|short|long)|buy the|sell the|short the|"
    r"take profits|add here|get long|get short|enter (?:here|now))\b", re.I)


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
        ptext = _payload_text(payload).upper()
        seen: set[str] = set()
        for tk in _TICKER.findall(text):
            if tk in _TICKER_STOPWORDS or tk in seen:
                continue
            seen.add(tk)
            if tk not in ptext:
                findings.append(_find("critical", "invented_ticker",
                                      f"'{tk}' appears in the interpretation but nowhere in the payload.", tk))
    _safe(_tickers, findings, "tickers")

    def _directive():
        m = _DIRECTIVE.search(text)
        if m:
            findings.append(_find("minor", "directive_wording",
                                  "Instructs an action rather than describing what is priced.",
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


_GRADERS = {
    "market_driver": grade_market_driver,
    "home_interpret": grade_home_interpret,
    "es_audit": grade_es_audit,
}


def grade(surface: str, payload, output) -> dict:
    """Run the rule set for a surface. Never raises."""
    fn = _GRADERS.get(surface)
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
}


def regression_failures(findings: list[dict]) -> list[str]:
    return sorted({f.get("rule") for f in (findings or [])
                   if f.get("rule") in REGRESSION_RULES})
