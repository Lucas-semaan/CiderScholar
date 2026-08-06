# Arborescence de référence

```text
CiderScholar/
├── AGENTS.md                         # règles d’ingénierie du dépôt
├── app/
│   ├── api/                          # routes FastAPI, schémas et gestion d’erreurs
│   │   ├── chatbot.py
│   │   ├── health.py
│   │   ├── ingestion.py
│   │   ├── jobs.py
│   │   ├── library.py
│   │   ├── search.py
│   │   ├── synthesis.py
│   │   └── system.py
│   ├── services/                     # orchestration testable des cas d’usage
│   ├── database/                     # SQLite, schéma et migrations
│   ├── jobs/                         # file durable, handlers, leases et reprise
│   ├── deep_research/                # pipeline full-text inactif et gate de promotion
│   ├── discovery/                    # hypothèses, données, analyses et gates humains
│   ├── corpus_packages/              # paquets communs signés, activation et rollback
│   ├── desktop/                      # superviseur, mises à jour et notifications Windows
│   ├── ingestion/                    # PDF, métadonnées, chunks et embeddings
│   ├── retrieval/                    # FTS5, vecteurs, fusion et classement
│   ├── llm/                          # ARGO, contrats, preuves et synthèses
│   ├── updates/                      # APIs bibliographiques et collecte
│   │   ├── harvest.py                # orchestration, pertinence et déduplication
│   │   └── harvest_queries.py        # vagues de requêtes cidricoles réutilisables
│   ├── models/                       # modèles de domaine Pydantic
│   ├── evaluation/                   # CiderQA, métriques, ablations et non-régressions
│   ├── config.py                     # configuration typée et garde-fous
│   └── main.py                       # fabrique FastAPI et service de la SPA
├── frontend/
│   ├── src/
│   │   ├── app/                      # routeur React
│   │   ├── components/
│   │   │   ├── layout/               # structure globale et navigation
│   │   │   └── ui/                   # primitives Tailwind partagées
│   │   ├── features/                 # pages organisées par domaine produit
│   │   │   ├── chatbot/
│   │   │   ├── dashboard/
│   │   │   ├── corpus/
│   │   │   ├── library/
│   │   │   ├── search/
│   │   │   ├── synthesis/
│   │   │   └── settings/
│   │   ├── hooks/                    # état asynchrone réutilisable
│   │   ├── lib/                      # client API et utilitaires purs
│   │   ├── styles/index.css          # Tailwind, tokens et base uniquement
│   │   └── types/                    # contrats de transport TypeScript
│   ├── package.json                  # scripts dev, build et CI frontend
│   ├── vite.config.ts
│   └── vitest.config.ts
├── scripts/                          # commandes d’exploitation
├── tests/                            # tests Python unitaires et API
├── docs/                             # décisions, audit, installation et historique
├── data/                             # données locales non versionnées
├── config.example.yaml               # profil local sûr sans secret
├── pyproject.toml
├── requirements.txt
└── README.md
```

## Dépendances autorisées

```text
frontend → HTTP API → services → domaine/adaptateurs → stockages
```

- Une page React n’importe jamais de code Python ni de détail de stockage.
- Une route API valide et délègue ; elle ne réimplémente pas un workflow.
- Un script appelle les mêmes services que l’API.
- SQLite est l’autorité ; les index sont reconstruisibles.
- Le dossier `frontend/dist` est généré et servi par FastAPI, jamais modifié à la main.
