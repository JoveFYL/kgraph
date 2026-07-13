# Job Redesign Knowledge Graph — Engineering Brief
**Phase:** Prototype / Proof of Concept
**Prepared for:** Development team
**Status:** Design finalized, ready to build

---

## 1. Objective

Build a prototype that does two things:

1. **Visualizes** the full universe of tasks across 80 organisations as an interactive, Obsidian-style knowledge graph, clustered by organisation and color-coded by automation exposure.
2. **Redesigns jobs**: given a job title + job description, automatically strip out fully-automatable tasks and replace them with human-centric or AI-augmented tasks pulled from elsewhere in the graph — tasks that are semantically close and skill-adjacent to the role that remains. Output includes a rationale and provenance (source org/job) for every task added.

**Why it matters:** this is an HR tool for redesigning roles impacted by AI, aimed at preserving jobs by reshaping them — not eliminating them.

---

## 2. Source data

Already available, labeled and clean:

| Field | Description |
|---|---|
| `task_line` | Individual task description |
| `job_title` | Job the task belongs to |
| `job_description` | Full job description text (shared across all tasks of that job) |
| `org_id` | One of 80 organisations |
| `agency_label` | `human-centric` \| `AI-augmented` \| `fully-automated` |

No skills field exists yet — we derive one (see §3).

Volume: **5,000+ tasks**, so the design assumes a real backend, not a client-side-only demo.

---

## 3. Offline pipeline (batch, runs once, re-run when data updates)

1. **Skill enrichment**: for each task, an LLM reads `task_line` + `job_description` for context and outputs a small set of implied skills/competencies. Store as `implied_skills: [...]`.
2. **Embedding**: embed each task from `task_line` (with `job_description` as context) into a vector.
3. **Graph construction**:
   - Nodes = tasks, grouped into clusters by `org_id`
   - Edge weight = `0.7 × text_similarity + 0.3 × implied_skills_overlap` (starting weights — tune after first look at the graph)
   - Cross-organisation edges are the important signal: they reveal which tasks recur across orgs regardless of label, which is what powers cross-org task transplants during redesign
4. Cache the resulting graph (nodes, edges, cluster assignments) as JSON, and load embeddings into pgvector for live nearest-neighbor queries.

---

## 4. Online pipeline (live, per user request)

1. LLM decomposes the input job description into discrete task lines
2. Each new task is matched to its nearest neighbor in the graph (pgvector similarity search) and **inherits that neighbor's `agency_label`**
   - Flag low-confidence matches (below similarity threshold) for human review rather than silently trusting the label
3. Strip all tasks classified `fully-automated`
4. Remaining tasks form the "anchor" — compute their embedding centroid and pool their `implied_skills`
5. For each removed task, query the graph for replacement candidates, scored by:
   - Proximity to the removed task (embedding similarity)
   - Synergy with the anchor (similarity to the centroid of tasks being kept)
   - Skill-gap distance (overlap with anchor's `implied_skills` — small gap = realistic reskill, large gap = stretch, flag accordingly)
   - Non-redundancy (penalize candidates too similar to tasks already kept)
   - Return top 2–3 candidates per gap, not just the single best match
6. LLM generates a rationale per swap, citing the source `org_id` / `job_title` the replacement task was drawn from, and explicitly notes the skill overlap driving the recommendation

---

## 5. Tech stack

| Layer | Choice | Notes |
|---|---|---|
| Storage | Postgres + pgvector | Handles 5,000+ task embeddings comfortably; avoids standing up Neo4j for a prototype |
| Offline graph computation | Python + NetworkX | Cluster detection (Louvain), edge computation, run as a batch script |
| LLM tasks | Claude API | Skill extraction, job decomposition, agency-label confidence scoring, rationale generation |
| Backend API | FastAPI (or equivalent) | Serves cached graph JSON + live redesign endpoint |
| Frontend | React + Cytoscape.js or react-force-graph | Force-directed graph, org clusters, color by `agency_label`, click-through detail panel |

---

## 6. API surface (minimum for demo)

- `GET /graph` → cached nodes/edges/clusters JSON for visualization
- `GET /task/{task_id}` → full task detail (org, job, label, implied_skills)
- `POST /redesign` → body: `{job_title, job_description}` → returns:
  - Original decomposed tasks + inherited labels
  - Stripped (fully-automated) tasks
  - Retained tasks
  - Replacement candidates per gap, each with score breakdown, rationale, and source provenance

---

## 7. Definition of done (prototype)

- [ ] Graph renders all 80 org clusters, color-coded by agency label, pannable/zoomable, click-to-inspect
- [ ] Batch enrichment + embedding pipeline runs end-to-end on the full dataset
- [ ] Live `/redesign` endpoint accepts a real job title + description and returns a redesigned task list with rationale + provenance within a few seconds
- [ ] Low-confidence label matches are visibly flagged in the UI, not silently accepted
- [ ] A human (HR reviewer) can edit/reject any proposed swap before the redesign is considered final — no auto-publish

---

## 8. Open items for the team to tune during build

- Edge similarity threshold (start ~0.5, adjust once the graph is visually too dense/sparse)
- α/β weighting between text similarity and skill overlap (start 0.7/0.3)
- Confidence threshold for flagging low-confidence agency-label matches
- Choice between Cytoscape.js vs react-force-graph depending on how the 5,000+ node graph performs in-browser (may need clustering/collapse at the org level with drill-down, rather than rendering all nodes at once)

---

## 9. Explicit non-goals for this phase

- No production deployment, auth, or multi-user support
- No auto-publishing of redesigned jobs — human review is required by design
- No skills taxonomy standardization — `implied_skills` is LLM-derived free text for this phase, not mapped to a formal taxonomy (e.g. O*NET) yet
