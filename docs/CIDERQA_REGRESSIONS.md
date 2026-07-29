# Cas de non-régression CiderQA

Après les vraies baselines EVL-016, un expert classe six questions distinctes ayant réellement
échoué : `negation`, `unit`, `population`, `page`, `source` et `forced_answer`. Le fichier JSON
d’entrée contient uniquement :

```json
[
  {
    "category": "negation",
    "question_id": "ciderqa-...",
    "rationale": "Description factuelle de l’erreur observée dans la baseline."
  }
]
```

La liste doit contenir exactement les six catégories. La commande suivante vérifie dans le rapport
signé que chaque question présente bien l’échec annoncé, puis fige le paquet :

```powershell
python -m scripts.prepare_ciderqa_regressions `
  --baseline artifacts/ciderqa/baseline-full-text.json `
  --classifications artifacts/ciderqa/regression-classifications.json `
  --output artifacts/ciderqa/regressions.json
```

Chaque candidat est ensuite contrôlé automatiquement :

```powershell
python -m scripts.replay_ciderqa_regressions `
  --package artifacts/ciderqa/regressions.json `
  --candidate artifacts/ciderqa/candidate.json `
  --output artifacts/ciderqa/regression-replay.json
```

Les trois erreurs sémantiques exigent exactitude et complétude parfaites sur leur question ; page et
source exigent une précision parfaite ; la réponse forcée exige l’abstention. Une signature, un jeu
ou un split différent est refusé.
