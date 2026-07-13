from pathlib import Path
import pandas as pd
import csv

CURRENT_DIR = Path(__file__).resolve().parent
DATA_PATH = CURRENT_DIR.parent / "data" / "Fractionalised_jobs.csv"


df = pd.read_csv(DATA_PATH)
max_length = df["Task_Line"].astype(str).str.len().max()
print(max_length)
