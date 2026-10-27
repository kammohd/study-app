# Study app backend

A FastAPI service on Google Cloud Run, with Supabase (Postgres + Google sign-in) as the database. It
replaced the old Google Sheet and Apps Script backend.

```
phone / laptop (GitHub Pages, index.html)
   │  Google sign-in via Supabase  →  JWT
   ▼
FastAPI on Cloud Run ──► Supabase Postgres (row-level security on every table)
   │         ▲
   │         └── Cloud Scheduler, hourly: POST /internal/nudge  (shared secret header)
   └──► Gemini API  (key in Secret Manager)      └──► web-push services (Apple / Google / Mozilla / Windows)
```

## Who can do what

- **Sign-up is invite-only.** A database trigger on `auth.users` refuses any email not in `allowed_emails`.
- **Progress, goals, attempts, per-user question/challenge state, push devices and nudge settings** are read
  and written with the user's own token, so Postgres row-level security keeps people apart.
- **AI routes and push registration** also require the email to be on the invite list, and AI calls count
  against a daily per-user limit (`DAILY_AI_LIMIT`, default 20).
- **The shared question and challenge banks** are written only by the backend (secret key) and are read-only
  to users.
- **`/internal/nudge`** is not for browsers: it needs the `x-cron-token` header and is closed if no token is set.

## Endpoints

| Route | Purpose |
|---|---|
| `GET /health` | liveness |
| `GET /me` | who am I, am I allowed AI, am I admin |
| `GET/POST /ticks`, `GET/PUT /goals`, `POST /attempts` | progress |
| `POST /quiz/next`, `POST /quiz/result` | serve a banked question, or generate and verify a batch when the bank is empty; wrong answers come back until right |
| `POST /challenge/next`, `/challenge/solved`, `/challenge/bad` | coding challenges; the app runs each reference solution in the browser and reports broken ones |
| `POST /ai/hint`, `POST /ai/grade` | AI feedback |
| `GET /ai/quota` | calls used and left today |
| `GET /quote` | one motivational line per person, cached 6 hours |
| `POST /push/subscribe`, `/push/unsubscribe` | register or remove a device (only real push-service hosts are accepted) |
| `PUT /me/nudge` | the app's timezone and this week's unmet goals, which the nudge is written from |
| `POST /internal/nudge` | hourly job: sends the 8pm kickoff and 9pm follow-up at each person's local time |
| `GET/POST/DELETE /admin/emails` | invite list (admins only) |

Errors from generation come back as `200` with an `{"error": ...}` field; quota is `429`; the model being down is `503`.

## Question quality (anti-hallucination)

A question reaches the bank only if it passes every gate in `app/quizgen.py`:

1. exactly 4 distinct options and a valid answer index;
2. topic questions carry an `evidence` quote, and 80% of its word-triples must appear in the supplied source;
3. a second, blind Gemini call answers it without seeing the claimed answer, and must agree and say it is sure;
4. options are shuffled so the right answer is not always first.

A batch that loses every question is retried once under the same quota charge. Coding challenges are
checked a second time in the browser (Pyodide) before they are shown.

## Nudges

Cloud Scheduler calls `/internal/nudge` every hour. For each person with a registered device the server
works out their local time: 8pm sends the kickoff, 9pm sends a follow-up only if they ticked nothing that
day, Sundays rest. The text is written by the fast Gemini model from the summary the app keeps in
`nudge_prefs` (tracks short of their weekly goal, with the next step in each). Nothing is sent when every
goal is met. A device the push service reports as gone is deleted. `last_nudge` prevents a retried job
sending twice.

## Configuration

Environment variables (on Cloud Run: plain variables, or Secret Manager secrets for the sensitive ones):

| Variable | Secret? | Meaning |
|---|---|---|
| `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` | no | public by design |
| `SUPABASE_SECRET_KEY` | **yes** | bypasses row-level security; backend only |
| `GEMINI_API_KEY` | **yes** | Secret Manager `gemini-api-key` |
| `VAPID_PRIVATE_KEY` | **yes** | PEM, Secret Manager `vapid-private-key`; the matching public key is in `index.html` |
| `CRON_TOKEN` | **yes** | Secret Manager `cron-token`; also in the Scheduler job's header |
| `VAPID_SUBJECT` | no | `mailto:` contact for push services |
| `DAILY_AI_LIMIT`, `CORS_ORIGINS` | no | per-user AI cap; allowed web origin |

Never commit `.env` or paste a secret in a chat or an issue.

## Database

Run once in the Supabase SQL editor, in order: `schema.sql`, `schema_phase3.sql` (banks and per-user
state), `schema_step3.sql` (push devices and nudge settings). All three are safe to re-run.

## Run and test locally

```
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m pytest -q          # no network: the database, Gemini and push services are fakes
.venv/Scripts/python -m uvicorn app.main:app --reload
```

## Deploy

```
gcloud run deploy study-app-api --source=. --region=us-central1 --project=<project> \
  --update-secrets=GEMINI_API_KEY=gemini-api-key:latest,VAPID_PRIVATE_KEY=vapid-private-key:latest,CRON_TOKEN=cron-token:latest
```

The service runs under its own service account with access to those secrets only, at one instance maximum.
A budget alert feeds the kill switch in `../killswitch`, which disables billing if spend passes the cap.

The scheduler job (one-off setup; `gcloud` prints header values, so keep its output out of chat and logs):

```
gcloud scheduler jobs create http nudge-hourly --location=us-central1 --schedule="0 * * * *" --time-zone=UTC \
  --uri=<service-url>/internal/nudge --http-method=POST \
  --headers="Content-Type=application/json,x-cron-token=<token>" --message-body="{}"
```

When updating its headers, pass `Content-Type=application/json` again, or `gcloud` resets it and the endpoint
answers `422`. To rotate the token: add a secret version, update the job header, redeploy the service,
then disable the old version.
