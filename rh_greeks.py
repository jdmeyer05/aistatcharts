import robin_stocks.robinhood as rh
from src.api_keys import get_secret

rh.login(get_secret("ROBINHOOD_USERNAME"), get_secret("ROBINHOOD_PASSWORD"), store_session=True)

options = rh.options.get_open_option_positions()
for o in options:
    data = rh.options.get_option_instrument_data_by_id(o.get("option_id", ""))
    mark_data = rh.options.get_option_market_data_by_id(o.get("option_id", ""))
    if isinstance(mark_data, list):
        mark_data = mark_data[0] if mark_data else {}

    chain = o.get("chain_symbol", "?")
    strike = data.get("strike_price", "?") if data else "?"
    opt_type = data.get("type", "?") if data else "?"
    direction = o.get("type", "?")

    # Check what Greeks/IV RH provides
    iv = mark_data.get("implied_volatility") if mark_data else None
    delta = mark_data.get("delta") if mark_data else None
    gamma = mark_data.get("gamma") if mark_data else None
    theta = mark_data.get("theta") if mark_data else None
    vega = mark_data.get("vega") if mark_data else None
    rho = mark_data.get("rho") if mark_data else None

    print(f"{direction:5s} {opt_type:4s} ${strike} {chain}")
    print(f"  IV={iv}  delta={delta}  gamma={gamma}  theta={theta}  vega={vega}  rho={rho}")
    print()
