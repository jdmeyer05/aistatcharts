import robin_stocks.robinhood as rh
from src.api_keys import get_secret
rh.login(get_secret("ROBINHOOD_USERNAME"), get_secret("ROBINHOOD_PASSWORD"), store_session=True)
print("done")
