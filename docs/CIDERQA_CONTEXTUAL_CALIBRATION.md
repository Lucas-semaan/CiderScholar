# Calibration CiderQA de la pertinence contextuelle

Ce protocole débloque `DRS-010` après constitution et gel du vrai CiderQA (`EVL-005` à `EVL-010`).
Il s’exécute hors ligne sur le split `development`. Les labels experts servent uniquement à choisir
le seuil ; ils ne sont jamais accessibles au pipeline d’inférence.

## Générer le paquet de revue

Le générateur lit uniquement les questions du split `development`, interroge les deux corpus locaux
et produit des résumés contextuels via ARGO. Il emploie des identifiants déterministes : relancer la
même commande reprend les snapshots existants. Il s’arrête proprement au quota et affiche
`quota_retry_at`; la même commande poursuit ensuite le travail.

```powershell
.\.venv\Scripts\python.exe -m scripts.generate_contextual_adjudication `
  --manifest "C:\chemin\ciderqa\manifest.json" `
  --output "C:\chemin\contextual-adjudication.json" `
  --summary-top-k 2 `
  --allow-argo
```

`--allow-argo` est obligatoire pour éviter tout appel réseau involontaire. Le générateur n’accède
jamais à `expected_answer`, `expected_claims` ni `reference_evidence`. Chaque entrée produite contient
la question, le résumé, sa provenance et `expert_relevant: null`.

L’expert travaille uniquement sur sa machine et remplace chaque valeur `null` par `true` ou `false`.
Le fichier de revue peut contenir du texte scientifique ou privé : il ne doit donc être ni commité,
ni partagé dans un rapport automatique.

Un paquet peut aussi être reconstruit depuis des snapshots existants, sans réseau :

```powershell
.\.venv\Scripts\python.exe -m scripts.prepare_contextual_adjudication `
  --manifest "C:\chemin\ciderqa\manifest.json" `
  --snapshots-root "data\cache\deep_research" `
  --output "C:\chemin\contextual-adjudication.json"
```

## Finaliser les observations

La finalisation refuse le paquet tant qu’une seule décision manque, puis retire questions et résumés :

```powershell
.\.venv\Scripts\python.exe -m scripts.finalize_contextual_adjudication `
  --adjudication "C:\chemin\contextual-adjudication.json" `
  --output "C:\chemin\contextual-observations.json"
```

## Observations attendues

Le fichier JSON contient au moins 20 fragments issus d’au moins 10 questions CiderQA différentes,
avec au moins un fragment pertinent et un fragment rejeté. Un couple question/empreinte ne peut
apparaître qu’une fois.

```json
{
  "schema_version": 1,
  "split": "development",
  "dataset_sha256": "<sha256 du split development gelé>",
  "observations": [
    {
      "question_id": "ciderqa-exemple-001",
      "text_sha256": "<sha256 du fragment>",
      "relevance_score": 0.82,
      "expert_relevant": true
    },
    {
      "question_id": "ciderqa-exemple-001",
      "text_sha256": "<sha256 d’un autre fragment>",
      "relevance_score": 0.21,
      "expert_relevant": false
    }
  ]
}
```

Le texte des fragments, les questions, les résumés, les PDF et les contenus de chat ne sont jamais
ajoutés à ce fichier final.

## Calcul reproductible

```powershell
.\.venv\Scripts\python.exe -m scripts.calibrate_contextual_relevance `
  --manifest "C:\chemin\ciderqa\manifest.json" `
  --observations "C:\chemin\contextual-observations.json" `
  --output "C:\chemin\contextual-calibration.json"
```

Le script vérifie le hash et les identifiants du split, ne fait aucun appel réseau et choisit le seuil
maximisant F1, puis la précision et enfin le seuil le plus strict en cas d’égalité.

Après revue experte du rapport, reporter dans la configuration :

```yaml
deep_research:
  contextual_relevance_threshold: <threshold>
  contextual_relevance_observations_sha256: <observations_sha256>
  contextual_summary_enabled: false
```

`contextual_summary_enabled` reste `false` jusqu’à la promotion globale `DRS-025`.
