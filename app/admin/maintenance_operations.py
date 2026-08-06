"""Production operations used by the durable weekly maintenance handler."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.admin.corpus_backup import (
    MaintenanceBackup,
    create_maintenance_backup,
    rollback_maintenance_backup,
)
from app.admin.suggestion_ingest import import_shared_suggestions
from app.config import Settings
from app.corpora import CorpusScope, LocalProfile, settings_for_corpus
from app.corpus_packages.builder import build_corpus_package
from app.corpus_packages.offline import CommonCorpusOfflineGuard
from app.corpus_packages.publisher import archive_published_package, publish_corpus_package
from app.corpus_packages.validation import validate_corpus_counts
from app.database.sqlite import Database
from app.ingestion.embeddings import SentenceTransformerBackend
from app.services.workflows import index_pending_chunks
from app.updates.cleanup import archive_and_purge_rejected_records
from app.updates.full_text import FullTextHarvestService
from app.updates.harvest import BibliographicHarvestStore, CiderPilotHarvester
from app.updates.vector_index import index_bibliographic_abstracts


class MaintenanceOperationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    counters: dict[str, int] = Field(default_factory=dict)
    details: dict[str, str] = Field(default_factory=dict)


class MaintenancePublication(BaseModel):
    model_config = ConfigDict(extra="forbid")

    corpus_version: str
    version_directory: str
    latest_path: str
    archive_sha256: str


class ProductionMaintenanceOperations:
    def __init__(self, settings: Settings, maintenance_id: UUID) -> None:
        self.settings = settings
        self.maintenance_id = maintenance_id
        self.root = settings.paths.data_dir / "admin" / "maintenance" / str(maintenance_id)

    def backup(self) -> MaintenanceBackup:
        return create_maintenance_backup(self.settings, self.maintenance_id)

    def suggestions(self) -> MaintenanceOperationResult:
        report = import_shared_suggestions(self.settings)
        return MaintenanceOperationResult(
            counters={
                "suggestions_scanned": report.scanned,
                "suggestions_imported": report.imported,
                "suggestions_duplicates": report.duplicates,
                "suggestions_rejected": report.rejected,
                "suggestions_corrupt": report.corrupt,
                "suggestion_errors": len(report.errors),
            }
        )

    def harvest(self) -> MaintenanceOperationResult:
        corpus_settings = settings_for_corpus(self.settings, CorpusScope.COMMON)
        database = Database(corpus_settings.paths.database_path)
        database.initialize()
        if not self.settings.harvest.enabled:
            return MaintenanceOperationResult(counters={"harvest_skipped": 1})
        report = CiderPilotHarvester(corpus_settings, database).run(force=True)
        store = BibliographicHarvestStore(database)
        merged = store.merge_doi_enrichment_duplicates()
        cleanup = archive_and_purge_rejected_records(corpus_settings, database)
        full_text_audit = None
        full_text_harvest = None
        if self.settings.full_text.enabled:
            full_text_audit, full_text_harvest = FullTextHarvestService(
                corpus_settings,
                database,
            ).run(
                include_slow_fallbacks=False,
                max_downloads=self.settings.full_text.max_downloads_per_run,
            )
        return MaintenanceOperationResult(
            counters={
                "harvest_received": report.raw_record_count,
                "harvest_accepted": report.accepted_record_count,
                "harvest_errors": len(report.errors),
                "doi_duplicates_merged": len(merged),
                "rejected_archived": cleanup.archived_records,
                "full_text_resolved": (full_text_audit.resolved_count if full_text_audit else 0),
                "full_text_ingested": (full_text_harvest.ingested if full_text_harvest else 0),
                "full_text_deferred": (full_text_harvest.deferred if full_text_harvest else 0),
                "full_text_failed": full_text_harvest.failed if full_text_harvest else 0,
            },
            details={
                "harvest_state": report.state,
                "full_text_priority": (
                    "europe_pmc,istex,core,hal,semantic_scholar,openalex,"
                    "unpaywall,doaj,crossref,elsevier"
                ),
            },
        )

    def index(self) -> MaintenanceOperationResult:
        common = Database(self.settings.paths.common_database_path)
        common.initialize()
        common_settings = settings_for_corpus(self.settings, CorpusScope.COMMON)
        common_report = index_pending_chunks(
            common_settings,
            common,
            retry_failed=True,
        )
        abstract_store = BibliographicHarvestStore(common)
        abstracts_indexed = 0
        if abstract_store.pending_abstracts(limit=1):
            abstract_report = index_bibliographic_abstracts(
                common_settings,
                abstract_store,
                SentenceTransformerBackend(common_settings),
            )
            abstracts_indexed = abstract_report.records_indexed
        return MaintenanceOperationResult(
            counters={
                "pdf_chunks_indexed": common_report.chunks_indexed,
                "abstracts_indexed": abstracts_indexed,
            }
        )

    def validate(self) -> MaintenanceOperationResult:
        with CommonCorpusOfflineGuard(self.settings) as guard:
            counts = validate_corpus_counts(self.settings, guard)
        return MaintenanceOperationResult(
            counters={
                "corpus_articles": counts.articles,
                "corpus_chunks": counts.chunks,
                "corpus_vectors": counts.vectors,
            }
        )

    def publish(self) -> MaintenancePublication:
        output = self.root / "publication"
        built = build_corpus_package(self.settings, output_root=output)
        protected = archive_published_package(
            self.settings,
            Path(built.version_directory),
            profile=LocalProfile.ADMIN,
        )
        publication = publish_corpus_package(
            self.settings,
            Path(built.version_directory),
            profile=LocalProfile.ADMIN,
        )
        return MaintenancePublication(
            corpus_version=built.manifest.corpus_version,
            version_directory=publication.version_directory,
            latest_path=publication.latest_path,
            archive_sha256=protected.archive_sha256,
        )

    def rollback(self, backup: MaintenanceBackup) -> None:
        rollback_maintenance_backup(self.settings, self.maintenance_id, backup)
