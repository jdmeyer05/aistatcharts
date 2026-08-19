# What actually moves the tape

Generated 2026-08-18 21:52. SPY daily bars, 3677 sessions from 2012-01-03 to 2026-08-18.

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
| 1 | Nonfarm payrolls | 175 | 1.39 | [1.20, 1.62] | 1.58 | 0.0015 | **yes** | 46% | 0.8353 |
| 2 | ISM services | 176 | 1.25 | [0.98, 1.42] | 1.42 | 0.0160 | no | 39% | 1.0829 |
| 3 | FOMC decision | 123 | 1.25 | [0.79, 1.62] | 1.41 | 0.0320 | no | 44% | 1.1628 |
| 4 | Empire State manufacturing | 150 | 1.23 | [0.92, 1.46] | 1.40 | 0.0220 | no | 37% | 0.849 |
| 5 | PCE price index | 151 | 1.19 | [0.96, 1.34] | 1.35 | 0.0445 | no | 35% | 0.8291 |
| 6 | Trade balance | 173 | 1.17 | [0.97, 1.43] | 1.32 | 0.0510 | no | 39% | 1.0782 |
| 7 | ISM manufacturing | 176 | 1.13 | [0.89, 1.30] | 1.28 | 0.1000 | no | 38% | 1.0745 |
| 8 | PPI | 174 | 1.12 | [1.02, 1.40] | 1.27 | 0.1349 | no | 37% | 1.0912 |
| 9 | Quarter end | 58 | 1.11 | [0.89, 1.42] | 1.26 | 0.2444 | no | 34% | 1.0765 |
| 10 | Industrial production | 175 | 1.09 | [0.94, 1.32] | 1.23 | 0.1679 | no | 33% | 0.9201 |
| 11 | Retail sales | 174 | 1.08 | [0.78, 1.34] | 1.22 | 0.1924 | no | 37% | 1.0897 |
| 12 | CPI | 175 | 1.06 | [0.87, 1.27] | 1.20 | 0.2774 | no | 37% | 1.0629 |
| 13 | Housing starts | 171 | 1.04 | [0.74, 1.26] | 1.17 | 0.3298 | no | 31% | 0.8229 |
| 14 | GDP | 171 | 1.02 | [0.82, 1.24] | 1.16 | 0.3513 | no | 29% | 1.1671 |
| 15 | Philly Fed manufacturing | 134 | 1.01 | [0.80, 1.27] | 1.14 | 0.4078 | no | 35% | 0.7687 |
| 16 | Month end | 175 | 1.00 | [0.86, 1.12] | 1.13 | 0.4363 | no | 28% | 1.1182 |
| 17 | Initial jobless claims | 757 | 1.00 | [0.89, 1.09] | 1.13 | 0.3993 | no | 34% | 1.0868 |
| 18 | U. Michigan sentiment (final) | 162 | 1.00 | [0.80, 1.28] | 1.13 | 0.4428 | no | 31% | 0.8112 |
| 19 | FOMC minutes | 122 | 0.96 | [0.76, 1.21] | 1.09 | 0.5872 | no | 34% | 1.0844 |
| 20 | Monthly opex | 175 | 0.94 | [0.69, 1.23] | 1.07 | 0.6557 | no | 35% | 1.0322 |
| 21 | Triple witching | 58 | 0.94 | [0.61, 1.33] | 1.07 | 0.6127 | no | 31% | 1.1807 |
| 22 | JOLTS job openings | 162 | 0.93 | [0.72, 1.09] | 1.05 | 0.7066 | no | 31% | 0.89 |
| 23 | New home sales | 167 | 0.85 | [0.65, 1.04] | 0.97 | 0.8926 | no | 34% | 0.8012 |

**Survived FDR: Nonfarm payrolls.** Everything else is inside the noise of its own calendar.

Three things the table is easy to misread:

- **ISM services ranks 2 and does not survive.** Its raw p is 0.0160, which would pass a single test and does not pass 23 of them. Read it as "possibly real, not established here" rather than as a null — its interval spans 0.98 to 1.42, which is the honest width at n=176 on a distribution this heavy-tailed.
- **Triple witching is a subset of monthly opex,** not an independent row — every
  witching Friday is also an opex Friday. They rank 21 and 20.
- **Both of them were an artefact until the dividend was taken out.** SPY goes
  ex-dividend on the third Friday of March, June, September and December — 57 of the
  58 witching days here — and on unadjusted closes that puts a mechanical ~0.25% drop
  into a session whose typical absolute move is near 0.5%. Measured that way witching
  came first at 1.53x with p=0.0055 and opex sixth. On adjusted closes they are
  0.94x and 0.94x, ranked
  21 and 20. The finding was the dividend.

### Persistence: the move does not carry

The `t+1` column is the same metric on the following session. Nothing here stays
elevated, and the biggest event in the table is followed by a QUIETER-than-normal
day: nonfarm payrolls runs 1.39x on the print and 0.84x the session after. Whatever these events do, they do it once and it is
over by the next close.

## 2. The ranking is not stable, and that is the finding

Mean rank across calendar years, with the spread. An event with a good average and a
wide spread is a different proposition from one that sits in the same place every
year, and the pooled table above cannot tell them apart.

Quarterly events (witching, quarter end) are absent: at four observations a year
there is no median worth ranking inside a single year, so `rank_by_year` skips them.

| Event | years | mean rank | best | worst | rank sd | in top 10 |
|---|---:|---:|---:|---:|---:|---:|
| Nonfarm payrolls | 15 | 5.9 | 1 | 16 | 4.04 | 87% |
| ISM services | 15 | 8.2 | 1 | 20 | 5.75 | 73% |
| Trade balance | 15 | 9.2 | 1 | 18 | 6.14 | 53% |
| PCE price index | 15 | 9.3 | 1 | 20 | 5.25 | 73% |
| PPI | 15 | 9.4 | 2 | 20 | 5.41 | 67% |
| Empire State manufacturing | 13 | 9.5 | 2 | 19 | 4.65 | 62% |
| ISM manufacturing | 15 | 9.7 | 1 | 21 | 6.66 | 60% |
| CPI | 15 | 10.1 | 1 | 19 | 6.32 | 53% |
| FOMC decision | 14 | 10.1 | 2 | 21 | 7.65 | 50% |
| Industrial production | 15 | 10.4 | 2 | 18 | 4.69 | 47% |
| Retail sales | 15 | 10.7 | 1 | 20 | 6.73 | 47% |
| U. Michigan sentiment (final) | 15 | 11.1 | 1 | 21 | 5.98 | 40% |
| FOMC minutes | 14 | 11.6 | 4 | 19 | 6.01 | 43% |
| Philly Fed manufacturing | 12 | 12.1 | 1 | 21 | 6.99 | 42% |
| Month end | 15 | 12.3 | 4 | 18 | 4.22 | 33% |
| Monthly opex | 15 | 12.5 | 1 | 21 | 6.56 | 40% |
| JOLTS job openings | 15 | 12.7 | 2 | 21 | 5.38 | 33% |
| Initial jobless claims | 15 | 12.7 | 4 | 18 | 3.9 | 20% |
| GDP | 15 | 12.7 | 2 | 21 | 5.96 | 33% |
| Housing starts | 15 | 12.8 | 3 | 21 | 6.54 | 40% |
| New home sales | 15 | 13.5 | 1 | 20 | 6.63 | 27% |

### Top five by year

- **2012** — Nonfarm payrolls 2.07 · JOLTS job openings 1.45 · ISM manufacturing 1.44 · PPI 1.35 · ISM services 1.19
- **2013** — ISM services 2.23 · FOMC decision 1.97 · Nonfarm payrolls 1.53 · Housing starts 1.45 · FOMC minutes 1.43
- **2014** — Retail sales 1.56 · PPI 1.50 · Housing starts 1.47 · FOMC minutes 1.41 · ISM manufacturing 1.38
- **2015** — Philly Fed manufacturing 2.53 · PCE price index 2.02 · FOMC decision 1.76 · FOMC minutes 1.75 · CPI 1.73
- **2016** — Retail sales 1.88 · Trade balance 1.61 · PPI 1.56 · Industrial production 1.41 · CPI 1.25
- **2017** — PCE price index 1.92 · Empire State manufacturing 1.50 · Nonfarm payrolls 1.45 · Trade balance 1.37 · Retail sales 1.31
- **2018** — New home sales 2.59 · GDP 2.25 · Nonfarm payrolls 2.17 · Philly Fed manufacturing 1.79 · PPI 1.72
- **2019** — ISM services 1.85 · ISM manufacturing 1.75 · Nonfarm payrolls 1.63 · Trade balance 1.25 · Monthly opex 1.22
- **2020** — Trade balance 2.00 · ISM services 1.88 · FOMC decision 1.40 · ISM manufacturing 1.29 · FOMC minutes 1.27
- **2021** — ISM manufacturing 1.72 · Retail sales 1.59 · Monthly opex 1.59 · Initial jobless claims 1.55 · GDP 1.54
- **2022** — U. Michigan sentiment (final) 2.25 · FOMC decision 2.22 · PCE price index 2.19 · CPI 1.75 · Empire State manufacturing 1.61
- **2023** — New home sales 1.92 · Philly Fed manufacturing 1.68 · FOMC decision 1.51 · Industrial production 1.44 · U. Michigan sentiment (final) 1.39
- **2024** — CPI 2.28 · FOMC decision 2.12 · Trade balance 1.74 · ISM services 1.68 · Nonfarm payrolls 1.67
- **2025** — Nonfarm payrolls 1.85 · CPI 1.82 · U. Michigan sentiment (final) 1.49 · Trade balance 1.15 · JOLTS job openings 1.06
- **2026** — Monthly opex 1.86 · Industrial production 1.63 · U. Michigan sentiment (final) 1.34 · Month end 1.32 · ISM services 1.31

## 3. Continuous drivers: what share of the daily move they explain

Rolling 126-session regression of SPY daily returns on four macro
markets. Latest window to 2026-08-18: total R² **0.430**.

| Driver | incremental R² (latest) | same-day corr | next-day corr |
|---|---:|---:|---:|
| Gold (GLD) | +0.1004 | +0.062 | +0.015 |
| Oil (USO) | +0.0557 | +0.295 | -0.034 |
| Dollar (DXY) | +0.0228 | -0.158 | -0.013 |
| Rates (TLT) | +0.0091 | -0.263 | +0.044 |

**Every next-day correlation is inside noise.** That column is the control, and it
says these relationships account for what happened rather than anticipate it. None of
this is a signal.

### Which macro driver mattered, by year

| Year | total R² | ranking |
|---|---:|---|
| 2012 | 0.655 | Rates +0.119 > Oil +0.062 > Dollar +0.028 > Gold +0.006 |
| 2013 | 0.350 | Rates +0.123 > Oil +0.065 > Dollar +0.013 > Gold +0.009 |
| 2014 | 0.195 | Rates +0.130 > Oil +0.039 > Gold +0.011 > Dollar +0.008 |
| 2015 | 0.241 | Rates +0.097 > Dollar +0.035 > Oil +0.032 > Gold +0.004 |
| 2016 | 0.400 | Oil +0.086 > Dollar +0.063 > Gold +0.051 > Rates +0.043 |
| 2017 | 0.120 | Rates +0.034 > Oil +0.022 > Dollar +0.009 > Gold +0.004 |
| 2018 | 0.166 | Oil +0.090 > Rates +0.033 > Dollar +0.008 > Gold +0.005 |
| 2019 | 0.314 | Rates +0.122 > Oil +0.083 > Gold +0.011 > Dollar +0.008 |
| 2020 | 0.402 | Rates +0.151 > Oil +0.111 > Gold +0.039 > Dollar +0.009 |
| 2021 | 0.198 | Oil +0.063 > Dollar +0.028 > Gold +0.015 > Rates +0.007 |
| 2022 | 0.214 | Dollar +0.103 > Oil +0.039 > Gold +0.032 > Rates +0.003 |
| 2023 | 0.228 | Dollar +0.076 > Oil +0.027 > Gold +0.024 > Rates +0.014 |
| 2024 | 0.148 | Dollar +0.046 > Gold +0.036 > Rates +0.013 > Oil +0.004 |
| 2025 | 0.144 | Oil +0.074 > Gold +0.017 > Rates +0.015 > Dollar +0.007 |
| 2026 | 0.194 | Oil +0.050 > Gold +0.040 > Rates +0.009 > Dollar +0.006 |

### Credit, held out on purpose

Adding high yield against duration takes the latest window from R² 0.430 to 0.668 — an incremental +0.2377, larger than any macro driver.

Held out of the headline ranking deliberately. HYG is itself a risk asset; equities and high yield falling together is closer to a definition than an explanation, which is why credit topped every year of this sample. Its incremental R² over macro is the part that is actually a finding.

### Composition — what kind of tape, not what drove it

| Spread | same-day corr | next-day corr |
|---|---:|---:|
| Breadth (RSP-SPY) | -0.041 | +0.036 |
| Semis (SMH-SPY) | +0.353 | +0.015 |
| Defensives (XLP-SPY) | -0.593 | +0.027 |
| Small caps (IWM-SPY) | +0.210 | +0.011 |

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