# study-app

A phone-first study app for one learner moving into technical consulting and AI engineering. It is a single installable web page (PWA) with no build step.

**Live:** https://kammohd.github.io/study-app/ (GitHub Pages, `main` branch)

## What's in it

| Track | Source | What you get |
|---|---|---|
| Python & AI | the 16-week plan inside `index.html` | daily lesson, day-matched videos, in-app Python exercises, AI quiz |
| DSA | `content/dsa-videos.json` | 16 pattern topics: short videos, a vetted summary, a coding exercise with hidden tests, AI quiz and AI challenges |
| Salesforce Admin | `content/salesforce-admin-videos.json` | 8 sections of the official exam outline: videos, the exam objectives, AI quiz |

Python runs in the browser with Pyodide, so coding practice works on a phone. The editor has a symbol bar for `: ( ) [ ] { } " ' = # _`.

## Content

Content lives in `content/*.json`, not in the code. Adding a topic is a data change:

```json
"two-pointers": {
  "title": "Two pointers",
  "videos": [["Video title", "<11-char YouTube id>", "5:43", "Channel"]],
  "focus": "A short, factual summary. AI questions are written from this text only.",
  "exercises": [{ "t": "Title", "q": "What to do", "c": "starter code", "test": "assert ..." }]
}
```

Rules for content:
- Every YouTube id must resolve. Check with `https://www.youtube.com/oembed?format=json&url=https://www.youtube.com/watch?v=<id>`.
- Every exercise must fail on its starter code and pass with a reference solution. Do not ship solutions.
- Salesforce sections carry the official `objectives`; re-check them against the current exam guide each release (`content/research/`).

To add a track, add it to `TRACKS` in `index.html` and drop its JSON in `content/`.

## Sync and AI backend

Progress syncs to a Google Sheet through an Apps Script web app (URL pasted in the app's Sync tab, never committed). The same backend writes AI quiz questions and challenges. The contract the app expects is in [`backend/README.md`](backend/README.md).

## Notifications

`notify.py` runs from `.github/workflows/nudge.yml` and sends a web-push nudge. Repo secrets: `EXEC_URL`, `VAPID_PRIVATE_KEY`, optional `VAPID_SUB` (a `mailto:` address).

## Run locally

```
python -m http.server 8765
```

Open http://localhost:8765. The service worker and push need HTTPS, so test those on the Pages site.
