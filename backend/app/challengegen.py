"""Write small Python coding challenges with Gemini. Ported from the Apps Script genChallengeBatch_.

The server cannot run the code safely, so correctness is checked twice elsewhere: the app runs each
reference solution against its own asserts in the browser before showing it, and reports broken ones
back (POST /challenge/bad) so they are never served again.
"""
from .quizgen import Ask, parse_json

BATCH = 5
FIELDS = ("title", "scaffold", "test", "solution")


async def challenge_batch(ask: Ask, *, topic_mode: bool, theme: str, week: int | None, recent: str,
                          source: str, avoid: list[str]) -> list[dict]:
    if topic_mode:
        who = "a learner practising data structures and algorithms in Python"
        scope = f"Topic: {theme}.\nSOURCE for the topic (stay inside it):\n{source[:2000]}\n"
    else:
        who = f"a Salesforce support engineer learning AI engineering, week {week or '?'} of 16"
        scope = f"Week theme: {theme}.\nRecently completed lessons: {recent or '(none yet, stick to the week theme basics)'}\n"
    prompt = (f"Generate {BATCH} DISTINCT small Python coding exercises as a strict JSON array for a study app.\n"
              f"Student: {who}.\n{scope}"
              f"Do NOT repeat these existing exercise titles/ideas: {'; '.join(avoid[-60:]) or '(none)'}"
              "\n\nHARD RULES for EVERY exercise:\n"
              "- Pure Python 3 standard library. No input(), no network, no pip installs, no randomness, no time.\n"
              "- Asks the student to write ONE small function (5-15 lines); helper classes such as ListNode or "
              "TreeNode may be given in the scaffold.\n"
              "- brief: state the exact input and output types and what to return for edge cases (empty input, single item).\n"
              "- scaffold: the function signature with a short hint comment, body 'return ...' (or a placeholder), "
              "and a print() demo call.\n"
              "- test: 3-5 plain assert statements with short helpful failure messages, including at least one edge "
              "case. Deterministic, no floats that round ambiguously, and only asserts things the brief says.\n"
              "- solution: the complete correct code that makes EVERY assert pass. Mentally run each assert against "
              "it before answering.\n"
              "- Difficulty: solvable in 3-8 minutes on a phone. Vary the concepts across the batch.\n\n"
              "Reply ONLY with a JSON array, no markdown fences:\n"
              '[{"title": "3-5 words", "brief": "1-2 sentences", "scaffold": "...", "test": "...", "solution": "..."}, ...]')
    items = parse_json(await ask(prompt, temperature=0.4, json=True))
    if not isinstance(items, list):
        items = [items]
    seen = {t.lower() for t in avoid}
    out = []
    for j in items:
        if (isinstance(j, dict) and all(isinstance(j.get(f), str) and j[f].strip() for f in FIELDS)
                and "assert " in j["test"] and j["title"].lower() not in seen):
            seen.add(j["title"].lower())
            out.append({**{f: j[f] for f in FIELDS}, "brief": str(j.get("brief") or "")})
    return out
