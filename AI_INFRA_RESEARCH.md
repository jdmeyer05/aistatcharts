# AI / Data Center Industry Page — Research & Design Brief
**Date:** 2026-08-02
**Status:** Research + brainstorm. Nothing built. No decisions locked.

---

## 1. Why this page, and what makes it different

Everyone has a "AI stocks" dashboard. The differentiated thing here is that this sector is a **physical chain with a financial chain bolted to it**, and the two are increasingly out of phase. Money is being committed years ahead of electrons, and electrons are being contracted years ahead of revenue.

The chain:

```
tokens/revenue  →  capex commitment  →  debt raised  →  chips ordered
      ↑                                                      ↓
      └──── energized MW ← transformers ← turbines ← land+permits+queue
```

**The signal is not any single link. It's the ratio between adjacent links, and when it breaks.**

That framing also connects to what this platform is already for. AI infrastructure is now the dominant driver of index-level earnings and of quarterly GDP prints — AI-related investment drove roughly **74% of the 2.1% annualized Q1 2026 GDP growth**, and AI datacenter/hardware/networking investment is ~**1.4% of GDP**, up from 0.7%. For an ES trader, this isn't a sector page. It's a macro page wearing a sector costume.

---

## 2. The tracking framework — seven layers

I'd organize the whole page around the capital cycle, not around tickers.

### Layer 0 — Demand: is there actual revenue underneath?
The question the whole edifice rests on.

| Metric | Why it matters | Current read (Aug 2026) |
|---|---|---|
| Aggregate token volume | Truest proxy for real inference demand | OpenRouter ~100T tokens/mo, 5× in six months |
| Frontier lab revenue run-rates | The revenue side of the capex/revenue gap | OpenAI ~$25B ARR, ~33% GM, ~$14B loss; Anthropic ~$30B run-rate, ~60% GM |
| Enterprise AI adoption | Breadth, not just depth | Census BTOS (biweekly, free API) |
| Price per token by capability tier | Deflation rate of the product | Falling fast — a margin squeeze on providers |
| Agentic task length (METR) | Does more compute still buy capability? | The capability→demand transmission |

**The ratio to build:** *revenue per dollar of cumulative capex*, and *revenue per GW energized*. Both are deteriorating. Charting the deterioration rate is the whole ballgame.

### Layer 1 — Capex & financing: where the money comes from
2026 hyperscaler capex is roughly **$725B across MSFT/GOOGL/AMZN/META**, up ~77% YoY (MSFT ~$190B, AMZN ~$200B, GOOGL $175–185B, META ~$139–145B). Oracle FY26 capex ~$55.7B, guiding FY27 toward ~$95B.

But the on-balance-sheet number understates it badly:
- ~**$1.65T in off-balance-sheet AI-linked obligations** across MSFT/GOOGL/AMZN/META/ORCL
- Moody's flagged **>$662B in off-balance-sheet commitments** — more than all their on-BS debt
- Meta's $30B Hyperion private-credit deal; Oracle's $38B package, $18B New Mexico loan, ~$13B Blue Owl/JPM SPV for the Abilene OpenAI site

**Track:** capex (10-Q XBRL), finance + operating lease commitments, purchase obligations, unconsolidated JV/SPV exposure, data center ABS/CMBS issuance and spreads, IG tech issuance as a share of total IG, CDS on ORCL and CRWV (CoreWeave CDS hit ~855bp in late July).

### Layer 2 — Silicon supply chain: the upstream constraint
- **CoWoS packaging sold out through 2026**; capacity ~75k wpm end-2025 → target 125–130k wpm end-2026
- **HBM fully allocated through 2026** across SK Hynix / Micron / Samsung; HBM3E prices up double digits YoY
- TSMC guiding **>30% FY26 revenue growth**; monthly revenue disclosure ~10th of each month is a genuinely tradeable high-frequency print

**Track:** TSMC monthly revenue, Taiwan + Korea monthly semiconductor export values (published very early in the month — best leading indicator in the whole stack), Korea semi export *price* indices, CoWoS/HBM allocation commentary, Nvidia/AMD/Broadcom RPO and supply commentary, networking/optics (Coherent, Fabrinet, Arista, Celestica) as the second-order read.

### Layer 3 — The shell: real estate and construction
- Northern Virginia vacancy at an **all-time low 0.3%** (Q1 2026), record **1,148 MW absorption**, inventory +1,136 MW YoY to 4,182 MW
- **96% of 2026 scheduled supply already committed**; preleasing pushed into 2027+
- Census private data center construction put-in-place growing ~30% YoY

**Track:** Census construction VIP (monthly), CBRE market stats (semiannual PDF), Epoch AI's datacenter dataset (satellite + permit derived, updated *today*), county-level permit filings for the big markets, construction employment.

### Layer 4 — Power: the actual binding constraint
This is where the page earns its keep, and where your existing EIA/ERCOT infrastructure gives you a real head start.

- **ERCOT large-load queue ~238.6 GW** (Mar 2026), 77.5% data centers — up ~4× in a year from 63 GW at end-2024
- **PJM**: 6,625 MW short in the Dec 2025 capacity auction, record prices; summer 2027 is the first expected shortfall season; potential 15 GW gap by 2030
- US generation+storage interconnection queue ~2,600 GW with 5+ year waits
- **GE Vernova gas turbine backlog 116 GW**, effectively sold out through 2030; GEV/Siemens Energy/MHI are ~75% of large-frame capacity
- **HV transformers quoted at 48–60 month lead times**; switchgear effectively sold out through 2028; HV breakers ~125 weeks
- ~**9.8 GW of hyperscaler nuclear PPAs** across 13 disclosed projects; TMI-1 restart pulled forward to H2 2027 after FERC's June 2026 waiver

**Track:** EIA-860M planned additions (monthly, API), ISO interconnection + large-load queues, capacity auction clearing prices, turbine/transformer backlogs from 8-Ks, PPA announcements, forward power and spark spreads (you already compute these).

### Layer 5 — Political and social constraint: the thing that actually kills projects
Underrated, and almost nobody models it quantitatively.

- Average household paid **$110 more for electricity** last year vs 2024
- Utilities requested **$31B in rate hikes in 2025** (vs $15B in 2024); **$9.4B in Q1 2026 alone**
- Ratepayer Protection Act cleared House E&C **52–0** — bipartisan
- Oregon already requires separate data-center rate classes; Arizona proposing water fees + killing the sales tax exemption; Delaware passed limits; moratoriums floated in multiple states

**Track:** EIA-861M retail rates by state and sector (monthly, API), electricity CPI vs headline CPI, rate case filing dollar volume, a state-legislation tracker, local project cancellations/referenda, GDELT intensity on data center opposition (you already have GDELT plumbing).

### Layer 6 — Unit economics: does the math close?
- H100 median rental ~**$2.29–3.12/hr** (May 2026), and notably it went *up* ~10% Dec→Jan — scarcity, not commoditization, at least for now
- B200 index 4.40 → 5.48 over Q1 2026
- Spot at ~48% of on-demand
- The depreciation fight: hyperscalers moved to 6-year lives; Burry's estimate is that a 2–3 year economic life implies a **>$176B cumulative 2026–28 earnings overstatement**

**Build the model, don't just chart it:** given $/GW all-in capex, GPU count per GW, and assumed life, solve for the $/GPU-hr required to hit a target IRR — then compare to the observed market rental rate. That single chart, updated monthly across GPU generations, is worth more than the rest of the page combined.

---

## 3. Brainstorm — the non-obvious things worth tracking

Ranked roughly by (differentiation × feasibility).

1. **Divergence monitor.** The flagship. Adjacent-link ratios on one screen with z-scores: capex vs revenue, announced GW vs energized GW, backlog vs delivered, chip shipments vs token growth, power contracted vs interconnection approved. Everyone charts levels; almost nobody charts the joints.

2. **Announcement decay / conversion funnel.** Announced GW → permit filed → interconnection agreement → steel in ground → energized. Historical interconnection completion rates run ~15–20%. Publishing a live conversion rate would directly puncture the headline "2,600 GW queue" number and is genuinely proprietary work.

3. **Circularity score.** Map the related-party web: Nvidia invests in neoclouds who buy Nvidia; OpenAI ↔ Oracle ↔ Nvidia; Meta ↔ CoreWeave ($35.2B) ↔ Nebius (up to $27B). Quantify what share of reported AI revenue is booked between counterparties with equity or financing links. A rising circularity ratio is the classic late-cycle tell.

4. **Grid ground truth.** EIA-930 gives hourly demand by balancing authority, free, and you already call it. Compute trailing load growth in datacenter-dense BAs and compare it to announced/contracted load in the same footprint. This is the single cleanest hype-vs-reality check available for free.

5. **Depreciation-adjusted earnings.** Recompute EPS for each hyperscaler under 3/4/5/6-year GPU lives. Show the sensitivity band. Pure XBRL + arithmetic, high shock value, nobody publishes it as a live series.

6. **Speed-to-power premium.** The valuation spread between sites with energized capacity and sites without. Visible in transaction comps and in how neoclouds describe their pipelines.

7. **Lease-term second derivative.** Average contract tenor, escalators, tenant credit quality, take-or-pay vs merchant. Terms shortening or credit quality deteriorating while volume still grows is what a top looks like.

8. **The loss waterfall.** For each major structure, who holds residual risk. Insurers absorbing datacenter ABS is the channel by which a GPU problem becomes a financial-system problem.

9. **Skilled labor.** Electrician/IBEW wage growth, journeyman availability, BLS employment in NAICS 238210 (electrical contractors) and 518210 (data processing/hosting). A hard physical constraint *and* an inflation transmission channel.

10. **Gas demand pull-through.** Incremental gas burn for data-center power is becoming a real driver of the natgas curve — ties directly into your existing /natgas page rather than duplicating it.

11. **Secondary GPU market prices.** Resale prices for A100/H100 are the market's actual verdict on useful life. Hard to source cleanly, very high signal if you can.

12. **Options positioning on the AI complex.** You already have the vol infrastructure. Skew and term structure on NVDA/ORCL/CRWV/GEV/VST/VRT as a crowdedness and stress gauge — this is the piece the rest of your platform uniquely enables.

13. **Curtailability share.** How much of announced load is contractually flexible. Flexible load is far cheaper to interconnect, so this number determines how much of the queue is actually buildable.

14. **Electricity CPI pass-through.** Electricity component of CPI vs headline, by region, against data center concentration. The Fed channel.

15. **Water and cooling.** Liquid cooling adoption rates, water withdrawal per MW, overlay against basin stress. Mostly a political-risk input rather than a standalone tab.

16. **RPO across the chain.** NVDA, CRWV ($99.4B as of Mar 2026, up from $66.8B at YE25), ORCL, GEV, VRT — plus a conversion rate on each. Note the double-counting: one dollar can appear as Nvidia revenue, CoreWeave capex, and Meta backlog simultaneously.

17. **Export controls / sovereign demand.** Gulf state buildouts, China restrictions, allocation shifts. Event-driven, best handled by an AI-summarized feed rather than a chart.

---

## 4. Data source catalog

### Tier A — free, real API, and you already hold the key
| Source | What you'd pull | Freq | Notes |
|---|---|---|---|
| **FRED** | Data center + power construction VIP, IP semis (NAICS 3344), computers & peripherals investment, electricity CPI/PPI, ICE BofA IG/HY spreads | M/Q | Already wired |
| **EIA v2** | 860M planned generators, 930 hourly demand by BA, 861M retail rates/sales by state+sector, Electric Power Monthly, STEO | M/H | Already wired |
| **SEC EDGAR XBRL** | Capex, PP&E, depreciation policy + expense, operating/finance lease commitments, purchase obligations, RPO | Q | `edgar.py` already has generic XBRL fetchers |
| **ERCOT API** | Large-load queue, generation interconnection, load | D | `ercot_api.py` already exists |
| **Polygon** | Prices/options for the AI complex | RT | Mind the known snapshot + bare-root traps |
| **GDELT** | Data center opposition / project news intensity | D | Already wired |
| **Polymarket** | AI-related outcome markets | 30m | Already wired |

### Tier B — free, needs a new (free) key or adapter
| Source | What | Freq |
|---|---|---|
| **Census Bureau API** | Construction VIP data-center category; **BTOS AI adoption** (biweekly — underused and excellent) | M / 2wk |
| **BEA API** | NIPA contributions to GDP growth: info processing equipment, software, structures | Q |
| **BLS API** | JOLTS, CES employment by NAICS (238210, 518210), wages, PPI electric power | M |
| **PJM Data Miner 2** | Capacity auction results, load, LMP | free key |
| **CAISO OASIS / MISO / NYISO / SPP** | Queues, load, capacity | varies |
| **Epoch AI** | **AI Data Centers** (satellite+permit derived: compute, power, timelines — updated 2026-08-02), AI Chip Sales, AI Chip Components (wafer/CoWoS/HBM), GPU Clusters, ML Hardware | CSV/ZIP, CC-BY |
| **LBNL "Queued Up"** | Annual national interconnection queue analysis | annual XLSX |
| **FERC eLibrary / eTariff** | Co-location and large-load dockets | scrape |
| **Taiwan MOF / Korea Customs** | Monthly semiconductor export value + price | M, very early |
| **TSMC IR** | Monthly revenue | ~10th |
| **OpenRouter** | Public model rankings and token volumes | live |
| **EPA CAMD/eGRID** | Plant-level hourly generation and emissions | H/annual |

### Tier C — free but manual/PDF
CBRE North America & Global Data Center Trends (semiannual), gas turbine and electrical-equipment backlogs from 8-Ks and earnings decks, GPU rental indices (aimultiple, getdeploying, Silicon Data), NCSL/state legislature trackers, state PUC rate case dockets.

### Tier D — paid, only if it earns its cost
SemiAnalysis datacenter model (probably the single best source in the space, priced accordingly), datacenterHawk, Silicon Data GPU index API, Wood Mackenzie / Grid Strategies, TrendForce, Bloomberg/ICE for CDS.

**Recommendation:** Tiers A+B alone support roughly 80% of what's described here. Don't buy anything until the free stack is exhausted.

---

## 5. Proposed page structure

`/ai-infrastructure` — the platform's multi-tab pattern, AI interpreter per tab, Supabase-cached bundle route, RSC shell. Suggested priority in brackets.

1. **Chain Dashboard** `[P0]` — the seven-layer chain in one view, plus the divergence monitor
2. **Power Buildout** `[P0]` — queues, 860M additions, capacity prices, turbine/transformer backlogs, PPAs
3. **Grid Reality** `[P0]` — EIA-930 realized load growth by BA vs announced; the hype-vs-electrons check
4. **Capex & Financing** `[P0]` — spend, off-BS obligations, debt issuance, ABS spreads, CDS
5. **Unit Economics** `[P1]` — GPU rental vs required rate, payback model, depreciation sensitivity
6. **Silicon Supply Chain** `[P1]` — TSMC/CoWoS/HBM, Taiwan+Korea export leading indicators
7. **Demand & Tokens** `[P1]` — token volume, lab revenue, adoption, price-per-token deflation
8. **Political Risk** `[P2]` — retail rates, rate cases, legislation, local opposition
9. **Real Estate** `[P2]` — vacancy, absorption, construction spend, project tracker
10. **Macro Transmission** `[P2]` — GDP contribution, employment, credit, index concentration

Ten is probably too many for one page. If it needs trimming, 1–4 is a complete and defensible product on its own; the rest can follow the Smart Money pattern of splitting into sub-pages.

---

## 6. Traps and accuracy notes

Given how this platform's past defects have clustered, flagging these up front:

- **Announced ≠ contracted ≠ energized ≠ consuming.** ERCOT's 238 GW is interconnection *requests*, heavily duplicated (developers shop the same project across BAs and file multiple times). Presenting it as forecast demand would be the single biggest error available here.
- **Interconnection queue GW is not a pipeline.** Historical completion runs ~15–20%. Any queue chart needs a completion-rate caveat rendered as visible text, not a tooltip.
- **Capex ≠ AI capex.** Hyperscaler capex includes plenty of non-AI. Don't let the headline stand in for the AI number without decomposing it.
- **Off-balance-sheet is where the leverage lives.** On-BS debt/EBITDA looks pristine for all five. Any leverage metric that ignores SPVs, JVs, and lease commitments is actively misleading.
- **Backlog double-counting.** The same dollar shows up as Nvidia revenue, CoreWeave capex, and Meta contracted backlog. Never sum RPO across the chain.
- **Depreciation life changes are non-cash earnings tailwinds.** Flag them separately from operating performance.
- **Census data-center construction gets revised.** With the May 2026 release, unadjusted data were revised back to Jan 2024. Cache the vintage, not just the latest value.
- **No default substitution.** Past defects on the home page traced to substituting a plausible default for an unknown. Here that would mean assuming a project's power is grid-connected when it's behind-the-meter, or assuming announced capacity equals nameplate. Show "unknown" as unknown.
- **yfinance is not thread-safe** — `yf.Ticker(tk).history()` only in concurrent paths.
- **Copy discipline:** describe what is priced or observed, never instruct a trade. Caveats as visible text.

---

## 7. Suggested build order

**Phase 1 — the spine (mostly reuses existing plumbing).**
EIA-860M planned additions + EIA-930 realized load by BA + ERCOT large-load queue + EDGAR XBRL capex/lease/depreciation pull + Census/BEA/FRED macro series. That's tabs 2, 3, 4 and most of 1, built almost entirely on adapters that already exist.

**Phase 2 — the analytics that make it proprietary.**
Divergence monitor, GPU payback model, depreciation-adjusted EPS, announcement conversion funnel. This is where the differentiation lives; none of it needs new data vendors.

**Phase 3 — the harder-to-source layers.**
Token/demand data, GPU rental indices, political risk tracker, real estate. More scraping, more manual maintenance, lower reliability.

---

## 8. Open questions for you

1. **Scope** — one big page, or a section (like Smart Money's 11 pages) from the start?
2. **Angle** — is this primarily an *ES/macro risk* lens (how AI capex drives the index and GDP), a *sector trading* lens (relative value across the complex), or a *fundamental industry monitor*? It changes which layers get depth.
3. **Political risk tab** — worth the maintenance burden, or is it too manual to keep honest?
4. **Paid data** — appetite for SemiAnalysis or Silicon Data, or free-only for now?
5. **Refresh cadence** — most of this is monthly/quarterly. Does that break the up-to-the-minute pattern used elsewhere, and is a "last updated / next release" treatment acceptable?
