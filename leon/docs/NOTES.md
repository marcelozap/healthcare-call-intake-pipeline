# Study notes

Two parts.

**Part 1** walks the system file by file — what each one does and the one thing
worth saying about it.

**Part 2** is data science fundamentals, explained from scratch, using the actual
numbers this project produces. Every figure below came out of a real run, so you
can re-derive any of them with `python -m app.ml.unreachable`.

Written for reading with a pen in hand. Take the definitions in your own words —
you will remember those, not mine.

---
---

# PART 1 — The system

## The shape of it

```
generate.py  →  feeds.py  →  pipeline.py  →  db.py  →  rules.py
 make data      break it     fix it          store it   judge it
                                                          ↓
                          extraction.py → validation.py   ↓
                          call → fields    check them     ↓
                                                          ↓
                                              ask.py ← ────┘
                                          question → SQL → answer
```

One sentence for the whole thing: **messy records from five systems become one
trustworthy table, rules decide who needs a human, and the result can be
questioned in plain English.**

---

## File by file

### `config.py`
Every setting in one place: database path, seed, confidence threshold, minimum
group size.

**Why it matters:** when a reviewer asks "where is that 0.80 threshold decided?"
there is one answer, not a search.

### `rng.py` — the reproducible random number generator
A port of JavaScript's `mulberry32` into Python.

**Why:** the browser prototype generates its cohort in JavaScript. If Python
generated a *different* cohort, the app and the database would disagree and every
number would be unverifiable. Same seed, same sequence, same 420 members.

**The hard part:** JavaScript coerces to signed 32-bit integers; Python integers
are unbounded. Every step needs masking or the sequences diverge on the first
call. Both fingerprint to `987aac1b8e9869b9`.

> **Say:** "The database and the demo generate the same data by construction, not
> by coincidence — and there's a hash that proves it."

### `domain.py` — the vocabulary
The controlled lists. If a value isn't here, it doesn't exist.

**The key decision:** `Outcome` has **eight** values, not two. Most diallers
record connected/not-connected, which collapses six situations needing six
different responses. A voicemail isn't a wrong number. A caregiver asking you to
call back isn't a refusal. "Needs a Spanish callback" isn't a failed contact at
all — it's a successful contact routed to the wrong queue.

### `generate.py` — the synthetic cohort
420 members, 995 attempts.

**One deliberate choice worth defending:** language misrouting is a property of
the *member*, not of each call. Being on the wrong queue is a configuration
mistake, so it affects every attempt. Modelling it per-call washes the effect out
to nothing and hides the finding.

### `feeds.py` — deliberately breaking the data
Writes the cohort out as five extracts in five formats: JSON, CSV, TSV, XML, and
an application log. Reintroduces two member-ID shapes, a timestamp split across
two columns, epoch seconds, booleans as `1`/`Y`/`true`, duplicate rows, blank
keys, `-1` sentinels.

**Why bother:** a pipeline that only sees clean input proves nothing.

### `pipeline.py` — putting it back together
Four conformance functions. **Learn these four — they are the most likely thing
you'll be asked about.**

| Function | The problem it solves |
|---|---|
| `conform_member_id` | Same person as `LMC-200100`, `200100`, `lmc200100`. Joins silently miss rows until this is fixed in one place. |
| `conform_timestamp` | Three conventions, one of them split across two columns, none stating a time zone. |
| `conform_outcome` | Vendor codes → closed set. Unrecognised becomes `unknown`, never guessed. A rising `unknown` count is the alarm that a vendor changed something. |
| `clean_talk_seconds` | `-1` means "not recorded". **Averaging it produces a number that is wrong and looks entirely plausible.** NULL is the honest answer. |

**The property that makes this provable:** cohort → messy files → conform →
*identical* cohort. Same fingerprint both ends. If a rule breaks, the hash moves
and `test_round_trip_reproduces_the_cohort` fails.

> **Say:** "The round trip is the test. That makes the conformance rules provably
> correct rather than just plausible."

### `schema.sql` + `db.py` — storage and self-checking
Three tables, one view, constraints, indexes.

`v_outreach_member` is the **semantic layer** — derived columns defined once, so
`transport_gap` means one thing everywhere.

Six data-quality checks run after every load and write to `dq_result`:
no orphan attempts, denormalised counts matching their source, nobody "reached"
without a reached attempt, no dialled attempts for members with no phone, and the
view agreeing with the rule engine.

> **Say:** "The pipeline refuses to serve on data that failed its own checks."

### `rules.py` — the ten safety rules
Each returns either `None` or a sentence explaining itself.

**Two decisions to defend:**

1. **Rules return an explanation, not a boolean.** A queue that says "flagged"
   without saying why gets ignored within a fortnight.
2. **There is no rule for "member speaks Spanish."** About three quarters of the
   cohort does — that's the population, not a finding. Only a *mismatch* between
   preferred language and the coordinator who called is worth flagging.

Firing rates from the real run:

| Rule | Fires | Share |
|---|---|---|
| language_mismatch | 78 | 18.6% |
| discharge_no_followup | 28 | 6.7% |
| wrong_number | 24 | 5.7% |
| transport_unarranged | 22 | 5.2% |
| interpreter_unmet | 29 | 6.9% |
| elderly_no_contact | 12 | 2.9% |

> **Say:** "A rule that never fires is unnecessary. A rule that fires on most of
> your cohort is a default, not a safety control — it'll be ignored in a fortnight.
> Both failure modes are visible on the rules screen."

### `extraction.py` — transcript to fields
Two engines, one output shape. Deterministic patterns always work; the model
engine runs when a key is configured and falls back automatically.

**Three things to point at:**
- `needs_interpreter` scores **0.72** — inferred, not stated. She said she prefers
  Spanish, never said "interpreter". Below the 0.80 threshold, so it's **held**,
  not committed.
- `medication_timing` is **explicitly unknown** — she said she doesn't remember.
  That's a fact worth storing, different from blank.
- `caregiver_contact` is **not discussed** — stays null. Collapsing "we didn't
  ask" into "no" is the same error class as averaging a sentinel.

### `validation.py` — the gate
Four checks, in order of importance:

1. **Allowlist** — a field not in the contract is refused before its value is read.
   Stops a model inventing a `diagnosis` field.
2. **Type** — `"probably"` is not a boolean. A permissive parser coerces it
   silently and fabricates an answer the model declined to give.
3. **Confidence in range** — a score of `7` on a 0–1 scale means the model isn't
   honouring the contract. Clamping to 1 is *worse* than refusing: it normalises
   nonsense into looking certain.
4. **The quote exists in the transcript** — the anti-fabrication control.

Against a deliberately hostile response: **2 accepted, 7 refused.**

> **Say:** "A model can produce a quote that's fluent, plausible, and entirely
> invented. Checking the span character by character against the source is the
> only defence."

### `ask.py` — question to SQL
The model returns a **query plan**, never SQL. The plan is allowlisted, then this
module compiles parameterised SQL from allowlisted identifiers.

**The security argument in one line:** because the model can't emit SQL, there is
no prompt-injection path to arbitrary SQL. Feeding `'; DROP TABLE
outreach_member;--` as a filter value gets refused at the allowlist — it never
reaches the database.

### `api.py` — the service
Eight endpoints, database opened **read-only** for everything served.

Every response carries `engine`, `fallback_reason` and `rejected`, so a caller can
always tell whether a model was involved and what got refused.

### The two trust boundaries — **memorise this one**

> "Extraction has to see the transcript, so content leaves the boundary — for real
> patient audio that needs a BAA-covered endpoint, never a public API.
> Question-answering sends metric and dimension *names* only and gets back a plan;
> no member row ever leaves. Same model, same key, completely different exposure.
> Collapsing those two into 'we use AI' is how organisations end up sending records
> somewhere they didn't intend to."

---
---

# PART 2 — Data science fundamentals

Using the real output of `python -m app.ml.unreachable`.

**The task:** predict which members the campaign will fail to reach, using only
information known *before* outreach begins.

---

## 1. The split

**Definition.** You divide data into a training set (the model learns from it) and
a test set (kept hidden, used once, to see how it does on data it hasn't seen).

**Here:** 420 members → **315 train / 105 test** (75/25).

**Stratified** means both halves keep the same proportion of the thing you're
predicting — 36.9% "not reached" in each. Without it a small test set can end up
with barely any positives and the score becomes noise.

**Why it exists:** a model can memorise. Testing on data it trained on measures
memory, not skill.

> ⚠️ **For your trading system:** never split time-series randomly. A random split
> lets the model see the future. You need walk-forward or purged splits.

---

## 2. The positive class and why baselines matter

**Here:** 36.9% of members were not reached. So 63.1% were.

**The trap:** a model that always predicts "reached" is **63% accurate** and
completely useless. Accuracy alone can't tell you whether a model is good.

**Fix:** always compare against baselines. This project uses two:

| Baseline | Accuracy | ROC-AUC | Recall |
|---|---|---|---|
| Majority class (always "reached") | 0.629 | **0.500** | 0.000 |
| One rule: flag everyone misrouted | 0.762 | 0.679 | 0.359 |
| **Logistic regression** | 0.771 | **0.759** | 0.410 |

**AUC of 0.500 is exactly random.** That's what the majority-class baseline scores
despite looking 63% accurate — which is the point of including it.

> **Say:** "If a model can't beat a single if-statement, it shouldn't be deployed."

---

## 3. The confusion matrix

Every prediction falls in one of four boxes.

|  | predicted **reached** | predicted **not reached** |
|---|---|---|
| **actually reached** | 65 — true negative | 1 — **false positive** |
| **actually not reached** | 23 — **false negative** | 16 — true positive |

- **False positive:** we said they'd be hard to reach, they weren't. Cost: a
  wasted phone call.
- **False negative:** we said they'd be fine, they were never reached. Cost: a
  member falls through.

**Here false negatives (23) massively outnumber false positives (1).** The model is
very cautious — when it flags someone it's almost always right, but it misses most
of the people it should catch.

**Which error is worse is a business question, not a modelling one.** For outreach,
a wasted call is cheap and a missed member isn't — so you'd probably want to trade
some precision for recall by lowering the threshold.

---

## 4. Precision, recall, F1

Draw these three from the table above.

**Precision** = of everyone we flagged, how many were real?
`16 / (16 + 1)` = **0.941**

**Recall** = of everyone we should have flagged, how many did we catch?
`16 / (16 + 23)` = **0.410**

**F1** = harmonic mean of the two = **0.571**. One number, but it hides which side
you're failing on — always look at precision and recall separately too.

**The trade-off:** they move in opposite directions. Flag more people and recall
rises while precision falls. Flag fewer and the reverse. The threshold you choose
decides where you sit, and `0.5` is a default, not an answer.

**Which one you care about depends on the cost:**
- Cancer screening → **recall**. Missing a case is catastrophic; a false alarm is a follow-up test.
- Spam filtering → **precision**. A missed spam is an annoyance; a real email in the spam folder is a disaster.
- This outreach → probably **recall**, because a phone call is cheap.

---

## 5. ROC-AUC and PR-AUC

A model outputs a **probability**, not a yes/no. Applying a threshold turns it
into a decision. These two metrics score the probabilities across *every possible
threshold*, so they measure the model rather than your threshold choice.

**ROC-AUC** — probability that a randomly chosen positive gets a higher score than
a randomly chosen negative.
- 0.5 = random. 1.0 = perfect. **Here: 0.759.**

**PR-AUC** (precision-recall) — **here: 0.734.**
- Better than ROC-AUC when the classes are imbalanced, because it ignores true
  negatives. If 99% of your data is negative, ROC-AUC flatters a model; PR-AUC
  doesn't.
- Its floor is the positive rate (0.369 here), not 0.5.

---

## 6. Cross-validation — and the honest number

**Definition.** Split the training data 5 ways. Train on 4, test on 1. Rotate.
Average the five scores.

**Why:** a single split might be lucky. Five gives you a mean *and* a spread.

**Here:**
```
5-fold CV on training:  ROC-AUC 0.697 +/- 0.050
Held-out test split:    ROC-AUC 0.759  (+0.062 vs CV)
```

The test split beat CV by more than one standard deviation. **That means it was a
favourable draw, and 0.697 is the more honest number to quote.**

The report says so out loud rather than quoting the flattering one.

> **Say:** "On a cohort this size I'd quote the cross-validation figure. Reporting
> only the number that looks best is how a model gets deployed on something nobody
> can reproduce."

---

## 7. Calibration (Brier score)

**The distinction:** a model can *rank* perfectly and still be *wrong about the
numbers*. If it says "70% likely" for a group, roughly 70% of them should actually
be positive. Otherwise the probability is meaningless and you can't pick a
threshold from it.

**Brier score** = mean squared error of the probabilities. **Lower is better.**

| | Brier |
|---|---|
| Majority baseline | 0.371 |
| Misrouted-only rule | 0.238 |
| **Logistic regression** | **0.164** |
| Gradient boosting | 0.178 |

Logistic regression is both more accurate *and* better calibrated here.

---

## 8. Overfitting and underfitting

**Overfitting** — the model memorised noise. Great on training data, poor on new
data. Tell-tale: a big gap between train and test scores.

**Underfitting** — too simple to capture the real pattern. Poor on both.

**Here:** gradient boosting (0.737) scored *worse* than logistic regression
(0.759). A more powerful model doing worse is the classic sign it overfit — 315
rows is not much to learn from, and the extra flexibility found noise.

> **Say:** "The simpler model won. On 315 rows that's what I'd expect, and it's
> also the one I can explain to a care manager."

---

## 9. Leakage — the one that ruins everything

**Definition.** A feature that contains information you wouldn't have at
prediction time. The model looks brilliant and is worthless in production.

**Here it would be easy to do by accident.** `attempt_count` correlates strongly
with being reached — but you only know it *after* the outreach you're trying to
predict. Using it would give a near-perfect model that could never be deployed.

**The guard in this project:**
```python
FORBIDDEN = {"attempt_count", "reached", "outcome",
             "needs_review", "flag_count", "high_flags"}
assert not (FORBIDDEN & set(FEATURES))
```
Plus a test that fails if one creeps back in.

**The borderline case worth discussing:** `misrouted` *is* included, and it's
legitimate — which language queue a member sits on is a routing configuration
known in advance, not an outcome.

> ⚠️ **For trading:** leakage is the number one killer. Restated fundamentals,
> survivorship-biased universes, and using the day's close to decide the day's
> trade are all leakage.

---

## 10. Feature importance

For logistic regression, each feature gets a **coefficient**. Exponentiate it and
you get an **odds ratio** — how much the odds multiply when that feature is true.

| Feature | Coefficient | Odds ratio | Meaning |
|---|---|---|---|
| `misrouted` | +3.47 | **×32.0** | wrong language queue → 32× the odds of never being reached |
| `has_phone` | −2.14 | ×0.12 | having a number → about a tenth the odds |
| `clinic_Westchester` | −0.77 | ×0.46 | |
| `outreach_reason_Post-discharge` | +0.45 | ×1.57 | |

**Why this matters more than the AUC:** the model independently rediscovered what
the `language_mismatch` safety rule already flags. That's a sanity check — the
model found a real mechanism, not noise.

> **Say:** "The top feature is the one my rules already catch. That's how I know
> it's learning something real."

---

## 11. Precision@K — the number that decides deployment

**The problem with all the above:** it assumes you act on every prediction. You
can't. The team has finite capacity.

**Precision@K** — if you can only work K cases today, take the K highest-scoring
and ask how many were real.

| K | Precision@K | Real cases found |
|---|---|---|
| 10 | **100%** | 10 |
| 25 | 72% | 18 |
| 50 | 52% | 26 |
| 100 | 39% | 39 |

**Random selection finds 37%.** So at K=50 the model roughly **doubles** the hit
rate for the same effort.

> **Say:** "Accuracy over the whole cohort doesn't decide anything, because
> capacity is finite. The only question is whether the top of the list is better
> than picking at random — and at fifty cases it's about twice as good."

---

## 12. What the model does *not* show

The section at the bottom of the report. **Know this cold, because it's the
question a data scientist will actually ask.**

1. **The data is synthetic and I wrote the generator.** The model is partly
   learning my own simulation. The AUC is a demonstration of method, not evidence
   about Leon's members.

2. **On real data, modelling isn't the first step.** The first step is
   establishing whether "reached" even means the same thing across every system
   feeding it — which, on the evidence of the dialler export in this project, it
   doesn't.

3. **A model only earns its keep if acting on it is cheaper than the outcome it
   predicts.** Predicting an unreachable member is worth something because the
   response is a phone call. That calculation belongs *before* anyone builds it.

> **Say:** "I'd rather show you a model I can explain the limits of than a number
> I can't defend."

---
---

# Quick reference

| Metric | Value | In one line |
|---|---|---|
| Cohort | 420 members, 995 attempts | |
| Positive class | 36.9% not reached | the base rate to beat |
| Train / test | 315 / 105 | stratified |
| Features | 12 | all pre-outreach |
| Majority baseline AUC | 0.500 | exactly random |
| One-rule baseline AUC | 0.679 | the bar to clear |
| **Logistic ROC-AUC** | **0.759 test / 0.697 CV** | quote the CV number |
| PR-AUC | 0.734 | better for imbalance |
| Precision / recall | 0.941 / 0.410 | cautious: rarely wrong, misses a lot |
| Brier | 0.164 | probabilities are honest |
| Top feature | `misrouted`, ×32 odds | matches the rules engine |
| Precision@50 | 52% vs 37% random | ~2× better use of capacity |
| Tests | 49 pytest, 32 doctests | |

## Commands

```
python run.py --check              build, verify, report, exit
python run.py                      serve at localhost:8000
pytest -q                          49 tests
python -m app.ml.unreachable       the full ML report
python -m doctest app/pipeline.py -v    the conformance rules
```
