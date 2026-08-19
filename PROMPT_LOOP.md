# Prompt Loop — the home page grading and rewriting itself

## What it does

Every time an AI block on the home page generates, the exact payload it was
shown is frozen next to what it said. Deterministic rules grade that output.
An adversarial pass reads the accumulated failures and proposes a rewritten
prompt. The rewrite is replayed against the incumbent on held-out historical
payloads, and only replaces it after winning two independent draws on different
days.

Three surfaces are covered:

| surface | what it is | prompt baseline |
|---|---|---|
| `market_driver` | the regime read at the top of home | `src/prompt_defaults.MARKET_DRIVER_SYSTEM` |
| `home_interpret` | the page-wide interpretation panel (home page only) | `src/prompt_defaults.BASE_SYSTEM` |
| `es_audit` | the ES card's contradiction auditor | `src/prompt_defaults.ES_AUDIT_SYSTEM` |

## The two scores, and why they are not interchangeable

**Rule score** (all three surfaces). Did the note stay faithful to the data it
was handed? Every number traceable to the payload, no invented tickers, no
contradiction with the rest of the page, no sub-20 VIX called "elevated". This
is deterministic, reproducible on a replayed payload years later, and incapable
of hallucinating. It is also incapable of telling you whether the read was
*right* — a perfectly grounded paragraph can call the regime backwards.

**Calibration** (`market_driver` only). The prompt emits 2-4 machine-resolvable
calls in a `calls` field the page never renders. Each settles from the first
close *at or after* the note to a close 1-5 sessions later, against the base
rate of the same call over the past year. The headline is **Brier skill**:
positive means the stated confidences carried information beyond quoting the
base rate; zero means they did not.

Only the rule score gates promotion. Calibration is measured forward and
reported, and deliberately never enters the gate — a challenger cannot make a
forecast about a week that has already happened.

The other two surfaces have no calibration record on purpose. The interpretation
panel describes what a page shows and the auditor reports contradictions inside
it; neither states anything price can settle, so scoring them against market
outcomes would mean inventing a claim they never made.

## The leak the claim schema exists to prevent

Measuring from the last completed close hands the model a free lunch: a note
written at 10:30 already knows SPY is up 0.4% today, so "SPY up ≥ 0.3%" would
resolve true from information it could see. Claims are therefore measured from
the **first close at or after** the note. A 10:30 Tuesday note for one session
is scored Tuesday-close to Wednesday-close: both endpoints unknown when it was
written. `tests/test_prompt_loop.py::test_reference_close_is_never_one_the_model_already_saw`
pins this.

## Promotion gate

A challenger is promoted only when **all** of these hold, twice, on different
days, against different holdout draws:

- zero regression-rule failures (the critical rules — each one a defect that
  reached a live page once already)
- no more critical findings than the champion
- no more generation failures than the champion
- 95% bootstrap CI on the paired score difference excludes zero
- mean improvement ≥ 0.02

Before any of that, a structural check refuses a challenger that dropped the
output schema, the `calls` contract, or 40% of the prompt's length.

## Files

```
supabase_prompt_loop.sql   5 tables + RLS (service_role only)
src/prompt_defaults.py     baseline prompt text — version 0, the floor
src/prompt_registry.py     which version is served; promote / reject / rollback
src/prompt_snapshots.py    request-path freezing of payload + output
src/grounding.py           numeric grounding (lifted out of api/routes/ai.py)
src/prompt_rules.py        the deterministic grader + the regression suite
src/prompt_claims.py       falsifiable calls, resolution, base rates, Brier skill
src/prompt_critic.py       adversarial critique + the editor that rewrites
src/prompt_replay.py       paired holdout replay and the promotion gate
src/prompt_loop.py         orchestration + reporting
api/routes/prompt_loop.py  admin endpoints + a narrow public track-record
frontend/app/prompt-loop   the scoreboard (admin, not in nav, not crawled)
tests/test_prompt_loop.py  32 tests, network-free
```

## Schedule

| stage | cadence | cost | where |
|---|---|---|---|
| snapshot | every real generation | free | inside the API routes |
| grade + resolve claims | hourly | free | `hourly-worker.yml`, task `all` |
| critique | 02:20 UTC daily | 2 Opus calls | `prompt-loop.yml` |
| evaluate | 03:00 UTC daily | ~50 generations | `prompt-loop.yml` |

## Setup, in order

1. **Run the migration.** Apply `supabase_prompt_loop.sql` in the Supabase SQL
   editor.
2. **Add `SUPABASE_SERVICE_ROLE_KEY` to GitHub repo secrets.** The tables are
   RLS'd to `service_role`. With the anon key every write fails silently and the
   loop looks like it is running while recording nothing. Both workflows already
   pass the secret through.
3. **Seed the baselines:** `python worker.py --task prompt_seed`, or POST
   `/api/prompt-loop/seed` as an admin. This records the git text as version 0
   and makes it champion.
4. Wait a day or two for snapshots to accumulate, then run
   `--task prompt_critique` and `--task prompt_evaluate` manually once to watch
   what they do before the cron takes over.

## Switching it off

| what | how | effect |
|---|---|---|
| stop serving edited prompts | `PROMPT_LOOP_DISABLED=1` | every surface pins to its git baseline immediately, no deploy |
| pin one surface | `PROMPT_LOOP_PIN=market_driver:3` | serves exactly that version |
| undo the last promotion | `POST /api/prompt-loop/rollback?surface=…` | re-promotes the previous version |
| serve a specific version now | `POST /api/prompt-loop/promote?surface=…&version=N` | bypasses the gate, manually |
| stop proposing changes | disable `prompt-loop.yml` | grading and calibration keep running |

The registry also refuses on its own: a champion whose body is under half the
baseline's length is treated as a corrupted write and the baseline is served
instead.

## What this does not prove

Two holdout wins is not proof. The sample is small, the rule score is a proxy,
and the severity weights that turn findings into a scalar were *chosen* for
ranking, not estimated from anything — read `counts`, treat `score` as a sort
key. Every experiment, including every rejection, is written to
`prompt_experiments`, so the claim "the prompts got better" can always be
checked against the record rather than taken on trust.
