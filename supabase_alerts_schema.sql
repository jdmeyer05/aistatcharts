-- Smart Money Alerts — user subscription table.
-- Run once in the Supabase SQL editor (or via CLI).
-- Safe to re-run; everything is IF NOT EXISTS / OR REPLACE.

create table if not exists public.user_alerts (
    id uuid primary key default gen_random_uuid(),
    user_email text not null,
    alert_type text not null check (alert_type in ('fund', 'ticker', 'politician', 'activist', 'keyword')),
    target text not null,
    label text,
    channels jsonb not null default '["email"]'::jsonb,
    active boolean not null default true,
    created_at timestamptz not null default now(),
    last_fired_at timestamptz
);

create index if not exists user_alerts_user_email_idx on public.user_alerts (user_email);
create index if not exists user_alerts_active_type_idx on public.user_alerts (active, alert_type);

-- Row Level Security: users see only their own alerts.
-- Reads/writes from our FastAPI service use the service_role key (bypasses RLS)
-- so backend endpoints continue to work regardless of policy.
alter table public.user_alerts enable row level security;

drop policy if exists "Users see their own alerts" on public.user_alerts;
create policy "Users see their own alerts"
    on public.user_alerts for select
    using (auth.jwt() ->> 'email' = user_email);

drop policy if exists "Users insert their own alerts" on public.user_alerts;
create policy "Users insert their own alerts"
    on public.user_alerts for insert
    with check (auth.jwt() ->> 'email' = user_email);

drop policy if exists "Users delete their own alerts" on public.user_alerts;
create policy "Users delete their own alerts"
    on public.user_alerts for delete
    using (auth.jwt() ->> 'email' = user_email);

drop policy if exists "Users update their own alerts" on public.user_alerts;
create policy "Users update their own alerts"
    on public.user_alerts for update
    using (auth.jwt() ->> 'email' = user_email);
