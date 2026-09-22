# FUS-104A — cartographie des dépendances d'extraction, cache et ingestion

Statut : relevé documentaire en lecture seule, 14 septembre 2026. Il décrit le
workspace sale observé, sans activer de parser externe.

## Décision et ordre d'intégration

La séquence sûre est FUS-101 contrat, FUS-102 persistance, FUS-103
registre/configuration, FUS-104 cache, jobs et API/UI, FUS-201 PyMuPDF à sortie
inchangée, puis adapters expérimentaux. FUS-102 et FUS-103 sont maintenant
présents mais ne sont pas encore raccordés au pipeline : FUS-104 est donc le
point d'intégration, non un second contrat. SQLite reste l'autorité ; cache,
FTS5 et Qdrant sont dérivés et reconstructibles.

Le contrat FUS-101 est déjà présent dans app/ingestion/pdf_extractor.py. La
migration 38 et les méthodes SQLite FUS-102, ainsi que la configuration et le
registre FUS-103, sont présents. FUS-104 doit consommer ces interfaces sans
créer un contrat concurrent ni importer un adaptateur externe au démarrage.

Flux actuel :

    PDF -> workflow upload/folder -> IngestionPipeline -> SHA-256
        -> <sha>.pages.json -> PyMuPdfExtractor -> ExtractedDocument
        -> chunker -> SQLite articles/chunks/éléments/OCR/jobs -> FTS5/Qdrant

Le chemin durable suit CorpusIngestionPayload, CorpusIngestionHandler, puis le
même workflow. La faiblesse FUS-104 est la clé de cache : elle ne contient que
le SHA du fichier, pas parser/version/configuration/modèle/contrat. Une sortie
alternative peut donc être réutilisée à tort.

## Producteurs et consommateurs

| Surface | Rôle et contrat actuel | Évolution FUS-102/103/104 | Tests, risque et rollback |
| --- | --- | --- | --- |
| app/ingestion/pdf_extractor.py : ExtractedDocument, PdfExtractor, PyMuPdfExtractor | Contrat source-derived : pages, éléments, OCR, format, identité, outline, warnings. Les sous-modèles Pydantic refusent les champs inconnus ; extract(Path) retourne ExtractedDocument. | FUS-103 construit l'extracteur depuis un registre sans import lourd. FUS-104 propage identité et état, FUS-201 conserve les sorties PyMuPDF. | tests/test_pdf_extractor.py. Risque sérialisation/cache legacy ; rollback PyMuPDF + lecture compatibilité. **Déjà sale.** |
| app/ingestion/pipeline.py : IngestionPipeline | Dédup SHA/DOI/texte, cache JSON atomique, chunk puis transaction SQLite. IngestionReport contient statut, compteurs, reprise, erreur. Au relevé, le fichier n'est pas marqué modifié et il instancie directement PyMuPdfExtractor ; il ne crée pas extraction_runs ni n'appelle ParserRegistry. | Créer/finaliser extraction_runs, clé complète, parser explicite et review_required. FUS-104 distingue miss, incompatible et réutilisé, sans fallback silencieux. | tests/test_pipeline.py. Risques cache SHA seul, re-extraction pendant dédup, succès avant index. Legacy lisible ; invalider seulement clés nouvelles. |
| app/ingestion/windows_ocr.py, OcrPageTrace | OCR de repli explicite ; OCR non admis persiste comme trace mais n'est jamais chunk/evidence. | Identité modèle/config dans run et politique FUS-103 review_required. | tests/test_windows_ocr.py et test_pipeline.py. Rollback conserve traces, jamais indexées. |
| app/ingestion/chunker.py, token_budget.py | Convertit PageText en chunks paginés avec tokenizer local. | La signature cache/rechunk dépend de l'extraction choisie ; aucune dépendance FastAPI ou registre. | tests/test_token_budget.py, test_rechunk_corpus.py. Éviter circularité pipeline-chunker. |
| app/config.py : ParserResourceLimits, ExtractionParserConfig, IngestionConfig, Paths | Configuration stricte chunk/OCR et chemins common PDF/extracted. `ParserResourceLimits` borne taille, pages, RAM, timeout, réponse, tentatives et backoff ; `ExtractionParserConfig` interdit les champs inconnus, propose `pymupdf` par défaut, les modes `docling`, `ragflow_deepdoc`, `auto_experimental`, la policy `fail`/`review_required`/`explicit_retry_builtin`, et exige flag expérimental + toutes les limites pour un mode externe. | FUS-104 sérialise un snapshot canonique de cette sélection pour le `config_sha256`, sans permettre d'override runtime durant une reprise. La policy est un contrat de configuration ; son effet job/pipeline reste à implémenter. | tests/test_config.py, test_parser_registry.py, test_pipeline.py. Risque : hasher une configuration partielle ou la politique sans l'appliquer. **Déjà sale.** |
| app/ingestion/parser_registry.py : ParserRegistry, ParserRegistryError, ParserCapability | Registre sans sonde ni import externe : `pymupdf` disponible via factory lazy ; `docling` et `ragflow_deepdoc` indisponibles ; `auto_experimental` inconnu. `capability()` produit un diagnostic strict et `create()` refuse les modes non disponibles, sans fallback. | FUS-104 appelle ce registre seulement après validation/configuration et persiste le parser réellement créé. Lorsqu'un adapter sera enregistré, la factory devra fournir une identité FUS-101 complète ; le type de factory est encore `object`, à resserrer au contrat extracteur sans circularité. | tests/test_parser_registry.py. Risque : déclarer un adapter disponible avant ses gates FUS-004/003, ou transformer un refus en PyMuPDF implicite. Rollback : conserver les capacités externes indisponibles. **Nouveau FUS observé.** |
| app/database/migrations.py : migration 38 | `extraction_runs` persiste file SHA, article FK `SET NULL`, parser/version, version de contrat, config SHA, modèle nom/SHA, états `started`/`completed`/`review_required`/`failed`, compteurs, SHA texte normalisé, durée et diagnostics bornés. L'index unique porte l'identité complète (modèle normalisé par `COALESCE`). | FUS-104 rattache cette identité au manifeste cache et au job ; il ne crée pas une seconde table de cache. | tests/test_extraction_runs.py. Risque : identité sans numéro de tentative mais une ligne durable par identité est voulue ; migration ascendante, sources intactes. **Déjà sale (FUS-102).** |
| app/database/sqlite.py : start_extraction_run, complete_extraction_run, mark_extraction_run_review_required, fail_extraction_run, extraction_run | Les méthodes valident SHA/chaînes/diagnostics, démarrent idempotemment une identité, peuvent attacher ultérieurement l'article, puis finalisent atomiquement depuis `started`. Elles refusent une transition terminale divergente ; `failed` garde seulement un diagnostic borné. | FUS-104 orchestre ces méthodes autour du cache/extracteur : start avant extraction, `completed` seulement après admission, `review_required` sans chunks/index, `failed` pour tout refus/erreur. L'admission article/chunks et la finalisation restent à rendre cohérentes dans le pipeline. | tests/test_extraction_runs.py, test_pipeline.py. Risque atomicité run/article ; aucun PDF/texte ne va dans les diagnostics. **Déjà sale (FUS-102).** |
| app/services/workflows.py : ingest_paths, ingest_and_index_paths | Entrée partagée API/job ; OCR explicite, retry mémoire, indexation seulement des articles résolus. | Propager parser demandé/réel, cache et warnings ; ne pas toucher retrieval/cache chat. | tests/test_ui_workflows.py. Risque fallback caché ou indexation review. **Déjà sale.** |
| app/jobs/contracts.py : CorpusIngestionPayload | Payload V1 : chemins PDF relatifs validés, sans parser. | Versionner sans casser V1 : choix parser, config snapshot et policy sûrs ; V1 reprend explicitement PyMuPDF. | tests/test_job_contracts.py. Risque jobs JSON existants, extra forbid. |
| app/jobs/repository.py, background_handlers.py, run_job_worker.py | Queue durable, confinement sous pdf_dir, étape INGESTION et résumé par statut. | Exposer parser demandé/réel, cache, warnings/review sans contenu scientifique ; ne charger aucun parser lourd au boot. | tests/test_background_jobs.py, test_job_worker.py. Snapshot requis pour reprise reproductible. |
| app/api/ingestion.py, schemas.py, serialization.py, system.py | Routes upload/folder/index/reindex, listing jobs et compteurs OCR/échec ; requêtes strictes. | Choix parser par action explicite ; projection bornée parser/cache/warnings/review. Modifier schéma, sérialisation et tests ensemble. | tests/test_web_api.py. Ne jamais exposer chemin, texte de warning ou config secrète. |
| frontend/src/lib/api.ts, frontend/src/types/api.ts | Client unique ; IngestionReport TS omet déjà element_count et ocr_uncertain_page_count pourtant produits backend. | Mettre à jour types et méthodes upload/folder en même commit. | Typecheck, ingestionSummary.test.ts, CorpusImportPanel tests. Divergence backend/TS actuelle à corriger. |
| frontend/src/features/corpus : CorpusPage, CorpusImportPanel, CorpusSupportPanels, summary, journal | Affiche états chunks_ready, duplicate, ocr_required, failed. | Afficher parser demandé/réel, réutilisation, warnings, revue et états complets accessibles. | Tests composants. Ne jamais afficher succès si review ou index incomplet. |
| scripts/rechunk_corpus.py | Relit directement <sha>.pages.json, rechunk transactionnel, remappe preuves/relations et FTS. | Sélectionner une extraction compatible explicitement ou refuser ; pas de dernier cache arbitraire. | tests/test_rechunk_corpus.py. Risque faux localisateur ; backup/staging permet rollback. |
| scripts/audit_local_pdf_metadata.py, enrich_corpus_metadata.py, corpus migration/merge | Lecteurs indirects de pdf_path, articles et jobs. | Ne doivent pas réécrire identité/cache parser. | Tests audit/enrichissement ; éviter migrations concurrentes. |
| app/updates/full_text.py, native_full_text_assets, harvest scripts | JATS/TEI/XML/texte persistent en base mais IngestionPipeline refuse tout sauf PDF. | FUS-104 ne les branche pas. FUS-304/305 ajoutent parsers sécurisés et admission distincte. | tests/test_native_full_text.py, test_bibliographic_unification.py. Ne jamais présenter XML comme PDF paginé. |

## Contrats HTTP et TypeScript à changer ensemble

| Contrat | Backend | Frontend | Invariants |
| --- | --- | --- | --- |
| Upload/folder et job | FolderIngestionRequest, upload, CorpusIngestionPayload, repository/handler | api.ts, CorpusPage, import panel | Choix explicite ; V1 lisible ; pas de chemin externe/champ inconnu. |
| IngestionReport | modèle pipeline, routes, sérialisation | type API, summary/import tests | parser demandé/réel, cache, review/warnings bornés ; compatibilité états. |
| GET corpus journal | list_ingestion_jobs, corpus_listing | IngestionJob, journal/support panels | Aucun texte PDF, config secrète ou diagnostic non borné. |
| Overview statistiques | system._corpus_statistics | CorpusStatistics et cartes | Compter revue/parser failure séparément du succès/OCR. |
| Reindex | route, workflow, index report | api.ts et action UI | Refuser extraction incompatible ; pas de fallback silencieux. |

## Checklist de non-régression

- [ ] La clé inclut SHA asset, parser id/version, contrat, config SHA, modèle nom/SHA.
- [ ] Cache legacy lisible uniquement par compatibilité documentée, jamais confondu avec alternatif.
- [ ] Job V1 reprend PyMuPDF explicitement ; job V2 est reproductible au redémarrage.
- [ ] Aucun modèle/client HTTP/index/parser lourd au démarrage ; aucun téléchargement implicite.
- [ ] Taille/pages/RAM/timeout/modèle absent/sortie invalide donnent état explicite et reprenable, sans texte scientifique dans logs.
- [ ] review_required n'écrit ni chunks ni index ; asset et diagnostic borné restent inspectables.
- [ ] Article/chunks/éléments/OCR/finalisation run sont atomiques ou idempotents.
- [ ] Pydantic API, api.ts, types, UI et tests évoluent ensemble ; UI accessible avec tous les états.
- [ ] Rechunk refuse une extraction inconnue/incompatible et conserve sa sauvegarde SQLite.
- [ ] SQLite demeure source de texte/preuve ; Qdrant ne décide pas une citation.
- [ ] Tests : hit/miss, changement parser/config/modèle/contrat, cache corrompu, job repris, OCR, rollback builtin, aucune connexion.

## Fichiers sales à préserver

À la vérification du 14 septembre, `app/ingestion/pipeline.py` n'est **pas**
marqué modifié ; l'étiquette « déjà sale » antérieure est donc retirée. Il ne
doit néanmoins pas être modifié incidentalement par FUS-104.

Modifications préexistantes ou parallèles à préserver, hors périmètre de ce
document : les fichiers chat/LLM/retrieval/UI listés par `git status`,
`app/services/workflows.py`, `app/updates/pilot_rag.py` et leurs tests. Les
modifications FUS attribuables au relevé sont distinctes : FUS-101
(`app/ingestion/pdf_extractor.py`, `tests/test_pdf_extractor.py`), FUS-102
(`app/database/migrations.py`, `app/database/sqlite.py`,
`tests/test_extraction_runs.py`) et FUS-103 (`app/config.py`,
`app/ingestion/parser_registry.py`, `tests/test_config.py`,
`tests/test_parser_registry.py`). Les nouveaux
`app/evaluation/extraction_benchmark.py`,
`scripts/validate_extraction_benchmark.py`,
`tests/test_extraction_benchmark.py` et leur documentation relèvent de
FUS-003. Cette attribution est documentaire : FUS-104 ne modifie aucun de ces
fichiers.

## Zones inconnues

1. Le raccordement reste absent : pipeline/jobs/API n'utilisent encore ni
   `ParserRegistry` ni `extraction_runs`. Il faut décider si le run est créé
   avant ou après la déduplication, puis documenter le cas duplicate sans
   produire un faux `completed`.
2. Format définitif du cache, manifeste, verrou concurrent et éviction non
   décidés : ne pas déduire le dernier cache d'un nom de fichier ; l'identité
   de la migration doit devenir sa clé canonique.
3. Aucun schéma job/HTTP/TypeScript ne porte encore sélection, identité réelle,
   réutilisation ou revue. La policy FUS-103 n'a donc pas encore de sémantique
   d'exécution observable.
4. Parsers JATS/TEI absents ; tout pont FUS-104 est prématuré.
5. Limites Windows RAM/disque/timeout, modèle Docling et contrôle licence/SBOM
   restent des gates FUS-004/003/103. Les bornes sont obligatoires dans la
   configuration externe, mais leurs valeurs opérationnelles restent à
   valider humainement.
