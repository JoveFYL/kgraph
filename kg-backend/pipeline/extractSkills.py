import os
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

CURRENT_DIR = Path(__file__).resolve().parent
DATA_DIR = CURRENT_DIR.parent / "data"
CONTENT_MODEL_REFERENCE_PATH = DATA_DIR / "content_model_reference.csv"


def main():
    df = pd.read_csv(CONTENT_MODEL_REFERENCE_PATH)

    # extract only skills and knowledge fields
    skills_df = df[df["Element ID"].str.contains(
        r"^2\.[ABC]", na=False, regex=True)].copy()

    # filter out non-leaf nodes (e.g. "2.A.1" is a leaf, but "2.A" is not)
    ids = skills_df["Element ID"].tolist()
    is_leaf = skills_df["Element ID"].apply(
        lambda eid: not any(other.startswith(eid + ".") for other in ids)
    )

    # apply boolean mask to filter df to only leaf nodes
    skills_df = skills_df[is_leaf]
    assert len(skills_df) == 68, f"expected 68 leaves, got {len(skills_df)}"
    skills_df = skills_df.rename(columns={
        "Element ID": "element_id",
        "Element Name": "name",
        "Description": "definition",
    })
    skills_df.to_csv(DATA_DIR / "onet_skills_df.csv", index=False)


if __name__ == "__main__":
    main()
