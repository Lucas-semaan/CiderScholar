# Audit final des critères d’acceptation

## Addendum — profil connecté du 2026-07-20

Le tableau historique ci-dessous audite le profil Ollama hors ligne livré par
`config.example.yaml`; il reste valide pour ce profil. Le `config.yaml` local de
la machine utilise maintenant, à la demande de l'utilisateur, INRAE ARGO et cinq
API de métadonnées bibliographiques. Il ne satisfait donc pas littéralement les
critères « sans connexion Internet » et « aucune donnée envoyée hors machine ».

Le périmètre transmis est néanmoins borné : seuls les passages retenus par le
RAG sont envoyés à ARGO, et seuls les termes d'une recherche explicitement
lancée sont envoyés à Crossref, Europe PMC, OpenAlex, Web of Science ou Scopus.
Les PDF, l'index vectoriel, SQLite et les modèles d'embeddings restent locaux.
Les clés résident dans les variables utilisateur Windows et ne sont ni écrites
dans le dépôt, ni journalisées, ni affichées par l'interface. Depuis le 2026-07-21,
l’interface produit est React/Tailwind et les opérations passent par l’API FastAPI.

La campagne après l'ajout de la base documentaire et des contraintes DOI compte 124 tests stricts
réussis, Ruff sans erreur, 80 % de couverture globale et `pip check` sans dépendance cassée. Les valeurs
97/83 % du tableau historique correspondent à la campagne du profil local avant
l'ajout des connecteurs.

Date : 2026-07-20  
Périmètre : MVP local, corpus de démonstration fictif et validations réelles E5/Qdrant/Qwen  
Conclusion : **13 critères sur 13 disposent d’une preuve de conformité**, avec les limites de portée
déclarées plus bas.

## Résultats

| Critère d’acceptation | État | Preuve principale |
|---|---:|---|
| Fonctionne sans connexion Internet | Conforme | Modèles acquis explicitement, E5 chargé avec `local_files_only=True`, Ollama local, benchmark hors ligne et `app.offline_mode=true`. |
| Utilise uniquement des composants gratuits | Conforme | Dépendances Python publiques, licence applicative AGPL-3.0 et Qwen3 sous Apache-2.0. Aucun service payant requis. |
| Aucune donnée n’est envoyée hors machine | Conforme | Services liés à `127.0.0.1`, client Ollama sans proxy, profil hors ligne sans API bibliographiques et tests de configuration négatifs. |
| Indexe un dossier de PDF | Conforme | `scripts.ingest_folder`, page Corpus et tests d’ingestion séquentielle/reprise. |
| Conserve les pages sources | Conforme | `chunks.page_start/page_end`, extraction page par page, sélection de passages et vérifications SQLite. |
| Retourne vingt articles distincts | Conforme | Agrégation par `article_id`, limite à 20 et test avec 25 articles distincts. Si le corpus en contient moins, tous les articles disponibles sont retournés. |
| Affiche les passages retenus | Conforme | Page Recherche React, fiches de preuves et services testés dans `app/services/workflows.py`. |
| Génère une synthèse avec Qwen via Ollama | Conforme | Exécutions réelles des étapes 11, 12 et 14 ; sorties JSON/Pydantic, deux niveaux génératifs et déchargement final. |
| Chaque affirmation possède une source | Conforme | `CitedStatement` exige au moins un `evidence_id`; validation contre les preuves autorisées et mesure de traçabilité 2/2. |
| Les références viennent de SQLite | Conforme | Pages, métadonnées, DOI, rendu des citations et bibliographie sont reconstruits depuis SQLite après génération. |
| Reste sous 16 Go de RAM | Conforme sur la machine cible testée | Pic E5 du benchmark : 1,131 Go RSS ; pic système Qwen observé : 14,68 Go ; modèle déchargé après usage. |
| Les tests principaux réussissent | Conforme | 97 tests Pytest stricts, couverture globale 83 %, Ruff propre et matrice dans `TEST_MATRIX.md`. |
| Procédure Windows reproductible | Conforme avec réserve d’interpréteur ci-dessous | `WINDOWS_INSTALL.md`, versions épinglées et résolution binaire complète simulée pour CPython 3.12/Windows x64. |

## Preuves reproductibles

Validation statique et automatisée :

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -W error -q
.\.venv\Scripts\python.exe -m pytest --cov=app --cov-report=term-missing -q
npm.cmd --prefix frontend run ci
.\.venv\Scripts\python.exe -m pip check
```

Validation du chemin utilisateur fictif :

```powershell
.\.venv\Scripts\python.exe -m scripts.create_demo_corpus
.\.venv\Scripts\python.exe -m scripts.ingest_folder --recursive
.\.venv\Scripts\python.exe -m scripts.rebuild_index
.\.venv\Scripts\python.exe -m scripts.benchmark_system --demo-corpus
```

La dernière campagne réelle a classé l’article attendu au premier rang pour les trois questions :
R@20, MRR, nDCG et rappel des concepts à 1,0. La précision standard P@20 vaut 0,05 parce qu’un seul
article est pertinent dans chaque cas et que le dénominateur reste 20. Les deux citations finales
contrôlées sont traçables et aucune des deux affirmations n’utilise une preuve absente.

La recette documentaire a aussi chargé `config.example.yaml`, exécuté `--help` sur les onze CLI
publiées et vérifié tous les liens Markdown locaux sans erreur.

## Confidentialité et souveraineté

- Le validateur refuse `offline_mode=true` avec les API bibliographiques actives.
- Le client Ollama n’accepte que `http://127.0.0.1:PORT`, désactive les proxys d’environnement et
  refuse les redirections et les modèles cloud.
- L’acquisition E5 est une commande séparée exigeant `--allow-network`; l’exécution courante refuse
  un modèle absent au lieu de le télécharger.
- Qdrant ne stocke pas le texte des fragments ; SQLite reste l’autorité pour le texte, les pages et
  les métadonnées.
- Les rapports et journaux techniques ne contiennent pas le texte intégral des articles.
- Streamlit est lancé avec la télémétrie désactivée et une adresse de boucle locale explicite.

Ces garanties couvrent le code livré et sa configuration par défaut. Une modification manuelle du
pare-feu, des paramètres Ollama, des commandes de lancement ou du code sort du périmètre de cet audit.

## Mémoire

Les étapes lourdes sont séquentielles : E5/Qdrant sont fermés avant Qwen, les passages sont bornés et
le modèle est déchargé avec `keep_alive=0`. Les mesures réelles les plus contraignantes sont :

- recherche/benchmark E5 : 1,131 Go de RSS processus ;
- extraction de preuves Qwen : environ 6 354,5 Mo de RSS `llama-server.exe` ;
- synthèse hiérarchique Qwen : environ 6 637,2 Mo de RSS `llama-server.exe` ;
- maximum système observé pendant la validation finale Qwen : 14,68 Go utilisés, sous 16 Go.

La marge est réelle mais faible lors d’une génération Qwen. Fermer les applications lourdes et ne pas
augmenter simultanément `num_ctx`, les passages ou les lots sur une machine de 16 Go.

## Limites connues et portée du MVP

Les critères d’acceptation ci-dessus sont satisfaits. Les éléments suivants du cahier des charges
élargi ne sont pas présentés comme livrés :

1. **Expansion automatique de requête** — les variantes, synonymes, traductions et concepts centraux
   peuvent être fournis manuellement dans l’interface et les CLI ; `app/llm/query_expansion.py` reste
   un point d’extension et Qwen ne les produit pas automatiquement.
2. **Reranker cross-encoder** — `app/retrieval/reranker.py` reste un point d’extension. Il est
   désactivé par défaut ; son poids 0,20 reste réservé et n’est pas redistribué aux canaux actifs.
3. **Cache de recherche signé** — l’ingestion possède un cache par SHA-256 et les requêtes, preuves,
   thèmes et synthèses sont durables et reprenables. Il n’existe pas encore de cache explicite de
   résultats indexé par le quadruplet hash de question/version du corpus/version du
   modèle/paramètres.
4. **Routes FastAPI métier** — les sondes de santé sont fonctionnelles ; ingestion, recherche et
   synthèse sont opérées par Streamlit, les workflows Python et les CLI. Les modules API métier sont
   encore des squelettes.
5. **Mises à jour bibliographiques hebdomadaires** — ce module était demandé comme facultatif. Les
   connecteurs et le planificateur sont des squelettes inactifs et aucune connexion externe n’est
   effectuée.
6. **OCR automatique ciblé** — les scans sont détectés et signalés `ocr_required`; Tesseract et
   OCRmyPDF ne sont pas intégrés. C’est cohérent avec l’interdiction de lancer un OCR massif.

Ces limites n’empêchent pas le chemin principal local : importer, indexer, rechercher, classer des
articles distincts, sélectionner des passages, extraire les preuves et synthétiser avec citations.

## Réserve de validation Windows/Python

La machine de développement ne dispose que de Python 3.14.6. Tous les tests fonctionnels réels de la
campagne finale ont donc été exécutés sur cet interpréteur, alors que la cible déclarée dans
`pyproject.toml` est Python 3.12.

Pour réduire ce risque, `pip` a effectué avec succès une résolution complète, sans installation, de
tous les paquets épinglés en imposant la plateforme `win_amd64`, Python 3.12 et l’ABI `cp312` :

```powershell
.\.venv\Scripts\python.exe -m pip install --dry-run --ignore-installed `
  --only-binary=:all: --platform win_amd64 --implementation cp `
  --python-version 3.12 --abi cp312 -r requirements.txt
```

Cette vérification prouve la disponibilité des roues Windows/Python 3.12, pas l’exécution du code sous
3.12. La dernière vérification d’installation doit donc être `pytest -q` sur la machine Windows cible,
comme indiqué dans `WINDOWS_INSTALL.md`.

## Décision

Le MVP peut être accepté au regard des 13 critères explicites. Le guide Windows, la matrice de tests,
les rapports réels et les limites ci-dessus forment le dossier de recette. Les fonctions résiduelles
doivent être planifiées comme évolutions distinctes et ne doivent pas être activées par configuration
avant leur implémentation et leurs propres mesures mémoire.
