from enrich import enrich_task
from openai import AzureOpenAI
import json
import os
import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

CURRENT_DIR = Path(__file__).resolve().parent
DATA_DIR = CURRENT_DIR.parent / "data"
SCHEMA_DF_DATA_PATH = DATA_DIR / "schema_df.csv"

df = pd.read_csv(SCHEMA_DF_DATA_PATH)

# A sample task line + its job context. Edit these, or pass a task as an argument.
TASKS = df.head(10)["task_line"]
JOB_DESCRIPTIONS = df.head(10)["job_description"]

# if len(sys.argv) > 2:
#     TASK = sys.argv[1]

client = AzureOpenAI(
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
)

for TASK, JOB_DESCRIPTION in zip(TASKS, JOB_DESCRIPTIONS):
    result = enrich_task(client, TASK, JOB_DESCRIPTION)
    print(f"Task: {TASK}\n")
    print(json.dumps(result, indent=2))

# result = enrich_task(client, TASK, JOB_DESCRIPTION)
