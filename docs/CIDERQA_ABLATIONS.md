# Ablations CiderQA du mode approfondi

Cette procédure mesure séparément les cinq étages du mode approfondi. Elle ne remplace pas le jeu
CiderQA réel ni son adjudication experte.

## Matrice figée

Les six exécutions utilisent exactement le même split, le même corpus, la même révision, les mêmes
versions de modèles, les mêmes graines et le même ordre de questions :

| Variante | Variantes bilingues | Reranker | Résumé contextuel | 2e itération | Citations |
|---|---:|---:|---:|---:|---:|
| `baseline` | non | non | non | non | non |
| `query_variants` | oui | non | non | non | non |
| `reranker` | non | oui | non | non | non |
| `contextual_summary` | non | non | oui | non | non |
| `iteration` | non | non | non | oui | non |
| `citation_traversal` | non | non | non | non | oui |

## Exécution

1. Geler le plan avant toute inférence :

   ```powershell
   python -m scripts.prepare_ciderqa_ablations `
     --dataset-version 1.0.0 `
     --dataset-sha256 <SHA256_SPLIT> `
     --split validation `
     --mode full_text `
     --corpus-sha256 <SHA256_CORPUS> `
     --code-revision <REVISION_GIT> `
     --model-versions-json '{"embedding":"...","reranker":"...","generator":"..."}' `
     --output artifacts/ciderqa/ablation-plan.json
   ```

2. Copier, pour chaque variante, les paramètres JSON imprimés dans `parameters` du
   `CiderQARunContext`. Produire puis signer les six rapports avec `scripts/evaluate_ciderqa.py`.

3. Comparer les rapports :

   ```powershell
   python -m scripts.compare_ciderqa_ablations `
     --plan artifacts/ciderqa/ablation-plan.json `
     --report baseline=artifacts/ciderqa/baseline.json `
     --report query_variants=artifacts/ciderqa/query-variants.json `
     --report reranker=artifacts/ciderqa/reranker.json `
     --report contextual_summary=artifacts/ciderqa/contextual-summary.json `
     --report iteration=artifacts/ciderqa/iteration.json `
     --report citation_traversal=artifacts/ciderqa/citation-traversal.json `
     --output artifacts/ciderqa/ablation-report.json
   ```

Le comparateur refuse toute signature modifiée, matrice incomplète, différence de jeu, corpus,
révision, modèles, graines, questions ou paramètres d’étage. Le rapport final contient les onze
deltas scientifiques, ainsi que les deltas de durée, mémoire, requêtes et coût ARGO par rapport au
baseline unique.
