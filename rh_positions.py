import robin_stocks.robinhood as rh
from src.api_keys import get_secret

rh.login(get_secret("ROBINHOOD_USERNAME"), get_secret("ROBINHOOD_PASSWORD"), store_session=True)

options = rh.options.get_open_option_positions()
print("=== OPTIONS ===")
for o in options:
    chain = o.get("chain_symbol", "?")
    qty = float(o.get("quantity", 0))
    avg = float(o.get("average_price", 0)) / 100
    direction = o.get("type", "?")
    data = rh.options.get_option_instrument_data_by_id(o.get("option_id", ""))
    if data:
        strike = data.get("strike_price", "?")
        exp = data.get("expiration_date", "?")
        opt_type = data.get("type", "?")
        side = "LONG" if direction == "long" else "SHORT"
        print(f"  {chain:5s} {opt_type:4s} ${strike:>8s} exp={exp} qty={qty:>3.0f} {side:5s} avg=${avg:>7.2f}")

# Group into spreads
print("\n=== SPREAD STRUCTURES ===")
by_chain = {}
for o in options:
    chain = o.get("chain_symbol", "?")
    data = rh.options.get_option_instrument_data_by_id(o.get("option_id", ""))
    if data:
        by_chain.setdefault(chain, []).append({
            "strike": float(data.get("strike_price", 0)),
            "exp": data.get("expiration_date", "?"),
            "type": data.get("type", "?"),
            "side": o.get("type", "?"),
            "qty": float(o.get("quantity", 0)),
            "avg": float(o.get("average_price", 0)) / 100,
        })

for chain, legs in by_chain.items():
    legs.sort(key=lambda x: (x["exp"], x["strike"]))
    print(f"\n  {chain}:")
    for leg in legs:
        print(f"    {leg['side']:5s} {leg['type']:4s} ${leg['strike']:.0f} exp={leg['exp']} qty={leg['qty']:.0f} avg=${leg['avg']:.2f}")

# Portfolio summary
print("\n=== PORTFOLIO ===")
profile = rh.profiles.load_portfolio_profile()
print(f"  Equity: ${float(profile.get('equity', 0)):,.2f}")
print(f"  Market Value: ${float(profile.get('market_value', 0)):,.2f}")
