-- Alert firings — history of times user_alerts actually triggered.
-- One row per (alert × fire time). Lets the UI show "3 new alerts since
-- last visit" and gives the email worker something to read to decide what
-- to send.

CREATE TABLE IF NOT EXISTS public.user_alert_firings (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  alert_id      UUID NOT NULL REFERENCES public.user_alerts(id) ON DELETE CASCADE,
  user_id       UUID NOT NULL,
  alert_type    TEXT NOT NULL,
  target        TEXT NOT NULL,
  fired_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  context       JSONB NOT NULL DEFAULT '{}'::jsonb,  -- pctile, net, diff, etc
  notified_at   TIMESTAMPTZ,                          -- when email/push was sent
  notify_error  TEXT                                  -- last error if any
);

CREATE INDEX IF NOT EXISTS user_alert_firings_user_fired_idx
  ON public.user_alert_firings (user_id, fired_at DESC);

CREATE INDEX IF NOT EXISTS user_alert_firings_alert_fired_idx
  ON public.user_alert_firings (alert_id, fired_at DESC);

-- Dedup guard: don't fire the same alert more than once per reporting date.
-- Extracted from context so we can look it up cheaply.
CREATE INDEX IF NOT EXISTS user_alert_firings_dedup_idx
  ON public.user_alert_firings (alert_id, ((context ->> 'report_date')));

-- RLS: users read their own; service_role writes everything.
ALTER TABLE public.user_alert_firings ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS user_alert_firings_own_read ON public.user_alert_firings;
CREATE POLICY user_alert_firings_own_read
  ON public.user_alert_firings
  FOR SELECT
  TO authenticated
  USING (user_id = auth.uid());
