-- Leaderboard support. Run once in the Supabase SQL Editor, after schema_step3.sql. Safe to re-run.
-- Maps a login to an email and records when they were last in the app, so the admin screen can show
-- who is doing what. Closed to browsers entirely (RLS on, no policies): only the backend reads or writes it.
create table if not exists public.profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  email text not null check (email = lower(email)),
  last_seen timestamptz not null default now()
);
alter table public.profiles enable row level security;
