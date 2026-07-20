CREATE EXTENSION IF NOT EXISTS vector;

DROP TABLE IF EXISTS tasks;
DROP TABLE IF EXISTS skill_vectors;
DROP TABLE IF EXISTS orgs;
DROP TABLE IF EXISTS onet_skills;

CREATE TABLE orgs (
    org_id SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE onet_skills (
    element_id TEXT PRIMARY KEY,
    element_name TEXT UNIQUE NOT NULL,
    definition TEXT
);

CREATE TABLE tasks (
    task_id SERIAL PRIMARY KEY,
    task_line TEXT NOT NULL,
    job_title VARCHAR(255) NOT NULL,
    job_description TEXT,
    org_id INT NOT NULL REFERENCES orgs(org_id),
    ai_integration_probability_percent NUMERIC(5,2) NOT NULL
        CHECK (ai_integration_probability_percent BETWEEN 0 AND 100),
    agency_label VARCHAR(20) GENERATED ALWAYS AS (
        CASE
            WHEN ai_integration_probability_percent < 20 THEN 'human-centric'
            WHEN ai_integration_probability_percent < 60 THEN 'AI-augmented'
            ELSE 'fully-automated'
        END
    ) STORED
        CHECK (agency_label IN ('human-centric', 'AI-augmented', 'fully-automated')),
    implied_skills TEXT[],
    embedding vector(1536),
    effort SMALLINT CHECK (effort BETWEEN 1 AND 5),
    onet_skills TEXT[],
    task_quality VARCHAR(20) CHECK (task_quality IN ('ok', 'vague', 'boilerplate'))
);

CREATE TABLE skill_vectors (
    skill text PRIMARY KEY,
    embedding vector(1536)
);

CREATE INDEX idx_tasks_org_id ON tasks(org_id);
CREATE INDEX idx_tasks_agency_label ON tasks(agency_label);
CREATE INDEX idx_skill_vectors_embedding ON skill_vectors USING hnsw ((embedding::halfvec(1536)) halfvec_cosine_ops);


-- Querying for 1536-dim using halfvec
-- SELECT id, content
-- FROM document_embeddings
-- ORDER BY embedding::halfvec(1536) <=> '[0.012, -0.023, ...]'::halfvec(1536)
-- LIMIT 5;