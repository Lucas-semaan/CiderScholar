# Baselines CiderQA

Les baselines `abstract_only` et `full_text` doivent provenir du même split CiderQA gelé. Elles
utilisent le même corpus, la même révision effective, les mêmes modèles, les mêmes graines et le
même ordre de questions. Elles ne sont pas des résultats PaperQA2.

Pour chaque mode, produire les résultats adjudicables puis le rapport signé avec
`python -m scripts.evaluate_ciderqa`. Les champs de durée, mémoire et consommation ARGO doivent
provenir de l’exécution observée.

Comparer ensuite les deux rapports :

```powershell
python -m scripts.compare_ciderqa_baselines `
  --abstract-only artifacts/ciderqa/baseline-abstract.json `
  --full-text artifacts/ciderqa/baseline-full-text.json `
  --output artifacts/ciderqa/baselines.json
```

Le comparateur refuse une signature altérée ou toute différence autre que le mode de preuve. Il
rapporte sans compensation les onze métriques scientifiques et les deltas de durée, mémoire,
requêtes et coût.
