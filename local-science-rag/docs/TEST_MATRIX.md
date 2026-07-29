# Matrice des tests obligatoires

L'étape 16 ajoute `test_argo_client.py` pour l'authentification, la liste des
modèles, les schémas JSON et les erreurs ARGO, ainsi que
`test_bibliographic_updates.py` pour les mappings, le confinement des secrets
dans les en-têtes, Clarivate Expanded, la déduplication et l'isolation des pannes
par source.

L'étape 17 ajoute `test_harvest.py` : fusion DOI multi-source, conservation de
l'abstract le plus complet, index FTS5, cadence hebdomadaire, plafond gratuit
OpenAlex, collection Qdrant séparée et fusion hybride locale.
`test_pilot_rag.py` contraint les identifiants de notices acceptés par ARGO,
refuse toute citation extérieure au contexte et contrôle le rendu DOI/abstract.
L'étape 18 ajoute la quarantaine de pertinence, l'élagage Qdrant, le rejet des
identifiants recopiés dans le texte, des normes ou interprétations de sécurité
non soutenues, et la relance bornée des sorties ARGO tronquées.
L'étape 19 ajoute la vérification DOI avant ingestion PDF, la contrainte DOI
unique insensible à la casse, la priorité du DOI sur le rapprochement titre/année
et les filtres de la base documentaire.
L’étape 21 ajoute la rotation des requêtes, la pagination multi-source, le complément DOI OpenAlex
groupé, le délai de trente jours après un échec et l’expansion bilingue locale des questions.
L’étape 22 ajoute l’archive DOI/titre avant purge, la conservation d’un DOI historique lors d’une
réapparition incomplète, la reprise du moissonnage massif, l’arrêt sur cible d’abstracts et le rejet
des homonymes CIDER/SIDRA, vinaigres, études cliniques, historiques et économiques hors périmètre.

Date de dernière validation : 2026-07-27.

| Exigence | Tests principaux | Niveau |
|---|---|---|
| Détection des doublons | empreinte SHA-256, `test_pipeline_detects_same_doi_across_different_pdf_files`, `test_store_uses_doi_before_title_fallback_and_browses_all_statuses` | unité + intégration SQLite |
| Archive des rejets | `test_rejected_records_are_archived_with_doi_and_title_before_purge`, `test_rejected_archive_preserves_a_historical_doi_when_new_hit_omits_it` | transaction SQLite + reprise |
| Filtre du corpus massif | `test_relevance_gate_rejects_broad_query_false_positives`, `test_relevance_gate_keeps_a_concise_legitimate_cider_article` | unité paramétrée |
| Extraction des pages | `test_extracts_text_with_one_based_page_numbers`, `test_empty_pdf_is_flagged_for_ocr` | intégration PyMuPDF |
| Découpage sans perte de page | `test_chunker_preserves_pages_sections_and_content_markers`, `test_chunker_never_bridges_nonconsecutive_pages` | unité |
| Recherche FTS5 | tous les tests de `test_lexical_search.py` | intégration SQLite |
| Recherche vectorielle | persistance, filtres, hydratation SQLite et suppression ciblée dans `test_vector_search.py` | intégration Qdrant local |
| Fusion RRF | `test_weighted_rrf_is_exact_deterministic_and_deduplicated`, filtres et variantes hybrides | unité + intégration |
| Vingt articles distincts | `test_selects_exactly_twenty_distinct_articles_from_twenty_five` | intégration classement |
| Validation JSON | sorties structurées ARGO, preuves et synthèses invalides dans `test_argo_client.py`, `test_article_evidence.py` et `test_final_synthesis.py` | contrat + métier |
| Impossibilité d’inventer un DOI | `test_invented_doi_and_excerpt_are_never_persisted`, `test_model_generated_doi_and_citation_text_are_forbidden` | sécurité |
| Reconstruction de la bibliographie | `test_interrupted_final_resumes_completed_theme_and_final_result` et tests d’exports UI | intégration SQLite |
| Réseau explicite | endpoint ARGO officiel, TLS obligatoire, secrets issus de l’environnement et téléchargement implicite des embeddings refusé | configuration + réseau |
| Ingestion interrompue | `test_pipeline_resumes_from_page_cache_after_database_error` | intégration reprise |
| Reprise après erreur | reprise embeddings, preuves et synthèse finale dans leurs modules respectifs | intégration état |
| Interface et API | Vitest sur le client HTTP et les états partagés, `test_web_api.py`, workflows dans `test_ui_workflows.py`, build Vite de production | composant + contrat HTTP |
| Métriques d’évaluation | `test_evaluation_metrics.py` | unité mathématique |
| Benchmark et traçabilité | `test_benchmark.py` | intégration rapport/SQLite |
| File durable étendue | `test_job_worker.py`, `test_deep_research_job.py`, `test_background_jobs.py`, `test_job_migrations.py` | contrats + reprise + migration |
| Deep Research | tests `test_deep_research_*.py`, cache, admission, citations, profils et promotion | unité + pipeline checkpointé |
| CiderQA | `test_ciderqa_*.py`, ablations, baselines, adjudication, promotion et régressions | rapports signés + gates hors ligne |
| Découverte assistée | `test_discovery_hypotheses.py`, `test_discovery_analysis.py` | contrats immuables + gates humains |
| Fonctions locales NEXT | `test_chat_local_features.py`, `test_corpus_signatures.py`, `test_windows_notifications.py` | API/SQLite + cryptographie + Windows |

## Commandes de validation

```powershell
.\.venv\Scripts\python.exe -m ruff format --check app scripts tests
.\.venv\Scripts\python.exe -m ruff check app scripts tests
.\.venv\Scripts\python.exe -m pytest -W error::ResourceWarning -q
npm.cmd --prefix frontend run ci
.\.venv\Scripts\python.exe -m scripts.benchmark_system --demo-corpus
```

Les tests fonctionnels utilisent des modèles simulés et des bases temporaires. Le benchmark
`--demo-corpus` est la validation locale réelle : il charge E5 depuis `data/models`, interroge FTS5
et Qdrant séquentiellement, puis évalue une synthèse SQLite terminée si une question correspondante
existe. Il ne déclenche jamais de génération ARGO automatiquement.

La précision à vingt divise les articles pertinents retrouvés par vingt, même lorsque le corpus de
démonstration ne contient que trois articles. Le rappel mesure uniquement les articles `expected` ;
le MRR et la précision acceptent aussi `acceptable`. Le nDCG attribue un gain de 2 aux articles
attendus et de 1 aux alternatives acceptables.
