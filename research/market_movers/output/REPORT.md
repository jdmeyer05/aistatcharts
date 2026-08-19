# What actually moves the tape

Generated 2026-08-18 21:48. SPY daily bars, 3677 sessions from 2012-01-03 to 2026-08-18.

**The metric.** Every session is divided by the trailing 60-session median absolute
move, so a value of 1.40 means "1.40x a day that was normal AT THE TIME". Without
that normalisation the ranking would partly be a ranking of which events happened
to fall in 2022. Close-to-close, not range: the releases that matter land at 08:30,
an hour before the bell, and an RTH range cannot see that reaction.

**No direction is claimed anywhere below.** The question is how big, not which way.

## 1. Scheduled events, ranked over the full sample

`xNormal` is the median relative move on those sessions. `p` comes from rotating the
whole event calendar to a random phase — a null that preserves both the calendar's
spacing and the way volatility clusters. `FDR` is Benjamini-Hochberg at 0.10 across
all events tested, not across the survivors.

| # | Event | n | xNormal | 95% CI | vs quiet day | p | FDR | ≥1.5x | t+1 |
|---|---|---:|---:|---|---:|---:|:--:|---:|---:|
| 1 | Triple witching | 58 | 1.53 | [1.25, 1.98] | 1.76 | 0.0055 | **yes** | 50% | 1.1575 |
| 2 | Nonfarm payrolls | 175 | 1.39 | [1.16, 1.57] | 1.60 | 0.0015 | **yes** | 46% | 0.8353 |
| 3 | FOMC decision | 123 | 1.25 | [0.79, 1.62] | 1.43 | 0.0325 | no | 45% | 1.1648 |
| 4 | ISM services | 176 | 1.24 | [0.97, 1.40] | 1.42 | 0.0190 | no | 39% | 1.0687 |
| 5 | Empire State manufacturing | 150 | 1.23 | [1.00, 1.43] | 1.42 | 0.0260 | no | 39% | 0.9807 |
| 6 | Monthly opex | 175 | 1.20 | [0.84, 1.48] | 1.38 | 0.0315 | no | 41% | 1.0181 |
| 7 | PCE price index | 151 | 1.16 | [0.93, 1.33] | 1.33 | 0.0845 | no | 36% | 0.8032 |
| 8 | Trade balance | 173 | 1.15 | [0.97, 1.40] | 1.32 | 0.0655 | no | 40% | 1.0756 |
| 9 | Quarter end | 58 | 1.13 | [0.85, 1.44] | 1.30 | 0.2284 | no | 34% | 1.0209 |
| 10 | PPI | 174 | 1.12 | [0.99, 1.36] | 1.29 | 0.1394 | no | 36% | 1.0823 |
| 11 | Industrial production | 175 | 1.12 | [0.97, 1.32] | 1.28 | 0.1219 | no | 35% | 0.9445 |
| 12 | ISM manufacturing | 176 | 1.10 | [0.89, 1.27] | 1.27 | 0.1604 | no | 38% | 1.0502 |
| 13 | Retail sales | 174 | 1.08 | [0.77, 1.34] | 1.24 | 0.1689 | no | 38% | 1.1229 |
| 14 | Housing starts | 171 | 1.08 | [0.74, 1.28] | 1.24 | 0.2104 | no | 32% | 0.8552 |
| 15 | CPI | 175 | 1.06 | [0.87, 1.30] | 1.22 | 0.2474 | no | 37% | 1.076 |
| 16 | Month end | 175 | 1.01 | [0.85, 1.13] | 1.16 | 0.4218 | no | 29% | 1.099 |
| 17 | GDP | 171 | 1.00 | [0.78, 1.17] | 1.15 | 0.4153 | no | 28% | 1.1639 |
| 18 | Philly Fed manufacturing | 134 | 0.98 | [0.80, 1.24] | 1.13 | 0.4928 | no | 33% | 0.9863 |
| 19 | Initial jobless claims | 757 | 0.97 | [0.89, 1.09] | 1.12 | 0.5932 | no | 33% | 1.1106 |
| 20 | U. Michigan sentiment (final) | 162 | 0.97 | [0.78, 1.26] | 1.12 | 0.5527 | no | 32% | 0.7903 |
| 21 | FOMC minutes | 122 | 0.96 | [0.76, 1.18] | 1.10 | 0.6017 | no | 32% | 1.0953 |
| 22 | JOLTS job openings | 162 | 0.91 | [0.72, 1.09] | 1.04 | 0.7511 | no | 30% | 0.8919 |
| 23 | New home sales | 167 | 0.84 | [0.65, 1.07] | 0.97 | 0.9010 | no | 34% | 0.8011 |

**Survived FDR: Triple witching, Nonfarm payrolls.** Everything else is inside the noise of its own calendar.

Two things the table is easy to misread:

- **FOMC is third and does not survive.** Its raw p is 0.0325, which would pass any
  single test and does not pass twenty-three of them. Read it as "probably real,
  not established here" rather than as a null — its confidence interval spans 0.79
  to 1.62, which is the honest width at n=123 on a distribution this heavy-tailed.
- **Triple witching is a subset of monthly opex,** not an independent row. Every
  witching Friday is also an opex Friday, so the two lines share 58 sessions.

### Persistence: the move does not carry

The `t+1` column is the same metric on the following session. Nothing here stays
elevated, and the biggest event in the table is followed by a QUIETER-than-normal
day: payrolls run 1.39x on the print and 0.84x the session after. Whatever these
events do, they do it once and it is over by the next close.

## 2. The ranking is not stable, and that is the finding

Mean rank across calendar years, with the spread. An event with a good average and a
wide spread is a different proposition from one that sits in the same place every
year, and the pooled table above cannot tell them apart.

| Event | years | mean rank | best | worst | rank sd | in top 10 |
|---|---:|---:|---:|---:|---:|---:|
| Nonfarm payrolls | 15 | 6.1 | 1 | 16 | 3.89 | 93% |
| ISM services | 15 | 8.7 | 1 | 20 | 5.56 | 73% |
| Trade balance | 15 | 9.5 | 1 | 18 | 5.8 | 53% |
| PPI | 15 | 9.7 | 3 | 20 | 5.26 | 67% |
| PCE price index | 15 | 9.7 | 2 | 20 | 5.81 | 67% |
| Empire State manufacturing | 13 | 9.9 | 3 | 20 | 4.8 | 54% |
| CPI | 15 | 9.9 | 1 | 19 | 6.19 | 60% |
| Industrial production | 15 | 10.2 | 2 | 20 | 5.1 | 40% |
| ISM manufacturing | 15 | 10.3 | 1 | 21 | 6.64 | 60% |
| FOMC decision | 14 | 10.3 | 2 | 21 | 7.67 | 50% |
| Monthly opex | 15 | 10.3 | 1 | 21 | 6.32 | 53% |
| Retail sales | 15 | 10.5 | 1 | 20 | 6.98 | 47% |
| U. Michigan sentiment (final) | 15 | 11.6 | 1 | 21 | 6.03 | 27% |
| FOMC minutes | 14 | 11.8 | 4 | 20 | 5.92 | 36% |
| Housing starts | 15 | 12.0 | 2 | 21 | 7.27 | 47% |
| Month end | 15 | 12.1 | 4 | 18 | 4.75 | 40% |
| Philly Fed manufacturing | 12 | 12.2 | 1 | 21 | 6.81 | 33% |
| GDP | 15 | 12.5 | 2 | 21 | 6.06 | 33% |
| Initial jobless claims | 15 | 12.7 | 5 | 18 | 3.62 | 27% |
| JOLTS job openings | 15 | 12.9 | 2 | 21 | 5.3 | 33% |
| New home sales | 15 | 13.7 | 1 | 20 | 6.64 | 27% |

### Top five by year

- **2012** — Nonfarm payrolls 2.08 · JOLTS job openings 1.49 · ISM manufacturing 1.43 · PPI 1.34 · Housing starts 1.23
- **2013** — ISM services 2.17 · FOMC decision 1.84 · Nonfarm payrolls 1.53 · Housing starts 1.45 · FOMC minutes 1.42
- **2014** — Retail sales 1.50 · Housing starts 1.47 · PPI 1.45 · FOMC minutes 1.42 · ISM manufacturing 1.34
- **2015** — Philly Fed manufacturing 2.53 · PCE price index 2.02 · FOMC decision 1.76 · FOMC minutes 1.75 · CPI 1.73
- **2016** — Retail sales 1.88 · Trade balance 1.55 · Housing starts 1.53 · PPI 1.51 · Industrial production 1.41
- **2017** — Retail sales 1.85 · PCE price index 1.82 · Monthly opex 1.79 · Industrial production 1.72 · Empire State manufacturing 1.72
- **2018** — New home sales 2.47 · GDP 2.22 · Nonfarm payrolls 2.07 · PPI 1.70 · Philly Fed manufacturing 1.65
- **2019** — ISM services 1.80 · ISM manufacturing 1.70 · Nonfarm payrolls 1.57 · Monthly opex 1.47 · Trade balance 1.25
- **2020** — Trade balance 2.00 · ISM services 1.90 · FOMC decision 1.36 · FOMC minutes 1.31 · ISM manufacturing 1.29
- **2021** — ISM manufacturing 1.72 · Retail sales 1.58 · Monthly opex 1.58 · GDP 1.52 · Initial jobless claims 1.52
- **2022** — U. Michigan sentiment (final) 2.28 · PCE price index 2.28 · FOMC decision 2.19 · CPI 1.75 · Empire State manufacturing 1.61
- **2023** — New home sales 1.83 · Philly Fed manufacturing 1.68 · FOMC decision 1.51 · Industrial production 1.44 · Nonfarm payrolls 1.37
- **2024** — CPI 2.17 · FOMC decision 2.12 · Empire State manufacturing 1.95 · Trade balance 1.74 · Month end 1.64
- **2025** — Nonfarm payrolls 1.85 · CPI 1.82 · U. Michigan sentiment (final) 1.45 · Trade balance 1.09 · Month end 1.07
- **2026** — Monthly opex 1.83 · Industrial production 1.63 · U. Michigan sentiment (final) 1.34 · Month end 1.32 · ISM services 1.31

## 3. Continuous drivers: what share of the daily move they explain

Rolling 126-session regression of SPY daily returns on four macro
markets. Latest window to 2026-08-18: total R² **0.435**.

| Driver | incremental R² (latest) | same-day corr | next-day corr |
|---|---:|---:|---:|
| Gold (GLD) | +0.1019 | +0.061 | +0.017 |
| Oil (USO) | +0.0570 | +0.295 | -0.034 |
| Dollar (DXY) | +0.0242 | -0.158 | -0.013 |
| Rates (TLT) | +0.0084 | -0.265 | +0.044 |

**Every next-day correlation is inside noise.** That column is the control, and it
says these relationships account for what happened rather than anticipate it. None of
this is a signal.

### Which macro driver mattered, by year

| Year | total R² | ranking |
|---|---:|---|
| 2012 | 0.655 | Rates +0.123 > Oil +0.061 > Dollar +0.027 > Gold +0.005 |
| 2013 | 0.350 | Rates +0.122 > Oil +0.067 > Dollar +0.014 > Gold +0.008 |
| 2014 | 0.199 | Rates +0.137 > Oil +0.038 > Gold +0.010 > Dollar +0.008 |
| 2015 | 0.244 | Rates +0.104 > Dollar +0.033 > Oil +0.031 > Gold +0.003 |
| 2016 | 0.399 | Oil +0.083 > Dollar +0.064 > Gold +0.052 > Rates +0.046 |
| 2017 | 0.120 | Rates +0.037 > Oil +0.020 > Dollar +0.010 > Gold +0.003 |
| 2018 | 0.167 | Oil +0.091 > Rates +0.034 > Dollar +0.008 > Gold +0.005 |
| 2019 | 0.310 | Rates +0.119 > Oil +0.084 > Gold +0.010 > Dollar +0.008 |
| 2020 | 0.403 | Rates +0.153 > Oil +0.110 > Gold +0.038 > Dollar +0.008 |
| 2021 | 0.196 | Oil +0.060 > Dollar +0.029 > Gold +0.015 > Rates +0.007 |
| 2022 | 0.216 | Dollar +0.105 > Oil +0.040 > Gold +0.032 > Rates +0.003 |
| 2023 | 0.228 | Dollar +0.077 > Oil +0.028 > Gold +0.026 > Rates +0.013 |
| 2024 | 0.148 | Dollar +0.047 > Gold +0.035 > Rates +0.011 > Oil +0.004 |
| 2025 | 0.143 | Oil +0.073 > Gold +0.017 > Rates +0.015 > Dollar +0.007 |
| 2026 | 0.196 | Oil +0.053 > Gold +0.041 > Rates +0.008 > Dollar +0.006 |

### Credit, held out on purpose

Adding high yield against duration takes the latest window from R² 0.435 to 0.668 — an incremental +0.2333, larger than any macro driver.

Held out of the headline ranking deliberately. HYG is itself a risk asset; equities and high yield falling together is closer to a definition than an explanation, which is why credit topped every year of this sample. Its incremental R² over macro is the part that is actually a finding.

### Composition — what kind of tape, not what drove it

| Spread | same-day corr | next-day corr |
|---|---:|---:|
| Breadth (RSP-SPY) | -0.049 | +0.027 |
| Semis (SMH-SPY) | +0.347 | +0.015 |
| Defensives (XLP-SPY) | -0.591 | +0.026 |
| Small caps (IWM-SPY) | +0.201 | +0.008 |

Descriptive, not explanatory. These are equity spreads against the index they are partly made of, so their co-movement with SPY is in part arithmetic. They say what kind of tape it was.

## What this does not say

- **Nothing here is predictive.** Event magnitude is measured on the day the event
  lands; driver attribution is same-day and its next-day column is flat.
- **Magnitude is not direction.** A 1.4x session is a wider session, not an up one.
- **Rule-derived dates are softer than fetched ones.** ISM, FOMC minutes, opex and
  month end come from calendar rules, not a publisher's calendar.
- **Events overlap.** Claims land on 20% of all sessions, NFP is always a Friday, and
  triple witching is a strict subset of monthly opex. The quiet-day column is the
  contrast that accounts for this; the raw ratio does not.
- **The event ranking and the driver ranking are separate questions.** One says which
  dates are wide, the other says which market SPY co-moved with. Neither implies the
  other, and nothing here connects them.
- **The regional surveys start late** — Empire in 2014, Philly in 2015 — because that
  is where FRED's release calendar begins for them.