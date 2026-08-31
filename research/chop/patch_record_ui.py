"""Render the walk-forward chop record beneath the SHAPE block."""
import io

p = "frontend/components/home/es-briefing.tsx"
s = io.open(p, encoding="utf-8").read()

# 1. the query, alongside the existing track-record query
a = '  const trackQ = useQuery<EsTrackRecord>({'
assert s.count(a) == 1
q = '''  // The chop read's own scorecard. Its own endpoint rather than a field on the
  // brief, for the same reason the character track record is: it describes the
  // MODULE, not today, and it moves once a session at most.
  const chopRecQ = useQuery<EsChopRecord>({
    queryKey: ["es-chop-record"],
    queryFn: fetchEsChopRecord,
    staleTime: 12 * 60 * 60 * 1000,
    retry: 1,
  });

'''
s = s.replace(a, q + a)

# 2. imports
imp_old = "  fetchEsBrief,\n"
assert s.count(imp_old) == 1, s.count(imp_old)
s = s.replace(imp_old, imp_old + "  fetchEsChopRecord,\n")

t_old = "  type EsTrackRecord,\n"
assert s.count(t_old) == 1, s.count(t_old)
s = s.replace(t_old, t_old + "  type EsChopRecord,\n")

# 3. the block itself, after the forward-null paragraph of the SHAPE card
anchor = """                          base rate — a measured null. This describes the tape behind you.
                        </p>
                      )}
"""
assert s.count(anchor) == 1, s.count(anchor)

add = """                  {/* ITS OWN RECORD, scored WALK-FORWARD. Every session is
                        graded against a fit built only from sessions before it, so
                        this is not the read marking its own homework — which is
                        precisely what a whole-sample replay would have been, and it
                        would have flattered the read by exactly the amount it is
                        overfitted.

                        Sits against the number it scores rather than on a separate
                        page: a track record a reader has to go and find is a track
                        record nobody checks. */}
                    {chopRecQ.data?.available && (chopRecQ.data.rows ?? []).length > 0 && (
                      <div className="mt-1 pt-1 border-t border-border/60 space-y-0.5">
                        {(() => {
                          const cur = d.chop_trend?.label;
                          const row = (chopRecQ.data?.rows ?? []).find((x) => x.label === cur);
                          if (!row || row.never_fired || row.delivered_pct == null) return null;
                          return (
                            <p className="text-[0.55rem] text-text-muted leading-snug">
                              <span className="uppercase tracking-wider">Its record: </span>
                              when this read said{" "}
                              <span className="text-text">{cur}</span>, the session finished
                              that way{" "}
                              <span className="text-text tabular-nums">
                                {row.delivered_pct.toFixed(0)}%
                              </span>{" "}
                              of the time out of sample
                              {row.claimed_floor_pct != null && (
                                <>
                                  {" "}
                                  against the{" "}
                                  <span className="tabular-nums">
                                    {row.claimed_floor_pct.toFixed(0)}%
                                  </span>{" "}
                                  the label claims
                                </>
                              )}{" "}
                              (n={row.n}, fired on {row.coverage_pct?.toFixed(0)}% of readings).
                            </p>
                          );
                        })()}
                        <p className="text-[0.55rem] text-text-muted/80 leading-snug">
                          Walk-forward over {chopRecQ.data.sessions_scored?.toLocaleString("en-US")}{" "}
                          sessions ({chopRecQ.data.scored_from} to {chopRecQ.data.scored_to}):
                          a {chopRecQ.data.train_min}-session training window refitted every{" "}
                          {chopRecQ.data.refit_every}, each session scored only against sessions
                          before it. The class cuts are refitted too — cutting them on the whole
                          sample would leak the future into the definition of the outcome.
                        </p>
                        {/* The improvement lever, printed rather than acted on.
                            Retuning a threshold using the same window that scored
                            it would spend the out-of-sample evidence that makes
                            the score worth reading. */}
                        {(chopRecQ.data.improvements ?? []).length > 0 && (
                          <p className="text-[0.55rem] text-amber-400/90 leading-snug">
                            {chopRecQ.data.improvements!.join(" ")}
                          </p>
                        )}
                        <p className="text-[0.55rem] text-text-muted/80 leading-snug">
                          {chopRecQ.data.hourly_reason}
                        </p>
                      </div>
                    )}
"""
s = s.replace(anchor, anchor + add)
io.open(p, "w", encoding="utf-8").write(s)
print("ui ok")
