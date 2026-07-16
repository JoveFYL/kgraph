import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
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
    onet_skills_df = skills_df.rename(columns={
        "Element ID": "element_id",
        "Element Name": "element_name",
        "Description": "definition",
    })

    # suffix every name with its category: O*NET reuses names across categories
    # (e.g. Mathematics is both skill 2.A.1.e and knowledge 2.C.4.a), and names
    # are the key used in the LLM enum and tasks.onet_skills
    category = onet_skills_df["element_id"].map(
        lambda eid: "Knowledge" if eid.startswith("2.C") else "Skill"
    )
    onet_skills_df = onet_skills_df.assign(
        element_name=onet_skills_df["element_name"] + " (" + category + ")"
    )
    assert onet_skills_df["element_name"].is_unique, "duplicate element names"

    onet_skills_df.to_csv(DATA_DIR / "onet_skills_df.csv", index=False)

    engine = create_engine(DATABASE_URL)
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM onet_skills"))
        onet_skills_df.to_sql("onet_skills", conn,
                              if_exists="append", index=False)

        # checks
        result = conn.execute(text("SELECT COUNT(*) FROM onet_skills"))
        count = result.scalar()
        assert count == 68, f"expected 68 rows in onet_skills, got {count}"


if __name__ == "__main__":
    main()
