from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import json
import os
import sys

from dotenv import load_dotenv
from openai import AzureOpenAI
from sqlalchemy import create_engine, text

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DATABASE_URL = os.getenv("DATABASE_URL")
# On Azure, MODEL is your chat *deployment name*, not the underlying model name.
MODEL = os.getenv("AZURE_OPENAI_DEPLOYMENT")

# concurrent in-flight requests. Start here; tune to your rate limit.
WORKERS = 8


def build_system_prompt(menu_text: str) -> str:
    return f"""You are an occupational analyst. You read individual task lines from real \
job descriptions and label them for a workforce-redesign tool. You are precise about the \
difference between a competency the task genuinely requires and words merely present in the text.

You are given a task line and, in <job_context>, the full description of the job it came from. \
Label ONLY the task line; use the context to disambiguate, not to describe the whole role.

Work in this order:

1. task_quality -- judge the task line first:
    "ok"          a real, specific task describing actual work.
    "vague"       generic or catch-all ("other duties as required", "support the team").
    "boilerplate" filler, legalese, or mission statements -- not a task at all.
2. onet_skills -- choose 0-5 items, ONLY from the O*NET menu below, that this task most \
specifically demands. Skills = doing; Knowledge = knowing. Pick the most specific; do not pad. \
If task_quality is not "ok", usually return an empty list.
3. implied_skills -- for an "ok" task, 2-5 short free-text competencies genuinely required \
(not a restatement of the text, not generic filler like "communication" unless the task is \
actually about that). For a non-"ok" task, return an empty list.
4. effort -- a coarse RELATIVE weight, 1-5, for how large a chunk of the role this task is. \
NOT hours:
    1 = trivial/quick   (log an entry, file a form)
    3 = moderate        (draft a standard report)
    5 = major/sustained (lead a negotiation, run an investigation)

O*NET menu -- choose onet_skills only from these exact names:

{menu_text}

Reason internally, then output only the JSON. Examples (real task lines from this corpus), \
shown in the exact format you must return:

<example>
Task: "Contribute to the data architecture engineering decisions to support data analytics"
{{"task_quality": "ok", "onet_skills": ["Systems Analysis (Skill)", "Computers and Electronics (Knowledge)"], "implied_skills": ["data architecture", "analytical reasoning"], "effort": 2}}
</example>

<example>
Task: "Identify non-compliance against policies and requirements during design, implementation and testing phases."
{{"task_quality": "ok", "onet_skills": ["Quality Control Analysis (Skill)", "Law and Government (Knowledge)"], "implied_skills": ["regulatory compliance", "quality assurance", "attention to detail"], "effort": 3}}
</example>

<example>
Task: "A significant part of your role involves working with system manufacturers and Operators to negotiate, review, and manage Long Term Service Support (LTSS) contracts, ensuring cost-effective and timely support throughout the asset life cycle."
{{"task_quality": "ok", "onet_skills": ["Negotiation (Skill)", "Management of Material Resources (Skill)"], "implied_skills": ["contract negotiation", "vendor management", "lifecycle planning"], "effort": 5}}
</example>

<example>
Task: "Other strategic work as required to support organisational objectives."
{{"task_quality": "vague", "onet_skills": [], "implied_skills": [], "effort": 1}}
</example>
"""


def build_response_format(enum_values: list[str]) -> dict:
    """The machine contract. Property ORDER = the model's reasoning order
    (least-to-most); the onet_skills enum is built from the same rows as the
    prompt menu so guidance and constraint can never drift."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "task_enrichment",
            "schema": {
                "type": "object",
                "properties": {
                    "task_quality": {
                        "type": "string",
                        "enum": ["ok", "vague", "boilerplate"],
                    },
                    "onet_skills": {
                        "type": "array",
                        "items": {"type": "string", "enum": enum_values},
                        "minItems": 0,
                        "maxItems": 5,
                    },
                    "implied_skills": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 0,
                        "maxItems": 5,
                    },
                    "effort": {"type": "integer", "minimum": 1, "maximum": 5},
                },
                "required": ["task_quality", "onet_skills", "implied_skills", "effort"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    }


def enrich_task(
    client: AzureOpenAI,
    system_prompt: str,
    response_format: dict,
    task_line: str,
    job_description: str,
) -> dict:
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": f"<job_context>{job_description}</job_context>\n\nTask: {task_line}",
            },
        ],
        response_format=response_format,
    )
    return json.loads(response.choices[0].message.content)


def load_menu(engine):
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT element_name, definition FROM onet_skills ORDER BY element_name"
        )).all()
    return rows


def build_menu_text(rows) -> str:
    """Group the 68 rows by their name suffix into a readable prompt menu."""
    skills = [r for r in rows if r.element_name.endswith("(Skill)")]
    knowledge = [r for r in rows if r.element_name.endswith("(Knowledge)")]

    def fmt(group):
        return "\n".join(f"- {r.element_name}: {r.definition}" for r in group)

    return f"SKILLS (doing):\n{fmt(skills)}\n\nKNOWLEDGE (knowing):\n{fmt(knowledge)}"


def process_one(client, system_prompt, response_format, engine, row):
    """One task, end to end: call the API, then write its own row. Runs inside a
    worker thread, so it must be self-contained -- no shared mutable state."""
    result = enrich_task(
        client, system_prompt, response_format,
        row.task_line, row.job_description or "",
    )
    with engine.begin() as conn:  # each thread checks out its OWN connection
        conn.execute(
            text(
                "UPDATE tasks SET task_quality = :quality, onet_skills = :onet, "
                "implied_skills = :skills, effort = :effort WHERE task_id = :id"
            ),
            {
                "quality": result["task_quality"],
                "onet": result["onet_skills"],
                "skills": result["implied_skills"],
                "effort": result["effort"],
                "id": row.task_id,
            },
        )
    return row.task_id, result


def main(limit=None):
    client = AzureOpenAI(
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
        max_retries=5,  # SDK auto-backoff on 429/5xx -- vital under concurrency
    )
    # pool must be >= WORKERS or threads block waiting for a DB connection
    engine = create_engine(DATABASE_URL, pool_size=WORKERS, max_overflow=4)

    # RAG: load the all onet skills once, then build prompt + enum from the same rows.
    menu_rows = load_menu(engine)
    enum_values = [r.element_name for r in menu_rows]
    system_prompt = build_system_prompt(build_menu_text(menu_rows))
    response_format = build_response_format(enum_values)

    # Resume key is onet_skills IS NULL: deliberately re-runs rows enriched
    # under the old 2-field version.
    sql = (
        "SELECT task_id, task_line, job_description FROM tasks "
        "WHERE onet_skills IS NULL "
    )
    sql += "ORDER BY random() " if limit else "ORDER BY task_id "
    if limit:
        sql += f"LIMIT {int(limit)}"

    with engine.connect() as conn:
        rows = conn.execute(text(sql)).all()

    print(f"{len(rows)} tasks to enrich with {WORKERS} workers" +
          (f" (pilot limit {limit})" if limit else ""))

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        # submit all up front; keep future->row so we can name failures
        futures = {
            pool.submit(process_one, client, system_prompt,
                        response_format, engine, row): row
            for row in rows
        }
        # as_completed yields each future the moment it finishes (any order)
        for i, fut in enumerate(as_completed(futures), start=1):
            row = futures[fut]
            try:
                task_id, result = fut.result()  # re-raises anything from the thread
                print(
                    f"[{i}/{len(rows)}] task_id={task_id} q={result['task_quality']} "
                    f"onet={result['onet_skills']} effort={result['effort']}"
                )
            except Exception as e:
                print(f"[{i}/{len(rows)}] task_id={row.task_id} failed: {e}")


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    main(limit)
