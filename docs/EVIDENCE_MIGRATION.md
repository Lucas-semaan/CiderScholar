# Migration des preuves historiques

Les tables de preuves scientifiques (`queries`, `article_evidence_runs`, `evidence`,
`synthesis_runs` et `theme_synthesis_runs`) appartiennent au corpus commun sous
`data/common/database`. Les jobs, conversations et journaux de quota restent dans la base
applicative `data/database`.

Prévisualiser la migration sans écrire :

```powershell
.\.venv\Scripts\python.exe -m scripts.migrate_legacy_evidence
```

Après contrôle du rapport JSON, appliquer la migration depuis un profil administrateur :

```powershell
.\.venv\Scripts\python.exe -m scripts.migrate_legacy_evidence --apply
```

Avant `--apply`, la commande vérifie les clés étrangères et chaque article/chunk référencé (ID,
SHA-256, article, pages et extrait verbatim), crée un snapshot SQLite cohérent du corpus commun
contrôlé par `quick_check` et SHA-256, puis copie les cinq tables dans une transaction unique. Les
PDF et Qdrant ne sont pas dupliqués, car ils ne sont pas modifiés par cette migration. Elle est
idempotente : une ligne déjà présente doit être strictement identique, sinon l'opération s'arrête
sans écraser la cible. La base applicative source n'est jamais modifiée.
