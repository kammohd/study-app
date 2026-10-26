-- Study app schema for Supabase (Postgres). Run once in the SQL Editor.
-- Every table has row-level security (RLS) on. The browser uses the public key, so RLS is the
-- real wall: a signed-in user can only touch rows where user_id is their own id.
-- Tables with no policy (allowed_emails, ai_usage) are closed to the browser entirely;
-- only the FastAPI backend, using the secret key, can read or write them.

-- The invite list. Only these emails can create an account (see the trigger below) and use AI.
-- The admin screen adds and removes rows through the backend; is_admin unlocks that screen.
create table if not exists public.allowed_emails (
  email text primary key check (email = lower(email)),
  is_admin boolean not null default false,
  added_at timestamptz not null default now()
);
alter table public.allowed_emails enable row level security;

-- Invite-only sign-up: refuse to create any account whose email is not on the list.
-- Runs before every insert into auth.users, so it covers Google sign-in, magic links and anonymous
-- sign-ins (no email) alike. Raising an exception makes the sign-in fail and no account is created.
create or replace function public.enforce_invite_only()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if new.email is null
     or not exists (select 1 from public.allowed_emails where email = lower(new.email)) then
    raise exception 'Access is by invitation only';
  end if;
  return new;
end;
$$;
revoke execute on function public.enforce_invite_only() from public, anon, authenticated;

drop trigger if exists invite_only on auth.users;
create trigger invite_only
  before insert on auth.users
  for each row execute function public.enforce_invite_only();

-- This repo is public, so no email addresses live in this file. After running it, add yourself
-- as admin in a separate SQL Editor query (never saved to a file):
--   insert into public.allowed_emails (email, is_admin) values ('you@gmail.com', true);
-- Everyone else is added from the admin screen once it exists.

-- Lesson / video / topic ticks. id is the same key the app already uses: w3t2, w1v2, dsa.graphs.done.
create table if not exists public.ticks (
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  id text not null,
  done_on date not null default current_date,
  primary key (user_id, id)
);
alter table public.ticks enable row level security;

-- Weekly goal per track (py, dsa, sf, sql): how many lessons or topics per week.
create table if not exists public.goals (
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  track text not null check (track in ('py', 'dsa', 'sf', 'sql')),
  per_week int not null check (per_week between 0 and 50),
  primary key (user_id, track)
);
alter table public.goals enable row level security;

-- Every quiz / challenge answer, for review and spaced repetition later.
create table if not exists public.attempts (
  id bigint generated always as identity primary key,
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  at timestamptz not null default now(),
  track text not null,
  kind text not null check (kind in ('quiz', 'challenge')),
  ref text not null,
  ok boolean not null
);
create index if not exists attempts_user_at on public.attempts (user_id, at desc);
alter table public.attempts enable row level security;

-- Shared bank of verified questions (replaces the quizbank / topicbank sheet tabs).
-- Everyone signed in can read; only the backend writes.
create table if not exists public.question_bank (
  id bigint generated always as identity primary key,
  track text not null,
  topic_id text not null,
  question text not null,
  options jsonb not null check (jsonb_array_length(options) = 4),
  correct int not null check (correct between 0 and 3),
  explanation text,
  evidence text,
  created_at timestamptz not null default now()
);
create index if not exists question_bank_topic on public.question_bank (track, topic_id);
alter table public.question_bank enable row level security;

-- Per-user daily AI call counter, enforced by the backend.
create table if not exists public.ai_usage (
  user_id uuid not null references auth.users(id) on delete cascade,
  day date not null default current_date,
  calls int not null default 0,
  primary key (user_id, day)
);
alter table public.ai_usage enable row level security;

-- Policies. (select auth.uid()) is evaluated once per query instead of once per row.
create policy "own ticks" on public.ticks
  for all to authenticated
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));

create policy "own goals" on public.goals
  for all to authenticated
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));

create policy "own attempts" on public.attempts
  for all to authenticated
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));

create policy "read bank" on public.question_bank
  for select to authenticated using (true);
