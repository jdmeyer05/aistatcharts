-- Open Interest history capture — built on the standard snapshot endpoint
-- since Polygon Starter doesn't expose historical OI anywhere else.
--
-- A daily scheduler job populates rows here at ~4:30 PM ET (post-close).
-- The UI then queries back the accumulated series.
--
-- Volume math at steady state (2σ window, OI≥10 filter):
--   200 tickers × ~200 strikes × 2 sides = ~80k rows/day, ~8 MB/day, ~3 GB/yr.
--   Well above Supabase free tier; plan on Pro ($25/mo) after ~2 months,
--   or move cold data to GCS Parquet down the road.

CREATE TABLE IF NOT EXISTS options_oi_history (
  ticker         TEXT   NOT NULL,
  capture_date   DATE   NOT NULL,
  strike         NUMERIC(12, 4) NOT NULL,
  expiration     DATE   NOT NULL,
  contract_type  TEXT   NOT NULL CHECK (contract_type IN ('call', 'put')),
  open_interest  INTEGER NOT NULL,
  volume         INTEGER,
  implied_vol    NUMERIC(8, 6),
  captured_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (ticker, capture_date, expiration, strike, contract_type)
);

CREATE INDEX IF NOT EXISTS idx_oi_ticker_date ON options_oi_history (ticker, capture_date DESC);
CREATE INDEX IF NOT EXISTS idx_oi_date ON options_oi_history (capture_date DESC);

-- Daily top-200 ranking (persisted per day so the UI always knows which
-- tickers were considered "top" on any given historical date).
CREATE TABLE IF NOT EXISTS options_oi_universe (
  capture_date   DATE   NOT NULL,
  ticker         TEXT   NOT NULL,
  rank           SMALLINT NOT NULL,
  total_oi       BIGINT NOT NULL,
  total_volume   BIGINT,
  captured_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (capture_date, ticker)
);

CREATE INDEX IF NOT EXISTS idx_oi_universe_date_rank ON options_oi_universe (capture_date DESC, rank);

-- RLS: reads open to authenticated users; writes only via service_role
-- (which the Cloud Run service uses).
ALTER TABLE options_oi_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE options_oi_universe ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS oi_history_read ON options_oi_history;
DROP POLICY IF EXISTS oi_universe_read ON options_oi_universe;

CREATE POLICY oi_history_read ON options_oi_history FOR SELECT
  TO authenticated USING (TRUE);
CREATE POLICY oi_universe_read ON options_oi_universe FOR SELECT
  TO authenticated USING (TRUE);
