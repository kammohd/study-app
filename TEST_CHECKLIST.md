# AI Study: test checklist

Run this on the live site after each deploy. Use **two real Google accounts** in two separate browser
profiles (or a phone plus a laptop): **User A** is the admin, **User B** is an invited test user.
Tick each box; write the failing step number and what you saw if anything fails.

## 0. Before you start

- [ ] Latest `main` is pushed and GitHub Pages has rebuilt (the Me tab footer shows the new build).
- [ ] Backend is on the newest Cloud Run revision (`curl <API>/health` returns `{"ok":true}`).
- [ ] Google OAuth: if the consent screen is still in **Testing**, add User B under *Audience → Test users*;
      otherwise User B is refused by Google before our invite list is even checked.
- [ ] User A signs in, opens **Me → Admin**, and invites User B's email.

## 1. Sign-in and access

- [ ] A: *Sign in with Google* works and the Me tab shows the account and "cloud synced".
- [ ] B (invited): signs in, lands signed in, no error.
- [ ] C (any other Google account, **not** invited): sign-in is refused with "Access is by invitation only".
- [ ] Signed out: the app still opens and shows the offline quiz set, with no AI buttons doing anything odd.
- [ ] B does **not** see the Admin card. A does.
- [ ] A removes B in Admin; B's next sign-in is refused. Re-invite B before continuing.

## 2. Progress sync (each user separately)

- [ ] A ticks a lesson; reload; still ticked.
- [ ] A signs in on a second device; the tick is there.
- [ ] A changes a weekly goal; it shows on the second device.
- [ ] B's account starts empty (A's ticks and goals are not visible to B).
- [ ] B ticks something; A does not see it.

## 3. Python quiz (AI)

- [ ] Open a Python week topic: a question appears within ~10 s (first one may take longer: the bank is empty).
- [ ] Options are shuffled (the right answer is not always first across 5 questions).
- [ ] Answer wrong: the right one is highlighted. Tap **Next**, go through the bank, and the wrong question
      comes back later.
- [ ] Answer right: that question never comes back for this user.
- [ ] B opens the same week: gets questions **instantly** from the shared bank (no AI wait) and A's right/wrong
      history does not affect what B sees.

## 4. Topic quizzes (AI, from source text)

- [ ] DSA topic: *Quiz me on this topic* returns a question with "Source: …" text shown after answering.
- [ ] Salesforce topic: a scenario question, with an exam objective as its source line.
- [ ] SQL topic (if it has a quiz button): same behaviour.
- [ ] Open a different topic: the question is about **that** topic, not the previous one.

## 5. Coding challenges (AI)

- [ ] Python week: *New AI challenge* shows "Loading Python…", then "Writing you a challenge…", then a new
      exercise appears in the Practice list.
- [ ] Solve it in the editor; it turns to "solved". Tap *New AI challenge* again: a different one.
- [ ] DSA topic: challenge works the same way.
- [ ] B asks for a challenge on the same week and is offered the one A already solved (solved state is per user).

## 6. Hint and grading (AI)

- [ ] Hint on a Python exercise returns 1-2 sentences and never the full solution.
- [ ] A free-text (🧠) exercise: a short or placeholder answer is **not** passed; a real answer is.

## 7. Limits and failure behaviour

- [ ] `GET /ai/quota` (or the Me tab, if shown) reports the daily limit and what is left, per user.
- [ ] Hitting the daily limit shows "Daily AI limit … reached" (not a blank or a spinner). A's usage
      does not reduce B's.
- [ ] Airplane mode: tapping an AI button shows an error message and the app stays usable.
- [ ] Tapping *Next question* quickly 5 times does not produce duplicate or stuck questions.

## 8. Data checks (Supabase SQL editor, as the project owner)

```sql
select count(*) from question_bank;                            -- grows only when a bank was empty
select user_id, status, count(*) from question_state group by 1, 2;   -- one set of rows per user
select user_id, day, calls from ai_usage order by day desc;    -- per-user counters
select track, key, title, bad from challenge_bank order by id desc limit 10;
select user_id, count(*) from ticks group by 1;                -- A and B separate
```

- [ ] `question_state` and `ticks` rows are split by user; no row has a null `user_id`.
- [ ] No two `question_bank` rows share the same `(track, topic_id, question)`.

## Known gaps (not expected to work for B yet)

These still run through the owner's Apps Script and are migrated in steps 3 and 4 of phase 3:

- Daily motivational quote.
- Push-notification nudges (owner only).
- Old Google Sheet tick sync and the "Google Sheets sync" card on the Me tab.

Phase 3 is **done** only when this checklist passes for both users and the known gaps above are gone.
