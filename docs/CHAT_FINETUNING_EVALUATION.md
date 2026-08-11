# Évaluation fiable des profils de réponse

Le terme « finetuning » désigne ici l'évaluation contrôlée des profils de prompt P0/P1/P2. Ce
processus ne modifie pas les poids d'un modèle.

## Invariants

- Une cellule est identifiée par `run_id + profile + question_id`.
- Le texte normalisé de la question est figé par SHA-256 dans le payload durable.
- Une cellule racine crée toujours une conversation neuve et vide.
- Une seule question utilisateur distincte est autorisée dans cette conversation.
- Un seul job durable peut être actif lors de la soumission d'une cellule d'évaluation.
- Le profil est attaché au job et ne dépend pas d'une modification ultérieure de la configuration
  globale.
- Une cellule déjà soumise ne peut pas être recréée avec un autre `client_request_id`.
- Un retry explicite réutilise le même message utilisateur et la même identité de cellule.
- Le verrou global d'un seul job actif s'applique aussi aux retries explicites.
- Tout état terminal produit un message assistant visible : réponse scientifique en cas de succès,
  notice bornée en cas d'échec ou d'annulation.

## Soumission atomique

Utiliser l'endpoint dédié, qui crée la conversation et le job dans une même transaction :

```http
POST /api/chatbot/evaluation/jobs
Content-Type: application/json

{
  "message": "Question scientifique immuable",
  "client_request_id": "11111111-1111-4111-8111-111111111111",
  "run_id": "cs-long-20260806-rerun-01",
  "question_id": "Q1",
  "profile": "p0"
}
```

Une seconde cellule reçoit HTTP 409 tant que le job précédent n'est pas terminal. L'appel est
idempotent si les mêmes identité de cellule et `client_request_id` sont renvoyés.

Le chat standard accepte également les métadonnées d'évaluation, mais exige alors une conversation
déjà créée et strictement vide. L'endpoint dédié reste la voie recommandée.

## Résultats et blocages

Chaque réponse réussie expose maintenant un statut explicite :

- `generated` : synthèse ARGO validée ;
- `partial_generated` : une partie citée et validée de la synthèse est rendue dans la structure
  normale, avec une limite localisée sur les axes non assemblés ;
- `abstained` : les preuves ne permettent aucune affirmation scientifique validable ;
- `diagnostic_only` : une étape technique n'a pas permis de produire une synthèse ; cette sortie est
  une anomalie mesurable du pipeline et non une affirmation d'absence dans le corpus.

`extractive_fallback` demeure lisible pour les anciennes réponses persistées, mais n'est plus produit :
les passages bruts et la succession de sources ne constituent pas une synthèse scientifique.

Les sorties dégradées sont des succès techniques visibles et persistés. Elles permettent de
poursuivre un lot et de distinguer la fiabilité du pipeline de la qualité scientifique de la
réponse. Seules `generated` et les affirmations effectivement validées de `partial_generated` peuvent
être notées comme contenu scientifique ; `abstained` et `diagnostic_only` ne sont pas des synthèses
ARGO réussies.

Un échec terminal ne disparaît plus du chat. Il persiste une réponse de type
`job_terminal_notice`, avec :

- l'identifiant du job ;
- l'état `failed` ou `cancelled` ;
- le code public (`validation`, `timeout`, etc.) ;
- un code diagnostique borné, par exemple `empty_answerable_statements`,
  `unsupported_evaluative_claim`, `question_integrity`, `internal_handler_error` ou
  `internal_persistence_error`.

Les réponses dégradées utilisent notamment `retrieval_no_qualified_evidence`,
`retrieval_unavailable`, `semantic_filter_empty`, `argo_protocol` et les codes précis de validation
scientifique. Le rapport sépare ainsi un problème de retrieval, de génération, de validation et de
persistance.

Le détail scientifique brut reste dans les journaux locaux et n'est pas exposé dans le chat.
Les notices terminales sont exclues de l'historique envoyé au générateur lors d'un retry.
La conversation active est rechargée pour les trois issues terminales (`succeeded`, `failed`,
`cancelled`) afin que la notice apparaisse sans navigation ni rechargement manuel.

## Audit obligatoire

Après le dernier job terminal :

```powershell
.\.venv\Scripts\python.exe -m scripts.audit_chat_finetuning `
  cs-long-20260806-rerun-01 `
  --database data/database/science_rag.sqlite3
```

L'audit est en lecture seule et échoue si :

- une conversation contient plusieurs questions utilisateur ;
- une empreinte ne correspond plus au texte ;
- un succès n'a pas de réponse visible ou de trace d'évaluation ;
- un échec ou une annulation n'a pas de notice visible ;
- la question de la réponse diffère de la question soumise ;
- plusieurs exécutions d'évaluation se chevauchent.

Un lot ne peut être noté ou comparé que si `complete=true` et `reliable=true`. Les corrections du
pipeline effectuées pendant un lot imposent un nouveau `run_id`.

## Campagne automatique et reprenable

Le worker durable CiderScholar doit être actif. Le pilote soumet ensuite exactement une cellule,
attend sa sortie terminale visible, la vérifie, puis seulement alors passe à la suivante. Pendant
ce temps, le backend refuse aussi la soumission de tout autre job durable afin de préserver
l'exécution mono-job.

Créer un manifeste sur le modèle de
`docs/examples/chat-finetuning-manifest.example.json`, puis lancer :

```powershell
.\.venv\Scripts\python.exe -m scripts.run_chat_finetuning `
  docs/examples/chat-finetuning-manifest.example.json `
  --database data/database/science_rag.sqlite3 `
  --output data/exports/chat-finetuning/run-20260806-01
```

Le répertoire de sortie contient à tout instant :

- `state.json`, état atomique permettant la reprise après interruption ;
- `events.jsonl`, journal sans texte de question, fondé sur identifiants et empreintes ;
- `audit.json`, contrôle machine de l'isolation et de la visibilité ;
- `report.md`, bilan lisible des sorties et diagnostics.

Le pilote respecte un `retry_at` de quota même s'il dépasse le timeout normal. Un job bloqué est
annulé coopérativement ; une question encore en file produit immédiatement une notice visible. Un
échec ou une annulation terminale ouvre le circuit et interdit de reprendre les cellules restantes
avec le même `run_id`.

Les tests manuels restent possibles avec l'endpoint dédié. Une campagne manuelle est valide si
chaque appel attend l'état terminal avant le suivant et si l'audit final est exécuté.
