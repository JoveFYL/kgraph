from pathlib import Path
import os

from dotenv import load_dotenv
from openai import AzureOpenAI
from sqlalchemy import create_engine, text

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DATABASE_URL = os.getenv("DATABASE_URL")
MODEL = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT")
engine = create_engine(DATABASE_URL)

client = AzureOpenAI(
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
)


def embed_batch(client, texts: list[str]) -> list[list[float]]:
    resp = client.embeddings.create(model=MODEL, input=texts)
    return [d.embedding for d in resp.data]


def embed_tasks(client, engine):
    with engine.begin() as conn:
        result = conn.execute(text(
            "SELECT task_id, task_line "
            "FROM tasks WHERE embedding IS NULL AND task_quality = 'ok'")).all()

        if not result:
            print("No tasks to embed.")
            return

        texts = [row.task_line for row in result]
        embeddings = embed_batch(client, texts)

        for row, embedding in zip(result, embeddings):
            conn.execute(text(
                "UPDATE tasks SET embedding = :embedding WHERE task_id = :id"),
                {"embedding": str(embedding), "id": row.task_id}
            )


def cosine_sim(conn, id_a, id_b):
    """Cosine similarity via pgvector's <=> distance operator (1 - distance)."""
    return conn.execute(
        text(
            "SELECT 1 - (a.embedding <=> b.embedding) "
            "FROM tasks a, tasks b WHERE a.task_id = :a AND b.task_id = :b"
        ),
        {"a": id_a, "b": id_b},
    ).scalar()


def get_distinct_skills(engine) -> list[str]:
    with engine.begin() as conn:
        result = conn.execute(text(
            "SELECT DISTINCT unnest(implied_skills) AS skill FROM tasks "
            "WHERE task_quality = 'ok' AND implied_skills IS NOT NULL "
            "EXCEPT SELECT skill FROM skill_vectors")).all()

        distinct_skills = [row.skill for row in result]
        return distinct_skills


def embed_skills(client, engine):
    distinct_skills = get_distinct_skills(engine)

    if not distinct_skills:
        print("No new skills to embed.")
        return

    with engine.begin() as conn:
        embeddings = embed_batch(client, distinct_skills)

        for skill, embedding in zip(distinct_skills, embeddings):
            conn.execute(text(
                "INSERT INTO skill_vectors (skill, embedding) VALUES (:skill, :embedding) "
                "ON CONFLICT (skill) DO UPDATE SET embedding = :embedding"),
                {"skill": skill, "embedding": str(embedding)}
            )


def main():
    embed_tasks(client, engine)
    embed_skills(client, engine)


if __name__ == "__main__":
    main()
