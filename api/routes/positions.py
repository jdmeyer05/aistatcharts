"""Position book endpoints — trade tracking, P&L, portfolio summary."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from api.deps import get_current_user

router = APIRouter()


class PositionInput(BaseModel):
    ticker: str
    type: str  # stock, iron_condor, calendar_spread, call, put
    qty: int
    entry_price: float
    details: dict = {}
    source_page: str = ""


class CloseInput(BaseModel):
    close_price: float
    exit_thesis: str = ""


@router.get("/")
async def list_positions(
    status: str = "open",
    user: str = Depends(get_current_user),
):
    """Get all positions, optionally filtered by status."""
    from src.position_book import get_positions
    return get_positions(status)


@router.get("/summary")
async def portfolio_summary(user: str = Depends(get_current_user)):
    """Get portfolio-level summary with P&L and alerts."""
    from src.position_book import get_portfolio_summary
    return get_portfolio_summary()


@router.post("/add")
async def add_position(pos: PositionInput, user: str = Depends(get_current_user)):
    """Add a new position to the book."""
    from src.position_book import add_position
    pos_id = add_position(
        ticker=pos.ticker.upper(),
        type=pos.type,
        qty=pos.qty,
        entry_price=pos.entry_price,
        details=pos.details,
        source_page=pos.source_page,
    )
    return {"id": pos_id}


@router.post("/{pos_id}/close")
async def close_position(pos_id: str, data: CloseInput, user: str = Depends(get_current_user)):
    """Close a position by ID."""
    from src.position_book import close_position
    close_position(pos_id, data.close_price, data.exit_thesis)
    return {"status": "closed", "id": pos_id}


@router.delete("/{pos_id}")
async def remove_position(pos_id: str, user: str = Depends(get_current_user)):
    """Remove a position entirely."""
    from src.position_book import remove_position
    remove_position(pos_id)
    return {"status": "removed", "id": pos_id}


@router.get("/robinhood")
async def robinhood_positions(user: str = Depends(get_current_user)):
    """Pull live positions from Robinhood."""
    import logging
    _log = logging.getLogger(__name__)

    try:
        import robin_stocks.robinhood as rh
        from src.api_keys import get_secret

        rh_user = get_secret("ROBINHOOD_USERNAME")
        rh_pass = get_secret("ROBINHOOD_PASSWORD")
        if not rh_user or not rh_pass:
            return {"success": False, "error": "Robinhood credentials not configured"}

        rh.login(rh_user, rh_pass, store_session=True)

        # Portfolio
        profile = rh.profiles.load_portfolio_profile()
        equity = float(profile.get("equity", 0) or 0)
        market_value = float(profile.get("market_value", 0) or 0)
        cash = float(profile.get("withdrawable_amount", 0) or 0)

        # Stock positions
        stocks = []
        # Quick industry lookup for concentration analysis
        THEME_MAP = {
            "RGTI": "Quantum Computing", "QUBT": "Quantum Computing",
            "QBTS": "Quantum Computing", "IONQ": "Quantum Computing",
            "UAMY": "Critical Minerals",
        }

        for p in rh.account.get_open_stock_positions():
            ticker = rh.stocks.get_symbol_by_url(p.get("instrument", ""))
            qty = float(p.get("quantity", 0))
            avg_cost = float(p.get("average_buy_price", 0))
            if qty <= 0:
                continue
            try:
                quote = rh.stocks.get_latest_price(ticker)
                current = float(quote[0]) if quote and quote[0] else 0
            except Exception:
                current = 0
            # Entry date from position created_at
            created = p.get("created_at", "")[:10]  # YYYY-MM-DD
            cost_basis = qty * avg_cost
            mkt_val = qty * current
            pl = mkt_val - cost_basis
            pl_pct = (pl / cost_basis * 100) if cost_basis > 0 else 0
            stocks.append({
                "ticker": ticker, "qty": round(qty, 2), "avg_cost": round(avg_cost, 2),
                "current_price": round(current, 2), "market_value": round(mkt_val, 2),
                "cost_basis": round(cost_basis, 2), "pl": round(pl, 2), "pl_pct": round(pl_pct, 2),
                "entry_date": created,
                "theme": THEME_MAP.get(ticker, "Other"),
            })

        # Option positions — group into spreads
        option_legs = []
        for o in rh.options.get_open_option_positions():
            data = rh.options.get_option_instrument_data_by_id(o.get("option_id", ""))
            if not data:
                continue
            chain = o.get("chain_symbol", "?")
            qty = float(o.get("quantity", 0))
            # RH average_price is in cents, negative for credits (short positions)
            avg_price_raw = float(o.get("average_price", 0) or 0) / 100  # per-share
            avg_price_abs = abs(avg_price_raw)  # always positive for display
            direction = o.get("type", "?")  # long/short
            strike = float(data.get("strike_price", 0))
            exp = data.get("expiration_date", "")
            opt_type = data.get("type", "")  # call/put
            # Current option mark price + Greeks from RH
            mark_data = {}
            try:
                raw_mark = rh.options.get_option_market_data_by_id(o.get("option_id", ""))
                if isinstance(raw_mark, list):
                    mark_data = raw_mark[0] if raw_mark else {}
                elif isinstance(raw_mark, dict):
                    mark_data = raw_mark
                mark_price = float(mark_data.get("adjusted_mark_price", 0) or 0)
            except Exception:
                mark_price = 0
            # Greeks (per share, from RH)
            iv = float(mark_data.get("implied_volatility", 0) or 0)
            delta = float(mark_data.get("delta", 0) or 0)
            gamma = float(mark_data.get("gamma", 0) or 0)
            theta = float(mark_data.get("theta", 0) or 0)
            vega = float(mark_data.get("vega", 0) or 0)
            # Sign convention: short positions flip sign
            sign = -1 if direction == "short" else 1
            # P&L per share: long = (mark - paid), short = (collected - mark)
            if direction == "short":
                pl_per_share = avg_price_abs - mark_price
            else:
                pl_per_share = mark_price - avg_price_abs
            pl_total = pl_per_share * 100 * qty  # per contract = 100 shares
            # Position Greeks = per-share greek × 100 shares × qty × sign
            pos_delta = delta * 100 * qty * sign
            pos_gamma = gamma * 100 * qty * sign  # short legs = negative gamma
            pos_theta = theta * 100 * qty * sign
            pos_vega = vega * 100 * qty * sign
            option_legs.append({
                "chain": chain, "strike": strike, "exp": exp, "opt_type": opt_type,
                "direction": direction, "qty": qty,
                "avg_price": round(avg_price_abs, 2),
                "current_price": round(mark_price * 100, 2),
                "pl": round(pl_total, 2),
                "iv": round(iv, 4),
                "delta": round(pos_delta, 1), "gamma": round(pos_gamma, 1),
                "theta": round(pos_theta, 1), "vega": round(pos_vega, 1),
            })

        # Group legs into spreads by chain + expiration
        spreads = []
        by_key: dict[str, list] = {}
        for leg in option_legs:
            key = f"{leg['chain']}_{leg['exp']}"
            by_key.setdefault(key, []).append(leg)

        for key, legs in by_key.items():
            chain = legs[0]["chain"]
            exp = legs[0]["exp"]
            legs.sort(key=lambda x: (x["opt_type"], x["strike"]))

            # Classify spread type
            calls = [l for l in legs if l["opt_type"] == "call"]
            puts = [l for l in legs if l["opt_type"] == "put"]
            short_legs = [l for l in legs if l["direction"] == "short"]
            long_legs = [l for l in legs if l["direction"] == "long"]
            total_qty = max(abs(l["qty"]) for l in legs) if legs else 0

            # P&L: sum of individual leg P&Ls (already correctly computed per leg)
            pl = sum(l.get("pl", 0) for l in legs)
            # Net premium collected (for display): sum of short credits - long debits
            net_premium = sum(l["avg_price"] * l["qty"] * (1 if l["direction"] == "short" else -1) * 100 for l in legs)
            # Current cost to close
            net_current = sum(l["current_price"] * l["qty"] * (1 if l["direction"] == "short" else -1) for l in legs)

            # Check if short calls are covered by stock holdings
            stock_shares = next((s["qty"] for s in stocks if s["ticker"] == chain), 0)
            short_call_contracts = sum(l["qty"] for l in legs if l["direction"] == "short" and l["opt_type"] == "call")
            is_covered = stock_shares >= short_call_contracts * 100

            if len(puts) == 2 and len(calls) == 2:
                spread_type = "Iron Condor"
            elif len(calls) == 2 and not puts:
                spread_type = "Bear Call" if any(l["direction"] == "short" for l in calls if l["strike"] == min(c["strike"] for c in calls)) else "Bull Call"
            elif len(puts) == 2 and not calls:
                spread_type = "Bull Put" if any(l["direction"] == "short" for l in puts if l["strike"] == max(p["strike"] for p in puts)) else "Bear Put"
            elif len(legs) == 1 and legs[0]["direction"] == "short" and legs[0]["opt_type"] == "call" and is_covered:
                spread_type = "Covered Call"
            elif len(legs) == 1 and legs[0]["direction"] == "short":
                spread_type = f"Naked Short {legs[0]['opt_type'].title()}"
            elif len(legs) == 1:
                spread_type = f"Long {legs[0]['opt_type'].title()}"
            else:
                spread_type = "Complex"

            # Get current stock price
            try:
                quote = rh.stocks.get_latest_price(chain)
                stock_price = float(quote[0]) if quote and quote[0] else 0
            except Exception:
                stock_price = 0

            # Strikes summary
            strikes_str = "/".join(f"${l['strike']:.0f}{l['opt_type'][0].upper()}" for l in legs)

            # Spread-level Greeks (sum of legs)
            sp_delta = round(sum(l.get("delta", 0) for l in legs), 1)
            sp_gamma = round(sum(l.get("gamma", 0) for l in legs), 1)
            sp_theta = round(sum(l.get("theta", 0) for l in legs), 1)
            sp_vega = round(sum(l.get("vega", 0) for l in legs), 1)

            spreads.append({
                "ticker": chain, "type": spread_type, "strikes": strikes_str,
                "expiration": exp, "qty": total_qty, "legs": legs,
                "net_premium": round(net_premium, 2), "current_value": round(net_current, 2),
                "pl": round(pl, 2), "stock_price": round(stock_price, 2),
                "short_strikes": [l["strike"] for l in short_legs],
                "long_strikes": [l["strike"] for l in long_legs],
                "greeks": {"delta": sp_delta, "gamma": sp_gamma, "theta": sp_theta, "vega": sp_vega},
            })

        # Total P&L
        stock_pl = sum(s["pl"] for s in stocks)
        option_pl = sum(s["pl"] for s in spreads)

        # Aggregate portfolio Greeks from all option legs
        all_legs = [leg for s in spreads for leg in s.get("legs", [])]
        port_delta = round(sum(l.get("delta", 0) for l in all_legs), 1)
        port_gamma = round(sum(l.get("gamma", 0) for l in all_legs), 1)
        port_theta = round(sum(l.get("theta", 0) for l in all_legs), 1)
        port_vega = round(sum(l.get("vega", 0) for l in all_legs), 1)
        # Add stock delta (each share = 1 delta)
        stock_delta = round(sum(s["qty"] for s in stocks), 1)

        # Concentration analysis
        theme_exposure: dict[str, float] = {}
        total_stock_val = sum(s["market_value"] for s in stocks) or 1
        for s in stocks:
            theme = s.get("theme", "Other")
            theme_exposure[theme] = theme_exposure.get(theme, 0) + s["market_value"]
        concentration = []
        for theme, val in sorted(theme_exposure.items(), key=lambda x: -x[1]):
            pct = round(val / total_stock_val * 100, 1)
            tickers_in = [s["ticker"] for s in stocks if s.get("theme") == theme]
            concentration.append({
                "theme": theme, "value": round(val, 0), "pct": pct,
                "tickers": tickers_in,
                "warning": "HIGH" if pct > 50 else "MODERATE" if pct > 30 else "OK",
            })

        return {
            "success": True,
            "portfolio": {
                "equity": round(equity, 2),
                "market_value": round(market_value, 2),
                "cash": round(cash, 2),
                "stock_pl": round(stock_pl, 2),
                "option_pl": round(option_pl, 2),
                "total_pl": round(stock_pl + option_pl, 2),
            },
            "greeks": {
                "delta": port_delta + stock_delta,
                "option_delta": port_delta,
                "stock_delta": stock_delta,
                "gamma": port_gamma,
                "theta": port_theta,
                "vega": port_vega,
            },
            "concentration": concentration,
            "stocks": sorted(stocks, key=lambda x: -abs(x["market_value"])),
            "spreads": spreads,
        }

    except Exception as e:
        _log.warning(f"Robinhood fetch failed: {e}")
        return {"success": False, "error": str(e)}


class HoldingsResearchRequest(BaseModel):
    tickers: list[str] = []


@router.post("/holdings-research")
async def holdings_research(req: HoldingsResearchRequest, user: str = Depends(get_current_user)):
    """Research held positions: Grok news search + yfinance fundamentals in parallel."""
    import json, re, logging
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from src.api_keys import get_secret
    from datetime import datetime as _dt

    _log = logging.getLogger(__name__)
    grok_key = get_secret("GROK_API_KEY")
    if not grok_key:
        return {"success": False, "error": "GROK_API_KEY not configured"}

    tickers = [t.strip().upper() for t in req.tickers[:10] if t.strip()]
    if not tickers:
        return {"success": False, "error": "No tickers provided"}

    today = _dt.utcnow().strftime("%A, %B %d, %Y")
    tickers_str = ", ".join(tickers)

    # ── Fundamentals from yfinance (parallel per ticker) ──
    def _fetch_fundamentals(ticker: str) -> dict:
        try:
            import yfinance as yf
            ytk = yf.Ticker(ticker)
            info = ytk.info or {}

            # Revenue / earnings
            revenue = info.get("totalRevenue") or info.get("revenue")
            revenue_growth = info.get("revenueGrowth")
            eps = info.get("trailingEps")
            gross_margins = info.get("grossMargins")
            operating_margins = info.get("operatingMargins")

            # Cash / burn
            total_cash = info.get("totalCash")
            total_debt = info.get("totalDebt", 0)
            free_cash_flow = info.get("freeCashflow")
            operating_cf = info.get("operatingCashflow")

            # Valuation
            market_cap = info.get("marketCap")
            pe_ratio = info.get("trailingPE")
            ps_ratio = info.get("priceToSalesTrailing12Months")

            # Analyst
            target_mean = info.get("targetMeanPrice")
            target_low = info.get("targetLowPrice")
            target_high = info.get("targetHighPrice")
            n_analysts = info.get("numberOfAnalystOpinions", 0)
            recommendation = info.get("recommendationKey", "")

            # Earnings dates
            try:
                edates = ytk.earnings_dates
                if edates is not None and len(edates) > 0:
                    now = _dt.now()
                    future = [d for d in edates.index if d.tz_localize(None) > now]
                    next_earnings = min(future).tz_localize(None).strftime("%Y-%m-%d") if future else None
                    next_earnings_days = (min(future).tz_localize(None) - now).days if future else None
                else:
                    next_earnings = next_earnings_days = None
            except Exception:
                next_earnings = next_earnings_days = None

            # Quarterly cash burn (from cash flow)
            quarterly_burn = None
            if operating_cf is not None and operating_cf < 0:
                quarterly_burn = abs(operating_cf) / 4  # rough quarterly
            cash_runway_quarters = None
            if total_cash and quarterly_burn and quarterly_burn > 0:
                cash_runway_quarters = round(total_cash / quarterly_burn, 1)

            def _fmt_num(n):
                if n is None: return None
                if abs(n) >= 1e9: return f"${n/1e9:.1f}B"
                if abs(n) >= 1e6: return f"${n/1e6:.1f}M"
                if abs(n) >= 1e3: return f"${n/1e3:.0f}K"
                return f"${n:.0f}"

            return {
                "ticker": ticker,
                "company": info.get("shortName") or info.get("longName", ticker),
                "market_cap": _fmt_num(market_cap),
                "market_cap_raw": market_cap,
                "revenue_ttm": _fmt_num(revenue),
                "revenue_growth": f"{revenue_growth*100:.0f}%" if revenue_growth else None,
                "eps": f"${eps:.2f}" if eps else None,
                "gross_margin": f"{gross_margins*100:.0f}%" if gross_margins else None,
                "operating_margin": f"{operating_margins*100:.0f}%" if operating_margins else None,
                "pe_ratio": round(pe_ratio, 1) if pe_ratio else None,
                "ps_ratio": round(ps_ratio, 1) if ps_ratio else None,
                "cash": _fmt_num(total_cash),
                "debt": _fmt_num(total_debt) if total_debt else None,
                "fcf": _fmt_num(free_cash_flow),
                "quarterly_burn": _fmt_num(quarterly_burn) if quarterly_burn else None,
                "cash_runway": f"{cash_runway_quarters:.0f} quarters" if cash_runway_quarters else None,
                "analyst_target": round(target_mean, 2) if target_mean else None,
                "analyst_low": round(target_low, 2) if target_low else None,
                "analyst_high": round(target_high, 2) if target_high else None,
                "analyst_count": n_analysts,
                "recommendation": recommendation,
                "next_earnings": next_earnings,
                "next_earnings_days": next_earnings_days,
            }
        except Exception as e:
            _log.warning(f"Fundamentals failed for {ticker}: {e}")
            return {"ticker": ticker, "company": ticker}

    # ── Grok news search ──
    def _grok_search() -> list:
        import httpx, time
        for attempt in range(3):
            if attempt > 0: time.sleep(3)
            try:
                resp = httpx.post(
                    "https://api.x.ai/v1/responses",
                    headers={"Authorization": f"Bearer {grok_key}", "Content-Type": "application/json"},
                    json={
                        "model": "grok-4-1-fast-reasoning",
                        "instructions": f"You are a fundamental equity research analyst. TODAY IS: {today}. "
                            "Use web_search and x_search to find RECENT developments for each ticker. "
                            "Search for the last 7-14 days of news. Do NOT rely on training data.",
                        "input": [{"role": "user", "content": f"""Research each of these held positions: {tickers_str}

For EACH ticker, search the web and X/Twitter for:
1. Earnings results or guidance changes (last 30 days)
2. Major partnerships, contracts, or product announcements
3. Analyst upgrades/downgrades or price target changes
4. SEC filings (8-K, insider buying/selling)
5. Industry-level developments that affect this company
6. Any negative developments: lawsuits, dilution, executive departures, competitive threats

Return a JSON array with one object per ticker:
[{{"ticker": "RGTI", "thesis_status": "intact" or "strengthened" or "weakened" or "broken",
  "developments": [{{"headline": "...", "date": "...", "impact": "positive" or "negative" or "neutral", "detail": "..."}}],
  "outlook": "one sentence", "risk": "single biggest risk"}}]

Be specific. Cite actual events with dates. Return ONLY the JSON array."""}],
                        "tools": [{"type": "web_search"}, {"type": "x_search"}],
                        "temperature": 0.1,
                    },
                    timeout=90.0,
                )
                resp.raise_for_status()
                data = resp.json()
                for out_item in data.get("output", []):
                    if out_item.get("type") == "message":
                        for content in out_item.get("content", []):
                            if content.get("type") == "output_text":
                                raw = content.get("text", "")
                                cleaned = re.sub(r"^```json?\s*\n?", "", raw.strip())
                                cleaned = re.sub(r"\n?\s*```\s*$", "", cleaned)
                                start = cleaned.find("[")
                                if start == -1: return []
                                depth, end = 0, start
                                for i in range(start, len(cleaned)):
                                    if cleaned[i] == "[": depth += 1
                                    elif cleaned[i] == "]": depth -= 1
                                    if depth == 0: end = i + 1; break
                                return json.loads(cleaned[start:end])
                return []
            except Exception as e:
                if attempt == 2: raise e
        return []

    # ── Run both in parallel ──
    _log.info(f"Holdings research: {tickers_str}")
    fundamentals_map = {}
    grok_results = []

    with ThreadPoolExecutor(max_workers=len(tickers) + 1) as pool:
        # Submit all fundamentals + grok
        fund_futs = {pool.submit(_fetch_fundamentals, tk): tk for tk in tickers}
        grok_fut = pool.submit(_grok_search)

        for fut in as_completed(list(fund_futs.keys()) + [grok_fut]):
            try:
                if fut == grok_fut:
                    grok_results = fut.result()
                else:
                    result = fut.result()
                    fundamentals_map[result["ticker"]] = result
            except Exception as e:
                _log.warning(f"Holdings research thread failed: {e}")

    # ── Merge fundamentals + Grok research ──
    research = []
    grok_map = {r.get("ticker", ""): r for r in grok_results}
    for tk in tickers:
        fund = fundamentals_map.get(tk, {"ticker": tk, "company": tk})
        grok = grok_map.get(tk, {})
        research.append({
            **fund,
            "thesis_status": grok.get("thesis_status", "intact"),
            "developments": grok.get("developments", []),
            "outlook": grok.get("outlook", "No recent analysis available."),
            "risk": grok.get("risk", "Unknown"),
        })

    _log.info(f"Holdings research: {len(research)} results")
    return {"success": True, "research": research}
