# Architecture RAG actuelle de CiderScholar

Date de l'audit : 7 août 2026\
Périmètre : état réel du workspace, y compris les modifications locales non validées présentes avant l'audit.\
Principe de lecture : les faits issus du code, de la configuration active et des bases opérationnelles sont distingués des interprétations. Aucun gain de qualité n'est revendiqué dans ce document.

## 1. Résumé exécutif

CiderScholar possède déjà un RAG scientifique local nettement plus avancé qu'un simple couplage « embeddings + prompt » : ingestion PDF reprenable, autorité SQLite, index dense Qdrant sans texte intégral, FTS5, fusion RRF, classement au niveau article, sélection de preuves, filtrage sémantique ARGO, génération structurée, citations construites par l'application, abstention, et un pipeline séparé de recherche approfondie avec checkpoints.

Le chemin de production principal reste néanmoins asymétrique :

1. les PDF sont structurés en pages, sections canoniques et éléments documentaires, mais les chunks textuels n'exploitent ni sous-sections, ni paragraphes persistés, ni hiérarchie parent-enfant ;
2. la recherche hybride est une fusion dense + FTS5, sans index sparse Qdrant, sans sélection documentaire dense dédiée et avec le reranker désactivé par défaut ;
3. la génération rapide est bien contrainte, mais sa vérification numérique est lexicale et une affirmation ne référence qu'une preuve ;
4. le mode approfondi est plus strict et reprenable, mais il est désactivé par défaut et garde seulement 12 fragments après reranking ;
5. l'infrastructure CiderQA mesure une grande partie de la chaîne, mais ne publie actuellement que Recall@20/nDCG@20 et ne porte pas de métrique déterministe de fidélité numérique.

L'index actif n'est pas « environ 700 dimensions » par troncature : il utilise la dimension native **768** de `intfloat/multilingual-e5-base`.

## 2. Topologie et responsabilités

| Couche                | Fichiers principaux                                                                                                                                                               | Responsabilité observée                                                               |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| API                   | `app/api/chatbot.py`, `app/api/schemas.py`                                                                                                                                        | Validation HTTP, contrats et adaptation des erreurs.                                  |
| Orchestration         | `app/services/workflows.py`, `app/jobs/chat_handler.py`                                                                                                                           | Assemblage de la recherche, du filtrage, de l'acquisition et de la génération.        |
| Ingestion             | `app/ingestion/pipeline.py`, `pdf_extractor.py`, `windows_ocr.py`, `metadata.py`, `chunker.py`, `embeddings.py`                                                                   | Hash, extraction, OCR optionnel, métadonnées, segmentation, embeddings.               |
| Persistance           | `app/database/sqlite.py`, `app/database/migrations.py`                                                                                                                            | Autorité des articles, chunks, preuves, éléments, états et FTS5.                      |
| Retrieval             | `app/retrieval/lexical_search.py`, `vector_search.py`, `hybrid_search.py`, `article_ranking.py`, `reranker.py`, `query_planning.py`, `semantic_filter.py`, `scientific_intent.py` | Recherche lexicale/dense, RRF, rang article, plan de requête, filtre sémantique.      |
| Preuves et synthèse   | `app/llm/article_evidence.py`, `app/llm/final_synthesis.py`, `app/updates/pilot_rag.py`                                                                                           | Sélection/extraction de preuves, réponses rapides/facettées, synthèse hiérarchique.   |
| LLM distant           | `app/llm/argo_client.py`, `app/llm/argo_quota.py`, `app/services/argo_quota.py`                                                                                                   | Unique client GPT-OSS, validation du modèle, quotas locaux et erreurs typées.         |
| Recherche approfondie | `app/deep_research/`                                                                                                                                                              | RRF, reranking, boucle de lacune, claims atomiques, vérification, admission et rendu. |
| Évaluation            | `app/evaluation/benchmark.py`, `metrics.py`, `ciderqa*.py`, `campaign.py`                                                                                                         | Classement, CiderQA, ablations, rapports signés et décision de promotion.             |
| Acquisition           | `app/updates/full_text.py`, `harvest.py`                                                                                                                                          | Découverte et téléchargement légal de PDF ou formats natifs.                          |

Les dépendances respectent globalement la séparation API → services → domaine/adaptateurs. Le frontend appelle `frontend/src/lib/api.ts` et ne touche pas SQLite ou Qdrant.

## 3. Configuration effectivement applicable

Les modèles Pydantic de `app/config.py` refusent les champs inconnus. La configuration active se trouve hors dépôt dans le répertoire utilisateur de CiderScholar ; aucun secret n'a été lu ni reproduit pendant l'audit.

### 3.1 Ingestion

`IngestionConfig` (`app/config.py:IngestionConfig`) :

- cible : 500 tokens estimés ;
- maximum annoncé : 750 ;
- overlap : 80, soit 10,7 % du maximum et 16 % de la cible ;
- seuil d'une page non vide : 25 caractères ;
- seuil de pages textuelles : 0,15 ;
- OCR par défaut : `fr-FR`, confiance 0,75 ;
- métadonnées inspectées sur 3 pages.

### 3.2 Embeddings et Qdrant

`EmbeddingConfig`, `QdrantConfig` :

- modèle : `intfloat/multilingual-e5-base`, installé localement ;
- modèle alternatif déclaré : `BAAI/bge-m3`, non indexé dans les corpus audités ;
- préfixes E5 : `query: ` et `passage: ` ;
- séquence maximale : 512 ; normalisation L2 ; CPU ;
- dimension détectée au chargement : 768 ;
- collection chunks : `science_chunks`, distance Cosine ;
- vecteurs et payload demandés sur disque ;
- collection séparée : `bibliographic_abstracts` ;
- aucun vecteur nommé, sparse vector, quantification, payload index ou paramètre HNSW explicite dans le code applicatif.

Le dossier Qdrant stocke une métadonnée de compatibilité modèle/dimension/distance. `QdrantLocalIndex.ensure_collection()` (`app/retrieval/vector_search.py`) valide modèle, dimension et Cosine ; il ne valide pas les paramètres physiques de l'index.

### 3.3 Retrieval et reranking

`RetrievalConfig`, `ArticleRankingConfig`, `RerankerConfig` :

- poids RRF : lexical 0,35, dense 0,45, réserve reranker 0,20 ;
- constante RRF : 60 ;
- candidats hybrides : 200 ; limite de sortie habituelle : 100 ;
- FTS : au plus 24 termes, préfixe dès 4 caractères ;
- rang article : meilleur fragment 0,40, top-3 0,25, titre 0,15, abstract 0,10, concepts centraux 0,10 ;
- au plus 8 chunks par article pour l'agrégation ;
- reranker installé : `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` ;
- reranker **désactivé par défaut**, CPU, batch 4, pool configuré 40.

Le poids « reranker » réservé n'est pas injecté dans `HybridSearchService.search()` : le reranking intervient plus tard au niveau article lorsque son option est activée.

### 3.4 GPT-OSS via ARGO

`ArgoConfig` et la configuration active :

- endpoint officiel ARGO imposé par validation ;
- modèle : `chat-gpt-oss-120b` ;
- température usuelle : 0,1 ;
- entrée maximale : 64 000 caractères ;
- sortie maximale : 8 192 tokens ;
- délai : 300 secondes ;
- validation de disponibilité du modèle mise en cache 300 secondes ;
- quotas applicatifs locaux : 20/minute, 120/heure, 200/3 heures.

`ArgoClient.chat()` (`app/llm/argo_client.py`) est le passage unique. Les réponses peuvent contenir `reasoning_content`, mais celui-ci n'est pas utilisé. Il n'existe pas de cache générique de réponse dans le chemin rapide. Les requêtes ne sont pas journalisées avec leurs prompts ou documents.

## 4. État réel des corpus

Deux bases ont été observées en lecture seule. Ces chiffres décrivent le 7 août 2026 et ne constituent pas un benchmark.

| Mesure                                         | Corpus privé actif | Corpus commun |
| ---------------------------------------------- | -----------------: | ------------: |
| Articles                                       |                578 |         7 743 |
| Articles indexés                               |                 45 |         7 636 |
| Articles validés/non encore pleinement indexés |                533 |           107 |
| Articles avec DOI                              |                506 |         4 401 |
| Articles avec abstract                         |                442 |         2 921 |
| Chunks                                         |             16 706 |       238 145 |
| Chunks indexés                                 |                334 |       235 154 |
| Chunks en attente                              |             16 372 |         2 991 |
| Tokens moyens estimés/chunk                    |             519,23 |        520,20 |
| Minimum observé                                |                  2 |             1 |
| Maximum observé                                |              3 294 |         6 400 |
| Éléments documentaires                         |              1 361 |       331 403 |
| Cellules de tableaux                           |             10 037 |     5 008 098 |
| Traces OCR                                     |                  4 |         1 976 |

Répartition des langues : privé 533 anglais, 41 français, 4 inconnues ; commun 5 410 anglais, 2 280 français, 53 inconnues.

Points importants :

- le maximum de 750 tokens n'est **pas respecté** par tous les chunks persistés ;
- aucun chunk observé ne possède de sous-section renseignée ;
- une majorité des chunks privés attend encore son embedding ;
- le corpus commun correspond bien à l'ordre de grandeur annoncé par l'utilisateur ;
- les formats natifs téléchargés existent seulement dans le corpus privé : 30 TEI XML téléchargés et 27 textes nettoyés disponibles ; ils ne sont pas injectés dans le RAG PDF actuel.

## 5. Ingestion documentaire exacte

### 5.1 Détection, hash et reprise

`app/services/workflows.py:pdf_paths()` énumère les PDF. `ingest_paths()` les traite séquentiellement et peut reprendre après limite mémoire.

`app/ingestion/pipeline.py:IngestionPipeline.ingest_file()` :

1. accepte seulement un PDF ;
2. calcule ou reçoit le SHA-256 ;
3. réutilise un JSON d'extraction lié au hash ;
4. détecte d'abord un doublon exact par SHA-256 ;
5. extrait le document ;
6. bascule vers l'état `ocr_required` si nécessaire ;
7. extrait les métadonnées ;
8. normalise et déduplique par DOI ;
9. segmente ;
10. persiste article, chunks, éléments et traces dans une transaction SQLite.

Le cache est stable par hash. Un fichier modifié qui conserve le DOI d'un article existant est considéré comme doublon DOI ; il n'existe pas de remplacement automatique de l'ancienne version.

### 5.2 PDF natif et cas difficiles

`app/ingestion/pdf_extractor.py:PyMuPdfExtractor.extract()` ouvre le PDF avec PyMuPDF, trie les blocs de chaque page par position et conserve le numéro de page exact. Un PDF illisible, chiffré ou malformé produit une `PdfExtractionError` et un état d'échec explicite.

Comportements observés :

- PDF multi-colonnes : l'ordre est une heuristique géométrique par blocs, sans modèle de layout ;
- tableaux : `page.find_tables()`, cellules et bounding boxes persistées ;
- figures : images embarquées/rectangles détectés, localisation persistée ;
- légendes : texte voisin le plus proche avec préfixe reconnu, ou relation vers un texte de page ;
- équations : aucune structure sémantique dédiée ; elles restent du texte si PyMuPDF les restitue ;
- bibliographie : aucune section/référence structurée dédiée ;
- métadonnées pauvres : titre de secours par première ligne significative, auteurs depuis le champ PDF, DOI par expression régulière, abstract par motif dans les trois premières pages ;
- mots-clés, affiliations et sous-sections : non persistés comme champs structurés.

### 5.3 OCR

`app/ingestion/windows_ocr.py:WindowsOcrPdfExtractor.extract()` réutilise le texte natif sur les pages riches et applique Windows OCR aux pages pauvres rendues à l'échelle 2. `ocr_text_confidence()` calcule une confiance heuristique. Les décisions `confident`, `low_confidence` et `empty` sont persistées.

L'OCR n'est pas déclenché implicitement par le chargement d'une page : un adaptateur OCR doit être fourni au workflow. La langue par défaut est française, sans sélection automatique multilingue. L'adaptateur OCR ne reconstruit pas les tables ou figures des pages OCRisées.

### 5.4 Formats natifs

`app/updates/full_text.py` découvre et télécharge légalement JATS, TEI, XML structuré, texte nettoyé ou texte brut depuis des liens typés. `_validate_native_asset()` effectue une validation de format, puis `FullTextStore.update_native_asset()` persiste l'état.

Ces actifs sont aujourd'hui une branche d'acquisition : ils ne sont ni parsés en structure scientifique, ni chunkés, ni indexés par `IngestionPipeline`.

## 6. Chunking actuel

`app/ingestion/chunker.py:ScientificChunker` :

- reconnaît par expressions régulières les titres canoniques anglais/français : abstract, introduction, méthodes, résultats, discussion, conclusion, suppléments ;
- transforme les lignes non vides en paragraphes ;
- sépare les phrases par ponctuation suivie d'une majuscule/chiffre ;
- assemble des unités jusqu'à 500 tokens estimés ou au plus 750 ;
- coupe à un changement de section ou à un saut de plus d'une page ;
- reprend une queue de phrases jusqu'à environ 80 tokens ;
- persiste texte exact, section, pages, index et compte estimé.

Il n'existe pas de :

- minimum configuré ;
- identifiant de paragraphe ou parent ;
- relation parent-enfant ;
- politique dédiée aux tableaux, figures, légendes, équations ou références ;
- protection explicite d'un couple valeur-unité, autre que rester dans la même phrase ;
- structure de sous-section.

La cause du dépassement de 750 est localisée dans `_split_long_sentence()` : la fonction détecte la longueur avec `estimate_tokens()` (mots **et ponctuation**), puis coupe par blocs approximatifs de mots (`text.split()`). Un bloc tabulaire ou fortement ponctué peut donc rester très au-dessus de la limite. Les chunks déjà persistés ne seront pas corrigés sans réingestion versionnée.

## 7. Indexation incrémentale

### 7.1 Chunks

`app/services/workflows.py:index_pending_chunks()` ouvre le backend et Qdrant à la demande. `app/ingestion/embeddings.py:EmbeddingBatchProcessor.run()` :

1. remet les statuts `processing` interrompus à un état reprenable ;
2. sélectionne `pending`, ou `failed` sur demande ;
3. marque un batch `processing` ;
4. encode avec préfixe passage ;
5. upsert les points Qdrant en attente bloquante ;
6. marque les chunks `indexed` ;
7. promeut l'article seulement lorsque tous ses chunks sont indexés.

Le payload Qdrant contient `kind`, `chunk_id`, `article_id`, `section`, pages et modèle, mais pas le texte. `QdrantLocalIndex.search()` renvoie des références ; `VectorSearchService.search()` réhydrate et contrôle le texte depuis SQLite.

### 7.2 Suppression et réindexation

`app/services/workflows.py:delete_article()` résout explicitement les chunk IDs, supprime les points Qdrant, puis supprime l'article SQLite avec cascades. Le PDF source n'est pas supprimé.

`reindex_article()` supprime les points, remet les statuts de l'article à zéro puis réencode. Il n'existe pas de synchronisation automatique complète « fichiers supprimés/modifiés ↔ SQLite ↔ Qdrant ». Les orphelins sont évités dans les opérations explicites, pas par un réconciliateur général par hash.

### 7.3 Abstracts bibliographiques

Le sous-système bibliographique dispose d'une synchronisation plus complète : hash de contenu, statuts, upsert des abstracts éligibles et suppression des IDs devenus inéligibles dans la collection `bibliographic_abstracts`.

## 8. Recherche lexicale, dense et hybride

### 8.1 Lexical

`app/retrieval/lexical_search.py:LexicalQueryBuilder.build()` applique NFKC/casefold, stopwords français/anglais, échappement FTS sûr, modes any/all/phrase et préfixes. `LexicalSearchService.search()` appelle `Database.lexical_search()` sur FTS5 `unicode61 remove_diacritics 2`.

`Database.lexical_search()` pondère section et texte et peut joindre les légendes synthétiques liées à un chunk. Les légendes synthétiques ne sont pas citables ; aucun exemplaire n'a été observé dans les deux corpus.

Limites : tokenisation des tirets, formules, DOI et ponctuation scientifique ; pas d'opérateur dédié pour concentration, température, taxon, souche ou DOI exact dans ce chemin.

### 8.2 Dense

`app/retrieval/vector_search.py:VectorSearchService.search()` encode la requête et interroge `QdrantLocalIndex.search()`. Un LRU en mémoire de 128 vecteurs de requêtes est indexé par modèle + SHA-256 de la chaîne. Le filtre porte sur le type, les articles et/ou sections.

Il n'y a pas de score minimal configuré. Les résultats restent donc relatifs au pool demandé.

### 8.3 Fusion

`app/retrieval/hybrid_search.py:HybridSearchService.search()` :

1. conserve la question originale ;
2. ajoute les variantes de requête dédupliquées ;
3. lance pour chaque variante une recherche FTS et dense ;
4. répartit les poids lexical/dense entre variantes ;
5. fusionne les listes par `reciprocal_rank_fusion()` ;
6. réhydrate les chunks depuis SQLite ;
7. se dégrade explicitement en lexical seul en cas de limite mémoire du dense.

Le traitement des variantes est séquentiel. Le RRF est chunk-level. Il n'existe pas de première passe dense au niveau document indépendante.

## 9. Requête, entités et multilingue

`app/retrieval/query_planning.py:ArgoQueryPlanningService.plan()` demande à GPT-OSS un JSON de 1 à 4 axes, termes français/anglais, matrices, exclusions et requêtes. Une réponse invalide est retentée une fois, puis `deterministic_query_plan()` fournit un plan de secours.

`app/retrieval/scientific_intent.py` ajoute des facettes scientifiques et des règles spécifiques à plusieurs thèmes cidricoles. Ce n'est pas un extracteur générique d'entités scientifiques. Il n'existe pas de modèle ou parseur déterministe général pour :

- molécules/formules ;
- microorganismes, espèces, souches ;
- matériel/méthodes ;
- température, pH, durée, concentration et unités ;
- DOI/identifiants scientifiques comme entités de retrieval.

La requête originale est toujours conservée. L'expansion bilingue est additive, jamais substitutive. Le plan ARGO généralise la question ; le fallback est un petit lexique bilingue et des règles de domaine.

## 10. Classement article et sélection de preuves

`app/retrieval/article_ranking.py:ArticleRankingService` agrège les chunks en articles avec les poids décrits en section 3.3, puis applique éventuellement une diversité thématique/année/revue.

Dans `app/services/workflows.py:search_common_corpus_full_text_evidence()` :

- 40 articles candidats pour concise/équilibré, 50 pour approfondi ;
- recherche globale et, lorsque le plan le justifie, recherche par axe ;
- score scientifique à règles pour matrices, processus, résultats et niveaux A-D ;
- quotas souples par axe ;
- reranking article optionnel ;
- sélection de passages par article.

`app/llm/article_evidence.py:EvidencePassageSelector.select()` combine overlap de termes, bonus de section, marqueurs quantitatifs et contradictions. Les méthodes sont différées sauf requête méthodologique. Les niveaux A/B reçoivent une expansion de voisinage bornée par `chunk_index` et un rayon dépendant de l'intensité. La déduplication proche est à 0,85 et la limite est 32 000 caractères par article.

Cette expansion joue partiellement le rôle d'un contexte parent, mais ne correspond pas à un parent documentaire persisté et ne garantit pas les bornes de paragraphe/sous-section.

## 11. Pools finaux et profils d'intensité

`app/chat_intensity.py:answer_intensity_budget()` définit les budgets suivants :

| Paramètre                     | Concise | Équilibré | Approfondi |
| ----------------------------- | ------: | --------: | ---------: |
| Variantes                     |       4 |         8 |          8 |
| Abstracts                     |      12 |        15 |         20 |
| Articles retenus              |       6 |         8 |         10 |
| Passages/article              |       3 |         4 |          6 |
| Chunks candidats/article      |      50 |        75 |        100 |
| Rayon de voisinage            |       1 |         2 |          3 |
| Records de preuve             |      12 |        16 |         20 |
| Items de contexte             |      12 |        20 |         20 |
| Caractères de contexte        |    24 k |      36 k |       42 k |
| Follow-up sur axes incomplets |     non |       oui |        oui |
| Sortie mono-axe               |   3 072 |     4 096 |      6 144 |
| Facettes générées             |       2 |         4 |          6 |
| Sortie finale                 |   4 096 |     6 144 |      8 192 |

`app/updates/pilot_rag.py:CiderEvidenceRagService._bounded_evidence()` limite toutefois le contexte à 10 articles et à 20 items en équilibré **comme** en approfondi, avec au plus deux passages texte et une figure par record et 2 400 caractères par passage. Le mode approfondi augmente réellement la recherche, le voisinage et la longueur, mais son pack final ne croît pas en nombre d'items au-delà d'équilibré.

Tous les profils gardent filtrage sémantique, citations, validation numérique et abstention. L'intensité ne désactive donc pas les garde-fous.

## 12. Tous les appels GPT-OSS du RAG

Tous passent par `ArgoClient.chat()`. Les températures et longueurs sont celles du code au jour de l'audit.

| Étape                        | Fichier / fonction                                                      | Appels et reprise                                           | Température / sortie                         |
| ---------------------------- | ----------------------------------------------------------------------- | ----------------------------------------------------------- | -------------------------------------------- |
| Plan de requête              | `query_planning.py:ArgoQueryPlanningService.plan()`                     | 1 + 1 si JSON invalide                                      | 0,1 / 1 800                                  |
| Filtre sémantique            | `semantic_filter.py:ArgoSemanticEvidenceFilter._assess_axis()`          | 1 par axe + 1 invalide                                      | 0,1 / 3 000                                  |
| Couverture                   | `retrieval/coverage.py` via `_semantic_filter_and_coverage()`           | 1 + 1 invalide                                              | 0,1 / 2 400                                  |
| Réponse rapide mono-axe      | `pilot_rag.py:CiderEvidenceRagService._generate_evidence_answer()`      | génération, une reprise longueur, une reprise validation    | 0,1 ; budget intensité                       |
| Réponse facettée             | `pilot_rag.py:answer_faceted()`                                         | 1 par facette puis assemblage ; reprises bornées et tracées | 0,1 ; correction configurable entre 0 et 0,2 |
| Extraction par article       | `article_evidence.py:ArticleEvidenceExtractor.extract()`                | 1/article + 1 JSON invalide                                 | 0,1 / 1 024                                  |
| Plan de thèmes               | `final_synthesis.py:_plan_themes()`                                     | 1 + 1 invalide                                              | 0,1 / budget synthèse                        |
| Synthèse par thème           | `final_synthesis.py:_synthesize_theme()`                                | 1/thème + 1 invalide                                        | 0,1 / budget synthèse                        |
| Synthèse finale              | `final_synthesis.py:_synthesize_final()`                                | 1 + 1 invalide                                              | 0,1 / 2 048 par défaut                       |
| Résumé contextuel deep       | `deep_research/contextual_summary.py`                                   | borné, checkpointé                                          | 0 / 512                                      |
| Extraction de claims deep    | `deep_research/claims.py`                                               | checkpointée                                                | 0 / 2 048                                    |
| Vérification sémantique deep | `deep_research/verification.py:SemanticClaimVerificationStage.verify()` | checkpointée                                                | 0 / 4 096                                    |
| Évaluation de lacune deep    | `deep_research/iteration.py`                                            | au plus une décision de follow-up                           | 0 / borné                                    |
| Légende synthétique          | pipeline visuel                                                         | 1/élément sélectionné                                       | 0 / 512                                      |

Les chemins abstract, mono-preuve et facetté utilisent désormais
`argo.scientific_correction_temperature` après un rejet de validation. La valeur par défaut est 0,1
et le contrat refuse toute valeur hors de 0–0,2. Chaque phase persiste dans `generation_traces` une
trace sans texte avec appels, reprises, tokens, température réellement utilisée et issue ; une phase
d'assemblage échouée avant réponse partielle reste comptée. Le choix 0 contre 0,1 demeure à calibrer
sur CiderQA et ne constitue pas encore un résultat de supériorité.

## 13. Construction du contexte et génération

### 13.1 Chemin chatbot principal

`app/services/workflows.py:_answer_chatbot()` orchestre :

1. historique borné ;
2. plan de requête ;
3. recherche des abstracts ;
4. recherche full text ;
5. fusion des preuves et éventuelle acquisition de deux PDF maximum ;
6. filtre sémantique A-D par axe ;
7. analyse de couverture ;
8. follow-up équilibré/approfondi si lacune explicite ;
9. pack de contexte ;
10. génération mono-axe ou facettée ;
11. validation et rendu applicatif.

La réponse persistée expose deux familles de mesures sans contenu scientifique. `retrieval_traces`
compte les variantes, candidats lexicaux/denses, union RRF, pools d'axes, entrée/sortie du reranker,
motifs de retrait et articles/passages réellement transmis à ARGO. `timings` agrège durée, tokens et
RAM observée avant/après chaque étape ; ces bornes ne sont pas qualifiées de pic mémoire.

Le filtre conserve `direct`, `supportive`, `peripheral`, `irrelevant`; seuls les candidats éligibles poursuivent. Une défaillance non-quota garde un état explicite et ne transforme pas silencieusement un document en preuve directe. Une sélection vide entraîne l'abstention.

### 13.2 Synthèse hiérarchique persistée

`extract_ranked_evidence()` puis `synthesize_query()` utilisent un autre chemin : `ArticleEvidenceExtractor` produit des preuves persistées par article ; `HierarchicalSynthesisService.synthesize()` planifie les thèmes, synthétise chaque thème, puis assemble la conclusion. Les checkpoints SQLite permettent `resume=True`.

Ce pipeline est un map-reduce réel, mais n'est pas le chemin de réponse rapide par défaut.

## 14. Preuve, citation et validation

### 14.1 Modèle de provenance

SQLite reste l'autorité : article → chunk → pages → preuve. Qdrant ne contient pas le texte intégral. `ChatEvidencePassage` et `ChatEvidenceRecord` transportent chunk, pages, section, niveau de preuve, titre, auteurs, année, revue et DOI.

Dans le chatbot, chaque `CitedEvidenceStatement` référence exactement un `evidence_id`. `_render_evidence_answer()` et `_apa_reference()` fabriquent ensuite citation auteur-date, pages et bibliographie à partir des métadonnées persistées. Le LLM ne compose pas le DOI ou le numéro de page.

Dans la synthèse hiérarchique, les extraits doivent être présents dans les passages autorisés et les IDs sont contrôlés. Les convergences et contradictions multi-articles exigent au moins deux articles distincts.

### 14.2 Vérification rapide

`pilot_rag.py:_validate_evidence_grounding()` contrôle :

- ID autorisé ;
- rejet des niveaux C/D ;
- présence textuelle exacte de chaque nombre cité dans la preuve ;
- présence de marqueurs source pour les affirmations causales, normatives, de sécurité ou évaluatives ;
- langue, forme et absence de fuite du processus interne.

Limites : le contrôle des nombres ne lie pas déterministement valeur et unité, ne traite pas conversion, signe, intervalle, incertitude ou précision, et peut accepter un nombre présent dans un autre contexte du même extrait. Il n'existe pas de modèle NLI post-génération général dans ce chemin.

### 14.3 Recherche approfondie

`app/deep_research/pipeline.py:DeepResearchPreparationOperations` enchaîne :

1. recherche et checkpoint sans texte ;
2. contrôle du reranking ;
3. résumé contextuel ;
4. évaluation d'une lacune et au plus une deuxième itération ;
5. traversée des DOI cités, profondeur 1, au plus 8 cibles ;
6. extraction de 20 claims atomiques maximum depuis 24 fragments maximum ;
7. vérification sémantique des dimensions implication, négation, unité, population, condition et temporalité ;
8. niveau épistémique et admission fail-closed ;
9. abstention si aucun claim admissible ;
10. rendu des citations/bibliographie par l'application.

Le retrieval deep (`app/deep_research/retrieval.py:DeepResearchRetrievalStage.search()`) utilise pour chaque variante 40 résultats lexicaux et 40 denses, RRF k=60, 80 fusionnés, 40 candidats cross-encoder puis 12 retenus. Il ne possède pas d'expansion parent-enfant persistée.

Le mode est désactivé par défaut et son activation exige un bundle de promotion CiderQA signé/valide ainsi que des contrôles mémoire/modèles.

## 15. Abstention et gestion des défaillances

- absence de preuve documentaire : résultat `insufficient` ;
- filtre sémantique vide : `abstained` ;
- ARGO indisponible/auth/quota/protocole : erreurs typées et diagnostic sans exposition des candidats bruts ;
- réponse partiellement validée : salvage des seules affirmations conformes ;
- deep research : admission fail-closed puis abstention si zéro claim ;
- index dense en limite mémoire : dégradation lexicale explicitement signalée ;
- ingestion : état `failed` ou `ocr_required`, jamais succès implicite.

## 16. Évaluation existante

### 16.1 Benchmark général

`app/evaluation/metrics.py:ranking_metrics()` calcule précision@k, rappel@k, MRR et nDCG. `app/evaluation/benchmark.py:BenchmarkRunner.run()` exécute des cas séquentiels, mesure mémoire/latence, fingerprint du corpus et traçabilité des IDs. Un run utilise un seul `top_k`, même si 10, 20 ou 50 peuvent être lancés séparément.

### 16.2 CiderQA

`app/evaluation/ciderqa.py` définit des splits stricts, hashés, pour questions directes, comparaison, multi-article, contradiction, abstention et follow-up, avec preuves abstract/body/table/figure.

`app/evaluation/ciderqa_metrics.py:evaluate_ciderqa_results()` calcule avec intervalles bootstrap :

- Recall@10/@20/@50, MRR et nDCG@10/@20/@50 aux niveaux notice/article/fragment ;
- exactitude et complétude des claims ;
- précision/rappel des citations ;
- taux d'entailment et exactitude de page ;
- fidélité numérique et couverture d'assessment lorsqu'elles sont annotées ;
- sensibilité/spécificité d'abstention, faux refus et Brier.

Chaque `CiderQAInferenceResult` peut en outre embarquer les mêmes `retrieval_traces` et `timings`
non textuels ; ils sont alors couverts par la signature du rapport. La campagne représentative et
l'agrégation p50/p95 par configuration restent à exécuter avant toute conclusion de supériorité.

`app/evaluation/ciderqa_ablation.py`, `ciderqa_promotion.py` et les rapports signés fournissent déjà la base nécessaire pour interdire une promotion non démontrée.

## 17. Invariants de confidentialité et ressources

- tous les embeddings et index Qdrant sont locaux ;
- le texte n'est pas dupliqué dans Qdrant ;
- les modèles locaux sont chargés à la demande et fermés explicitement ;
- GPT-OSS reçoit seulement les contextes bornés nécessaires via ARGO ;
- aucun PDF complet, clé ou jeton n'est renvoyé par l'API ou écrit dans les logs inspectés ;
- les traces d'observabilité excluent requête, identifiant de source, titre, DOI et extrait ;
- Qdrant local utilise un verrou de ressource pour éviter les écritures concurrentes ;
- les appels d'acquisition externes sont bornés, temporisés et reprenables.

## 18. Conclusions factuelles de l'audit

1. La fondation de traçabilité est solide : SQLite est l'autorité, les pages et IDs sont persistés, les citations sont construites par l'application.
2. Le principal défaut déterministe est l'invariant de taille des chunks non respecté sur les phrases/blocs fortement ponctués.
3. Le modèle dense courant est E5-base 768 dimensions ; aucun benchmark local ne prouve que BGE-M3 ou Jina v3 serait meilleur sur CiderScholar.
4. Le retrieval est hybride au sens dense + FTS5 + RRF, mais pas encore une cascade document → chunk → parent.
5. Le reranker est présent mais désactivé par défaut ; son pool, son modèle et sa calibration ne sont pas validés sur CiderQA.
6. La génération rapide est fortement encadrée, mais la fidélité numérique reste partielle et mono-preuve par affirmation.
7. Les profils d'intensité ont de vraies différences de recherche et de budget ; le pack final plafonne cependant équilibré et approfondi au même nombre d'items.
8. La recherche approfondie offre les contrôles scientifiques les plus stricts, mais elle n'est pas le comportement opérationnel par défaut.
9. Les formats TEI/JATS disponibles ne participent pas au RAG.
10. L'infrastructure d'évaluation est crédible, mais elle doit être étendue avant toute migration de modèle ou architecture annoncée comme meilleure.
