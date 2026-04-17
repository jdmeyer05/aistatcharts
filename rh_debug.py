import robin_stocks.robinhood as rh
from src.api_keys import get_secret

rh.login(get_secret("ROBINHOOD_USERNAME"), get_secret("ROBINHOOD_PASSWORD"), store_session=True)

options = rh.options.get_open_option_positions()
print("=== RAW SPY OPTION DATA ===")
for o in options:
    chain = o.get("chain_symbol", "?")
    if chain != "SPY":
        continue
    data = rh.options.get_option_instrument_data_by_id(o.get("option_id", ""))
    mark_data = rh.options.get_option_market_data_by_id(o.get("option_id", ""))

    strike = data.get("strike_price", "?") if data else "?"
    opt_type = data.get("type", "?") if data else "?"
    direction = o.get("type", "?")
    qty = float(o.get("quantity", 0))
    avg_price_raw = o.get("average_price", "?")
    avg_price = float(avg_price_raw or 0) / 100  # RH stores in cents

    # mark_data could be a list
    if isinstance(mark_data, list):
        mark_data = mark_data[0] if mark_data else {}
    mark_price = float(mark_data.get("adjusted_mark_price", 0) or 0) if mark_data else 0

    print(f"{direction:5s} {opt_type:4s} ${strike} qty={qty:.0f}")
    print(f"  avg_price raw={avg_price_raw}  => ${avg_price:.4f}/share (${avg_price*100:.2f}/contract)")
    print(f"  mark={mark_price:.4f}/share (${mark_price*100:.2f}/contract)")

    # P&L per contract
    if direction == "short":
        pl_per = (avg_price - mark_price) * 100  # collected - current cost
    else:
        pl_per = (mark_price - avg_price) * 100  # current value - paid
    print(f"  P&L per contract: ${pl_per:.2f} × {qty:.0f} = ${pl_per * qty:.2f}")
    print()

# Net P&L
print("=== NET CALCULATION ===")
total = 0
for o in options:
    if o.get("chain_symbol") != "SPY":
        continue
    data = rh.options.get_option_instrument_data_by_id(o.get("option_id", ""))
    mark_data = rh.options.get_option_market_data_by_id(o.get("option_id", ""))
    if isinstance(mark_data, list):
        mark_data = mark_data[0] if mark_data else {}

    direction = o.get("type", "?")
    qty = float(o.get("quantity", 0))
    avg = float(o.get("average_price", 0) or 0) / 100
    mark = float(mark_data.get("adjusted_mark_price", 0) or 0) if mark_data else 0

    if direction == "short":
        pl = (avg - mark) * 100 * qty
    else:
        pl = (mark - avg) * 100 * qty
    total += pl
    strike = data.get("strike_price", "?") if data else "?"
    opt_type = data.get("type", "?") if data else "?"
    print(f"  {direction:5s} {opt_type:4s} ${strike} => P&L ${pl:,.2f}")

print(f"\n  TOTAL SPY P&L: ${total:,.2f}")
