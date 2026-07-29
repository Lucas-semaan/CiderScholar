# Protocole CiderQA

Version du protocole : `1.0.0`  
Statut : protocole figé avant constitution du jeu et avant modification du pipeline scientifique  
Autorité des résultats : fichiers CiderQA validés, corpus SQLite empreinté et rapports signés par hash

## Objectif et population

CiderQA mesure la capacité de CiderScholar à retrouver, citer et synthétiser des preuves relatives à
la science du cidre sans transformer une absence de preuve en conclusion. La population documentaire
est constituée d’articles, actes ou rapports scientifiques cidricoles réels dont le texte utilisé par
l’évaluation est présent dans le corpus commun et possède une provenance, un hash et des pages
vérifiables.

Le jeu cible contient au moins 100 questions indépendantes. Aucun PDF synthétique de démonstration,
document privé, contenu interne non publié ou texte dont le droit d’usage n’est pas établi ne compte
dans ce seuil. Une question est admise seulement après vérification de ses preuves de référence dans
le fichier local empreinté.

## Tâches évaluées

Chaque question appartient à une tâche principale :

1. réponse directe à partir d’un ou plusieurs passages ;
2. comparaison de méthodes, populations ou conditions ;
3. synthèse multi-articles, y compris résultats contradictoires ;
4. abstention lorsque le corpus ne permet pas de répondre ;
5. suivi conversationnel dont le contexte autorisé est explicitement versionné.

Le jeu doit contenir au moins 25 cas nécessitant le corps, un tableau ou une figure, 15 cas non
répondables et 20 cas multi-articles, comparatifs ou contradictoires. Le français et l’anglais
représentent chacun 45 à 55 % du total et couvrent toutes les tâches applicables.

## Séparation et gel

L’unité de séparation est la question, avec regroupement obligatoire des variantes et suivis d’une
même question dans un seul jeu. Les articles peuvent être communs à plusieurs jeux seulement si les
questions et preuves cibles sont distinctes ; le rapport signale ce chevauchement.

| Jeu | Part cible | Usage autorisé |
| --- | ---: | --- |
| Développement | 50 % | Débogage, analyse d’erreurs et évolution du code. |
| Validation | 30 % | Choix de paramètres, prompts, seuils et décision de promotion. |
| Test final | 20 % | Une exécution après gel ; jamais utilisé pour régler le système. |

Le manifeste du test final, son ordre aléatoire et son hash SHA-256 sont gelés avant toute mesure de
promotion. Les labels du test final sont conservés séparément du fichier d’inférence. Toute lecture
des labels finaux pendant le réglage invalide le cycle et impose une nouvelle version majeure du jeu.

## Annotation et aveugle

Deux experts du domaine annotent indépendamment répondabilité, réponse de référence, affirmations
atomiques et preuves. Ils voient les documents sources mais jamais une réponse CiderScholar pendant
la création ou l’arbitrage de la référence. Un troisième expert arbitre les désaccords persistants.

Pour chaque preuve, l’annotation enregistre l’identifiant d’article, le hash du fichier, le type de
preuve (`abstract`, `body`, `table`, `figure`), la ou les pages et un extrait verbatim. Une réponse de
référence ne peut citer une preuve inaccessible ou uniquement déduite de métadonnées.

## Métriques

Les résultats sont publiés par jeu, langue, tâche, caractère répondable et niveau de preuve, avec
intervalle bootstrap à 95 % sur les questions.

### Recherche

- rappel documentaire@20, MRR et nDCG@20 au niveau notice et article ;
- rappel@20 et nDCG@20 au niveau fragment ;
- taux de récupération de chaque preuve de référence.

Les labels attendus servent uniquement au calcul après inférence. Ni article, ni concept attendu, ni
extrait ou page de référence n’entre dans la recherche, les variantes de requête ou le prompt.

### Réponse et citations

- exactitude et complétude des affirmations atomiques, notées séparément par expert ;
- précision et rappel des citations par affirmation ;
- implication sémantique de l’affirmation par l’extrait cité, avec contrôles de négation, unité,
  population, condition et temporalité ;
- taux d’affirmations sans preuve et taux de localisateurs de page exacts.

Une réponse citée mais factuellement fausse obtient une exactitude nulle pour l’affirmation concernée.
Une citation bibliographiquement correcte mais non impliquante est comptée comme fausse.

### Abstention

- sensibilité d’abstention sur les cas non répondables ;
- spécificité, ou un moins le taux de faux refus, sur les cas répondables ;
- valeur prédictive des réponses produites et courbe de calibration du score d’insuffisance.

Répondables et non-répondables sont toujours rapportés séparément.

## Seuils d’acceptation initiaux

Sur validation, puis une fois sur test final :

- rappel article@20 ≥ 0,90, MRR ≥ 0,75 et nDCG@20 ≥ 0,80 ;
- exactitude atomique ≥ 0,85 et complétude ≥ 0,80 ;
- précision des citations ≥ 0,95, rappel des citations ≥ 0,85 et pages exactes ≥ 0,95 ;
- sensibilité d’abstention ≥ 0,85 et spécificité ≥ 0,85 ;
- aucune fuite de label, citation fabriquée, preuve privée ou régression P0/P1.

Une promotion exige tous les seuils. Les tolérances de régression détaillées sont adoptées dans la
politique `EVL-017` ; jusque-là, aucune amélioration partielle ne devient le défaut.

## Règles d’exclusion et écarts

Une question est exclue avant ouverture des résultats si elle est dupliquée, ambiguë sans arbitrage,
hors domaine, dépend d’une preuve absente, contient une page erronée, expose une donnée privée ou a été
annotée en consultant une sortie du système. Le motif et l’identité de version restent consignés ; une
exclusion ne sert jamais à retirer après coup une erreur du système.

Toute correction de label après gel produit un journal d’écart et une nouvelle version du jeu. Une
modification de question, réponse, répondabilité, article, extrait ou page modifie l’empreinte.

## Exécution reproductible

Le runner fonctionne localement et refuse les appels réseau implicites. Chaque rapport consigne :

- versions et hash du jeu, du corpus, du code, des prompts et des modèles ;
- mode abstract-only ou full-text, paramètres de recherche, seuils et graines ;
- système, mémoire de pointe, durée et nombre/coût des appels ARGO explicitement autorisés ;
- métriques par strate, exclusions préenregistrées et empreinte SHA-256 du rapport.

Une exécution avec ARGO nécessite une option explicite et un budget borné. Sans cette option, toute
tentative réseau échoue ; un résultat simulé porte ce statut et ne peut être publié comme baseline.

Avant toute exécution, le validateur hors ligne vérifie les trois fichiers gelés, leurs hashes,
l’absence de famille traversant les splits et les quotas structurels publics :

```powershell
.\.venv\Scripts\python.exe -m scripts.validate_ciderqa_dataset `
  --manifest "C:\chemin\ciderqa\manifest.json" `
  --output "C:\chemin\ciderqa\readiness.json"
```

Un rapport `structurally_ready: true` ne remplace jamais la validation experte en aveugle ; le champ
`expert_validation_required` reste toujours vrai.
