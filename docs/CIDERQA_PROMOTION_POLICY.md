# Politique de promotion scientifique CiderQA

Version : `1.0.0` — adoptée avant observation des baselines CiderQA réelles.

Une version candidate ne devient jamais la version par défaut sur la seule base d’une amélioration
globale. Elle doit être comparée à une baseline signée sur exactement le même hash de jeu, le même
split final et le même mode (`abstract_only` ou `full_text`). Une baseline absente, une signature
invalide ou un hash différent bloque la décision.

## Seuils absolus

La candidate doit atteindre simultanément : rappel article@20 0,90, MRR 0,75, nDCG@20 0,80,
exactitude 0,85, complétude 0,80, précision des citations 0,95, rappel des citations 0,85, exactitude
des pages 0,95, sensibilité et spécificité d’abstention 0,85.

## Budget maximal de régression

Par rapport à la baseline, la baisse maximale autorisée est :

| Mesure | Baisse maximale |
| --- | ---: |
| rappel article@20, MRR, nDCG@20 | 0,02 |
| exactitude | 0,01 |
| complétude | 0,02 |
| précision des citations | 0,005 |
| rappel des citations | 0,01 |
| implication sémantique | 0,01 |
| exactitude des pages | 0,005 |
| sensibilité/spécificité d’abstention | 0,02 |

Une seule violation bloque la promotion, même si une autre mesure progresse. Toute fuite de label,
citation fabriquée, réponse forcée, défaut P0/P1 ou dépassement du budget explicite ARGO bloque aussi
la promotion hors calcul numérique.

## Procédure

1. Vérifier signatures, hash de jeu, split, mode et protocole.
2. Appliquer les seuils absolus puis les budgets de régression avec le module
   `app.evaluation.ciderqa_promotion`.
3. Publier la décision et tous ses motifs, y compris lorsqu’elle est refusée.
4. Ne jamais modifier cette version de politique pour sauver une candidate ; toute évolution produit
   une nouvelle version adoptée avant le cycle suivant.
