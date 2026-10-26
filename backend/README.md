# AI backend contract

The app talks to one Apps Script web app (`/exec`). The script is `Backend_Script` in the repo root (v10): paste all of it into the Apps Script editor, then Deploy → Manage deployments → edit → New version. Run `testGrounding` once from the editor to check it. This file is the contract the front end relies on, and the rules the prompts follow so questions stay true to their source.

Everything below is implemented in `Backend_Script` v10 and was tested against a mocked Apps Script with a fake model that invents quotes and gives wrong answers (all rejected).

## Requests the app sends (POST, JSON body)

| Body | When | Expected response |
|---|---|---|
| `{ticks:{id: date\|null}}` | tick a lesson, video or topic | stored |
| `{quiz:"next", week}` | Python & AI quiz | `{question, options[], correct}` |
| `{quiz:"next", week, track, topicId, topic, section, source}` | **topic quiz** (DSA, Salesforce) | `{topicId, question, options[], correct, explanation, evidence}` |
| `{challenge:"next", week, theme, done[], avoid[]}` | Python challenge | `{title, brief, scaffold, test, solution}` |
| `{challenge:"next", ..., track, topicId, source}` | **topic challenge** (DSA) | same, plus `topicId` echoed back |
| `{grade:{task, answer}}` / `{hint:{task, code}}` | AI feedback | `{ok, feedback}` / `{hint}` |
| `{attempt:{date, week, kind, ref, ok}}` | every answer | stored in the `attempts` tab |

For topic requests, `week` is the current plan week and the topic key (101+ for DSA, 201+ for Salesforce) is sent as `week` only inside `attempt` rows.

The app rejects any topic response whose `topicId` does not match the request, so an old backend shows "Backend update needed" instead of serving a wrong-topic question.

## Rules the prompts must enforce (anti-hallucination)

1. **Source only.** The prompt includes `source` (official exam objectives, or a vetted summary) and says: write the question from this text and nothing else. If the text does not support a question, return `{error:"insufficient source"}`.
2. **Evidence.** Every question returns `evidence`: a short quote from `source` that proves the correct answer. A question without a quote is discarded.
3. **No outside facts.** No version numbers, limits, prices or feature names that are not in `source`.
4. **One correct answer.** Exactly 4 options, `correct` is the 0-based index, distractors are plausible but clearly wrong given the source.
5. **Verify before serving.** A second call gets only `question` + `options` + `source` and must pick the answer blind; if it disagrees with `correct`, the question is discarded and regenerated (max 2 tries).
6. **Temperature low** (0 to 0.3) for generation and 0 for verification.
7. **Code challenges** are verified in the browser: the app runs the reference `solution` against the `test` in Pyodide and discards the challenge if it fails. Keep that. Tests must be plain `assert` lines with a message, no randomness, no I/O.
8. **Bank, don't regenerate forever.** Store accepted questions in `quizbank` and serve from it; a nightly refill must stop before the 6-minute Apps Script limit (generate a handful per run, keep a cursor).

## Sheet tabs

| Tab | Purpose |
|---|---|
| `progress` | lesson ticks (`w3t2`) plus any other id the app ticks (videos `w1v2`, topics `dsa.graphs.done`) |
| `quizbank` | Python & AI questions: date, week, question, options, correct, status, explanation |
| `topicbank` | DSA / Salesforce questions: date, topicId, question, options, correct, status, explanation, evidence |
| `challenges` | AI coding challenges; the `week` column holds the plan week or, for topics, the topicId |
| `attempts` | every right/wrong attempt |

## The refillBanks timeout (fixed in v10)

The nightly trigger was killed at the 6-minute limit. Calls to `gemini-flash-latest` "think" by default and nothing capped the run. v10 sends `thinkingBudget: 0` for batch jobs, checks a 4-minute budget before each step, and logs the time each step finished. If it ever fails again, open Executions in Apps Script and read the log lines.

## Week tracking

`stats()` now finds your week from the first unticked lesson row, not from a start date. Only `w<week>t<day>` rows count as lessons done.
