-- Phase 3, step 3: per-user push notifications and nudges. Run once in the Supabase SQL Editor,
-- after schema_phase3.sql. Safe to re-run. Replaces the Apps Script "push" tab.

-- One row per device a person turned notifications on for. endpoint is the push service URL
-- (unique per device); sub is the full browser subscription (endpoint + keys) the backend sends to.
create table if not exists public.push_subs (
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  endpoint text not null,
  sub jsonb not null,
  updated_at timestamptz not null default now(),
  primary key (user_id, endpoint)
);
alter table public.push_subs enable row level security;

-- Per-user nudge settings. The app refreshes next_task after every sync (it knows the plan; the server
-- does not). tz decides when "8pm" is. last_nudge ("2026-10-28:1") stops a retried job sending twice.
create table if not exists public.nudge_prefs (
  user_id uuid primary key default auth.uid() references auth.users(id) on delete cascade,
  tz text not null default 'Asia/Dubai',
  next_task text not null default '',
  enabled boolean not null default true,
  last_nudge text not null default '',
  updated_at timestamptz not null default now()
);
alter table public.nudge_prefs enable row level security;

drop policy if exists "own push subs" on public.push_subs;
create policy "own push subs" on public.push_subs
  for all to authenticated
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));

drop policy if exists "own nudge prefs" on public.nudge_prefs;
create policy "own nudge prefs" on public.nudge_prefs
  for all to authenticated
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));
