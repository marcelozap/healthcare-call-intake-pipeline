# Pen And Paper Data Science Notes

## The Big Picture

This project is a synthetic healthcare outreach platform. It demonstrates the full path from messy operational data to a usable AI/data product.

Write this first:

```text
Raw data is not the product. The product is trusted, structured, validated data that can support rules, analytics, ML, and AI workflows.
```

## What The App Does

The app simulates bilingual Medicare outreach for a clinic network.

Plain English:

```text
A call or outreach attempt becomes a database row. Data-quality rules decide what is trustworthy. Safety rules decide who needs human follow-up. The app lets users query the result in plain English without exposing member rows to the model.
```

Main flow:

```text
Synthetic cohort
  -> messy source extracts
  -> conformance pipeline
  -> SQLite database
  -> quality checks
  -> rules engine
  -> API + frontend
  -> optional ML model / optional LLM paths
```

## Why The Commands Matter

### `pip install fastapi uvicorn scikit-learn pandas pytest`

Installs the libraries needed to run the service, tests, and ML report.

Write:

```text
FastAPI = backend API
Uvicorn = local web server
pytest = test runner
pandas + scikit-learn = ML/data science report
```

### `python run.py --check`

Builds the whole synthetic data system, verifies it, prints the report, then exits.

What it proves:

- Synthetic members can be generated.
- Messy raw feeds can be written.
- Pipeline can conform raw feeds into one structure.
- SQLite database can be loaded.
- Data-quality checks pass.

Expected final line:

```text
all checks passed
```

Interview line:

```text
I like that the app refuses to serve if its own data-quality checks fail. That is the right mindset for healthcare analytics: do not show a dashboard on untrusted data.
```

### `pytest -q`

Runs the automated test suite.

Expected:

```text
49 passed
```

What tests prove:

- The random generator is reproducible.
- Pipeline round-trip works.
- Sentinel values are not averaged incorrectly.
- Unknown values are not collapsed into false.
- Query validation prevents unsafe SQL.
- Extraction validation rejects untraceable AI outputs.
- Small-cell / privacy protections work.

Interview line:

```text
The tests are organized around failure modes, not just files. That is useful because data systems fail through subtle meaning errors: leaked features, broken joins, sentinel values, and over-specific queries.
```

### `python -m app.ml.unreachable`

Runs the ML model and prints the evaluation.

What it is modeling:

```text
Which members are likely to be unreachable or need a different outreach strategy.
```

What to look for:

- Metrics table.
- Train/test split.
- Precision / recall tradeoff.
- Feature importance.
- Caveats section called "WHAT THIS DOES NOT SHOW".

Interview line:

```text
The model is not presented as magic. The useful part is the evaluation and caveats: what it predicts, what it does not prove, and where human workflow decisions still matter.
```

### `python run.py`

Starts the web app.

Expected header:

```text
SQLite connected · 420 members
```

Meaning:

```text
The app is connected to the real local SQLite database and is serving the synthetic cohort.
```

## Data Science Fundamentals In This Project

### 1. Synthetic Data

Synthetic data means fake data generated to behave like real operational data.

Why it matters:

- Safe to demo.
- No PHI.
- Reproducible.
- Lets you test workflow and analytics without violating privacy.

Say:

```text
The project uses synthetic data so the architecture and data-quality patterns are visible without exposing real member information.
```

### 2. Reproducibility

The same seed creates the same cohort every time.

Why it matters:

- Tests are stable.
- Browser demo and Python app can agree.
- Metrics do not drift randomly.

Say:

```text
Reproducibility matters because if a metric changes, I want to know it changed because code or data changed, not because the generator produced a different sample.
```

### 3. Messy Source Feeds

The app writes five raw sources:

- `campaign.json`
- `dialler.csv`
- `member_access.tsv`
- `discharge.xml`
- `portal.log`

Why this matters:

```text
Real enterprise data rarely arrives in one clean table. It arrives as APIs, CSVs, TSVs, XML, logs, and event records with different keys and meanings.
```

### 4. Conformance Pipeline

Conformance means mapping different source formats into one canonical structure.

Examples:

- Normalize member IDs.
- Parse timestamps.
- Map status values.
- Deduplicate rows.
- Quarantine invalid records.
- Count every transformation.

Say:

```text
The conformance pipeline is the layer that turns messy operational extracts into trusted, structured records.
```

### 5. Data Quality Checks

Data quality checks ask:

- Did records load?
- Are keys valid?
- Are duplicates handled?
- Are sentinel values treated correctly?
- Are privacy constraints enforced?
- Are metrics trustworthy?

Say:

```text
In a healthcare context, a dashboard is only as trustworthy as the data-quality checks behind it.
```

### 6. Quarantine Table

Bad records are held with a reason instead of silently dropped.

Why it matters:

```text
Dropping rows hides source-system problems. Quarantine preserves traceability and gives operations a path to fix the upstream issue.
```

Say:

```text
I like quarantine over silent dropping because it keeps the failure visible and auditable.
```

### 7. Sentinel Values

A sentinel value is a fake value used by a system to mean something else.

Example:

```text
-1 means "not recorded"
```

Wrong approach:

```text
Average -1 as if it were real wait time.
```

Correct approach:

```text
Convert -1 to NULL so it is excluded from averages.
```

Say:

```text
Sentinel values are dangerous because they can produce numbers that look plausible but are wrong.
```

### 8. Unknown Is Not False

If a call did not discuss caregiver contact, that does not mean caregiver contact is false.

Write:

```text
Unknown != No
Not discussed != False
Missing != Zero
```

Say:

```text
One of the most important data modeling habits is preserving uncertainty instead of collapsing it into a false value.
```

### 9. Rules Engine

The rules engine flags members who need human follow-up.

Examples:

- Language mismatch.
- Recent discharge.
- Unreachable after attempts.
- Missing caregiver contact.
- High-risk follow-up need.

Say:

```text
Rules are transparent. They are useful when the logic needs to be explainable to operations and clinical staff.
```

### 10. ML Model

The ML model predicts unreachable members.

Core terms:

- Feature: input variable used by model.
- Label: outcome the model tries to predict.
- Train/test split: train on one part, evaluate on held-out data.
- Precision: when the model flags someone, how often is it right?
- Recall: of all people who should be flagged, how many did it find?
- Threshold: cutoff for deciding positive vs negative.
- Leakage: using information that would not be available at prediction time.

Say:

```text
The model should be evaluated as a decision-support tool, not as proof of causality.
```

## Precision vs Recall

Write this big:

```text
Precision = "When I flag someone, am I right?"
Recall = "Of everyone I should flag, how many did I catch?"
```

Healthcare outreach example:

```text
High precision: fewer wasted staff calls, but may miss some people.
High recall: catches more people, but staff may review more false positives.
```

Interview line:

```text
The right threshold depends on workflow capacity. If staff can only review 50 members, precision matters more. If missing a high-risk patient is costly, recall matters more.
```

## Feature Leakage

Feature leakage means the model accidentally uses information from the future or from the answer itself.

Example:

```text
Predicting unreachable members using a field that is only known after outreach is complete.
```

Say:

```text
Leakage makes a model look better in testing than it will perform in real use.
```

## Query Safety And LLM Safety

The model never writes raw SQL.

Instead:

```text
Question -> validated query plan -> allowlisted SQL compiler -> parameterized SQL
```

Why it matters:

- Prevents prompt injection into arbitrary SQL.
- Keeps allowed tables/columns controlled.
- Protects member-level data.

Say:

```text
The LLM decides what to ask, not how to query the database directly.
```

## Extraction Safety

Every extracted value needs a source span from the transcript.

Why:

```text
If the AI says a value came from the call, the system should be able to point to the exact words in the transcript.
```

Say:

```text
Extraction without traceability is not safe enough for healthcare workflows.
```

## Trust Boundaries

There are two AI boundaries:

### Extraction

The model sees transcript content.

Risk:

```text
Real audio/transcripts may contain PHI, so this needs a BAA-covered endpoint and strict controls.
```

### Question Answering

The model sees schema/metric names only.

Risk is lower because:

```text
No member rows leave the system.
```

Say:

```text
Not all AI calls have the same risk. Extraction and analytics question-answering cross different trust boundaries.
```

## How This Connects To Your Publix Experience

Publix version:

```text
Messy enterprise operational data comes from different systems and must be mapped, normalized, validated, linked, and made traceable before analytics or AI workflows can use it.
```

Healthcare version:

```text
Patient outreach, intake, call records, discharge feeds, portal logs, and access data have the same fundamental problem: fragmented records need to become structured, validated, and traceable before they support safe decisions.
```

Bridge:

```text
My background is not just writing code around data. It is understanding that the formatting, validation, and traceability layer is what makes analytics and AI trustworthy.
```

## Master Interview Answer

```text
This project demonstrates the full data science workflow behind a healthcare AI system. It starts with synthetic member data, writes messy source extracts, conforms them into one schema, loads SQLite, runs data-quality checks, applies transparent rules, and includes an ML model with evaluation caveats. What I like about it is that it treats AI as the last step, not the first step. The data has to be structured, validated, traceable, and safe before rules, ML, or LLMs should act on it.
```

## Words To Know Cold

- Synthetic data
- Cohort
- Source feed
- Canonical schema
- Conformance
- Data quality
- Quarantine
- Traceability
- Sentinel value
- NULL
- Unknown vs false
- Rules engine
- Feature
- Label
- Train/test split
- Precision
- Recall
- Threshold
- Leakage
- Allowlist
- Parameterized SQL
- Source span
- Trust boundary
- PHI
- BAA
- Human review

