import robin_stocks.robinhood as rh
from src.api_keys import get_secret

rh.login(get_secret("ROBINHOOD_USERNAME"), get_secret("ROBINHOOD_PASSWORD"), store_session=True)

# Check stock order fields
orders = rh.orders.get_all_stock_orders()
if orders:
    o = orders[0]
    print("Stock order fields:", list(o.keys()))
    for o in orders[:3]:
        ticker = rh.stocks.get_symbol_by_url(o.get("instrument", ""))
        print(f"  {o.get('side')} {ticker} qty={o.get('quantity')} price={o.get('average_price') or o.get('price')} state={o.get('state')} date={o.get('created_at','')[:10]}")

# Check option order fields
opt_orders = rh.orders.get_all_option_orders()
if opt_orders:
    print("\nOption order fields:", list(opt_orders[0].keys())[:15])
    for o in opt_orders[:3]:
        legs = o.get("legs", [])
        print(f"  {o.get('opening_strategy') or o.get('closing_strategy')} state={o.get('state')} date={o.get('created_at','')[:10]} premium={o.get('premium')}")
        for leg in legs[:2]:
            print(f"    {leg.get('side')} {leg.get('option_type')} strike={leg.get('strike_price')}")
