from pathlib import Path
import json
import os

from dotenv import load_dotenv
from openai import AzureOpenAI
from sqlalchemy import create_engine, text

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DATABASE_URL = os.getenv("DATABASE_URL")
# On Azure, MODEL is your chat *deployment name*, not the underlying model name.
MODEL = os.getenv("AZURE_OPENAI_DEPLOYMENT")

SYSTEM_PROMPT = """You label individual tasks from real job descriptions for a workforce \
redesign tool. For the given task (with its job's full description as context), return:

- implied_skills: 2-5 short competencies genuinely required to do THIS task specifically \
(not restated task text, not generic filler like "communication" unless the task is \
actually about communicating).
- effort: a coarse RELATIVE weight, 1-5, for how much of the job this one task represents. \
NOT hours -- a relative sense of "how big a chunk of the role is this":
    1 = trivial/quick     (log an entry, file a form)
    3 = moderate          (draft a standard report)
    5 = major/sustained   (lead a negotiation, run an investigation)

Examples (drawn from real task lines in this corpus):

Task: "Contribute to the data architecture engineering decisions to support data analytics"
implied_skills: ["data architecture", "analytical reasoning"]
effort: 2

Task: "Identify non-compliance against policies and requirements during design, \
implementation and testing phases."
implied_skills: ["regulatory compliance", "quality assurance", "attention to detail"]
effort: 3

Task: "A significant part of your role involves working with system manufacturers and \
Operators to negotiate, review, and manage Long Term Service Support (LTSS) contracts, \
ensuring cost-effective and timely support throughout the asset life cycle."
implied_skills: ["contract negotiation", "vendor management", "lifecycle planning"]
effort: 5
"""

RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "task_enrichment",
        "schema": {
            "type": "object",
            "properties": {
                "implied_skills": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "maxItems": 5,
                },
                "effort": {"type": "integer", "minimum": 1, "maximum": 5},
            },
            "required": ["implied_skills", "effort"],
            "additionalProperties": False,
        },
        "strict": True,
    },
}


def enrich_task(client: AzureOpenAI, task_line: str, job_description: str) -> dict:
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Job context: {job_description}\n\nTask: {task_line}",
            },
        ],
        response_format=RESPONSE_FORMAT,
    )
    return json.loads(response.choices[0].message.content)


def main():
    client = AzureOpenAI(
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
    )
    engine = create_engine(DATABASE_URL)

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT task_id, task_line, job_description FROM tasks "
                "WHERE implied_skills IS NULL ORDER BY task_id"
            )
        ).all()

    print(f"{len(rows)} unscored tasks remaining")

    for i, row in enumerate(rows, start=1):
        try:
            result = enrich_task(client, row.task_line, row.job_description or "")
        except Exception as e:
            print(f"[{i}/{len(rows)}] task_id={row.task_id} failed: {e}")
            continue

        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE tasks SET implied_skills = :skills, effort = :effort "
                    "WHERE task_id = :id"
                ),
                {
                    "skills": result["implied_skills"],
                    "effort": result["effort"],
                    "id": row.task_id,
                },
            )
        print(
            f"[{i}/{len(rows)}] task_id={row.task_id} effort={result['effort']} "
            f"skills={result['implied_skills']}"
        )


if __name__ == "__main__":
    main()
