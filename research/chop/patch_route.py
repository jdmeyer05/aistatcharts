"""Add the /es-chop-record endpoint and its frontend client."""
import io

p = "api/routes/market.py"
s = io.open(p, encoding="utf-8").read()
a = '''@router.get("/es-analogs")'''
assert s.count(a) == 1
add = '''@router.get("/es-chop-record")
async def es_chop_record(user: str = Depends(get_current_user)):
    """Walk-forward scorecard for the chop/trend read.

    Separate from the brief for the same reason the character track record is:
    it describes the MODULE rather than today, and it moves once a session at
    most. `chop_track_record` holds its own 12h cache.
    """
    from src.es_chop_record import chop_track_record
    return await asyncio.to_thread(chop_track_record)


'''
s = s.replace(a, add + a)
io.open(p, "w", encoding="utf-8").write(s)
print("route ok")

p2 = "frontend/lib/api.ts"
t = io.open(p2, encoding="utf-8").read()
anchor = 'export interface EsChopTrend {'
assert t.count(anchor) == 1
decl = '''/** Walk-forward scorecard for the chop/trend read. Every session is scored
 *  against a fit built only from sessions BEFORE it, so these are out-of-sample
 *  numbers rather than the read grading its own homework. The hourly rows are
 *  deliberately unscored — they make no prediction. */
export interface EsChopRecord {
  available: boolean;
  reason?: string;
  rows?: Array<{
    label: string;
    n: number;
    never_fired?: boolean;
    coverage_pct?: number;
    delivered_pct?: number;
    claimed_floor_pct?: number | null;
    claimed_avg_pct?: number | null;
    clears_floor?: boolean;
    margin_pp?: number | null;
  }>;
  eras?: Array<{
    era: string; from: string; to: string;
    confident_delivered_pct?: number | null;
    likely_delivered_pct?: number | null;
  }>;
  observations?: number;
  sessions_scored?: number;
  scored_from?: string;
  scored_to?: string;
  train_min?: number;
  refit_every?: number;
  /** Measured statements about what would improve the read — only where the
   *  numbers support a direction. Empty is a valid, documented answer. */
  improvements?: string[];
  hourly_scored?: boolean;
  hourly_reason?: string;
  method?: string;
}

'''
t = t.replace(anchor, decl + anchor)

fn_anchor = 'export async function fetchEsBrief'
assert t.count(fn_anchor) == 1
fn = '''export async function fetchEsChopRecord(): Promise<EsChopRecord> {
  return apiFetch("/api/market/es-chop-record", { timeoutMs: 30_000 });
}

'''
t = t.replace(fn_anchor, fn + fn_anchor)
io.open(p2, "w", encoding="utf-8").write(t)
print("client ok")
