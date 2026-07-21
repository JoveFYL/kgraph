# pipeline/calibrate_classify.py
from pathlib import Path
from typing import NamedTuple
import os

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
engine = create_engine(os.getenv("DATABASE_URL"))

MAX_K = 10

# ----------------------------------------------------------------------
# CALIBRATED CONSTANTS  -- the output of this file, consumed by /redesign.
# Re-derive with recommend_thresholds(collect()) whenever the data changes,
# then paste the printed block over this one.
#
# Calibrated 2026-07-21 on n=1312 ok+embedded tasks, by recommend_thresholds().
# Label mix: AI-augmented 984 / fully-automated 290 / human-centric 38 (75/22/3).
#   K=3      chosen on STRIP PRECISION, not overall accuracy -- accuracy peaks
#            at k=5 (79.3%) but barely beats the 75% majority-guess baseline,
#            so it is not a useful selector. k=3 unanimity is a tighter locality
#            signal AND more tasks clear it: 33 strips @ 90.9% vs k=5's 8 @ 87.5%.
#   FLOOR    weak signal: random pairs 0.779 vs rank-1 0.913, too narrow a band
#            to discriminate. Kept as a sanity floor only -- do not tune it.
#   STRIP_N  the real knob. At k=3, N=3 means unanimous.
# Measured strip precision 90.9% over 33/1312 tasks (3 wrong strips).
# CAVEAT: 33 samples -> that 90.9% carries roughly +/-10pp. Treat 'high'
# confidence as "reviewer can skim", never as "no human needed".
# ----------------------------------------------------------------------
K = 3
FLOOR = 0.86
STRIP_N = 3

# The only label that triggers an irreversible action (task removal).
STRIP_LABEL = "fully-automated"

# A strip proposal must clear this to be marked "high" confidence rather than
# being sent to the HR reviewer. Set from the cost of a wrong strip: deleting a
# task that should have been kept, in a tool whose purpose is preserving jobs.
TARGET_STRIP_PRECISION = 0.90


class Prediction(NamedTuple):
    """What /redesign step 2 hands to step 3.

    strip_proposed is a PROPOSAL, never a deletion -- the brief requires an HR
    reviewer to approve every swap. confidence tells the UI whether to
    pre-check the box ('high') or demand a look ('review')."""
    label: str
    strip_proposed: bool
    confidence: str | None   # 'high' | 'review'; None when nothing is stripped
    mean_cos: float
    agreement: int


def knn(conn, task_id, k=MAX_K, exclude_same_org=False):
    """Leave-one-out: k nearest OTHER ok tasks to task_id.
    Returns rows nearest-first: (task_id, agency_label, cosine, same_org)."""
    # Step 1: fetch the anchor's own vector + org. ::text -> canonical '[a,b,...]'
    anchor = conn.execute(
        text("SELECT embedding::text AS vec, org_id FROM tasks WHERE task_id = :id"),
        {"id": task_id},
    ).one()

    # Step 2: index-accelerated neighbour search. Both sides cast to halfvec(1536)
    # so the HNSW index is used (uncast -> silent Seq Scan)
    org_clause = "AND b.org_id <> :anchor_org" if exclude_same_org else ""
    sql = text(f"""
        SELECT b.task_id, b.agency_label,
               1 - (b.embedding::halfvec(1536) <=> (:vec)::halfvec(1536)) AS cosine,
               (b.org_id = :anchor_org) AS same_org
        FROM tasks b
        WHERE b.task_id <> :id
          AND b.task_quality = 'ok'
          AND b.embedding IS NOT NULL
          {org_clause}
        ORDER BY b.embedding::halfvec(1536) <=> (:vec)::halfvec(1536)
        LIMIT :k
    """)
    return conn.execute(
        sql,
        {"id": task_id, "vec": anchor.vec, "anchor_org": anchor.org_id, "k": k},
    ).all()


def predict(neighbours, k=K, floor=FLOOR, strip_n=STRIP_N):
    """neighbours = nearest-first list from knn(). Returns a Prediction.

    The label drives exactly one irreversible action -- stripping the task --
    so the gates guard THAT decision and nothing else:

      label != STRIP_LABEL   -> keep. No gate, no review. Keeping is free; the
                                worst case is a missed strip, which preserves a
                                job and is the error this tool should prefer.
      label == STRIP_LABEL   -> propose a strip, gated:
          both gates pass    -> confidence 'high'   (~92% precise)
          either fails       -> confidence 'review' (~67% precise -- 1 in 3
                                strips would be wrong, so a human must look)

    Never returns a deletion, only a proposal. Step 3 must respect confidence."""
    top = neighbours[:k]
    mean_cos = sum(n.cosine for n in top) / len(top)          # Gate A input
    votes = {}                                                # Gate B input
    for n in top:
        votes[n.agency_label] = votes.get(n.agency_label, 0) + 1
    # dict is in nearest-first insertion order, so max() breaks ties toward the
    # nearer label. per_task() mirrors this -- keep them in sync.
    winner, count = max(votes.items(), key=lambda kv: kv[1])

    if winner != STRIP_LABEL:
        return Prediction(winner, False, None, mean_cos, count)

    confident = mean_cos >= floor and count >= strip_n
    return Prediction(winner, True, "high" if confident else "review",
                      mean_cos, count)


# --- collect leave-one-out results ONCE into a flat table ---
def collect(exclude_same_org=False):
    rows = []
    with engine.begin() as conn:
        ids = conn.execute(text(
            "SELECT task_id, agency_label FROM tasks "
            "WHERE task_quality = 'ok' AND embedding IS NOT NULL")).all()

        for t in ids:
            nb = knn(conn, t.task_id, MAX_K, exclude_same_org)
            for rank, n in enumerate(nb, start=1):
                rows.append({
                    "task_id": t.task_id,
                    "true_label": t.agency_label,
                    "rank": rank,                 # 1 = nearest
                    "cosine": float(n.cosine),
                    "neighbour_label": n.agency_label,
                    "same_org": n.same_org,
                })
    return pd.DataFrame(rows)   # one row per (task, neighbour). ~1312 * 10


# --- shared helper: collapse the flat table to ONE row per task at count k ---
def per_task(df, k):
    """One row per task for the given k. Columns: true_label, pred_label,
    mean_cos, agreement, correct. Gates (floor/N) are NOT applied here -- they're
    cheap masks on mean_cos/agreement, so callers reuse this across a threshold sweep.
    The vote mirrors predict(): nearest-first, ties -> nearest label's."""
    top = df[df.loc[:, "rank"] <= k].sort_values(["task_id", "rank"])

    def agg(g):
        votes = {}
        for lbl in g.loc[:, "neighbour_label"]:          # already nearest-first
            votes[lbl] = votes.get(lbl, 0) + 1
        # first max in insertion(=rank) order
        winner = max(votes, key=votes.get)
        return pd.Series({"pred_label": winner,
                          "mean_cos": g.loc[:, "cosine"].mean(),
                          "agreement": votes[winner]})

    out = top.groupby("task_id")[
        ["cosine", "neighbour_label"]].apply(agg).copy()
    out.loc[:, "true_label"] = df.groupby("task_id")["true_label"].first()
    out.loc[:, "correct"] = out.loc[:,
                                    "pred_label"] == out.loc[:, "true_label"]
    return out


# ----------------------------------------------------------------------
# RECALIBRATION -- run this when the data changes, paste the printed block
# over the CALIBRATED CONSTANTS at the top of this file.
# ----------------------------------------------------------------------
def strip_precision(pt, floor, n_agree):
    """Among tasks this config would PROPOSE STRIPPING, what fraction really
    are STRIP_LABEL? This is the number that matters -- overall accuracy is
    dominated by the majority class and hides the only costly error."""
    proposed = ((pt["pred_label"] == STRIP_LABEL)
                & (pt["mean_cos"] >= floor)
                & (pt["agreement"] >= n_agree))
    if not proposed.any():
        return float("nan"), 0
    return pt.loc[proposed, "correct"].mean(), int(proposed.sum())


def recommend_thresholds(df, target=TARGET_STRIP_PRECISION, min_strips=10,
                         ks=(3, 5, 7, 10), floors=(0.82, 0.86, 0.88, 0.90)):
    """Search (k, floor, N) for the config that hits `target` strip precision
    while proposing the MOST strips -- precision is the constraint, volume is
    what we maximise, because a config that strips nothing is safe but useless.

    min_strips guards against a config looking perfect on 2 lucky tasks."""
    print("\n=== Recommendation: configs meeting "
          f"{target:.0%} strip precision (min {min_strips} strips) ===")
    candidates = []
    for k in ks:
        pt = per_task(df, k)
        for floor in floors:
            for n_agree in range(2, k + 1):
                prec, count = strip_precision(pt, floor, n_agree)
                if count >= min_strips and prec >= target:
                    candidates.append((count, prec, k, floor, n_agree))

    if not candidates:
        print(f"  NONE. No config reaches {target:.0%} strip precision on")
        print(f"  {df['task_id'].nunique()} tasks. Every strip needs a human.")
        print("  Ship with strip_proposed=True, confidence='review' for all,")
        print("  and do not auto-strip anything.")
        return None

    candidates.sort(reverse=True)          # most strips first
    print(f"  {'k':>3} {'floor':>6} {'N':>3} {'strips':>7} {'precision':>10}")
    for count, prec, k, floor, n_agree in candidates[:8]:
        print(f"  {k:3d} {floor:6.2f} {n_agree:3d} {count:7d} {prec:10.1%}")

    count, prec, k, floor, n_agree = candidates[0]
    n_tasks = df["task_id"].nunique()
    print("\n  --- paste over CALIBRATED CONSTANTS ---")
    print(f"  # Calibrated on n={n_tasks} ok+embedded tasks.")
    print(f"  # Measured strip precision {prec:.1%} over {count}/{n_tasks} tasks.")
    print(f"  K = {k}")
    print(f"  FLOOR = {floor}")
    print(f"  STRIP_N = {n_agree}")
    return k, floor, n_agree


# ----------------------------------------------------------------------
# TESTS
# ----------------------------------------------------------------------
def test1_distance_distribution(df):
    """Cosine by neighbour rank -> where 'same task' separates from 'unrelated'. => FLOOR"""
    print("\n=== Test 1: cosine by neighbour rank ===")
    stats = df.groupby("rank")["cosine"].describe()[
        ["mean", "25%", "50%", "75%"]]
    print(stats.round(3))
    # Read: rank-1 mean ~ 'same work' level; it decays with rank. The floor sits
    # below the rank-1 cluster but above where neighbours stop being relevant.


def test1b_random_baseline(df, n=2000):
    """Cosine between RANDOM task pairs -- the 'unrelated' baseline of the band.
    ada-002 cosines bunch high, so the real floor sits BETWEEN this and the
    rank-1 cluster from test1, set by percentile inside that narrow band."""
    import random
    ids = list(df["task_id"].unique())
    with engine.begin() as conn:
        cos = []
        for _ in range(n):
            a, b = random.sample(ids, 2)
            c = conn.execute(text(
                "SELECT 1 - (x.embedding::halfvec(1536) <=> y.embedding::halfvec(1536)) "
                "FROM tasks x, tasks y WHERE x.task_id=:a AND y.task_id=:b"),
                {"a": int(a), "b": int(b)}).scalar()
            cos.append(float(c))
    print("\n=== Test 1b: random-pair cosine (unrelated baseline) ===")
    print(pd.Series(cos).describe()[["mean", "25%", "50%", "75%"]].round(3))


def test2_accuracy_vs_k(df):
    """Leave-one-out label accuracy vs k, NO gates. Pick the k that peaks/plateaus."""
    print("\n=== Test 2: accuracy vs k (no gates) ===")
    for k in (3, 5, 7, 10):
        acc = per_task(df, k).loc[:, "correct"].mean()
        print(f"  k={k:2d}  accuracy={acc:.3f}")
    # The winning k feeds tests 3-5 (set K below).


def test3_agreement_distribution(df, k):
    """Winner's vote count, split correct vs wrong. Wrong preds should cluster
    at LOW agreement -> that's what N filters out. => N"""
    print(f"\n=== Test 3: agreement of winner, correct vs wrong (k={k}) ===")
    pt = per_task(df, k)
    table = (pt.groupby(["agreement", "correct"]).size()
               .unstack(fill_value=0)
               .rename(columns={True: "correct", False: "wrong"}))
    print(table)
    # Choose N just above the band where 'wrong' still concentrates.


def test4_coverage_vs_precision(df, k, floors=(0.82, 0.84, 0.86, 0.88, 0.90),
                                ns=(3, 4, 5)):
    """THE decision. Sweep (floor, N): coverage = % that pass both gates (get
    auto-classified); precision = accuracy among those. Pick a strict point --
    high precision, coverage you accept -- since the rest is safely 'keep + flag'."""
    print(f"\n=== Test 4: coverage vs precision sweep (k={k}) ===")
    pt = per_task(df, k)
    print(f"{'floor':>6} {'N':>3} {'coverage':>9} {'precision':>10}")
    for floor in floors:
        for n_agree in ns:
            passed = (pt["mean_cos"] >= floor) & (pt["agreement"] >= n_agree)
            coverage = passed.mean()
            precision = pt.loc[passed, "correct"].mean(
            ) if passed.any() else float("nan")
            print(f"{floor:6.2f} {n_agree:3d} {coverage:9.1%} {precision:10.1%}")


def test5_confusion_by_label(df, k, floor, n_agree):
    """Confusion matrix among GATED predictions. Watch the
    fully-automated <-> AI-augmented cell: a wrong strip is the costly error."""
    print(
        f"\n=== Test 5: confusion, gated (k={k}, floor={floor}, N={n_agree}) ===")
    pt = per_task(df, k)
    passed = (pt["mean_cos"] >= floor) & (pt["agreement"] >= n_agree)
    g = pt[passed]
    print(f"  ({passed.sum()}/{len(pt)} tasks passed the gates)")
    print(pd.crosstab(g["true_label"], g["pred_label"]))


if __name__ == "__main__":
    df = collect(exclude_same_org=False)
    # df = collect(exclude_same_org=True)   # the harder, realistic cross-org case

    test1_distance_distribution(df)
    test1b_random_baseline(df)
    test2_accuracy_vs_k(df)

    test3_agreement_distribution(df, K)
    test4_coverage_vs_precision(df, K)
    test5_confusion_by_label(df, K, FLOOR, STRIP_N)

    # The decision. Everything above is diagnostics for reading this output.
    recommend_thresholds(df)
