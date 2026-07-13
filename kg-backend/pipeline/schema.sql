CREATE EXTENSION IF NOT EXISTS vector;

DROP TABLE IF EXISTS tasks;
DROP TABLE IF EXISTS skills;

CREATE TABLE tasks (
    task_id SERIAL PRIMARY KEY,
    task_line TEXT NOT NULL,
    job_title VARCHAR(255) NOT NULL,
    job_description TEXT,
    org_id INT NOT NULL,
    AI_Integration_Probability_Percent NUMERIC(5,2) NOT NULL
        CHECK (AI_Integration_Probability_Percent BETWEEN 0 AND 100),
    agency_label VARCHAR(20) GENERATED ALWAYS AS (
        CASE
            WHEN AI_Integration_Probability_Percent < 20 THEN 'human-centric'
            WHEN AI_Integration_Probability_Percent < 60 THEN 'AI-augmented'
            ELSE 'fully-automated'
        END
    ) STORED
        CHECK (agency_label IN ('human-centric', 'AI-augmented', 'fully-automated')),
    implied_skills TEXT[],
    effort SMALLINT CHECK (effort BETWEEN 1 AND 5),
    embedding vector(3072)
);

CREATE TABLE skills (
    skill text PRIMARY KEY,
    embedding vector(3072)
);

CREATE INDEX idx_tasks_org_id ON tasks(org_id);
CREATE INDEX idx_tasks_agency_label ON tasks(agency_label);
CREATE INDEX idx_skills_embedding ON skills USING hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops);

-- Querying for 3072-dim using halfvec
-- SELECT id, content
-- FROM document_embeddings
-- ORDER BY embedding::halfvec(3072) <=> '[0.012, -0.023, ...]'::halfvec(3072)
-- LIMIT 5;