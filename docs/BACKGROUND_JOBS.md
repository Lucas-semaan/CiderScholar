# Travaux locaux durables

La file SQLite accepte cinq types fermés : `chat_answer`, `deep_research`,
`weekly_maintenance`, `long_synthesis` et `corpus_ingestion`.

Les campagnes contrôlées de profils P0/P1/P2 utilisent le même job `chat_answer`, mais avec une
identité de cellule et une conversation isolée. Voir
[`CHAT_FINETUNING_EVALUATION.md`](CHAT_FINETUNING_EVALUATION.md).

Les synthèses longues et les ingestions de corpus sont exécutées par
`python -m scripts.run_job_worker`. Elles utilisent les mêmes leases,
heartbeats, reprises bornées, annulations, projections publiques sans payload
et notifications facultatives que les réponses conversationnelles.

Le worker renouvelle automatiquement le lease pendant tout handler long, au
plus toutes les 30 secondes et avant le tiers de sa durée. Une perte de lease
interdit la persistance du résultat.

## Ingestion du corpus

Les PDF placés dans le répertoire du corpus commun sont validés comme chemins
relatifs sûrs, puis le handler les résout sous `data/common/pdf`. Les textes,
preuves et vecteurs sont tous écrits dans l’unique base SQLite et l’unique
index Qdrant du corpus. Le résultat de file ne contient que des compteurs
d’état, jamais le texte ni les chemins des documents.

Les travaux peuvent être suivis, annulés ou relancés via `/api/jobs/{job_id}`.

## Progression d'une réponse scientifique

Un job `chat_answer` publie des frontières durables correspondant au travail réel, dans cet
ordre :

1. analyse et planification de la question ;
2. recherche locale dans le corpus ;
3. enrichissement bibliographique, seulement lorsqu'il est autorisé et demandé ;
4. classement et fusion des passages ;
5. sélection sémantique des preuves ;
6. contrôle de couverture et recherche complémentaire éventuelle ;
7. analyse locale des figures, seulement lorsqu'elle est demandée ;
8. génération de la réponse finale ;
9. validation scientifique ;
10. enregistrement atomique du résultat.

Le premier appel ARGO sert normalement à la planification. Il ne doit donc jamais être affiché
comme une génération finale. L'étape historique `argo` reste acceptée en lecture pour les anciens
jobs, mais les nouveaux jobs utilisent les étapes détaillées ci-dessus.

## Diagnostic de traitement

`GET /api/system/diagnostics` expose l’état local utile à l’exploitation : heartbeat du worker,
travaux actifs, mémoire du processus API et du worker, mémoire système disponible et alertes
structurées. Il ne retourne ni payload de job, ni question, ni réponse, ni PID ou secret.

La page **Diagnostic** présente ces mesures pour distinguer une recherche longue d’un worker
indisponible. Le bouton d’actualisation effectue seulement ces lectures locales ; il ne génère pas
de réponse ARGO.
