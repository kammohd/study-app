-- Invite requests. Run once in the Supabase SQL Editor, after schema_leaderboard.sql. Safe to re-run.
-- Anyone may ask to be invited; only an admin decides. The table is closed to browsers entirely
-- (row-level security on, no policies): the public form goes through the API, which stores the request.
create table if not exists public.invite_requests (
  id bigint generated always as identity primary key,
  email text not null unique check (email = lower(email) and length(email) <= 200),
  name text not null default '' check (length(name) <= 80),
  note text not null default '' check (length(note) <= 300),
  status text not null default 'pending' check (status in ('pending', 'approved', 'rejected')),
  -- a keyed hash of the requester's network address, used only to limit abuse; the address itself is never stored
  ip_hash text not null default '',
  created_at timestamptz not null default now(),
  decided_at timestamptz,
  decided_by text not null default ''
);
create index if not exists invite_requests_status on public.invite_requests (status, created_at);
create index if not exists invite_requests_ip on public.invite_requests (ip_hash, created_at desc);
alter table public.invite_requests enable row level security;
