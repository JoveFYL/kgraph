import os
import sys
from pathlib import Path

import ftfy
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
CURRENT_DIR = Path(__file__).resolve().parent
DATA_DIR = CURRENT_DIR.parent / "data"
FRACTIONALISED_JOBS_DATA_PATH = DATA_DIR / "Fractionalised_jobs.csv"

HUMAN_CENTRIC_MAX = 20
AI_AUGMENTED_MAX = 60


def label(pct):
    if pct < HUMAN_CENTRIC_MAX:
        return "human-centric"
    elif pct < AI_AUGMENTED_MAX:
        return "AI-augmented"
    else:
        return "fully-automated"


def canary(ok, message, *details):
    if not ok:
        print(message, *details)
        sys.exit(1)


def main():
    df = pd.read_csv(FRACTIONALISED_JOBS_DATA_PATH)

    # repair mojibake from a double UTF-8 encoding round-trip somewhere upstream
    # (~184 rows, e.g. "Subgroupâ€™s" -> "Subgroup's"); uncurl_quotes=False so
    # already-correct curly quotes elsewhere are left untouched
    df.loc[:, "Task_Line"] = df["Task_Line"].apply(
        lambda s: ftfy.fix_text(s, uncurl_quotes=False) if isinstance(s, str) else s)

    # create job_description column
    df.loc[:, "job_description"] = df.groupby(
        "S/N")["Task_Line"].transform(" ".join)

    # filter non-task rows
    tasks_df = df[~df["AI_Integration_Reasoning"].str.contains(
        "not a task", case=False, na=False)].copy()

    engine = create_engine(DATABASE_URL)

    # clear previous run's data so re-running this script doesn't duplicate rows
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE tasks, orgs RESTART IDENTITY CASCADE"))

    # insert distinct orgs, then read back the ids Postgres assigned
    agencies_df = pd.DataFrame(
        {"name": sorted(df["Original_Agency"].unique())})
    with engine.begin() as conn:
        agencies_df.to_sql("orgs", conn, if_exists="append", index=False)
        org_id_map = dict(conn.execute(
            text("SELECT name, org_id FROM orgs")).all())

    # map Original_Agency to org_id
    tasks_df.loc[:, "org_id"] = tasks_df["Original_Agency"].map(org_id_map)

    # rename columns to match the schema and select only the relevant columns
    schema_df = tasks_df.rename(columns={
        "Task_Line": "task_line",
        "Original_Job_Title": "job_title",
        "AI_Integration_Probability_Percent": "ai_integration_probability_percent",
    })[["task_line", "job_title", "job_description", "org_id", "ai_integration_probability_percent"]]

    # save the filtered tasks to a new CSV file
    schema_df.to_csv(DATA_DIR / "schema_df.csv", index=False)

    # insert tasks
    with engine.begin() as conn:
        schema_df.to_sql("tasks", conn, if_exists="append", index=False)

    # checks
    with engine.connect() as conn:
        total_count, distinct_count = conn.execute(text(
            "SELECT COUNT(*), COUNT(DISTINCT org_id) FROM tasks"
        )).one()
        print(f"Number of tasks in database: {total_count}")
        print(f"Distinct organizations in database: {distinct_count}")

        canary(total_count == len(schema_df),
               "number of tasks not equal to number of rows in schema_df")
        canary(distinct_count == len(org_id_map),
               "number of distinct org_id not equal to number of unique Original_Agency")

        # per-group canary: agency_label is GENERATED in Postgres from
        # ai_integration_probability_percent, so recompute the same buckets on
        # the source side and compare (org_id, agency_label) counts against
        labeled_df = schema_df.assign(
            agency_label=schema_df["ai_integration_probability_percent"].apply(label))
        source_counts = labeled_df.groupby(
            ["org_id", "agency_label"]).size().to_dict()

        db_counts_result = conn.execute(text(
            "SELECT org_id, agency_label, COUNT(*) AS cnt FROM tasks GROUP BY org_id, agency_label"
        ))
        db_counts = {(row.org_id, row.agency_label)
                      : row.cnt for row in db_counts_result}

        canary(source_counts == db_counts,
               "org_id/agency_label group counts do not match between source and database",
               "source:", source_counts, "database:", db_counts)

    print("All canary checks passed.")


if __name__ == "__main__":
    main()
