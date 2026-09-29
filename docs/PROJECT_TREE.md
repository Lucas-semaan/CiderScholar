# Carte du dépôt pour le développement

Cette carte décrit les responsabilités du code présent. Pour les règles de contribution,
voir [`AGENTS.md`](../AGENTS.md) ; pour toute modification scientifique du corpus ou du RAG,
commencer par [`HOW_TO_WORK_ON_CIDERSCHOLAR.md`](HOW_TO_WORK_ON_CIDERSCHOLAR.md).

## Chemins d'exécution

| Point d'entrée | Parcours à suivre dans le code |
| --- | --- |
| Interface | `frontend/src/main.tsx` → `frontend/src/app/App.tsx` → `features/` → `frontend/src/lib/api.ts` |
| API | `app/main.py` → `app/api/` → `app/services/` → modules métier et stockage |
| Jobs durables | `scripts/run_job_worker.py` → `app/jobs/worker.py` → handlers de `app/jobs/` → services métier |
| Collecte | commandes de `scripts/harvest_*.py` → `app/updates/` → `app/database/` |
| Évaluation | commandes de `scripts/*ciderqa*.py` → `app/evaluation/` et `tests/` |

L'API et le worker sont deux processus distincts. Les routes de jobs créent et lisent des états
persistés ; elles n'exécutent pas un traitement lourd pendant la requête HTTP.

## Responsabilités des dossiers

| Dossier | Responsabilité |
| --- | --- |
| `app/api/` | Contrats HTTP, validation et traduction des erreurs. |
| `app/services/` | Cas d'usage de l'interface ; `workflows.py` orchestre le parcours scientifique du chat. |
| `app/jobs/` | File durable, leases, reprise et handlers. |
| `app/database/` | SQLite, schéma et migrations versionnées. |
| `app/ingestion/` | Extraction PDF/texte natif, métadonnées, chunks et embeddings. |
| `app/retrieval/` | Recherche lexicale et dense, fusion, classement et sélection de preuves. |
| `app/llm/` | Client ARGO, contrats de génération, vérification et synthèse. |
| `app/updates/` | Fournisseurs bibliographiques, acquisition et indexation des abstracts. |
| `app/deep_research/` | Pipeline de recherche approfondie et ses contrôles de promotion. |
| `app/evaluation/` | CiderQA, campagnes, métriques et rapports. |
| `app/knowledge/`, `knowledge/`, `wiki/` | Routage de connaissance et contenu éditorial ; le wiki ne remplace pas une preuve SQLite. |
| `app/corpus_packages/`, `app/admin/` | Paquets, signatures, activation, sauvegarde et maintenance. |
| `app/desktop/`, `installer/` | Parcours et artefacts Windows conservés pour la distribution historique. |
| `frontend/src/components/ui/` | Primitives visuelles partagées ; `features/` contient les écrans métier. |
| `scripts/` | Commandes d'exploitation. Elles appellent les mêmes services que l'API lorsqu'un workflow est partagé. |
| `tests/`, `frontend/src/**/*.test.ts*` | Tests backend et frontend. |
| `docs/` | Méthode, contrats, décisions, audits et historique. |

Le texte, les pages, les métadonnées et les preuves font autorité dans SQLite. Qdrant porte les
vecteurs et identifiants nécessaires à la recherche. Les sorties de `frontend/dist`, `data`,
`build` et `artifacts` sont locales ou générées et ne constituent pas du code source.
Le nom de distribution Python `local-science-rag` dans `pyproject.toml` est historique ; le produit
et ce dépôt sont nommés CiderScholar, tandis que le package importable reste `app`.

## Quel document lire ?

- [`README.md`](../README.md) : installation et parcours utilisateur.
- [`HOW_TO_WORK_ON_CIDERSCHOLAR.md`](HOW_TO_WORK_ON_CIDERSCHOLAR.md) : méthode scientifique en vigueur.
- [`ROADMAP.md`](ROADMAP.md) : cible et tâches à venir ; une cible n'est pas une fonction déjà active.
- [`BACKGROUND_JOBS.md`](BACKGROUND_JOBS.md) : contrats du worker et reprise.
- [`CHATBOT_RESPONSE_CONTRACT.md`](CHATBOT_RESPONSE_CONTRACT.md) : rendu et preuves de la réponse.
- [`CURRENT_ARCHITECTURE.md`](../CURRENT_ARCHITECTURE.md),
  [`TARGET_ARCHITECTURE.md`](../TARGET_ARCHITECTURE.md) et les rapports datés : photographies
  d'audit ou propositions à leur date, à confronter au code et au guide méthodologique actuel.

Avant de retirer un script, une route ou une migration, rechercher ses appels dans le code, les tests,
les commandes documentées et les usages d'exploitation. Des opérations Windows et des migrations
historiques restent intentionnellement présentes tant qu'une migration vers la VM Linux n'est pas
terminée.
