"""Minimal explicit migration registry for the SQLite schema."""

from __future__ import annotations

import sqlite3

CURRENT_SCHEMA_VERSION = 31

MIGRATIONS: dict[int, str] = {
    2: """
        CREATE TABLE IF NOT EXISTS article_evidence_runs (
            query_id TEXT NOT NULL REFERENCES queries(id) ON DELETE CASCADE,
            article_id TEXT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
            state TEXT NOT NULL CHECK(
                state IN ('pending', 'processing', 'completed', 'failed')
            ),
            relevance_score REAL CHECK(
                relevance_score IS NULL OR relevance_score BETWEEN 0.0 AND 1.0
            ),
            question_addressed TEXT,
            topics TEXT NOT NULL DEFAULT '[]',
            contradictions TEXT NOT NULL DEFAULT '[]',
            missing_information TEXT NOT NULL DEFAULT '[]',
            selected_chunk_ids TEXT NOT NULL DEFAULT '[]',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            error_type TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(query_id, article_id)
        );

        CREATE INDEX IF NOT EXISTS idx_article_evidence_runs_state
            ON article_evidence_runs(query_id, state);
    """,
    3: """
        CREATE TABLE IF NOT EXISTS synthesis_runs (
            query_id TEXT PRIMARY KEY REFERENCES queries(id) ON DELETE CASCADE,
            state TEXT NOT NULL CHECK(
                state IN ('pending', 'processing', 'completed', 'failed')
            ),
            model_version TEXT,
            theme_plan TEXT,
            final_synthesis TEXT,
            answer_markdown TEXT,
            cited_evidence_ids TEXT NOT NULL DEFAULT '[]',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            error_type TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS theme_synthesis_runs (
            query_id TEXT NOT NULL REFERENCES synthesis_runs(query_id) ON DELETE CASCADE,
            theme_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK(
                state IN ('pending', 'processing', 'completed', 'failed')
            ),
            theme_label TEXT NOT NULL,
            article_ids TEXT NOT NULL,
            synthesis_json TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            error_type TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(query_id, theme_id)
        );

        CREATE INDEX IF NOT EXISTS idx_theme_synthesis_runs_state
            ON theme_synthesis_runs(query_id, state);
    """,
    4: """
        CREATE TABLE IF NOT EXISTS bibliographic_records (
            id TEXT PRIMARY KEY,
            canonical_key TEXT NOT NULL UNIQUE,
            doi TEXT,
            title TEXT NOT NULL,
            abstract TEXT,
            authors TEXT NOT NULL DEFAULT '[]',
            journal TEXT,
            publication_year INTEGER CHECK(
                publication_year IS NULL OR publication_year BETWEEN 1600 AND 2200
            ),
            citation_count INTEGER CHECK(
                citation_count IS NULL OR citation_count >= 0
            ),
            url TEXT,
            content_hash TEXT NOT NULL CHECK(length(content_hash) = 64),
            embedding_status TEXT NOT NULL DEFAULT 'pending' CHECK(
                embedding_status IN ('not_applicable', 'pending', 'indexed', 'failed')
            ),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_bibliographic_records_doi
            ON bibliographic_records(doi);
        CREATE INDEX IF NOT EXISTS idx_bibliographic_records_embedding
            ON bibliographic_records(embedding_status);

        CREATE TABLE IF NOT EXISTS bibliographic_record_sources (
            record_id TEXT NOT NULL REFERENCES bibliographic_records(id)
                ON DELETE CASCADE,
            source TEXT NOT NULL,
            source_id TEXT NOT NULL,
            first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(record_id, source, source_id)
        );

        CREATE TABLE IF NOT EXISTS bibliographic_harvest_runs (
            id TEXT PRIMARY KEY,
            profile TEXT NOT NULL,
            state TEXT NOT NULL CHECK(
                state IN ('running', 'completed', 'partial', 'failed')
            ),
            themes TEXT NOT NULL,
            sources TEXT NOT NULL,
            per_source_limit INTEGER NOT NULL CHECK(per_source_limit BETWEEN 1 AND 50),
            request_delay_seconds REAL NOT NULL CHECK(request_delay_seconds >= 0),
            raw_record_count INTEGER NOT NULL DEFAULT 0,
            unique_record_count INTEGER NOT NULL DEFAULT 0,
            abstract_record_count INTEGER NOT NULL DEFAULT 0,
            errors TEXT NOT NULL DEFAULT '[]',
            started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS bibliographic_harvest_hits (
            run_id TEXT NOT NULL REFERENCES bibliographic_harvest_runs(id)
                ON DELETE CASCADE,
            theme TEXT NOT NULL,
            record_id TEXT NOT NULL REFERENCES bibliographic_records(id)
                ON DELETE CASCADE,
            source TEXT NOT NULL,
            rank INTEGER NOT NULL CHECK(rank >= 1),
            PRIMARY KEY(run_id, theme, record_id, source)
        );

        CREATE INDEX IF NOT EXISTS idx_bibliographic_harvest_hits_record
            ON bibliographic_harvest_hits(record_id);

        CREATE VIRTUAL TABLE IF NOT EXISTS bibliographic_records_fts USING fts5(
            record_id UNINDEXED,
            title,
            abstract,
            authors,
            journal,
            tokenize = 'unicode61 remove_diacritics 2'
        );

        CREATE TRIGGER IF NOT EXISTS bibliographic_records_fts_insert
        AFTER INSERT ON bibliographic_records BEGIN
            INSERT INTO bibliographic_records_fts(
                rowid, record_id, title, abstract, authors, journal
            ) VALUES (
                new.rowid, new.id, new.title, new.abstract, new.authors, new.journal
            );
        END;

        CREATE TRIGGER IF NOT EXISTS bibliographic_records_fts_delete
        AFTER DELETE ON bibliographic_records BEGIN
            DELETE FROM bibliographic_records_fts WHERE rowid = old.rowid;
        END;

        CREATE TRIGGER IF NOT EXISTS bibliographic_records_fts_update
        AFTER UPDATE ON bibliographic_records BEGIN
            DELETE FROM bibliographic_records_fts WHERE rowid = old.rowid;
            INSERT INTO bibliographic_records_fts(
                rowid, record_id, title, abstract, authors, journal
            ) VALUES (
                new.rowid, new.id, new.title, new.abstract, new.authors, new.journal
            );
        END;
    """,
    5: """
        ALTER TABLE bibliographic_records ADD COLUMN relevance_status TEXT
            NOT NULL DEFAULT 'unreviewed' CHECK(
                relevance_status IN ('unreviewed', 'accepted', 'review', 'rejected')
            );
        ALTER TABLE bibliographic_records ADD COLUMN relevance_score REAL
            CHECK(relevance_score IS NULL OR relevance_score BETWEEN 0.0 AND 1.0);
        ALTER TABLE bibliographic_records ADD COLUMN relevance_reason TEXT;
        ALTER TABLE bibliographic_records ADD COLUMN relevance_theme TEXT;

        CREATE INDEX IF NOT EXISTS idx_bibliographic_records_relevance
            ON bibliographic_records(relevance_status, embedding_status);

        ALTER TABLE bibliographic_harvest_hits ADD COLUMN relevance_status TEXT
            NOT NULL DEFAULT 'unreviewed' CHECK(
                relevance_status IN ('unreviewed', 'accepted', 'review', 'rejected')
            );
        ALTER TABLE bibliographic_harvest_hits ADD COLUMN relevance_score REAL
            CHECK(relevance_score IS NULL OR relevance_score BETWEEN 0.0 AND 1.0);
        ALTER TABLE bibliographic_harvest_hits ADD COLUMN relevance_reason TEXT;

        ALTER TABLE bibliographic_harvest_runs ADD COLUMN accepted_record_count INTEGER
            NOT NULL DEFAULT 0 CHECK(accepted_record_count >= 0);
        ALTER TABLE bibliographic_harvest_runs ADD COLUMN accepted_abstract_count INTEGER
            NOT NULL DEFAULT 0 CHECK(accepted_abstract_count >= 0);
    """,
    6: """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_articles_doi_nocase
            ON articles(doi COLLATE NOCASE)
            WHERE doi IS NOT NULL;

        CREATE UNIQUE INDEX IF NOT EXISTS ux_bibliographic_records_doi_nocase
            ON bibliographic_records(doi COLLATE NOCASE)
            WHERE doi IS NOT NULL;
    """,
    7: """
        CREATE TABLE IF NOT EXISTS rejected_bibliographic_archive (
            original_record_id TEXT PRIMARY KEY,
            canonical_key TEXT NOT NULL,
            doi TEXT,
            title TEXT NOT NULL,
            relevance_score REAL CHECK(
                relevance_score IS NULL OR relevance_score BETWEEN 0.0 AND 1.0
            ),
            relevance_reason TEXT,
            relevance_theme TEXT,
            sources TEXT NOT NULL DEFAULT '[]',
            harvest_run_ids TEXT NOT NULL DEFAULT '[]',
            original_created_at TEXT,
            original_updated_at TEXT,
            first_archived_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_archived_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            rejection_count INTEGER NOT NULL DEFAULT 1 CHECK(rejection_count >= 1)
        );

        CREATE INDEX IF NOT EXISTS idx_rejected_bibliographic_archive_doi
            ON rejected_bibliographic_archive(doi COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS idx_rejected_bibliographic_archive_title
            ON rejected_bibliographic_archive(title COLLATE NOCASE);
    """,
    8: """
        ALTER TABLE bibliographic_records ADD COLUMN manual_decision TEXT
            CHECK(manual_decision IS NULL OR manual_decision = 'accepted');
        ALTER TABLE bibliographic_records ADD COLUMN manual_reviewed_at TEXT;
    """,
    9: """
        CREATE TABLE IF NOT EXISTS publisher_access_runs (
            id TEXT PRIMARY KEY,
            profile_id TEXT NOT NULL,
            authorization_reference TEXT NOT NULL,
            state TEXT NOT NULL CHECK(
                state IN ('queued', 'running', 'completed', 'partial', 'failed')
            ),
            requested_record_count INTEGER NOT NULL CHECK(requested_record_count >= 1),
            completed_record_count INTEGER NOT NULL DEFAULT 0,
            failed_record_count INTEGER NOT NULL DEFAULT 0,
            error_type TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            started_at TEXT,
            completed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS publisher_full_text_assets (
            id TEXT PRIMARY KEY,
            record_id TEXT NOT NULL REFERENCES bibliographic_records(id) ON DELETE CASCADE,
            article_id TEXT REFERENCES articles(id) ON DELETE SET NULL,
            run_id TEXT NOT NULL REFERENCES publisher_access_runs(id) ON DELETE CASCADE,
            profile_id TEXT NOT NULL,
            acquisition_method TEXT NOT NULL CHECK(
                acquisition_method IN ('browser_pdf_link', 'browser_rendered_pdf')
            ),
            source_url TEXT NOT NULL,
            final_url TEXT NOT NULL,
            media_type TEXT NOT NULL,
            file_path TEXT NOT NULL,
            sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
            byte_count INTEGER NOT NULL CHECK(byte_count > 0),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(record_id, sha256)
        );

        CREATE INDEX IF NOT EXISTS idx_publisher_assets_record
            ON publisher_full_text_assets(record_id);
        CREATE INDEX IF NOT EXISTS idx_publisher_assets_article
            ON publisher_full_text_assets(article_id);

        CREATE TABLE IF NOT EXISTS publisher_access_run_items (
            run_id TEXT NOT NULL REFERENCES publisher_access_runs(id) ON DELETE CASCADE,
            record_id TEXT NOT NULL REFERENCES bibliographic_records(id) ON DELETE CASCADE,
            state TEXT NOT NULL CHECK(
                state IN ('queued', 'processing', 'completed', 'failed')
            ),
            asset_id TEXT REFERENCES publisher_full_text_assets(id) ON DELETE SET NULL,
            error_type TEXT,
            error_message TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(run_id, record_id)
        );

        CREATE INDEX IF NOT EXISTS idx_publisher_run_items_state
            ON publisher_access_run_items(run_id, state);
    """,
    10: """
        CREATE TABLE IF NOT EXISTS chat_conversations (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 120),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_chat_conversations_updated
            ON chat_conversations(updated_at DESC);

        CREATE TABLE IF NOT EXISTS chat_messages (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES chat_conversations(id)
                ON DELETE CASCADE,
            position INTEGER NOT NULL CHECK(position >= 0),
            role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
            content TEXT NOT NULL CHECK(length(trim(content)) > 0),
            response_json TEXT,
            response_time_milliseconds REAL CHECK(
                response_time_milliseconds IS NULL OR response_time_milliseconds >= 0
            ),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(conversation_id, position)
        );

        CREATE INDEX IF NOT EXISTS idx_chat_messages_conversation
            ON chat_messages(conversation_id, position);
    """,
    11: """
        CREATE TABLE IF NOT EXISTS argo_request_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            windows_user TEXT NOT NULL CHECK(length(trim(windows_user)) > 0),
            endpoint TEXT NOT NULL CHECK(length(trim(endpoint)) > 0),
            requested_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_argo_request_events_quota
            ON argo_request_events(windows_user, requested_at);
    """,
    12: """
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL CHECK(type IN ('chat_answer')),
            state TEXT NOT NULL CHECK(
                state IN (
                    'queued', 'running', 'succeeded', 'failed',
                    'cancel_requested', 'cancelled'
                )
            ),
            step TEXT NOT NULL CHECK(
                step IN (
                    'waiting', 'search', 'enrichment', 'argo',
                    'validation', 'persistence'
                )
            ),
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
            priority INTEGER NOT NULL DEFAULT 100 CHECK(priority BETWEEN 0 AND 1000),
            attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt BETWEEN 0 AND 3),
            available_at TEXT NOT NULL,
            worker_id TEXT,
            lease_expires_at TEXT,
            heartbeat_at TEXT,
            conversation_id TEXT NOT NULL REFERENCES chat_conversations(id)
                ON DELETE CASCADE,
            user_message_id TEXT NOT NULL REFERENCES chat_messages(id)
                ON DELETE CASCADE,
            result_message_id TEXT REFERENCES chat_messages(id) ON DELETE SET NULL,
            client_request_id TEXT NOT NULL CHECK(length(client_request_id) = 36),
            error_code TEXT CHECK(
                error_code IS NULL OR error_code IN (
                    'timeout', 'quota', 'authentication', 'validation'
                )
            ),
            error_message TEXT CHECK(
                error_message IS NULL OR length(error_message) BETWEEN 1 AND 300
            ),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            UNIQUE(conversation_id, client_request_id),
            CHECK(
                state != 'running'
                OR (worker_id IS NOT NULL AND lease_expires_at IS NOT NULL)
            )
        );

        CREATE TABLE IF NOT EXISTS job_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            state TEXT NOT NULL CHECK(
                state IN (
                    'queued', 'running', 'succeeded', 'failed',
                    'cancel_requested', 'cancelled'
                )
            ),
            step TEXT NOT NULL CHECK(
                step IN (
                    'waiting', 'search', 'enrichment', 'argo',
                    'validation', 'persistence'
                )
            ),
            technical_message TEXT CHECK(
                technical_message IS NULL OR length(technical_message) BETWEEN 1 AND 300
            ),
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_jobs_claim
            ON jobs(state, priority, available_at, created_at);
        CREATE INDEX IF NOT EXISTS idx_jobs_conversation
            ON jobs(conversation_id, state, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_job_events_job
            ON job_events(job_id, created_at, id);
    """,
    13: """
        DROP INDEX IF EXISTS idx_jobs_claim;
        DROP INDEX IF EXISTS idx_jobs_conversation;
        DROP INDEX IF EXISTS idx_job_events_job;

        ALTER TABLE job_events RENAME TO job_events_v12;
        ALTER TABLE jobs RENAME TO jobs_v12;

        CREATE TABLE jobs (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL CHECK(type IN ('chat_answer', 'weekly_maintenance')),
            state TEXT NOT NULL CHECK(
                state IN (
                    'queued', 'running', 'succeeded', 'failed',
                    'cancel_requested', 'cancelled'
                )
            ),
            step TEXT NOT NULL CHECK(
                step IN (
                    'waiting', 'search', 'enrichment', 'argo', 'validation',
                    'persistence', 'backup', 'suggestions', 'harvest', 'index', 'publish'
                )
            ),
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
            priority INTEGER NOT NULL DEFAULT 100 CHECK(priority BETWEEN 0 AND 1000),
            attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt BETWEEN 0 AND 3),
            available_at TEXT NOT NULL,
            worker_id TEXT,
            lease_expires_at TEXT,
            heartbeat_at TEXT,
            conversation_id TEXT NOT NULL REFERENCES chat_conversations(id)
                ON DELETE CASCADE,
            user_message_id TEXT NOT NULL REFERENCES chat_messages(id)
                ON DELETE CASCADE,
            result_message_id TEXT REFERENCES chat_messages(id) ON DELETE SET NULL,
            client_request_id TEXT NOT NULL CHECK(length(client_request_id) = 36),
            error_code TEXT CHECK(
                error_code IS NULL OR error_code IN (
                    'timeout', 'quota', 'authentication', 'validation'
                )
            ),
            error_message TEXT CHECK(
                error_message IS NULL OR length(error_message) BETWEEN 1 AND 300
            ),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            UNIQUE(conversation_id, client_request_id),
            CHECK(
                state != 'running'
                OR (worker_id IS NOT NULL AND lease_expires_at IS NOT NULL)
            )
        );

        INSERT INTO jobs SELECT * FROM jobs_v12;

        CREATE TABLE job_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            state TEXT NOT NULL CHECK(
                state IN (
                    'queued', 'running', 'succeeded', 'failed',
                    'cancel_requested', 'cancelled'
                )
            ),
            step TEXT NOT NULL CHECK(
                step IN (
                    'waiting', 'search', 'enrichment', 'argo', 'validation',
                    'persistence', 'backup', 'suggestions', 'harvest', 'index', 'publish'
                )
            ),
            technical_message TEXT CHECK(
                technical_message IS NULL OR length(technical_message) BETWEEN 1 AND 300
            ),
            created_at TEXT NOT NULL
        );

        INSERT INTO job_events SELECT * FROM job_events_v12;
        DROP TABLE job_events_v12;
        DROP TABLE jobs_v12;

        CREATE INDEX idx_jobs_claim
            ON jobs(state, priority, available_at, created_at);
        CREATE INDEX idx_jobs_conversation
            ON jobs(conversation_id, state, created_at DESC);
        CREATE INDEX idx_job_events_job
            ON job_events(job_id, created_at, id);
        CREATE UNIQUE INDEX idx_single_active_weekly_maintenance
            ON jobs(type)
            WHERE type = 'weekly_maintenance'
              AND state IN ('queued', 'running', 'cancel_requested');
    """,
    14: """
        CREATE TABLE IF NOT EXISTS full_text_assets (
            id TEXT PRIMARY KEY,
            record_id TEXT NOT NULL REFERENCES bibliographic_records(id) ON DELETE CASCADE,
            article_id TEXT REFERENCES articles(id) ON DELETE SET NULL,
            doi TEXT NOT NULL,
            source TEXT NOT NULL,
            provider_id TEXT,
            source_url TEXT,
            final_url TEXT,
            media_type TEXT,
            license TEXT,
            state TEXT NOT NULL CHECK(
                state IN (
                    'available', 'authentication_required', 'unavailable',
                    'downloading', 'downloaded', 'ingested', 'failed'
                )
            ),
            file_path TEXT,
            sha256 TEXT CHECK(sha256 IS NULL OR length(sha256) = 64),
            byte_count INTEGER CHECK(byte_count IS NULL OR byte_count > 0),
            error_type TEXT,
            error_message TEXT,
            checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(record_id, source)
        );

        CREATE INDEX IF NOT EXISTS idx_full_text_assets_doi
            ON full_text_assets(doi COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS idx_full_text_assets_state
            ON full_text_assets(state, source);
        CREATE INDEX IF NOT EXISTS idx_full_text_assets_article
            ON full_text_assets(article_id);
    """,
    15: """
        CREATE TABLE IF NOT EXISTS full_text_provider_cooldowns (
            scope TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            host TEXT,
            reason TEXT NOT NULL,
            http_status INTEGER CHECK(
                http_status IS NULL OR http_status BETWEEN 100 AND 599
            ),
            retry_at TEXT NOT NULL,
            failure_count INTEGER NOT NULL DEFAULT 1 CHECK(failure_count > 0),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_full_text_cooldowns_retry
            ON full_text_provider_cooldowns(retry_at, source);
    """,
    16: """
        CREATE TABLE IF NOT EXISTS rag_growth_state (
            name TEXT PRIMARY KEY,
            target_pdf_count INTEGER NOT NULL CHECK(target_pdf_count > 0),
            current_pdf_count INTEGER NOT NULL DEFAULT 0 CHECK(current_pdf_count >= 0),
            query_set_index INTEGER NOT NULL DEFAULT 0 CHECK(query_set_index >= 0),
            cycle_count INTEGER NOT NULL DEFAULT 0 CHECK(cycle_count >= 0),
            state TEXT NOT NULL DEFAULT 'active' CHECK(
                state IN ('active', 'waiting', 'complete', 'blocked')
            ),
            next_retry_at TEXT,
            last_report_path TEXT,
            last_error TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """,
    17: """
        CREATE TABLE IF NOT EXISTS pilot_defects (
            id TEXT PRIMARY KEY CHECK(length(id) = 36),
            defect_type TEXT NOT NULL CHECK(
                defect_type IN (
                    'blocking', 'functional', 'usability', 'performance', 'other'
                )
            ),
            step TEXT NOT NULL CHECK(length(trim(step)) BETWEEN 1 AND 80),
            description TEXT NOT NULL CHECK(
                length(trim(description)) BETWEEN 1 AND 1500
            ),
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_pilot_defects_created
            ON pilot_defects(created_at DESC);
    """,
    18: """-- Rebuilt by add_deep_research_job_contract.""",
    19: """-- Rebuilt by add_deep_research_reranking_step.""",
    20: """
        CREATE TABLE IF NOT EXISTS document_elements (
            id TEXT PRIMARY KEY,
            article_id TEXT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
            local_element_id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('table', 'figure')),
            page_number INTEGER NOT NULL CHECK(page_number >= 1),
            bbox_json TEXT NOT NULL CHECK(json_valid(bbox_json)),
            source_kind TEXT NOT NULL CHECK(
                source_kind IN ('pdf_embedded', 'windows_ocr')
            ),
            source_locator TEXT,
            original_caption TEXT,
            synthetic_caption TEXT,
            UNIQUE(article_id, local_element_id),
            CHECK(original_caption IS NULL OR length(trim(original_caption)) > 0),
            CHECK(synthetic_caption IS NULL OR length(trim(synthetic_caption)) > 0)
        );

        CREATE INDEX IF NOT EXISTS idx_document_elements_article
            ON document_elements(article_id, page_number, kind);

        CREATE TABLE IF NOT EXISTS document_table_cells (
            element_id TEXT NOT NULL REFERENCES document_elements(id) ON DELETE CASCADE,
            row_index INTEGER NOT NULL CHECK(row_index >= 0),
            column_index INTEGER NOT NULL CHECK(column_index >= 0),
            text TEXT NOT NULL,
            PRIMARY KEY(element_id, row_index, column_index)
        );

        CREATE TABLE IF NOT EXISTS document_element_relations (
            element_id TEXT NOT NULL REFERENCES document_elements(id) ON DELETE CASCADE,
            relation TEXT NOT NULL CHECK(relation = 'nearest_page_text'),
            page_number INTEGER NOT NULL CHECK(page_number >= 1),
            related_chunk_id INTEGER REFERENCES chunks(id) ON DELETE SET NULL,
            source_excerpt TEXT NOT NULL CHECK(length(trim(source_excerpt)) > 0),
            source_excerpt_sha256 TEXT NOT NULL CHECK(length(source_excerpt_sha256) = 64),
            PRIMARY KEY(element_id, relation, source_excerpt_sha256)
        );
    """,
    21: """
        CREATE TABLE IF NOT EXISTS ocr_page_traces (
            pdf_sha256 TEXT NOT NULL CHECK(length(pdf_sha256) = 64),
            page_number INTEGER NOT NULL CHECK(page_number >= 1),
            article_id TEXT REFERENCES articles(id) ON DELETE SET NULL,
            language TEXT NOT NULL CHECK(length(trim(language)) >= 2),
            confidence REAL NOT NULL CHECK(confidence BETWEEN 0.0 AND 1.0),
            confidence_method TEXT NOT NULL CHECK(
                confidence_method = 'heuristic_text_quality_v1'
            ),
            embedded_text_original TEXT NOT NULL,
            ocr_text TEXT NOT NULL,
            admitted INTEGER NOT NULL CHECK(admitted IN (0, 1)),
            decision_reason TEXT NOT NULL CHECK(
                decision_reason IN (
                    'ocr_confident', 'ocr_low_confidence', 'ocr_empty'
                )
            ),
            PRIMARY KEY(pdf_sha256, page_number),
            CHECK(
                admitted = CASE
                    WHEN decision_reason = 'ocr_confident' THEN 1
                    ELSE 0
                END
            )
        );

        CREATE INDEX IF NOT EXISTS idx_ocr_page_traces_article
            ON ocr_page_traces(article_id, page_number);
    """,
    22: """
        CREATE VIRTUAL TABLE IF NOT EXISTS document_element_captions_fts USING fts5(
            element_id UNINDEXED,
            synthetic_caption,
            tokenize = 'unicode61 remove_diacritics 2'
        );

        INSERT INTO document_element_captions_fts(
            rowid, element_id, synthetic_caption
        )
        SELECT rowid, id, synthetic_caption
        FROM document_elements
        WHERE synthetic_caption IS NOT NULL;

        CREATE TRIGGER IF NOT EXISTS document_captions_fts_insert
        AFTER INSERT ON document_elements
        WHEN new.synthetic_caption IS NOT NULL BEGIN
            INSERT INTO document_element_captions_fts(
                rowid, element_id, synthetic_caption
            ) VALUES (new.rowid, new.id, new.synthetic_caption);
        END;

        CREATE TRIGGER IF NOT EXISTS document_captions_fts_delete
        AFTER DELETE ON document_elements
        WHEN old.synthetic_caption IS NOT NULL BEGIN
            DELETE FROM document_element_captions_fts WHERE rowid = old.rowid;
        END;

        CREATE TRIGGER IF NOT EXISTS document_captions_fts_update
        AFTER UPDATE OF synthetic_caption ON document_elements BEGIN
            DELETE FROM document_element_captions_fts WHERE rowid = old.rowid;
            INSERT INTO document_element_captions_fts(
                rowid, element_id, synthetic_caption
            )
            SELECT new.rowid, new.id, new.synthetic_caption
            WHERE new.synthetic_caption IS NOT NULL;
        END;
    """,
    23: """
        CREATE TABLE IF NOT EXISTS discovery_hypotheses (
            id TEXT PRIMARY KEY CHECK(length(id) = 36),
            status TEXT NOT NULL DEFAULT 'draft' CHECK(
                status IN ('draft', 'retained', 'rejected')
            ),
            current_version INTEGER NOT NULL DEFAULT 0 CHECK(current_version >= 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS discovery_hypothesis_versions (
            hypothesis_id TEXT NOT NULL REFERENCES discovery_hypotheses(id) ON DELETE RESTRICT,
            version INTEGER NOT NULL CHECK(version >= 1),
            content_json TEXT NOT NULL CHECK(json_valid(content_json)),
            question_sha256 TEXT NOT NULL CHECK(length(question_sha256) = 64),
            corpus_sha256 TEXT NOT NULL CHECK(length(corpus_sha256) = 64),
            evidence_sha256 TEXT NOT NULL CHECK(length(evidence_sha256) = 64),
            model_sha256 TEXT NOT NULL CHECK(length(model_sha256) = 64),
            prompt_sha256 TEXT NOT NULL CHECK(length(prompt_sha256) = 64),
            parent_version_sha256 TEXT CHECK(
                parent_version_sha256 IS NULL OR length(parent_version_sha256) = 64
            ),
            version_sha256 TEXT NOT NULL UNIQUE CHECK(length(version_sha256) = 64),
            created_at TEXT NOT NULL,
            PRIMARY KEY(hypothesis_id, version)
        );

        CREATE TRIGGER IF NOT EXISTS discovery_versions_no_update
        BEFORE UPDATE ON discovery_hypothesis_versions BEGIN
            SELECT RAISE(ABORT, 'hypothesis versions are immutable');
        END;

        CREATE TRIGGER IF NOT EXISTS discovery_versions_no_delete
        BEFORE DELETE ON discovery_hypothesis_versions BEGIN
            SELECT RAISE(ABORT, 'hypothesis versions are immutable');
        END;

        CREATE TABLE IF NOT EXISTS discovery_hypothesis_reviews (
            id TEXT PRIMARY KEY CHECK(length(id) = 36),
            hypothesis_id TEXT NOT NULL REFERENCES discovery_hypotheses(id) ON DELETE RESTRICT,
            version INTEGER NOT NULL,
            decision TEXT NOT NULL CHECK(decision IN ('retain', 'reject')),
            expert_reference TEXT NOT NULL CHECK(length(trim(expert_reference)) >= 3),
            comment TEXT CHECK(comment IS NULL OR length(comment) <= 2000),
            created_at TEXT NOT NULL,
            FOREIGN KEY(hypothesis_id, version)
                REFERENCES discovery_hypothesis_versions(hypothesis_id, version)
        );

        CREATE TABLE IF NOT EXISTS discovery_pairwise_comparisons (
            id TEXT PRIMARY KEY CHECK(length(id) = 36),
            left_hypothesis_id TEXT NOT NULL REFERENCES discovery_hypotheses(id),
            right_hypothesis_id TEXT NOT NULL REFERENCES discovery_hypotheses(id),
            winner_hypothesis_id TEXT NOT NULL REFERENCES discovery_hypotheses(id),
            judge_reference TEXT NOT NULL CHECK(length(trim(judge_reference)) >= 3),
            rubric_version TEXT NOT NULL,
            left_presented_first INTEGER NOT NULL CHECK(left_presented_first IN (0, 1)),
            created_at TEXT NOT NULL,
            CHECK(left_hypothesis_id != right_hypothesis_id),
            CHECK(winner_hypothesis_id IN (left_hypothesis_id, right_hypothesis_id))
        );

        CREATE TABLE IF NOT EXISTS discovery_ranking_snapshots (
            id TEXT PRIMARY KEY CHECK(length(id) = 36),
            comparison_sha256 TEXT NOT NULL CHECK(length(comparison_sha256) = 64),
            ranking_json TEXT NOT NULL CHECK(json_valid(ranking_json)),
            seed INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS experimental_dataset_manifests (
            id TEXT PRIMARY KEY CHECK(length(id) = 36),
            kind TEXT NOT NULL CHECK(
                kind IN ('fermentation', 'volatiles', 'polyphenols', 'sensory')
            ),
            raw_sha256 TEXT NOT NULL CHECK(length(raw_sha256) = 64),
            manifest_json TEXT NOT NULL CHECK(json_valid(manifest_json)),
            imported_by TEXT NOT NULL CHECK(length(trim(imported_by)) >= 3),
            created_at TEXT NOT NULL,
            UNIQUE(kind, raw_sha256)
        );

        CREATE TABLE IF NOT EXISTS discovery_analysis_records (
            id TEXT PRIMARY KEY CHECK(length(id) = 36),
            dataset_id TEXT NOT NULL REFERENCES experimental_dataset_manifests(id),
            record_json TEXT NOT NULL CHECK(json_valid(record_json)),
            input_sha256 TEXT NOT NULL CHECK(length(input_sha256) = 64),
            output_sha256 TEXT NOT NULL CHECK(length(output_sha256) = 64),
            approved_by TEXT NOT NULL CHECK(length(trim(approved_by)) >= 3),
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS discovery_cycle_approvals (
            id TEXT PRIMARY KEY CHECK(length(id) = 36),
            previous_hypothesis_id TEXT NOT NULL REFERENCES discovery_hypotheses(id),
            analysis_id TEXT NOT NULL REFERENCES discovery_analysis_records(id),
            next_hypothesis_id TEXT REFERENCES discovery_hypotheses(id),
            decision TEXT NOT NULL CHECK(decision IN ('approve_next', 'stop')),
            expert_reference TEXT NOT NULL CHECK(length(trim(expert_reference)) >= 3),
            provenance_json TEXT NOT NULL CHECK(json_valid(provenance_json)),
            comment TEXT CHECK(comment IS NULL OR length(comment) <= 2000),
            created_at TEXT NOT NULL
        );
    """,
    24: """
        CREATE TABLE IF NOT EXISTS chat_message_feedback (
            message_id TEXT PRIMARY KEY REFERENCES chat_messages(id) ON DELETE CASCADE,
            helpful INTEGER NOT NULL CHECK(helpful IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chat_conversation_favorites (
            conversation_id TEXT PRIMARY KEY
                REFERENCES chat_conversations(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL
        );
    """,
    25: """-- Rebuilt by add_background_job_contracts.""",
    26: """
        CREATE TABLE IF NOT EXISTS figure_analysis_runs (
            id TEXT PRIMARY KEY,
            element_id TEXT NOT NULL REFERENCES document_elements(id) ON DELETE CASCADE,
            source_document_sha256 TEXT NOT NULL CHECK(length(source_document_sha256) = 64),
            question_sha256 TEXT NOT NULL CHECK(length(question_sha256) = 64),
            image_sha256 TEXT NOT NULL CHECK(length(image_sha256) = 64),
            analysis_contract_sha256 TEXT NOT NULL CHECK(length(analysis_contract_sha256) = 64),
            model_name TEXT NOT NULL CHECK(length(trim(model_name)) > 0),
            model_revision TEXT NOT NULL CHECK(length(trim(model_revision)) > 0),
            prompt_version TEXT NOT NULL CHECK(length(trim(prompt_version)) > 0),
            figure_type TEXT NOT NULL CHECK(
                figure_type IN ('graph', 'diagram', 'photo', 'map', 'microscopy', 'other')
            ),
            relevance_score REAL NOT NULL CHECK(relevance_score BETWEEN 0.0 AND 1.0),
            readability_score REAL NOT NULL CHECK(readability_score BETWEEN 0.0 AND 1.0),
            supports_answer INTEGER NOT NULL CHECK(supports_answer IN (0, 1)),
            status TEXT NOT NULL CHECK(status IN ('candidate', 'validated', 'rejected')),
            validation_reason TEXT NOT NULL CHECK(length(trim(validation_reason)) > 0),
            observation_text TEXT NOT NULL CHECK(length(trim(observation_text)) > 0),
            visible_variables_json TEXT NOT NULL CHECK(json_valid(visible_variables_json)),
            visible_units_json TEXT NOT NULL CHECK(json_valid(visible_units_json)),
            trends_json TEXT NOT NULL CHECK(json_valid(trends_json)),
            limitations_json TEXT NOT NULL CHECK(json_valid(limitations_json)),
            duration_seconds REAL NOT NULL CHECK(duration_seconds >= 0.0),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(
                element_id, analysis_contract_sha256, image_sha256,
                model_name, model_revision
            )
        );

        CREATE INDEX IF NOT EXISTS idx_figure_analysis_element
            ON figure_analysis_runs(element_id, created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_figure_analysis_admitted
            ON figure_analysis_runs(status, relevance_score DESC, readability_score DESC);
    """,
    27: """-- Rebuilt by rename_private_ingestion_job_contract.""",
    28: """
        CREATE TABLE IF NOT EXISTS native_full_text_assets (
            id TEXT PRIMARY KEY,
            record_id TEXT NOT NULL REFERENCES bibliographic_records(id) ON DELETE CASCADE,
            doi TEXT NOT NULL,
            source TEXT NOT NULL,
            format TEXT NOT NULL CHECK(
                format IN (
                    'jats_xml', 'tei_xml', 'structured_xml', 'cleaned_text', 'plain_text'
                )
            ),
            provider_id TEXT,
            source_url TEXT NOT NULL,
            final_url TEXT,
            media_type TEXT NOT NULL,
            license TEXT,
            state TEXT NOT NULL CHECK(
                state IN (
                    'available', 'authentication_required', 'downloading',
                    'downloaded', 'failed'
                )
            ),
            file_path TEXT,
            sha256 TEXT CHECK(sha256 IS NULL OR length(sha256) = 64),
            byte_count INTEGER CHECK(byte_count IS NULL OR byte_count > 0),
            error_type TEXT,
            error_message TEXT,
            checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(record_id, source, format)
        );

        CREATE INDEX IF NOT EXISTS idx_native_full_text_assets_doi
            ON native_full_text_assets(doi COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS idx_native_full_text_assets_state
            ON native_full_text_assets(state, source, format);
    """,
    29: "",
    30: """
        ALTER TABLE bibliographic_records ADD COLUMN work_type TEXT;
        ALTER TABLE bibliographic_records ADD COLUMN publisher TEXT;
        ALTER TABLE articles ADD COLUMN work_type TEXT;
        ALTER TABLE articles ADD COLUMN publisher TEXT;
    """,
    31: "",
}


def current_version(connection: sqlite3.Connection) -> int:
    try:
        row = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0] or 0)


def ensure_current(connection: sqlite3.Connection) -> None:
    version = current_version(connection)
    if version > CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"Unsupported database schema {version}; expected {CURRENT_SCHEMA_VERSION}"
        )
    for target_version in range(version + 1, CURRENT_SCHEMA_VERSION + 1):
        migration = MIGRATIONS.get(target_version)
        if migration is None:
            raise RuntimeError(f"Missing database migration {target_version}")
        if target_version == 6:
            _assert_unique_dois(connection)
        if target_version == 18:
            from app.database.deep_research_migration import add_deep_research_job_contract

            add_deep_research_job_contract(connection)
        elif target_version == 19:
            from app.database.deep_research_migration import add_deep_research_reranking_step

            add_deep_research_reranking_step(connection)
        elif target_version == 25:
            from app.database.background_job_migration import add_background_job_contracts

            add_background_job_contracts(connection)
        elif target_version == 27:
            from app.database.corpus_ingestion_job_migration import (
                rename_private_ingestion_job_contract,
            )

            rename_private_ingestion_job_contract(connection)
        elif target_version == 29:
            from app.database.chat_progress_migration import add_chat_progress_steps

            add_chat_progress_steps(connection)
        elif target_version == 31:
            _ensure_bibliographic_type_columns(connection)
        else:
            connection.executescript(migration)
        connection.execute("INSERT INTO schema_version(version) VALUES (?)", (target_version,))


def _ensure_bibliographic_type_columns(connection: sqlite3.Connection) -> None:
    """Repair databases that observed an earlier, partial version 30 migration."""

    for table in ("bibliographic_records", "articles"):
        columns = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
        for column in ("work_type", "publisher"):
            if column not in columns:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")


def _assert_unique_dois(connection: sqlite3.Connection) -> None:
    """Fail closed before adding DOI constraints; never discard scientific records."""

    for table in ("articles", "bibliographic_records"):
        duplicate = connection.execute(
            f"""
            SELECT lower(trim(doi)) AS normalized_doi, COUNT(*) AS duplicate_count
            FROM {table}
            WHERE doi IS NOT NULL AND trim(doi) != ''
            GROUP BY lower(trim(doi))
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        ).fetchone()
        if duplicate is not None:
            raise RuntimeError(
                f"DOI duplicate audit failed for {table}: {duplicate[0]} "
                f"appears {duplicate[1]} times"
            )
