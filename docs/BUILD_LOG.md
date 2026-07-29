# Journal de construction et de validation

## Étape 26 — Variantes bilingues, Reranker multilingue et cascade RRF (DRS-005 à DRS-008)

Date de validation : 2026-07-23. Intégration complète de l'étage de recherche bilingue et de réévaluation par CrossEncoder pour Deep Research :
- `DRS-005` : `build_bilingual_variants` dérive des variantes bilingues inspectables stockées dans le champ `variants` de `DeepResearchSearchSnapshot` sans utiliser de labels d'évaluation.
- `DRS-006` : Module `MultilingualReranker` (`app/retrieval/reranker.py`) s'appuyant sur `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`, avec chargement paresseux et libération de ressources `close()`.
- `DRS-007` : Adaptation aux profils mémoire (`reranker_batch_size` 2 sur 8 GB, 4 sur 16 GB ; `reranker_candidate_limit` 40 sur 8 GB, 80 sur 16 GB).
- `DRS-008` : Déroulement de la cascade bilingue -> multi-corpus -> fusion RRF -> reranking CrossEncoder dans `DeepResearchRetrievalStage`.

## Étape 21 — enrichissement progressif et autonomie du corpus


Date de validation : 2026-07-21. Deux vagues complémentaires ont été exécutées sur Crossref,
Europe PMC, OpenAlex, Web of Science Expanded et Scopus. La première a reçu 112 notices brutes,
93 distinctes et 21 abstracts acceptés. Un seul appel OpenAlex groupé a ensuite contrôlé 36 DOI
acceptés sans abstract : 35 notices ont été retrouvées et 20 abstracts ajoutés. La seconde vague,
francophone et centrée sur les procédés et dérivés normands, a reçu 53 notices brutes et ajouté deux
abstracts acceptés supplémentaires.

Le cumul local atteint 271 notices stockées, 79 acceptées et 62 abstracts acceptés, tous indexés dans
Qdrant. L’audit DOI ne trouve aucun doublon. Le périmètre protéines/azote passe de zéro à cinq
abstracts exploitables ; une question française sur l’azote place désormais aux cinq premiers rangs
des travaux sur l’azote assimilable, la nutrition des levures et la fermentation cidricole.

La collecte alterne maintenant quatre vagues thématiques et pagine automatiquement au cycle suivant.
Les DOI non résolus ne sont retentés qu’après trente jours, E5 n’est chargé que si un embedding est
en attente et les questions françaises reçoivent une expansion terminologique cidricole locale avant
FTS5/E5. Les appels restent séquentiels, temporisés et plafonnés à 0,05 USD OpenAlex par campagne.

## Étape 19 — base documentaire et unicité DOI

Date de validation : 2026-07-20. Une cinquième page Streamlit, **Base
documentaire**, expose les 154 notices locales avec recherche titre/DOI/journal,
filtres de pertinence, thème, source et abstract, pagination et détail de la
provenance. Les notices acceptées, à réviser et mises en quarantaine restent
visibles sans modifier le périmètre du RAG, qui continue à n'interroger que les
abstracts acceptés.

La migration SQLite v6 ajoute des index DOI uniques insensibles à la casse aux
articles PDF et aux notices bibliographiques. Elle audite d'abord les données et
échoue sans supprimer de publication si un doublon historique existe. L'audit de
la base réelle est propre : 144 DOI bibliographiques, aucun doublon ; aucun DOI
PDF dupliqué. L'ingestion PDF contrôle désormais explicitement le DOI après
extraction, et la collecte donne priorité au DOI avant toute identité de secours
par titre et année. Deux publications portant le même titre et la même année mais
des DOI différents ne sont plus fusionnées.

Validation finale : 124 tests réussis, couverture globale 80 %, Ruff et
`pip check` sans erreur.

## Étape 18 — premier chargement curé et durcissement du RAG ARGO

Date de validation : 2026-07-20. Le corpus pilote initial a été audité titre par
titre : la recherche brute contenait notamment de la bière, un distillat de
prune, de la protéomique générale et un article sur la drosophile. Une migration
SQLite v5 ajoute donc les états `accepted`, `review` et `rejected`, leur score et
leur justification. Toutes les notices restent locales, mais seules les notices
acceptées peuvent être indexées ou interrogées par le RAG ; les anciens points
Qdrant devenus inéligibles sont retirés automatiquement.

Un premier chargement élargi à cinq résultats par thème/source a reçu 109
notices sans erreur : 93 distinctes, 53 avec abstract, 34 retenues après le
resserrement final et 19 abstracts retenus. Le cumul local atteint 154 notices
stockées, dont 57 retenues et 24 abstracts retenus, tous vectorisés. OpenAlex a
consommé 0,008 USD de budget gratuit, de 0,982 à 0,974 USD. Le bonus thématique
du classement hybride place désormais les références cidricoles spécialisées en
tête pour la microbiologie, les polyphénols, la distillation et l'azote.

Le RAG ARGO est limité à six abstracts et six phrases. Un validateur contrôle
les identifiants, les nombres, les fragments, les formulations normatives et les
interprétations de sécurité ; une correction unique est permise, ainsi qu'une
relance pour une sortie tronquée. Le test réel final a produit six affirmations
autonomes appuyées sur six sources autorisées, sans identifiant interne ni norme
inventée. La configuration a été confrontée à la documentation ARGO officielle :
endpoint `/api/chat/completions`, modèle `chat-gpt-oss-20b` et quotas publiés de
20 requêtes/minute, 120/heure et 200 par fenêtre de 180 minutes.
Validation finale : 121 tests stricts, couverture globale 81 %, Ruff et
`pip check` sans erreur.

## Étape 17 — pilote de corpus cidricole externe

Date de validation : 2026-07-20. Huit requêtes couvrent la biochimie, la
microbiologie, les polyphénols, les protéines et l'azote, le jus de pomme, le
Calvados et les eaux-de-vie, le Pommeau, ainsi que les arômes et procédés. Le
pilote limite chaque combinaison thème/source à trois notices, chaque client à
un appel par seconde et la campagne à 120 notices brutes.

La campagne réelle a reçu 96 notices sans erreur, fusionnées en 82 notices
locales, dont 39 avec abstract. OpenAlex a consommé 0,008 USD de son allocation
gratuite quotidienne : le solde est passé de 0,990 à 0,982 USD, sans crédit
prépayé. Les 39 abstracts ont été encodés localement avec E5 et enregistrés dans
la collection Qdrant séparée `bibliographic_abstracts`. Quatre recherches
hybrides FTS5/E5 ont contrôlé microbiologie, polyphénols, Calvados et
protéines/azote.

Un appel RAG réel a ensuite sélectionné huit abstracts et demandé à ARGO une
réponse structurée sur les groupes microbiens et paramètres biochimiques à
surveiller. La réponse a utilisé cinq notices autorisées, 3 599 tokens d'entrée
et 1 379 tokens de sortie ; chaque énoncé a été rendu avec DOI ou identifiant de
notice, le type `abstract` et la source.

Les notices externes restent distinctes des articles PDF : aucune page fictive
n'est créée et le chemin historique de synthèse page-traçable n'est pas encore
mélangé à ce corpus pilote. Validation finale : 116 tests stricts, couverture
globale 81 %, Ruff et `pip check` propres.

## Étape 16 — INRAE ARGO et sources bibliographiques migrées

Date de validation : 2026-07-20. Un backend de génération ARGO compatible avec
les workflows existants remplace temporairement Qwen dans le profil local. Le
modèle `chat-gpt-oss-20b` a été trouvé dans le compte puis validé par une requête
réelle à sortie JSON structurée. Le profil Ollama hors ligne demeure le défaut de
`config.example.yaml`.

Les variables du projet historique `CiderScholar` ont été inspectées et migrées
vers les variables utilisateur Windows. Les secrets ne sont présents dans aucun
fichier de la v2. Les connecteurs Crossref, Europe PMC, OpenAlex, Clarivate Web
of Science Expanded et Elsevier Scopus sont intégrés dans un service séquentiel,
explicite et tolérant aux pannes par source. Une recherche réelle
`cider fermentation`, limitée à une notice par source, a obtenu cinq sources sur
cinq et cinq notices dédupliquées, toutes avec DOI. Google Scholar/SerpAPI a été
écarté : cette source n'était pas activée dans la liste officielle historique.

Validation finale de l'étape : 111 tests avec avertissements traités comme
erreurs, 81 % de couverture globale, Ruff propre et aucune dépendance cassée
selon `pip check`.

Date : 2026-07-17. Environnement de développement disponible : Windows, Python 3.14.6. Cible du projet :
Python 3.12.x. Le code déclare donc `requires-python = ">=3.12,<3.13"`; une validation finale sur 3.12
reste requise dès que cet interpréteur est installé.

## Étape 1 — configuration et arborescence

Contrôles : mode hors ligne incompatible avec les API actives, poids de recherche totalisant 1, chemins
confinés dans `data`.

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_config.py -q
```

## Étape 2 — SQLite et FTS5

Contrôles : tables requises, triggers FTS5, transaction atomique, fermeture explicite des fichiers sous
Windows.

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_database.py -q
```

## Étapes 3 et 4 — extraction et découpage

Contrôles : PDF synthétique réel de deux pages, pages en base 1, détection OCR, sections, plafond de
tokens, absence de saut entre pages éloignées et DOI absent non inventé.

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_pdf_extractor.py tests\test_metadata.py tests\test_chunker.py -q
```

## Pipeline fonctionnel

Contrôles : déduplication, FTS5, état OCR sans OCR automatique et reprise du cache après panne SQLite
simulée.

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_pipeline.py tests\test_deduplication.py -q
python -m scripts.create_demo_corpus
python -m scripts.ingest_folder data\pdf\demo --recursive
```

Résultat du test de bout en bout : 3 articles, 3 pages chacun, 18 fragments au total, tous à l’état
`chunks_ready`.

## Validation complète

```powershell
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\python.exe -m pytest -q
```

Résultat initial du jalon ingestion : 19 tests réussis et aucune erreur Ruff.

## Mémoire

Une ingestion séquentielle neuve des trois PDF synthétiques, base et cache placés dans un dossier
temporaire, a été mesurée via `psutil.Process().memory_info().peak_wset`.

```text
statuses = [chunks_ready, chunks_ready, chunks_ready]
articles = 3
chunks = 18
peak_working_set_mb = 63.80
```

Cette valeur concerne uniquement configuration, SQLite, PyMuPDF et découpage.

## Étape 5 — embeddings multilingues

Contrôles unitaires : modèle manquant sans accès réseau, préfixes E5, lots bornés, matrice de dimension
stable, panne du stockage, reprise de `processing` et fermeture du backend.

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_embeddings.py tests\test_config.py -q
```

Le modèle a été préparé par une opération réseau explicite, isolée du corpus :

```powershell
.\.venv\Scripts\python.exe -m scripts.prepare_embedding_model --allow-network
```

Le test réel suivant a ensuite été exécuté avec `HF_HUB_OFFLINE=1`,
`TRANSFORMERS_OFFLINE=1` et `HF_DATASETS_OFFLINE=1` : une question française, deux passages pertinents
FR/EN et un passage astronomique non pertinent.

```text
query_shape = (1, 768)
document_shape = (3, 768)
norms = [1.0, 1.0, 1.0]
similarities = [0.90276, 0.86005, 0.78704]
best_document_index = 0
elapsed_seconds = 16.364
peak_working_set_mb = 1077.26
rss_after_close_mb = 439.07
```

Le premier appel inclut le chargement des poids. Le meilleur résultat est le passage français attendu.
La validation complète après cette étape compte 26 tests réussis et aucune erreur Ruff.

## Étape 6 — indexation Qdrant locale

La collection utilise Qdrant embarqué (`path=data/qdrant`), cosinus, vecteurs sur disque et payload sur
disque. Les tests couvrent persistance après réouverture, filtre article, dimensions/modèles
incompatibles, hydratation SQLite, lots bornés et reconstruction des états.

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_vector_search.py -q
$env:HF_HUB_OFFLINE='1'
$env:TRANSFORMERS_OFFLINE='1'
.\.venv\Scripts\python.exe -m scripts.rebuild_index
```

Résultat réel :

```text
collection = science_chunks
qdrant_points = 18
embedding_statuses = [(indexed, 18)]
article_statuses = [(indexed, 3)]
model_name = intfloat/multilingual-e5-base
vector_dimension = 768
on_disk_vectors = true
```

Une seconde exécution a retourné `indexés=0`, confirmant l’idempotence. Trois recherches ont placé en
première position l’article synthétique attendu : température/arômes en français, azote/levure en
anglais et polyphénols/stockage en français.

```text
scores de tête = [0.86927, 0.88513, 0.84092]
total pour 3 requêtes, chargement du modèle inclus = 11.804 s
peak_working_set_mb = 1111.23
taille Qdrant pour 18 points = 0.15 Mo
```

Validation complète : 31 tests réussis et aucune erreur Ruff.

## Étape 7 — recherche lexicale FTS5

La couche lexicale construit une expression FTS5 sûre à partir de texte naturel, applique BM25 et
hydrate titre, section, texte et pages depuis SQLite. Les tests couvrent accents, préfixes, opérateurs
malveillants, modes de requête, statuts de validation, filtres et limites.

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_lexical_search.py -q
.\.venv\Scripts\python.exe -m scripts.search_lexical "température arômes fermentation cidre" --limit 5
```

Les trois requêtes de démonstration ont retourné le bon article en première position :

```text
température/arômes/fermentation/cidre -> Temperature and Aroma Formation...
nitrogen/yeast/fermentation/time      -> Nitrogen Availability and Synthetic Yeast Kinetics
polyphenols/stockage/huit/semaines    -> Stockage local et stabilité fictive des polyphénols
```

Mesure sur 300 recherches, limite 20 :

```text
mean_latency_ms = 4.205
p95_latency_ms = 5.779
peak_working_set_mb = 33.20
qdrant_imported = false
sentence_transformers_imported = false
```

Validation complète : 37 tests réussis et aucune erreur Ruff.

## Étape 8 — fusion hybride RRF

Les tests vérifient la formule pondérée exacte, les doublons, les erreurs de configuration, l’ordre
déterministe, les filtres communs, les variantes et l’hydratation SQLite.

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_hybrid_search.py -q
$env:HF_HUB_OFFLINE='1'
$env:TRANSFORMERS_OFFLINE='1'
.\.venv\Scripts\python.exe -m scripts.search_hybrid "température arômes fermentation cidre" --limit 8
```

Résultats du corpus de démonstration :

```text
température/arômes/cidre -> Temperature and Aroma Formation... (L2/V1)
nitrogen/yeast/time      -> Nitrogen Availability...            (L1/V1)
polyphénols/stockage     -> Stockage local et stabilité...       (L2/V1)
first_query_seconds = [10.195, 0.052, 0.054]
warm_mean_seconds = 0.053
warm_p95_seconds = 0.061
peak_working_set_mb = 1112.29
```

Le premier temps inclut le chargement des poids E5. Validation complète : 42 tests réussis et aucune
erreur Ruff.

## Étape 9 — classement de vingt articles distincts

Les tests couvrent la formule d’agrégation, le titre et le résumé, le concept central, les métadonnées
SQLite, les exclusions, le résultat vide, le report d’un quasi-doublon et surtout la sélection exacte de
20 identifiants uniques parmi 25 articles synthétiques.

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_article_ranking.py tests\test_config.py -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q
python -m scripts.rank_articles "How does fermentation temperature affect cider aroma?" `
  --count 20 --diversity balanced --central-concept "fermentation temperature" --json
```

Validation automatisée : 47 tests réussis et aucune erreur Ruff. Sur les trois articles fictifs, les
trois sont retournés et l’article température/arômes arrive premier. Mesure dans un même processus :

```text
first_query_seconds = 11.713
warm_query_seconds = 0.078
peak_process_rss_mb = 1110.29
```

Le dossier local `C:\Users\lsemaan\Documents\ciderscholar v2\Docs tests` a aussi été traité dans un
index isolé sous `data/validation-step09`, sans modifier le corpus de démonstration :

```text
PDF détectés = 38
articles exploitables = 36
pages parcourues = 298
fragments = 626
OCR requis = 1
échec de contrainte DOI dupliqué = 1
lots d'embeddings = 79 x 8 maximum
points Qdrant = 626
échecs d'embeddings = 0
durée d'indexation = 439.139 s
pic processus indexation = 1.341 Go
```

La requête réelle FR/EN sur température, souche de levure et arômes a produit 26 articles candidats et
20 articles sélectionnés, tous distincts. L’article intitulé *Volatile Compounds in Cider: Inoculation
Time and Fermentation Temperature Effects* est classé premier. Une seconde recherche excluant son UUID
a conservé 20 résultats et confirmé son absence.

```text
selected_articles = 20
unique_article_ids = 20
first_query_seconds = 10.817
warm_query_seconds = 0.516
peak_process_rss_mb = 1133.32
```

Le détail de validation est conservé dans `Docs tests/step-09-article-ranking-test-report.md`.

## Étape 10 — connexion Ollama locale

Le client vérifie l’URL de boucle locale, refuse les modèles cloud, ignore les proxys, contrôle
l’installation exacte du modèle, borne entrée/contexte/sortie, sérialise les générations et valide les
sorties structurées avec Pydantic. La fermeture décharge explicitement le modèle.

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_ollama_client.py tests\test_health.py tests\test_config.py -q
python -m scripts.test_ollama --health-only --json
python -m scripts.test_ollama --json
```

Environnement local observé : Ollama 0.32.1 sur `127.0.0.1:11434`, modèle `qwen3:8b` de 8,2 milliards
de paramètres, quantification Q4_K_M, fichier local de 5 225 388 164 octets. Le diagnostic ne contient
aucun passage du corpus et demande seulement le résultat entier de `2 + 2`.

```text
validated_response = {status: ok, result: 4}
cold_wall_seconds = 17.860
cold_ollama_seconds = 17.789
cold_load_seconds = 11.845
warm_wall_seconds = 3.715
warm_ollama_seconds = 3.590
warm_load_seconds = 0.239
llama_server_rss_gb = 6.054
peak_system_used_gb = 13.704
minimum_system_available_gb = 1.761
models_after_close = 0
```

La première exécution indépendante a aussi confirmé 53 tokens d’entrée, 17 tokens de sortie et une
réponse validée. Le garde mémoire a émis l’avertissement prévu au-dessus de 13 Go. Le rapport détaillé
est conservé dans `Docs tests/step-10-ollama-test-report.md`. Validation complète : 58 tests réussis
et aucune erreur Ruff.

## Étape 11 — sélection des passages et extraction des preuves

Date de validation : 2026-07-20. La migration SQLite v2 ajoute une fiche d’exécution par couple
question/article. La sélection reste confinée à l’article, conserve trois à huit passages, favorise
Results/Discussion/Conclusion, repousse les méthodes non demandées et borne le contexte à 32 000
caractères. E5 et Qdrant sont fermés avant le chargement de Qwen.

La sortie attendue reprend le schéma `ArticleEvidence`. Les champs supplémentaires sont interdits et
une deuxième barrière vérifie dans SQLite l’identité de l’article et du chunk, les pages exactes et la
présence verbatim de chaque extrait. L’écriture des preuves et le passage de l’état à `completed` sont
atomiques. Une seule correction est permise après une réponse invalide.

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_article_evidence.py tests\test_config.py tests\test_database.py -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q
python -m scripts.extract_article_evidence `
  "Quel effet la température de fermentation a-t-elle sur les arômes du cidre ?" `
  --article-count 20 --passages 5 --diversity balanced
```

Les tests couvrent la sélection bornée, le rejet d’un chunk d’un autre article, la persistance et la
reprise, une première page erronée suivie d’une correction, ainsi que le rejet d’un DOI ou d’un extrait
inventé sans fuite du texte dans les logs. Un contrôle final a aussi isolé les tests fonctionnels des
fluctuations de RAM de l’hôte et ajouté des tests déterministes des deux seuils d’arrêt du garde
mémoire. Validation finale : 67 tests réussis en 4,31 s et aucune erreur Ruff.

La validation progressive sur le corpus fictif a révélé deux incompatibilités utiles. Le schéma
Pydantic intégral dépassait le sous-ensemble de grammaire accepté par Ollama ; il a été remplacé pour
la génération par un schéma structurel plus simple, la validation Pydantic complète restant
obligatoire après réponse. Qwen paraphrasait ensuite les citations malgré le prompt ; les extraits,
chunks, articles et pages autorisés sont désormais des énumérations dynamiques du schéma. Une nouvelle
génération fictive a alors persisté trois preuves exactes en une tentative. La reprise complète depuis
SQLite a pris 0,086 s sans charger de modèle.

Le test réel a réutilisé l’index isolé de l’étape 9 et le premier article classé, *Volatile Compounds
in Cider: Inoculation Time and Fermentation Temperature Effects*. Une première version non contrainte
avait correctement échoué après deux extraits paraphrasés, sans persister de preuve. Après correction,
le serveur Ollama a été redémarré puis la même exécution a repris ses cinq passages mémorisés :

```text
query_id = 2b445b0b-83c3-4e78-b799-1094ff0f867c
article_id = 00f17488-a493-4910-8a92-562b374a3566
state = completed
findings = 3
all_excerpts_verified = true
attempts_this_call = 1
generation_seconds = 173.724
wall_seconds = 180.653
prompt_tokens = 746
output_tokens = 666
peak_llama_server_rss_mb = 6354.5
peak_system_used_gb = 13.2
minimum_available_mb = 2278.1
loaded_models_after_close = 0
sqlite_resume_seconds = 0.014432
```

Les trois preuves pointent les pages 1, 1 et 4–5 des chunks SQLite 309 et 316. Chaque extrait a été
retrouvé verbatim avant et après persistance. Le compteur historique vaut quatre lancements car il
inclut les tentatives antérieures interrompues ou refusées ; la tentative corrigée finale a réussi du
premier coup. Le rapport détaillé est conservé dans
`Docs tests/step-11-evidence-extraction-test-report.md`.

## Étape 12 — synthèse scientifique hiérarchique

Date de validation : 2026-07-20. La migration SQLite v3 ajoute `synthesis_runs` et
`theme_synthesis_runs`. Le plan thématique, chaque synthèse intermédiaire et la synthèse finale ont
leur propre état et leur compteur de lancements. Une interruption finale conserve donc les thèmes
déjà validés.

Les sorties Qwen ne contiennent pas de bibliographie ni de texte de citation. Elles associent chaque
énoncé factuel à des UUID `evidence` autorisés par le schéma Ollama. Pydantic interdit les champs
supplémentaires, les DOI et les citations rédigées par le modèle ; le service et SQLite contrôlent
ensuite l’appartenance de chaque UUID. Le Markdown et la bibliographie sont rendus localement depuis
les pages et métadonnées SQLite.

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_final_synthesis.py tests\test_database.py tests\test_config.py -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q
python -m scripts.synthesize_query QUERY_UUID --json
```

Les tests couvrent le plan de thèmes, les citations et pages SQLite, le rejet d’un UUID de preuve
inventé, l’unique réessai, l’interdiction d’un DOI ou d’une citation générée, l’impossibilité de
revendiquer un consensus avec un seul article, la reprise après interruption et la reconstruction de
la bibliographie après modification des métadonnées SQLite. Validation finale : 73 tests réussis en
6,80 s et aucune erreur Ruff.

La validation réelle a repris les trois preuves de l’étape 11, sans E5 ni Qdrant. Comme un seul article
possédait des preuves, le thème a été construit sans appel de planification ; Qwen a effectué une
génération thématique puis une génération finale :

```text
query_id = 2b445b0b-83c3-4e78-b799-1094ff0f867c
state = completed
synthesis_attempt_count = 1
themes = 1
theme_summary_statements = 3
final_direct_answer_statements = 2
final_consensus_statements = 0
final_convergent_statements = 0
final_contradictory_statements = 0
final_missing_information = 2
cited_evidence = 3
citations_valid = true
pages_rendered = 3/3
bibliography_entries_from_sqlite = 1
llm_calls = 2
wall_seconds = 245.089
theme_prompt_tokens = 1159
theme_output_tokens = 422
theme_ollama_seconds = 154.788
final_prompt_tokens = 739
final_output_tokens = 265
final_ollama_seconds = 88.632
peak_llama_server_rss_mb = 6637.2
peak_system_used_gb = 13.4
minimum_available_mb = 2070.5
loaded_models_after_close = 0
sqlite_resume_seconds = 0.044328
```

L’absence de consensus, convergence ou contradiction inter-articles est volontaire : une seule fiche
réelle était disponible et la validation exige au moins deux articles distincts pour ces sections.
La reprise complète a effectué zéro appel LLM, a reconstruit le Markdown et la bibliographie depuis
SQLite et a conservé les trois pages de preuve. Le rapport détaillé est conservé dans
`Docs tests/step-12-hierarchical-synthesis-test-report.md`.

## Étape 13 — interface Streamlit

Date de validation : 2026-07-20. L’interface locale comporte les quatre pages demandées. La page
Corpus accepte des PDF ou un dossier, expose les états d’ingestion et permet l’indexation, la
réindexation ciblée et la suppression confirmée sans effacer le PDF. La page Recherche affiche les
articles distincts, les composantes de score et les passages paginés, avec exclusion manuelle avant
l’extraction des preuves. La page Synthèse montre les fiches et thèmes persistés, reprend le calcul
et exporte Markdown, JSON et BibTeX. La page Administration valide des paramètres de session, teste
les services locaux et affiche les journaux.

Les opérations ont été isolées dans `ui/workflows.py`. Les envois sont écrits atomiquement sous
`data/pdf/uploads`, les suppressions Qdrant sont explicites et les réglages de session ne modifient
pas `config.yaml`. E5 et Qwen ne sont pas chargés pendant le rendu initial.

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\test_ui_workflows.py tests\test_streamlit_app.py tests\test_database.py `
  tests\test_embeddings.py tests\test_vector_search.py -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m streamlit run ui\streamlit_app.py `
  --server.address 127.0.0.1 --server.port 8501 --server.headless true `
  --browser.gatherUsageStats false
```

Les 25 tests ciblés ont réussi en 13,37 s. La validation finale compte 84 tests réussis en 20,12 s
et Ruff ne signale aucune erreur. `streamlit.testing.v1.AppTest` a ouvert séparément Corpus,
Recherche, Synthèse et Administration avec une configuration et une base temporaires ; aucune page
n’a produit d’exception.

Le test réel a lié Streamlit exclusivement à `127.0.0.1:8501`; la sonde `/_stcore/health` a répondu
HTTP 200. Le rendu initial du processus serveur occupait 193,6 Mo de RSS et montrait trois articles,
18 fragments et 18 vecteurs, sans modèle Qwen chargé. Une recherche réellement soumise depuis le
navigateur a chargé E5 à la demande puis classé les trois articles fictifs. Le premier résultat,
*Stockage local et stabilité fictive des polyphénols*, a obtenu 0,8375 et affiché six passages avec
leurs sections, identifiants de chunks et pages. L’échantillon mémoire après cette première recherche
CPU indiquait 555,9 Mo de RSS et 1 378,1 Mo de mémoire privée pour le serveur, bien sous la cible de
16 Go.

Le même parcours navigateur a ouvert les quatre pages, confirmé la présence permanente de la
bannière hors ligne, validé les paramètres de session et contrôlé l’absence d’erreurs JavaScript. Le
serveur et ses processus ont ensuite été arrêtés ; aucun écouteur ne restait sur le port 8501. Le
rapport détaillé est conservé dans `Docs tests/step-13-streamlit-interface-test-report.md`.

## Étape 14 — tests et évaluation reproductible

Date de validation : 2026-07-20. Les anciens squelettes `app/evaluation/metrics.py`,
`app/evaluation/benchmark.py` et `scripts/benchmark_system.py` sont désormais fonctionnels. Un cas
JSON strict conserve la question, les articles attendus et acceptables, les concepts attendus et un
`query_id` facultatif. Le banc calcule P@k, rappel@k, MRR, nDCG gradué, rappel des concepts, durée,
pic RSS et pic mémoire système. Il contrôle les `evidence_id` des synthèses persistées sans demander
à un second modèle de juger le texte.

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\test_evaluation_metrics.py tests\test_benchmark.py -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -W error::ResourceWarning -q
.\.venv\Scripts\python.exe -m pytest `
  --cov=app --cov=ui --cov-report=term-missing -q
.\.venv\Scripts\python.exe -m scripts.benchmark_system --demo-corpus `
  --markdown-output "..\Docs tests\step-14-evaluation-benchmark-report.md" `
  --json-output data\exports\step-14-evaluation-benchmark.json
```

Les 11 nouveaux tests vérifient les formules, la pertinence graduée, la normalisation accentuée, les
références absentes, l’agrégation, le format JSON, l’empreinte du corpus, le rendu Markdown et les
écritures atomiques. Ils ont réussi en 2,77 s. Les helpers de tests ouvrant directement SQLite ont
été corrigés avec `closing()` afin d’éliminer les avertissements de ressources. `pytest-cov==7.1.0`,
déjà épinglé dans `requirements.txt`, a été installé dans la venv de développement.

La trace d’un dernier avertissement a identifié un objet SQLite temporaire créé par
`qdrant-client==1.18.0` pour vérifier le schéma d’une collection. Sous Python 3.14, son context
manager valide la transaction sans fermer la connexion. `QdrantLocalIndex.close()` ferme d’abord le
client durable, puis force la collecte de ce temporaire sous un filtre limité à ce seul
`ResourceWarning`. La validation finale stricte a réussi : 95 tests en 25,96 s, couverture globale
83 %, aucune alerte de ressource et aucune erreur Ruff.

La synthèse fictive `c8810895-356d-4ab8-86fd-69d5c8ea4946` a été terminée pour fournir une référence
de traçabilité réelle. Qwen a effectué deux appels en 304,560 s. `llama-server.exe` est resté autour
de 5 989 Mo de RSS ; le maximum signalé de mémoire système utilisée était 14,68 Go. Le calcul s’est
terminé normalement puis `keep_alive=0` a ramené le nombre de modèles chargés à zéro.

Le benchmark final du corpus fictif a produit :

```text
corpus_version = eb212963264de28da76b13e96c3e4ea439128b3ab06f5c7f8549ea061309408c
cases = 3
precision_at_20 = 0.0500
recall_at_20 = 1.0000
mean_reciprocal_rank = 1.0000
ndcg_at_20 = 1.0000
concept_recall = 1.0000
traceable_citations = 2/2 = 100%
unsupported_assertions = 0/2 = 0%
duration_seconds = 47.656
peak_process_rss_gb = 1.131
peak_system_used_gb = 9.964
loaded_ollama_models_after_validation = 0
```

La précision vaut 0,05 parce que P@20 utilise le dénominateur standard 20 alors que chaque cas ne
possède qu’un article pertinent dans un corpus de trois articles. Pour les trois questions, cet
article est classé premier ; le rappel, le MRR et le nDCG atteignent donc 1,0. Le rapport Markdown est
conservé dans `Docs tests/step-14-evaluation-benchmark-report.md` et le JSON complet sous
`data/exports/step-14-evaluation-benchmark.json`.

## Étape 15 — documentation et audit d’acceptation

Date de validation : 2026-07-20. Le guide `docs/WINDOWS_INSTALL.md` décrit l’installation Windows 11
sans Docker, y compris le cas où `Activate.ps1` est interdit, l’acquisition unique de Qwen et E5, le
verrouillage hors ligne, le parcours fictif, l’import d’un corpus réel, la sauvegarde, la restauration
et les diagnostics usuels. `docs/ACCEPTANCE_AUDIT.md` relie chacun des treize critères d’acceptation à
une preuve et sépare explicitement les limites du cahier des charges élargi.

L’audit de code a conduit à deux corrections de configuration et de dépendances : le reranker
non implémenté est maintenant désactivé par défaut, et les paquets inutilisés `httpx2` et
`langdetect` ont été retirés de `requirements.txt`. Le client HTTP réellement utilisé reste
`httpx==0.28.1`; la détection de langue du pipeline est déterministe et locale.

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_config.py -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -W error -q
.\.venv\Scripts\python.exe -m pytest --cov=app --cov=ui --cov-report=term-missing -q
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pip install --dry-run --ignore-installed `
  --only-binary=:all: --platform win_amd64 --implementation cp `
  --python-version 3.12 --abi cp312 -r requirements.txt
```

Le contrôle des CLI a aussi corrigé `scripts.create_demo_corpus` : `--help` affichait auparavant les
chemins de fichiers tout en régénérant le corpus. La commande dispose maintenant d’un véritable
parseur, d’un argument `--destination` et d’un test garantissant l’absence d’effet de bord de l’aide.

Une alerte Qdrant non déterministe est ensuite réapparue sous la forme d’un
`PytestUnraisableExceptionWarning`. Le filtre/ramasse-miettes du jalon 14 a été remplacé par la
suppression de la cause : le client embarqué utilise `force_disable_check_same_thread=True`, cohérent
avec les opérations applicatives séquentielles, et ne crée plus la sonde SQLite temporaire fautive de
`qdrant-client==1.18.0`. Cinq campagnes ciblées consécutives puis la suite stricte complète n’ont
produit aucun avertissement.

Résultats finaux : 14 tests de configuration, un test ciblé de la CLI, puis 97 tests complets avec
tous les avertissements traités comme erreurs en 39,33 s ; 97 tests sous couverture globale de 83 %
en 49,37 s ; Ruff et `pip check`
propres. La résolution forcée des seules roues binaires épinglées pour CPython 3.12, ABI `cp312` et
Windows x64 a réussi. Les onze modules CLI publiés répondent à `--help` et tous les liens Markdown
locaux du dépôt se résolvent.

La réserve restante est documentée : l’hôte de développement ne possède que Python 3.14.6. La
compatibilité d’installation Python 3.12 est donc vérifiée par résolution des roues, mais le dernier
`pytest -q` doit encore être exécuté sur l’interpréteur 3.12 de la machine cible. Les mesures réelles
E5/Qdrant/Qwen du jalon 14 restent la preuve mémoire finale ; aucun modèle lourd n’a été rechargé pour
ce jalon documentaire.

## Jalon 20 — refonte web React/Tailwind et API applicative

L’interface Streamlit a été retirée au profit d’une SPA React 19/TypeScript organisée par
fonctionnalité. Tailwind CSS 4 devient l’unique architecture graphique, avec des primitives
réutilisables pour les cartes, boutons, badges, formulaires, dialogues et états de retour. Les six
espaces produit sont Tableau de bord, Corpus, Bibliothèque, Recherche, Synthèses et Paramètres.

FastAPI expose désormais les workflows métier du corpus, de la bibliothèque, du RAG et des
synthèses. La logique réutilisable a été déplacée vers `app/services`, les requêtes sont validées avec
des schémas stricts et les réglages de session ne révèlent jamais les secrets. Le build Vite est servi
directement par FastAPI sur la boucle locale. `AGENTS.md`, l’arborescence et les procédures Windows
ont été alignés sur cette architecture.

## Jalon 22 — corpus cidricole massif et archive des rejets

Date de validation : 2026-07-21. La migration SQLite v7 ajoute
`rejected_bibliographic_archive`. La purge est ordonnée : copie DOI/titre et provenance, contrôle de
l’archive, export JSON atomique, suppression des points Qdrant, puis suppression SQLite avec cascade
FTS5. Une interruption avant la dernière étape conserve donc la notice rejetée dans la base active.

Le nouveau mode `scripts.harvest_cider_bulk` utilise des vagues courtes et ancrées en français,
anglais et espagnol. Il pagine cinq fournisseurs, déduplique d’abord par DOI, enrichit les DOI sans
abstract par lots OpenAlex et s’arrête sur un nombre net de nouveaux abstracts acceptés. Une première
stratégie à faible rendement a été interrompue proprement après six cycles ; le profil ciblé a repris
sur quatorze cycles sans perte de données.

La passe a traité 17 691 résultats bruts. Après déduplication, reclassification et purge, la base
active contient 1 368 notices acceptées, dont 1 064 abstracts tous indexés, et 1 053 notices à revoir
hors RAG. Le corpus initial comptait 62 abstracts : le gain net est donc de 1 002 articles. Il ne
reste aucun rejet actif ni DOI dupliqué. L’archive contient 5 516 lignes avec titre, dont 5 112 avec
DOI. Le nombre de points Qdrant est exactement égal aux 1 064 abstracts acceptés.

L’audit de pertinence a durci le filtre contre les vinaigres en anglais et en français, les cidres
explicitement produits avec un fruit autre que la pomme, les usages informatiques de CIDER/SIDRA,
les études routières, historiques, économiques et cliniques hors conception cidricole. Les contrôles
hybrides donnent des résultats directement thématiques sur microbiologie, polyphénols, azote,
clarification et distillation. Les thèmes les moins fournis restent les protéines (22 abstracts) et
le Pommeau (7 abstracts).

Le rapport opérationnel détaillé est conservé dans
`Docs tests/step-22-large-cider-corpus-harvest-report.md`; les exports horodatés sont sous
`data/exports`.

Validation finale : 107 fichiers conformes au formatage Ruff, aucune erreur Ruff, 142 tests Python
réussis en 18,78 s, dépendances Python cohérentes, 3 tests Vitest réussis, ESLint et TypeScript
propres, et build Vite de production réussi. FastAPI a ensuite été redémarré sur `127.0.0.1:8000` ;
les routes de santé et de synthèse de la bibliothèque exposent les mêmes compteurs finaux.

## Jalon 23 — extension exploitable du corpus bibliographique

Date de validation : 2026-07-21. Le point de départ de cette passe était de 1 040 abstracts acceptés
et indexés. La collecte a été arrêtée explicitement à la demande de l’utilisateur après 47 cycles
principaux terminés et une dernière vague partielle conservée. Le résultat figé est de 2 688
abstracts acceptés, soit un gain net de 1 648 nouveaux articles exploitables. Les 2 688 abstracts
ont tous un point dans la collection bibliographique Qdrant et l’indexation n’a produit aucun échec.

Les stratégies de requêtes ont été séparées dans `app/updates/harvest_queries.py`. La commande
massive accepte désormais un ensemble de requêtes, un sous-ensemble de fournisseurs et une page de
départ. Le filtre cidricole reconnaît aussi les matières premières et coproduits de pomme — fruit,
cultivar, pomace, pulpe, pelure, pépin et gâteau de presse — tout en conservant les exclusions santé,
vinaigre, homonymes logiciels et fruits hors pomme.

La base active contient 3 702 notices acceptées, dont les 2 688 avec abstract indexé, et 2 265
notices à revoir hors RAG. Les 4 646 rejets de la dernière purge ont été archivés avant suppression ;
l’archive cumulée contient 16 847 entrées et aucun rejet ne reste actif. Parmi les abstracts actifs,
2 415 possèdent un DOI. L’audit trouve zéro DOI dupliqué et zéro clé canonique dupliquée.

Les recherches hybrides de contrôle remontent des références directement pertinentes sur la
fermentation par levures et bactéries lactiques, les procyanidines des pomaces, l’azote assimilable et
la clarification des jus, les composés volatils et sensoriels, puis la distillation et le
vieillissement des eaux-de-vie de cidre.

Validation finale : Ruff format et lint réussis sur 112 fichiers, 164 tests Python réussis, puis
Prettier, ESLint, TypeScript, 9 tests Vitest et le build Vite de production réussis. Le rapport
détaillé est conservé dans `Docs tests/step-23-rag-corpus-extension-report.md`.

## Jalon 24 — corpus actif limité aux notices avec abstract

Date de validation : 2026-07-21. Les fournisseurs bibliographiques peuvent renvoyer une notice avec
DOI, titre et métadonnées sans fournir d’abstract. Ces notices étaient conservées pour une tentative
d’enrichissement ultérieure et n’étaient jamais indexées, mais elles restaient visibles dans la base
documentaire. La règle produit exige désormais que toute notice active possède un abstract.

`BibliographicHarvestStore.reject_abstractless_records()` applique cette règle après le complément
OpenAlex DOI et avant l’archivage sécurisé. Elle reclassifie les notices sans abstract, met à jour les
compteurs des cycles, puis laisse le mécanisme existant archiver DOI/titre et provenance avant la
suppression. Les commandes pilote et massive appliquent toutes deux automatiquement ce nettoyage.

Une sauvegarde SQLite a été créée avant migration. Les 2 517 notices sans abstract ont ensuite été
archivées et supprimées. La base active contient maintenant 3 450 notices, toutes avec abstract :
2 688 acceptées et indexées dans le RAG, plus 762 à revoir hors RAG. La collection Qdrant contient
exactement 2 688 points, aucun rejet ne reste actif et l’archive cumulée contient 19 279 entrées.

Validation finale : Ruff format et lint réussis sur 112 fichiers, 165 tests Python réussis, puis
Prettier, ESLint, TypeScript, 9 tests Vitest et le build Vite de production réussis. L’API redémarrée
confirme `stored_records = stored_abstracts = 3450` et `indexed = abstracts = 2688`.

Le rapport détaillé est conservé dans
`Docs tests/step-24-abstract-only-corpus-cleanup.md`.

## Jalon 25 — bibliothèque unifiée et corpus scientifique IFPC

Date de validation : 2026-07-21. La rubrique autonome « Corpus PDF » a été supprimée de la
navigation. Les notices bibliographiques et les PDF locaux sont désormais deux sous-catégories de la
page « Base documentaire » ; l’ancienne URL `/corpus` reste un lien de compatibilité et redirige vers
la sous-catégorie PDF.

Les 40 cahiers techniques exposés par le catalogue officiel IFPC ont été importés en PDF local. Les
33 documents image ont été OCRisés localement avec Windows OCR en français. Tous les documents sont
exploitables : 40 articles, 271 fragments indexés et aucun échec. Trente-neuf PDF proviennent du lien
IFPC actif et le document dont le lien officiel est mort est conservé depuis sa capture historique.

Une collecte complémentaire de deux pages a interrogé séquentiellement Crossref, Europe PMC,
OpenAlex, Clarivate et Elsevier pour Pascal Poupard, Hugues Guichard et Rémi Bauduin. Elle a consolidé
482 réponses brutes en 360 notices uniques, sans erreur fournisseur. Après les règles d’abstract et
de pertinence, la base contient 40 publications scientifiques uniques mentionnant au moins un des
trois auteurs : 30 sont acceptées et indexées, 10 restent à revoir hors RAG. Vingt anciennes notices
sans DOI ont été fusionnées avec leur unique version DOI, et les 10 anciens vecteurs concernés ont
été supprimés.

Le découpage paresseux des écrans frontend ramène chaque paquet de production sous 252 kB. Validation
finale : Ruff format et lint réussis, 180 tests Python réussis, puis Prettier, ESLint, TypeScript, 15
tests Vitest et le build Vite de production réussis sans avertissement. Le rapport détaillé est
conservé dans `Docs tests/step-25-ifpc-library-and-publications.md`.
