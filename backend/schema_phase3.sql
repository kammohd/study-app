-- Phase 3 additions. Run once in the Supabase SQL Editor, after schema.sql. Safe to re-run.
-- Replaces the Apps Script sheet tabs quizbank, topicbank and challenges.
-- Shape: the verified questions / challenges are SHARED (one bank for everyone); what each
-- person has answered or solved lives in a per-user state table, so users never affect each other.

-- A question may only enter the bank once per topic.
create unique index if not exists question_bank_unique
  on public.question_bank (track, topic_id, question);

-- Per-user progress on a banked question: served (shown, no answer yet), right (never again),
-- retry (answered wrong, resurfaces until right).
create table if not exists public.question_state (
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  question_id bigint not null references public.question_bank(id) on delete cascade,
  status text not null check (status in ('served', 'right', 'retry')),
  served_on date not null default current_date,
  primary key (user_id, question_id)
);
alter table public.question_state enable row level security;

-- Shared bank of coding challenges. key is the plan week ("w3") for Python or the topic id for DSA.
-- bad = the reference solution failed its own tests in the browser, so it is never served again.
create table if not exists public.challenge_bank (
  id bigint generated always as identity primary key,
  track text not null,
  key text not null,
  title text not null,
  brief text not null default '',
  scaffold text not null,
  test text not null,
  solution text not null,
  bad boolean not null default false,
  created_at timestamptz not null default now(),
  unique (track, key, title)
);
create index if not exists challenge_bank_key on public.challenge_bank (track, key);
alter table public.challenge_bank enable row level security;

-- Per-user: which challenges they solved, and the code that passed.
create table if not exists public.challenge_state (
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  challenge_id bigint not null references public.challenge_bank(id) on delete cascade,
  solved_on date not null default current_date,
  code text,
  primary key (user_id, challenge_id)
);
alter table public.challenge_state enable row level security;

-- Policies. The backend writes both banks with the secret key; users only read them.
drop policy if exists "own question state" on public.question_state;
create policy "own question state" on public.question_state
  for all to authenticated
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));

drop policy if exists "read challenges" on public.challenge_bank;
create policy "read challenges" on public.challenge_bank
  for select to authenticated using (not bad);

drop policy if exists "own challenge state" on public.challenge_state;
create policy "own challenge state" on public.challenge_state
  for all to authenticated
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));
