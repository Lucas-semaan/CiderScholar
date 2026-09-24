PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO schema_version(version) VALUES (1);

CREATE TABLE IF NOT EXISTS articles (
    id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE CHECK(length(sha256) = 64),
    doi TEXT UNIQUE,
    title TEXT NOT NULL,
    abstract TEXT,
    authors TEXT NOT NULL DEFAULT '[]',
    journal TEXT,
    publication_year INTEGER CHECK(
        publication_year IS NULL OR publication_year BETWEEN 1600 AND 2200
    ),
    language TEXT,
    pdf_path TEXT NOT NULL,
    validation_status TEXT NOT NULL DEFAULT 'validated' CHECK(
        validation_status IN (
            'discovered', 'metadata_only', 'downloaded', 'awaiting_validation',
            'validated', 'indexed', 'rejected', 'failed'
        )
    ),
    source TEXT NOT NULL DEFAULT 'local',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    indexed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_articles_doi ON articles(doi);
CREATE INDEX IF NOT EXISTS idx_articles_year ON articles(publication_year);
CREATE INDEX IF NOT EXISTS idx_articles_validation ON articles(validation_status);

CREATE TABLE IF NOT EXISTS article_retrieval_exclusions (
    article_id TEXT PRIMARY KEY REFERENCES articles(id) ON DELETE CASCADE,
    reason TEXT NOT NULL CHECK(length(trim(reason)) > 0),
    excluded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id TEXT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    section TEXT,
    subsection TEXT,
    page_start INTEGER,
    page_end INTEGER,
    chunk_index INTEGER NOT NULL CHECK(chunk_index >= 0),
    text TEXT NOT NULL CHECK(length(trim(text)) > 0),
    token_count INTEGER NOT NULL CHECK(token_count > 0),
    embedding_status TEXT NOT NULL DEFAULT 'pending' CHECK(
        embedding_status IN ('pending', 'processing', 'indexed', 'failed')
    ),
    CHECK(
        (page_start IS NULL AND page_end IS NULL)
        OR (page_start >= 1 AND page_end >= page_start)
    ),
    UNIQUE(article_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_chunks_article ON chunks(article_id);
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_status ON chunks(embedding_status);

CREATE TABLE IF NOT EXISTS ascocid_wiki_documents (
    id TEXT PRIMARY KEY CHECK(length(trim(id)) BETWEEN 1 AND 128),
    relative_path TEXT NOT NULL UNIQUE CHECK(length(trim(relative_path)) > 0),
    filename TEXT NOT NULL CHECK(length(trim(filename)) > 0),
    source_sha256 TEXT NOT NULL CHECK(
        length(source_sha256) = 64 AND source_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    article_id TEXT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    indexed_file_path TEXT NOT NULL CHECK(length(trim(indexed_file_path)) > 0),
    indexed_file_sha256 TEXT NOT NULL CHECK(
        length(indexed_file_sha256) = 64 AND indexed_file_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    state TEXT NOT NULL CHECK(state IN ('indexed', 'review', 'failed')),
    error_type TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ascocid_wiki_documents_article
    ON ascocid_wiki_documents(article_id, state);
CREATE INDEX IF NOT EXISTS idx_ascocid_wiki_documents_sha
    ON ascocid_wiki_documents(source_sha256);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED,
    article_id UNINDEXED,
    section,
    text,
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS chunks_fts_insert AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, chunk_id, article_id, section, text)
    VALUES (new.id, CAST(new.id AS TEXT), new.article_id, new.section, new.text);
END;

CREATE TRIGGER IF NOT EXISTS chunks_fts_delete AFTER DELETE ON chunks BEGIN
    DELETE FROM chunks_fts WHERE rowid = old.id;
END;

CREATE TRIGGER IF NOT EXISTS chunks_fts_update AFTER UPDATE ON chunks BEGIN
    DELETE FROM chunks_fts WHERE rowid = old.id;
    INSERT INTO chunks_fts(rowid, chunk_id, article_id, section, text)
    VALUES (new.id, CAST(new.id AS TEXT), new.article_id, new.section, new.text);
END;

CREATE TABLE IF NOT EXISTS queries (
    id TEXT PRIMARY KEY,
    original_query TEXT NOT NULL,
    expanded_queries TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    duration_seconds REAL,
    selected_article_ids TEXT NOT NULL DEFAULT '[]',
    corpus_version TEXT,
    model_version TEXT,
    parameters_hash TEXT
);

CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,
    query_id TEXT NOT NULL REFERENCES queries(id) ON DELETE CASCADE,
    article_id TEXT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    claim TEXT NOT NULL,
    source_excerpt TEXT NOT NULL,
    page_start INTEGER,
    page_end INTEGER,
    locator_kind TEXT NOT NULL DEFAULT 'page' CHECK(locator_kind IN ('page', 'structural')),
    section_path TEXT,
    paragraph_start INTEGER,
    paragraph_end INTEGER,
    xml_id_start TEXT,
    xml_id_end TEXT,
    relevance_score REAL NOT NULL CHECK(relevance_score BETWEEN 0.0 AND 1.0),
    CHECK(
        (locator_kind = 'page' AND page_start >= 1 AND page_end >= page_start
         AND section_path IS NULL AND paragraph_start IS NULL AND paragraph_end IS NULL)
        OR (locator_kind = 'structural' AND page_start IS NULL AND page_end IS NULL
            AND length(trim(section_path)) > 0 AND paragraph_start >= 0
            AND paragraph_end >= paragraph_start)
    )
);

CREATE INDEX IF NOT EXISTS idx_evidence_query ON evidence(query_id);
CREATE INDEX IF NOT EXISTS idx_evidence_article ON evidence(article_id);

CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pdf_path TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
    state TEXT NOT NULL CHECK(
        state IN (
            'pending', 'extracting', 'extracted', 'chunking', 'persisting',
            'chunks_ready', 'ocr_required', 'failed'
        )
    ),
    article_id TEXT REFERENCES articles(id) ON DELETE SET NULL,
    error_type TEXT,
    error_message TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(pdf_path, sha256)
);

CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_state ON ingestion_jobs(state);
