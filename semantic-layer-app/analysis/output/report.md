# Online Retail II: segmentation and forecasting

## Data
- Raw rows (both sheets): 1,067,371
- After removing exact duplicates (incl. the 1–9 Dec 2010 sheet overlap): 1,033,036
- Sales rows (no cancellations, quantity > 0, price > 0): 1,007,913
- Sales rows with a customer ID (used for segmentation): 779,425

## Segmentation (RFM + K-means)
- Customers: 5,878; as-of date 2011-12-10
- Features: log(1+x) of recency, frequency, monetary, standardised; K-means with 10 starts, seed 42
- Chosen k = 4 (best silhouette for k ≥ 3); mean silhouette 0.365
- Agreement between RFM segments and clusters (adjusted Rand index): 0.43

| k | Inertia | Silhouette |
|---|---|---|
| 2 | 8,589.0 | 0.441 |
| 3 | 6,354.3 | 0.35 |
| 4 | 4,921.2 | 0.365 |
| 5 | 4,099.1 | 0.344 |
| 6 | 3,554.7 | 0.336 |
| 7 | 3,194.5 | 0.306 |
| 8 | 2,902.4 | 0.303 |
| 9 | 2,656.6 | 0.291 |
| 10 | 2,467.4 | 0.289 |

| Cluster | Customers | Customer % | Revenue % | Avg recency (days) | Avg orders | Avg spend £ | Silhouette |
|---|---|---|---|---|---|---|---|
| High spend, frequent, active | 1,196 | 20.3 | 73.9 | 27.7 | 19.3 | 10,731 | 0.331 |
| High spend, frequent, cooling | 1,459 | 24.8 | 16.4 | 230.1 | 5.1 | 1,948 | 0.314 |
| Mid spend, regular, active | 1,250 | 21.3 | 6.2 | 28.3 | 3.1 | 858 | 0.291 |
| Low spend, rare, lapsed | 1,973 | 33.6 | 3.6 | 394.9 | 1.4 | 317 | 0.47 |

| RFM segment | Customers | Customer % | Revenue % |
|---|---|---|---|
| Champions | 830 | 14.1 | 51.1 |
| Loyal Customers | 1,167 | 19.9 | 27.8 |
| At Risk | 754 | 12.8 | 5.8 |
| Potential Loyalists | 711 | 12.1 | 4.7 |
| Hibernating | 1,526 | 26.0 | 3.8 |
| Can't Lose Them | 71 | 1.2 | 3.3 |
| Need Attention | 268 | 4.6 | 2.0 |
| About to Sleep | 384 | 6.5 | 1.2 |
| Promising | 113 | 1.9 | 0.2 |
| New Customers | 54 | 0.9 | 0.1 |

- Warning: Spend is very skewed (skewness 25); features are log-transformed before clustering.

## Forecasting (SARIMA vs baselines)

### Monthly
- History: 24 complete months, 2009-12-01 to 2011-11-01; seasonal strength 1.0
- Final model: SARIMA(0,0,2)(0,1,0,12) (AIC 238.7); Ljung-Box p = 0.799
- Backtest: last 4 months, model SARIMA(0,0,1)(0,1,0,12) chosen on training data only

| Model | MAPE % | sMAPE % | MAE £ | RMSE £ |
|---|---|---|---|---|
| sarima | 5.9 | 6.2 | 60,213 | 75,761 |
| naive | 31.8 | 40.2 | 399,276 | 479,892 |
| seasonal_naive | 6.1 | 6.5 | 61,885 | 77,057 |

- Improvement in MAE over seasonal naive: 2.7% · confidence: **fair**
- Note: The last period was incomplete and was dropped.
- Note: The backtest is small (4 test periods, model fitted on 6 effective periods), so its accuracy figures are indicative only.

### Weekly
- History: 104 complete weeks, 2009-12-07 to 2011-11-28; seasonal strength 1.0
- Final model: SARIMA(0,0,2)(0,1,0,52) (AIC 1187.3); Ljung-Box p = 0.884
- Backtest: last 13 weeks, model SARIMA(2,0,2)(0,1,0,52) chosen on training data only

| Model | MAPE % | sMAPE % | MAE £ | RMSE £ |
|---|---|---|---|---|
| sarima | 18.0 | 17.6 | 45,035 | 56,933 |
| naive | 44.2 | 58.3 | 134,551 | 146,871 |
| seasonal_naive | 18.1 | 17.7 | 45,257 | 57,994 |

- Improvement in MAE over seasonal naive: 0.5% · confidence: **fair**
- Note: 2 period(s) with no data were filled with 0.
- Note: The first period was incomplete and was dropped.
- Note: The last period was incomplete and was dropped.
- Note: Some lower interval bounds are below zero; the metric cannot be negative, so read them as 0.
