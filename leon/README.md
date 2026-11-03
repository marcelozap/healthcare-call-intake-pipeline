# Leon Care Outreach

A working service for bilingual Medicare outreach: a call becomes a database
row, rules decide who needs a person, and the result can be queried in plain
English.

**All data is synthetic.** No real member information exists anywhere in this
project. The clinic names are Leon's; everything attached to them is generated.

---

## Run it

```
pip install fastapi uvicorn
python run.py
```

Then open **http://localhost:8000** — and **/docs** for the interactive API.

`run.py` does the whole setup itself: generates the cohort, writes five messy
source extracts, runs the pipeline over them, loads SQLite, runs six data-quality
checks, and **refuses to serve if any of them fail**.

```
python run.py --check      build and verify, print the report, exit
python run.py --rebuild    force a full rebuild
python run.py --port 8080
pytest -q                  49 tests
python -m app.ml.unreachable   the prediction model and its evaluation
```

Optional extras:

```
pip install scikit-learn pandas          # the ML report
pip install python-multipart openai-whisper   # audio transcription
```

Set `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY`) in your environment to switch on
the model paths. Without one, everything falls back to deterministic engines and
the app still works end to end.

---

## What is actually real

| | |
|---|---|
| **SQLite database** | Real. Three tables, one view, indexes, constraints. Built by the pipeline. |
| **SQL** | Real. Every answer on the Ask panel is a parameterised query you can see. |
| **The pipeline** | Real. Five source formats conformed to one schema, with a quarantine table. |
| **The ML model** | Real scikit-learn, real train/test split. See the caveats it prints. |
| **The transcript** | Prewritten. Extraction is deterministic unless you supply an API key. |
| **Audio transcription** | Real Whisper, but optional and off by default. |
| **The members** | Synthetic. Generated from a fixed seed. |

The header of the running app shows which of these are live right now, so
"is this real?" is answered on screen rather than in conversation.

---

## How it fits together

```
 generate.py          feeds.py              pipeline.py           db.py
 ──────────           ────────              ───────────           ─────
 seeded cohort  ──▶   campaign.json    ──┐
                      dialler.csv      ──┤  conform ids
                      member_access.tsv──┼─▶ parse timestamps ──▶ SQLite
                      discharge.xml    ──┤  dedupe + quarantine    + view
                      portal.log       ──┘  count every rule
                                                                    │
                                            rules.py  ◀─────────────┤
                                            10 safety rules         │
                                                                    │
   extraction.py ──▶ validation.py                    ask.py  ◀─────┘
   transcript to      allowlist, types,               question to plan
   fields             confidence, span                to validated SQL
```

---

## Decisions worth defending

**The RNG is ported to Python from JavaScript** (`rng.py`), so the database and
the browser prototype generate the same cohort bit for bit. Both fingerprint to
`987aac1b8e9869b9`. The numbers cannot disagree.

**The pipeline round trip is the test.** Cohort → five messy files → conform →
identical cohort. If a conformance rule breaks, the fingerprint moves and
`test_round_trip_reproduces_the_cohort` fails. That makes the rules provably
correct rather than merely plausible.

**Sentinels become NULL, not zero.** The dialler writes `-1` for "not recorded".
Averaging it produces a number that is wrong and looks entirely reasonable.

**"Not discussed" is not "no".** `caregiver_contact` stays null when a call never
covered it. Collapsing unknown into false is the same class of error.

**The model never writes SQL.** It returns a query plan, which is checked against
an allowlist, and this code compiles parameterised SQL from allowlisted
identifiers. There is no prompt-injection path to arbitrary SQL because the model
was never able to emit SQL in the first place.

**Extraction is refused unless it can be traced.** Every model-extracted value
must carry a verbatim span that appears in the transcript. A fluent, plausible,
invented quote is the failure mode that matters, and checking the span character
by character is the only defence.

**There is no rule for "member speaks Spanish."** About three quarters of the
cohort does — that is the population, not a finding. Only a *mismatch* between
preferred language and the coordinator who called is worth flagging.

**Two different trust boundaries.** Extraction must see the transcript, so
content leaves the boundary and real audio would need a BAA-covered endpoint.
Question answering sends metric and dimension names only; no member row ever
leaves. Collapsing those two into "we use AI" is how records end up somewhere
nobody intended.

---

## Tests

```
pytest -q     # 49 tests
```

Organised by what could go wrong rather than by module. Each test's docstring
names the mistake it would catch — a desynced RNG, a leaked feature, a sentinel
averaged into a metric, a query narrowed until it identifies one person.

---

## Layout

```
run.py                  one command: build, verify, serve
requirements.txt
app/
  config.py             every setting, in one place
  console.py            makes output safe on a Windows code page
  rng.py                the JavaScript-compatible PRNG
  domain.py             the controlled vocabulary and the records
  generate.py           the synthetic cohort
  feeds.py              writes it out as five messy extracts
  pipeline.py           conforms those extracts back
  schema.sql            tables, constraints, and the semantic view
  db.py                 load and the data-quality checks
  rules.py              the ten safety rules
  extraction.py         transcript to fields
  validation.py         the gate in front of the model
  ask.py                question to validated SQL
  api.py                the HTTP service
  ml/unreachable.py     the prediction model and its evaluation
web/index.html          the frontend
tests/test_platform.py  49 tests
```
