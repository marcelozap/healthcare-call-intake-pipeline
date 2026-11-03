"""
Predict which members the campaign will fail to reach.

    python -m app.ml.unreachable

Read this before reading the metrics
------------------------------------
The data is synthetic and I wrote the generator. A model trained on it is partly
learning my own simulation, so **the AUC below is not evidence about Leon's real
members** and should not be quoted as if it were. What this file demonstrates is
the method: a stratified split, baselines that have to be beaten, calibration,
and a threshold chosen from an operational constraint rather than from `0.5`.

On real data the first task would be the opposite of impressive — establishing
whether "reached" even means the same thing across the sources feeding it.

Leakage
-------
Only features knowable *before* outreach begins are used. Anything derived from
the attempts themselves (`attempt_count`, outcomes, whether a callback was
requested) would leak the answer, so they are excluded and there is a test that
fails if one creeps back in.

`misrouted` is included and it is legitimate: which language queue a member sits
on is a routing configuration known in advance, not an outcome. It is also the
strongest feature, which is a useful result — the model independently rediscovers
what the `language_mismatch` safety rule already flags.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import app.console  # noqa: F401  - makes Windows console output safe
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (average_precision_score, brier_score_loss,
                             confusion_matrix, f1_score, precision_score,
                             recall_score, roc_auc_score)
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from app.domain import Member
from app.generate import generate_cohort

RANDOM_STATE = 42
TEST_SIZE = 0.25

NUMERIC = ["age"]
CATEGORICAL = ["clinic", "language", "contact_method", "outreach_reason"]
BOOLEAN = ["caregiver_known", "needs_interpreter", "transport_needed",
           "has_phone", "has_appointment", "recently_discharged", "misrouted"]
FEATURES = NUMERIC + CATEGORICAL + BOOLEAN

#: Anything computed from the attempts would leak. Asserted in the tests.
FORBIDDEN = {"attempt_count", "reached", "outcome", "needs_review",
             "flag_count", "high_flags"}


def to_frame(members: list[Member]) -> pd.DataFrame:
    """Feature table. One row per member, pre-outreach information only."""
    return pd.DataFrame([{
        "age": m.age,
        "clinic": m.clinic,
        "language": m.language,
        "contact_method": m.contact_method,
        "outreach_reason": m.reason.label,
        "caregiver_known": int(m.caregiver_known),
        "needs_interpreter": int(m.needs_interpreter),
        "transport_needed": int(m.transport_needed),
        "has_phone": int(m.has_phone),
        "has_appointment": int(m.has_appointment),
        "recently_discharged": int(m.discharge_days is not None),
        "misrouted": int(m.misrouted),
        # target: the thing we want to predict, kept separate downstream
        "not_reached": int(not m.reached),
    } for m in members])


def build_pipeline(kind: str = "logistic") -> Pipeline:
    """Preprocessing plus an estimator, as one object.

    Everything lives inside the Pipeline so the scaler and encoder are fitted on
    the training fold only. Fitting them on all the data before splitting is the
    most common way a good-looking score turns out to be meaningless.
    """
    preprocess = ColumnTransformer([
        ("num", StandardScaler(), NUMERIC),
        ("cat", OneHotEncoder(handle_unknown="ignore", drop="first"), CATEGORICAL),
        ("bool", "passthrough", BOOLEAN),
    ])
    estimator = (
        LogisticRegression(max_iter=1000, random_state=RANDOM_STATE)
        if kind == "logistic"
        else GradientBoostingClassifier(random_state=RANDOM_STATE)
    )
    return Pipeline([("prep", preprocess), ("model", estimator)])


@dataclass
class Evaluation:
    name: str
    accuracy: float
    precision: float
    recall: float
    f1: float
    roc_auc: float
    pr_auc: float
    brier: float
    confusion: np.ndarray = field(repr=False)

    def row(self) -> str:
        return (f"  {self.name:<26}{self.accuracy:>8.3f}{self.precision:>10.3f}"
                f"{self.recall:>8.3f}{self.f1:>7.3f}{self.roc_auc:>8.3f}"
                f"{self.pr_auc:>8.3f}{self.brier:>8.3f}")


def evaluate(name: str, y_true, y_pred, y_prob) -> Evaluation:
    return Evaluation(
        name=name,
        accuracy=float((y_true == y_pred).mean()),
        precision=precision_score(y_true, y_pred, zero_division=0),
        recall=recall_score(y_true, y_pred, zero_division=0),
        f1=f1_score(y_true, y_pred, zero_division=0),
        roc_auc=roc_auc_score(y_true, y_prob),
        pr_auc=average_precision_score(y_true, y_prob),
        # Brier score: are the probabilities honest, not just well-ordered?
        # A model can rank perfectly and still be badly calibrated, and a
        # miscalibrated probability is useless for deciding a threshold.
        brier=brier_score_loss(y_true, y_prob),
        confusion=confusion_matrix(y_true, y_pred),
    )


def precision_at_k(y_true, y_prob, k: int) -> tuple[float, int]:
    """If the team can only work K extra cases today, how many are real?

    This is the number that decides whether a model is worth deploying.
    Accuracy over the whole cohort is not, because capacity is finite and the
    only members that matter are the ones at the top of the list.
    """
    order = np.argsort(y_prob)[::-1][:k]
    hits = int(np.asarray(y_true)[order].sum())
    return hits / k, hits


def run(verbose: bool = True) -> dict:
    members = generate_cohort()
    frame = to_frame(members)

    assert not (FORBIDDEN & set(FEATURES)), "a post-outreach feature leaked in"

    X = frame[FEATURES]
    y = frame["not_reached"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=TEST_SIZE, random_state=RANDOM_STATE, stratify=y)

    results: list[Evaluation] = []

    # ---- baselines the model has to beat --------------------------------
    # Without these a score is unreadable: on a 37% positive class, a model that
    # always says "no" already looks 63% accurate.
    majority = DummyClassifier(strategy="most_frequent").fit(X_train, y_train)
    results.append(evaluate("baseline: majority class", y_test,
                            majority.predict(X_test),
                            majority.predict_proba(X_test)[:, 1]))

    # A one-rule baseline: flag everyone on the wrong language queue. If the
    # model cannot beat a single if-statement, it should not be deployed.
    rule_pred = X_test["misrouted"].to_numpy()
    results.append(evaluate("baseline: misrouted only", y_test, rule_pred,
                            rule_pred.astype(float)))

    # ---- models ---------------------------------------------------------
    fitted: dict[str, Pipeline] = {}
    for kind, label in [("logistic", "logistic regression"),
                        ("boosted", "gradient boosting")]:
        pipeline = build_pipeline(kind).fit(X_train, y_train)
        fitted[kind] = pipeline
        prob = pipeline.predict_proba(X_test)[:, 1]
        results.append(evaluate(label, y_test, pipeline.predict(X_test), prob))

    # Cross-validation on the training set only, to check the single split
    # was not lucky.
    cv = cross_val_score(
        build_pipeline("logistic"), X_train, y_train,
        cv=StratifiedKFold(5, shuffle=True, random_state=RANDOM_STATE), scoring="roc_auc")

    model = fitted["logistic"]
    prob = model.predict_proba(X_test)[:, 1]

    if verbose:
        _report(frame, y, y_train, y_test, results, cv, model, prob)

    return {
        "results": {r.name: r for r in results},
        "cv_auc_mean": float(cv.mean()),
        "cv_auc_std": float(cv.std()),
        "model": model,
        "n_train": len(X_train),
        "n_test": len(X_test),
    }


def _coefficients(model: Pipeline) -> pd.DataFrame:
    """Logistic coefficients as odds ratios, which are readable out loud."""
    names = model.named_steps["prep"].get_feature_names_out()
    coefs = model.named_steps["model"].coef_[0]
    return (pd.DataFrame({"feature": [n.split("__", 1)[-1] for n in names],
                          "coefficient": coefs,
                          "odds_ratio": np.exp(coefs)})
            .sort_values("coefficient", key=abs, ascending=False)
            .reset_index(drop=True))


def _report(frame, y, y_train, y_test, results, cv, model, prob) -> None:
    print("=" * 86)
    print("PREDICTING WHICH MEMBERS THE CAMPAIGN WILL NOT REACH")
    print("=" * 86)
    print(f"\ncohort {len(frame)}  |  train {len(y_train)}  |  test {len(y_test)}"
          f"  |  positive class {y.mean():.1%} (not reached)")
    print(f"features: {len(FEATURES)}, all knowable before outreach begins")

    print(f"\n{'':<26}{'accuracy':>8}{'precision':>10}{'recall':>8}"
          f"{'F1':>7}{'ROC-AUC':>8}{'PR-AUC':>8}{'Brier':>8}")
    print("-" * 86)
    for r in results:
        print(r.row())

    # Compare the held-out split against cross-validation and say plainly which
    # way it went. Reporting only the flattering one is how a model gets
    # deployed on a number nobody can reproduce.
    test_auc = next(r.roc_auc for r in results if r.name == "logistic regression")
    gap = test_auc - cv.mean()
    print(f"\n5-fold CV on the training set: ROC-AUC {cv.mean():.3f} +/- {cv.std():.3f}")
    print(f"held-out test split:          ROC-AUC {test_auc:.3f}  ({gap:+.3f} vs CV)")
    if gap > cv.std():
        print("  The test split scored better than cross-validation by more than one")
        print("  standard deviation, so it was a favourable draw. On a cohort this size")
        print(f"  the CV figure ({cv.mean():.3f}) is the more honest one to quote.")
    elif gap < -cv.std():
        print("  The test split scored worse than cross-validation - worth investigating")
        print("  before trusting either number.")
    else:
        print("  The two agree within one standard deviation, so the split was fair.")

    tn, fp, fn, tp = results[2].confusion.ravel()
    print(f"\nconfusion matrix, logistic regression, threshold 0.5")
    print(f"                     predicted reached   predicted not reached")
    print(f"  actually reached  {tn:>16}    {fp:>21}")
    print(f"  actually not      {fn:>16}    {tp:>21}")

    print("\ntop features by absolute coefficient")
    for _, row in _coefficients(model).head(8).iterrows():
        arrow = "increases" if row.coefficient > 0 else "decreases"
        print(f"  {row.feature:<34}{row.coefficient:>7.2f}   "
              f"odds x{row.odds_ratio:>5.2f}  {arrow} risk")

    print("\noperational view - if the team can work K extra cases today")
    print(f"  {'K':>5}{'precision@K':>14}{'real cases found':>19}")
    for k in (10, 25, 50, 100):
        if k <= len(y_test):
            p, hits = precision_at_k(y_test.to_numpy(), prob, k)
            print(f"  {k:>5}{p:>13.1%}{hits:>19}")
    print(f"  random selection would find about {y.mean():.0%} of any K")

    print("\n" + "-" * 86)
    print("WHAT THIS DOES NOT SHOW")
    print("-" * 86)
    print("""  The data is synthetic and I wrote the generator, so the model is partly
  learning my own simulation. These numbers are a demonstration of method, not
  evidence about Leon's members, and quoting the AUC as if it were the latter
  would be misleading.

  On real data the honest first step is not modelling. It is establishing
  whether 'reached' means the same thing across every system that feeds it —
  which, on the evidence of the dialler export in this project, it does not.

  The model also earns its keep only if acting on it is cheaper than the
  outcome it predicts. Predicting an unreachable member is worth something
  because the response is a phone call. That calculation has to be done before
  anyone builds this, not after.""")


if __name__ == "__main__":
    run()
