# KG Backend — Design Risks & Open Problems

## Decisions locked (review session)

- **Vector-first, graph deferred.** Build `/redesign` on pgvector nearest-neighbor.
  NetworkX + Louvain (`graph.json`) moves to the frontend milestone — its only
  consumer is the visualization. Build order: **M0 → M1 (enrich) → M2 (embed) →
  M6 (redesign)**; M3–M4 (graph) come later with the picture.
- **Redesign is count-parity-constrained**, not an open pool. Strip `N` automated
  tasks → refill `N` slots with skill-adjacent human/AI-augmented tasks
  (MMR-diverse; exclude automated + same-job sources).
- **Effort scoring dropped (2026-07-27).** The coarse 1–5 LLM effort score and its
  Option B AI-augmented discount are gone — `tasks.effort` column dropped, `effort`
  removed from the enrichment prompt, JSON schema, and write path. R6 now stops on
  count parity. See E1 for why and what it costs.

---

## Open questions

- **Should R6 stop early on weak candidates?** Count parity fills exactly `N` slots
  even if the last pick is a poor match. A relevance floor (stop when the best
  remaining MMR score drops below a threshold, and deliver fewer than `N`) is the
  obvious guard, but the threshold is uncalibrated and there's no golden set to tune
  it on yet. Prototype: fill all `N` and let mandatory human review (§7/§9) catch
  weak picks; revisit if the tail is consistently junk.

---

## Problem

### E1 — Stopping condition for the refill loop (effort field DROPPED 2026-07-27)

**The problem, restated:** R6 strips automated tasks and refills the job with
replacements. Something has to say _when to stop refilling_. Source data (brief §2)
carries no size or duration per task, and users submit free-text job descriptions with
no per-task estimates — so any measure of "how much work is this task" has to be
**derived**, like `implied_skills`.

**What was tried and dropped:** a coarse LLM-assigned effort score (integer 1–5,
"1 = file a form, 5 = lead a negotiation"), produced in the same M1 enrichment call and
stored in `tasks.effort`. It gave R6 a budget: `B = Σ effort(stripped)`, refill until
`Σ effort(added) ≈ B`, with AI-augmented tasks discounted at budget time (the "Option B"
semantics). Dropped because it was a guess stacked on a guess — an LLM rating task
weight from one line of text, with no ground truth to calibrate against and no check
available beyond "does a 1 look lighter than a 5." The AI-augmented discount factor
added a second uncalibrated knob multiplying the first. Column dropped and the field
removed from the enrichment prompt, JSON schema, and write path on 2026-07-27.

**Replacement — count parity:** strip `N` automated tasks, refill `N` slots. `N` is
exact, needs no model call, and can't drift. See R6's loop.

**Accepted tradeoff (state it plainly):** the original objection to 1:1 counting still
holds — one trivial automated task ≠ one heavy human task, so a redesigned job can come
out meaningfully heavier or lighter than it went in, and nothing in the pipeline
detects that. What makes this acceptable rather than merely simpler: every replacement
already goes to **mandatory human review with rationale** (brief §7/§9), and "this is
too much work for one person" is a judgment the reviewer can actually make from the
task list in front of them — while the LLM demonstrably could not. The check moved
from an unvalidatable number to the human who was always in the loop anyway.

**If this proves wrong:** the symptom to watch for in review is reviewers routinely
cutting picks for workload reasons. That's the signal to revisit — but with a measure
grounded in something real (reviewer-supplied estimates on a sample, say), not another
LLM guess.

**Status:** effort dropped; count parity in R6.

### P2 — Embedding context drowns the task, kills the cross-org signal

**Where:** offline pipeline, embed step (brief §3.2).
**Failure:** brief says embed `task_line` "with `job_description` as context." The
job description is shared across all of a job's tasks and is much longer than a task
line. Concatenated, it dominates each vector → every task in a job looks near-
identical, and cross-org similarity (the entire point) is buried under intra-job
similarity.

**Fix:** option 1 = embed `task_line` alone (or heavily weighted); use `job_description` only as
LLM context during skill enrichment, not in the embedded string. option 2 = If job context is
wanted in the vector, prepend a one-line summary, not the full description. Validate
on the planted cross-org pairs before trusting anything downstream.

**Status:** embed task_line alone for prototype

### P3 — Free-text `implied_skills` won't string-overlap

**Where:** offline enrichment + any skill-overlap scoring (brief §3.3, §4.5).

**Failure:** LLM is too creative, so `implied_skills` will never have string overlap, resulting in null results (taxonomy deferred, §9).
"De-escalate customers" / "conflict de-escalation" / "handling upset callers" are the
same skill with zero shared tokens. Exact set-overlap ≈ 0 even for adjacent tasks, so
the 0.3 skill edge term and the skill-gap factor become noise.

**Fix:** embed the skills too and compute overlap in embedding space (soft Jaccard /
max cosine between skill sets)

**Clarification:** this does NOT create a second graph. Tasks are the only node type;
skills are a per-task attribute (list of strings). Store one shared `skill_string →
vector` hashmap; dedup collapses _identical_ strings only (a cost optimization).
Near-synonyms ("empathy" ≈ "active listening") are reconciled at _comparison time_ via
cosine similarity, not at dedup time. Skill overlap feeds the `0.3 ×` term of the
task–task edge / redesign scoring; it never becomes its own network.

**Status:** embed distinct skills into shared lookup

### R1 — Decomposition granularity drift

**Where:** online, LLM job decomposition (brief §4.1).
**Failure:** the LLM decomposes the input job description into tasks; every downstream
step assumes these resemble seed `task_line`s in granularity/phrasing. Coarse output
("handle customer inquiries") vs fine seed lines silently craters NN match quality.
**Fix:** few-shot the decompose prompt with real `task_line` examples so output
matches corpus style; dedup/normalize before matching.
**Status:** open

### R2 — Single-NN label inheritance is high variance

**Where:** online, label inheritance (brief §4.2).

**Failure:** inheriting the one nearest neighbor's `agency_label` lets a single neighbour influence too much --> too much variance

**Fix:** kNN majority vote (k≈5) with a **dual gate**, then flag failures for review:

- Pull top-k neighbors (k≈5); label = majority vote.
- Gate (a) **absolute floor**: mean of the k task-to-task cosines ≥ ~0.5 — is the
  neighborhood even close? Catches novel/off-grain tasks
- Gate (b) **agreement**: ≥ ~3 or 4 of neighbors share the winning label
- Need **both**: agreement without the floor = confidently-but-uniformly-wrong at
  cosine 0.5; floor without agreement = close but genuinely split (e.g. 3 automated /
  2 human). Fail either → don't auto-decide; **keep + flag** for human review
  (never silently strip; satisfies brief §7/§9 hard constraint).
- Thresholds (k, 0.5, 3/4) are §8-style tuning knobs — tune on
  real matches.

**Which vectors:** gate (a)'s mean similarity uses the **task vectors** (whole
`task_line`, the P2 space), NOT the P3 skill vectors. Inheritance is a task-to-task
"what is this task overall / is it automatable" question; the k cosines pgvector
already returns from the NN query _are_ the numbers you average. Skill vectors serve
the separate skill-overlap/skill-gap scoring, not the strip decision — two
tasks can share a skill while differing in automatability. Two vector sets stored
separately: `tasks.embedding` (task vectors) vs the skill→vector lookup.

**Status:** kNN vote + dual gate on task vectors; keep + flag failures

### R5 — Candidate pool not label-filtered

**Where:** online, replacement search (brief §4.5).

**Failure:** brief never restricts the candidate pool by label → a stripped automated
task can be "replaced" by another org's automated task that's embedding-close,
re-introducing automatable work. Worse: the tasks embedding-_closest_ to a stripped
automated task are usually other automated tasks (same work) → raw-proximity ranking
launders automatable work through a different `org_id`.

**Fix — hard filter on the replacement candidate pool, applied _before_ scoring:**

```sql
candidate pool = corpus tasks
  WHERE agency_label IN ('human-centric','AI-augmented')      -- (1) label eligibility
    AND NOT (org_id = :input_org AND job_title = :input_job)   -- (2) exclude exact source job
```

- **(1) Label filter** — a replacement must be non-automatable by definition. Hard
  `WHERE`, not a score penalty: no similarity is high enough to justify re-adding
  automatable work.
- **(2) Exclude the exact source job** (org + title _together_), NOT the whole org.
  Recommending a job its own task back is circular — that's the only hard part.
  Same-org-but-_different_-job stays eligible and is often the _most_ feasible
  transplant (same institutional context). Excluding the whole org would smuggle a
  soft "prefer cross-org" preference into a hard filter and discard high-plausibility
  candidates. (Clause only bites when the input job already exists in the corpus,
  e.g. demoing on a seed job; free + correct otherwise.)

**Not filters (deliberately):**

- **Cross-org preference** = soft desirability (novelty) → belongs in _scoring_, not
  `WHERE`. Prototype: let skill-adjacency rank naturally; optional small cross-org
  bonus later. Don't penalize same-org candidates.
- **Domain plausibility** (the "MoM officer taking a MoH task?" worry) → handled by
  scoring candidates on **skill-adjacency to the anchor** + **mandatory human review
  with rationale** (brief §7/§9), not by an org filter. A transplant is an _analogy
  suggestion_ (skill pattern found elsewhere, cited by provenance, rewritten by the
  reviewer), not a literal reassignment. Alien candidates score low on skill-adjacency
  and never reach top-3.

**Automated tasks stay in the table (not deleted):** inheritance (R2) needs them as
reference examples of "what automatable looks like"; the viz needs them as colored
nodes. R5's filter is scoped to the _replacement query only_ — automated tasks are
read-only reference data: matched _against_ and stripped _away_, never handed out.

**Governing principle (recurring):** hard eligibility → filter; soft desirability → score.

**Status:** label filter + exact-source-job exclusion; cross-org & plausibility handled in scoring/review

### R6 — Scoring underspecified; now count-parity-constrained

**Where:** online, candidate scoring (brief §4.5) + stopping condition.

**Fundamental problem:** the brief lists 4 factors and implies `score = weighted sum`,
take top-k. That shape is wrong on 3 counts:

1. Only 2 of the 4 are static per-candidate cosine numbers (proximity, synergy).
   **Non-redundancy** depends on what's _already picked_ → sequential, not a fixed term.
2. **Skill-gap is non-monotonic** ("tiny = redundant, medium = ideal, huge = stretch"
   — a hump). A linear weight can't say "the middle is best."
3. Selection is **sequential, not a ranking you slice**. Even with a fixed target count
   (`N` = tasks stripped, E1), you can't rank once and take the top `N`: point 1 means
   each pick changes the scores of everything left in the pool.

**The four factors split by _role_, they are NOT four weights:**
| Brief factor | What it really is | Where it goes |
|---|---|---|
| Proximity (↔ removed task) | static relevance | `relevance` term (cosine) |
| Synergy (↔ retained job) | static relevance | `relevance` term — **max-sim to any one retained task, NOT centroid** (see note) |
| Non-redundancy | sequential penalty | MMR `−λ·max-sim-to-already-picked`, _inside the loop_ |
| Skill-gap | non-monotonic feasibility | band flag (drop/keep/flag), _not_ a score term |

**Fix — greedy, count-bounded MMR (two independent gears in one loop):**

```
N = number of stripped automated tasks              (count parity, E1)
pool = R5's filtered candidates
precompute per candidate: relevance = f(proximity, synergy);  skill-band (below)
drop 'redundant'-band candidates
selected = []
while len(selected) < N and candidates remain:         # gear B: COUNT = when to stop
    pick argmax( relevance − λ·max_sim(cand, selected) )  # gear A: MMR = who's next (diversity)
    selected += cand;  annotate if 'stretch'
```

Gear A (non-redundancy) = _which_ candidate next (keeps picks diverse). Gear B (count)
= _when to stop_. Independent — don't conflate "diverse enough" with "enough tasks."
**Under-fill:** if the filtered pool empties before `N`, return fewer and say so in the
output. Never relax R5's label filter or the 'redundant' drop to hit the number — a
short list is a finding, a padded one is a lie.

**Skill-gap band — measures feasibility, not similarity ("can this person realistically
learn it?"), computed in the P3 skill-vector space:**

```
candidate skills  vs  anchor's pooled skills (retained tasks' implied_skills)
for each candidate skill s:
    coverage(s) = max cosine(s, a) over anchor skills a      # already present?
    novelty(s)  = 1 − coverage(s)
gap = mean novelty(s)          # 0 = fully covered, 1 = foreign
```

Example — counselor anchor (counseling + report-writing):

- "mediate disputes" {de-escalation, active listening, documentation} → novelties
  .21/.26/.15 → gap ≈ .21 → **realistic**.
- needs {statistical modeling, python} → novelties ~.8 → gap ≈ .8 → **stretch** (perfect
  embedding match but not doable — exactly what the band catches).

Cut gap into bands:

```
gap < t_low          → REDUNDANT  (drop — skill already present, reshapes nothing)
t_low ≤ gap ≤ t_high → REALISTIC  (keep — genuine learnable reskill = the goal)
gap > t_high         → STRETCH    (keep + FLAG for reviewer)
```

`t_low`/`t_high` are **calibrated, not derived**: guess → eyeball real buckets → check
T1 golden-set transplants land in 'realistic'. Cosines bunch high with
text-embedding-3-large, so set cuts by **percentile** of the gap distribution, not fixed
absolute values.

**Knobs to write down before coding:** proximity/synergy weights in `relevance`; MMR λ;
the two band percentiles. (R6's analogue of the brief §8 tuning items. Note this list
got shorter when effort went — no budget tolerance to tune.)

**Synergy note:** brief §4.4 says represent the
retrained job as one _embedding centroid_ (average of retained-task vectors) and score
synergy as similarity to it. A job spans several skill facets; their average lands in
empty space _between_ facets, resembling none → rewards bland middle-of-road tasks,
penalizes tasks that strongly fit one real facet.

**Fix**: **synergy = max cosine to any
single retained task** (optional heavier version: cluster anchor into facets, score vs
nearest facet). NOTE: this kills the centroid only _as a synergy yardstick_ — §4.4's
skill _pooling_ is fine (a union of skill strings, not an averaged vector) and stays.
**Status:** understood — fix agreed (greedy count-bounded MMR, `N` = tasks stripped;
4 factors split by role; skill-gap = percentile bands on mean skill-novelty; synergy =
max-sim not centroid)

### S0 — Taxonomy transition: O\*NET via hybrid enrichment (supersedes brief §9 non-goal)

**Decision:** map skills to O\*NET (35 Skills + 33 Knowledge = 68-item menu, names +
definitions from Content Model Reference).

**Option C — hybrid extraction:** one
enrichment call returns both `implied_skills` (free text, kept) AND `onet_skills`
(picks from the menu, enum-enforced in the JSON schema → invalid labels impossible).
Requires full re-run of enrichment. New column `tasks.onet_skills TEXT[]`; menu stored
in `onet_skills` reference table + `data/onet_skills.csv`. Scope: skills only — no
tasks→DWA, no jobs→SOC. **Pick count: `minItems 0, maxItems 5`** — 0 so junk (non-`ok`)
rows carry no tags, 5 (not 4) to let multi-facet tasks tag fully; run averaged 2.82
tags/`ok` task, so 5 is a ceiling, not a target.

**Feeds:** edge skill-overlap term becomes exact Jaccard on `onet_skills` (replaces
P3's soft-Jaccard as the 0.3 term); free-text skill vectors (P3) remain for R6
skill-gap banding and rationale color.

**Status:** done. Full corpus enriched (1,452 tasks, 0 nulls); quality gate holds
(0 non-`ok` rows tagged).

### S1 — O\*NET collapses digital work into ~3 coarse tags

**Problem:** data science, DevOps, cybersecurity all map to `Programming` /
`Computers and Electronics` / `Systems Analysis`; no cybersecurity item exists
(Public Safety and Security = physical).

**Concern:** skill-gap on canonical tags alone reads "data scientist ↔ firmware dev =
zero gap"; edges within digital roles lose discrimination.

**Solution — division of labor between signals:** the 0.7 text-similarity term already
discriminates fine grain (task lines embed far apart); canonical Jaccard is only the
0.3 structural boost. Redesign scoring uses both layers: O\*NET overlap = coarse
same-family gate; free-text skill embeddings (P3 space) = fine gap distance (R6 bands).
Rationale cites both. **Escape hatch:** if pilot shows >~15% of tasks landing in
`Programming`/`Computers and Electronics`, add a small namespaced house extension
(e.g. `X.1 Cybersecurity`) — not pre-emptively.

**Status:** resolved; pilot confirmed **5.6%** of tags in `Programming`/`Computers and
Electronics` (« 15% trigger) — no house extension needed.

### S2 — Junk task lines can't be tagged ("Other strategic work as required")

**Problem:** corpus contains contentless tasks and boilerplate rows that are
requirements text, not tasks ("Able to work independently…"). Forcing ≥1 O\*NET tag on
them manufactures fake edges.

**Concern:** junk connects to everything via forced tags; worst case a junk line is
offered as a replacement task in `/redesign`.

**Solution — enrichment doubles as quality gate:** add `task_quality: ok | vague |
boilerplate` to the enrichment schema; when ≠ `ok`, `onet_skills` may be empty
(minItems 0). Downstream: excluded from skill-overlap edges; **hard-excluded** from
R5's candidate pool; dimmed/flagged in viz; same field applied to online decomposition
output (R1) so JD boilerplate never becomes a fake task. Pilot reports junk rate →
decide clean-at-source vs live-with-flags.

**Status:** agreed; one schema field, no new pipeline stage.
