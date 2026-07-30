"""Flatten the tasks table into a {nodes, links} graph payload for the frontend.

Postgres stores no hierarchy — `tasks` is 1,452 flat rows, and a "job" is just an
(org_id, job_title) pair that repeats. This script materialises that implied
org -> job -> task tree, then immediately flattens it again into the shape
d3-force and Pixi actually consume: two arrays, nodes and links.

Node ids are type-prefixed strings so an org and a task can never collide.
Run: python kg-backend/pipeline/export_graph.py
"""

from collections import defaultdict
from pathlib import Path
import json
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DATABASE_URL = os.getenv("DATABASE_URL")
OUT_PATH = Path(__file__).resolve().parent.parent.parent / \
    "frontend" / "public" / "graph.json"

engine = create_engine(DATABASE_URL)


def fetch_rows(conn):
    """One flat query. No nesting in SQL — grouping happens in Python where it's readable."""
    return conn.execute(text(
        "SELECT o.org_id, o.name AS org_name, t.job_title, t.task_id, "
        "       t.task_line, t.agency_label, t.ai_integration_probability_percent "
        "FROM tasks t JOIN orgs o USING (org_id) "
        "ORDER BY o.org_id, t.job_title, t.task_id")).all()


def build_graph(rows):
    """Flat rows -> {nodes, links}. Jobs are keyed on (org_id, job_title), never the
    title alone: two orgs can use the same title, and merging them would invent a
    cross-org link that does not exist in the data."""
    nodes, links = [], []
    orgs = {}
    jobs = {}
    job_task_count = defaultdict(int)

    for r in rows:
        org_key = f"org:{r.org_id}"
        if org_key not in orgs:
            orgs[org_key] = True
            nodes.append({"id": org_key, "type": "org", "label": r.org_name})

        job_key = f"job:{r.org_id}:{r.job_title}"
        if job_key not in jobs:
            jobs[job_key] = True
            nodes.append({"id": job_key, "type": "job", "label": r.job_title,
                          "org_id": r.org_id})
            links.append({"source": job_key, "target": org_key})

        task_key = f"task:{r.task_id}"
        nodes.append({
            "id": task_key,
            "type": "task",
            "label": r.task_line,
            "org_id": r.org_id,
            "agency_label": r.agency_label,
            "ai_pct": float(r.ai_integration_probability_percent),
        })
        links.append({"source": task_key, "target": job_key})
        job_task_count[job_key] += 1

    # a job's weight is how many tasks hang off it — the frontend sizes nodes by this
    for n in nodes:
        if n["type"] == "job":
            n["task_count"] = job_task_count[n["id"]]

    return {"nodes": nodes, "links": links}


def main():
    with engine.begin() as conn:
        rows = fetch_rows(conn)

    graph = build_graph(rows)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(graph))

    counts = defaultdict(int)
    for n in graph["nodes"]:
        counts[n["type"]] += 1
    print(f"{OUT_PATH}: {len(graph['nodes'])} nodes "
          f"({dict(counts)}), {len(graph['links'])} links")


if __name__ == "__main__":
    main()
