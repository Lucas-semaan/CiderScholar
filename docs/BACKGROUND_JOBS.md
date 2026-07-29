# Travaux locaux durables

La file SQLite accepte cinq types fermés :

- `chat_answer` ;
- `deep_research` ;
- `weekly_maintenance` ;
- `long_synthesis` ;
- `private_ingestion`.

Les synthèses longues et l’ingestion privée sont soumises par HTTP avec une réponse `202`, puis
exécutées par `python -m scripts.run_job_worker`. Elles utilisent les mêmes leases, heartbeats,
reprises bornées, annulations, projections publiques sans payload et notifications facultatives que
les réponses conversationnelles.

Le worker renouvelle automatiquement le lease pendant tout handler long, au plus toutes les
30 secondes et avant le tiers de sa durée. Une perte de lease interdit la persistance du résultat ;
un test avec lease court couvre un traitement plus long que sa durée initiale.

## Synthèse longue

`POST /api/synthesis/{query_id}/run` vérifie que la requête et ses preuves SQLite existent, persiste
un `LongSynthesisPayload` versionné et rend immédiatement le travail public. Le handler appelle la
synthèse hiérarchique reprenable et ne publie le résultat qu’après le contrôle d’annulation final.

## Ingestion privée

Les routes `/api/private-corpus/upload` et `/api/private-corpus/folder` copient d’abord au plus
100 PDF dans `private/pdf/uploads`, puis ne placent en file que des chemins relatifs validés. Le
handler reconstruit chaque chemin sous cette racine, refuse toute sortie de répertoire et utilise
uniquement la base SQLite privée. Le résultat stocké dans la file contient des compteurs d’état,
jamais le texte ni les chemins des documents.

Les travaux peuvent être suivis, annulés ou relancés via `/api/jobs/{job_id}`. Le worker doit rester
actif dans l’application desktop ; une interruption de processus est récupérée par expiration du
lease sans perdre le payload ni les checkpoints de synthèse.
