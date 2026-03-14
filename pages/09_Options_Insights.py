import numpy as np
from scipy.stats import norm
from datetime import datetime
import matplotlib.pyplot as plt
import seaborn as sns

def black_scholes_option_price(S, K, T, r, sigma, option_type='call'):
    if T == 0:
        if option_type == 'call':
            return max(S - K, 0)
        elif option_type == 'put':
            return max(K - S, 0)
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    if option_type == 'call':
        price = S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    elif option_type == 'put':
        price = K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
    return price

# User Inputs
current_stock_price = float(input("Enter the current stock price: "))
strike_price = float(input("Enter the strike price: "))
expiration_date_input = input("Enter the expiration date (YYYY-MM-DD): ")
volatility = float(input("Enter the volatility (as a decimal, e.g., 0.1792 for 17.92%): "))
risk_free_rate = float(input("Enter the risk-free rate (as a decimal, e.g., 0.043 for 4.3%): "))
option_type_input = input("Enter 'call' or 'put' for the option type: ").lower()
option_type = 'put' if option_type_input == 'put' else 'call'
current_option_price = float(input("Enter the current option price: "))

# Parse the expiration date
expiration_date = datetime.strptime(expiration_date_input, '%Y-%m-%d')

# Calculate the number of days to expiration from today
current_date = datetime.now()
days_to_expiration = (expiration_date - current_date).days

# Adjust price range based on option type
if option_type == 'call':
    price_changes = np.arange(current_stock_price-7, current_stock_price + 7, 1)
else:
    price_changes = np.arange(current_stock_price - 20, current_stock_price + 1, 1)

# Generate range of days to expiration
days = np.arange(0, days_to_expiration + 1, 1)  # Including today (day 0)

# Initialize the matrix to hold option prices
matrix = np.zeros((len(price_changes), len(days)))

# Calculate option prices for the matrix
for i, price in enumerate(price_changes):
    for j, day in enumerate(days):
        T_new = day / 365
        matrix[i, j] = round(black_scholes_option_price(price, strike_price, T_new, risk_free_rate, volatility, option_type=option_type), 1)

# Create a custom colormap
cmap = sns.diverging_palette(10, 150, as_cmap=True)

# Dynamically adjust the plot size
plot_width = max(30, days_to_expiration // 10)
plot_height = 8 * 1.2  # Increase height by 20%

# Plot the heatmap
plt.figure(figsize=(plot_width, plot_height))
ax = sns.heatmap(matrix, cmap=cmap, center=current_option_price, xticklabels=days[::-1], yticklabels=np.round(price_changes), annot=True, fmt=".1f", cbar=False)
plt.title(f'{option_type.capitalize()} Option Price Heatmap')
plt.xlabel('Days to Expiration')
plt.ylabel('Stock Price')
plt.gca().invert_xaxis()  # Reverse x-axis order

# Add a contour line for the current option price
contour = ax.contour(matrix, levels=[current_option_price], colors='black', linewidths=2, linestyles='dashed', origin='lower')
ax.clabel(contour, inline=True, fontsize=10)

plt.show()
