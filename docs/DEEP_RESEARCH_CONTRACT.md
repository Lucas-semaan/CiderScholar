# Contrats Réponse rapide et Analyse approfondie

Version : `1.0.0`  
Activation : la Réponse rapide est disponible ; l’Analyse approfondie reste désactivée jusqu’à
`DRS-025` et au franchissement du gate CiderQA.

## Réponse rapide

- Sources : notices et abstracts du corpus commun local ; enrichissement bibliographique en direct
  seulement après action explicite du profil autorisé. Aucun document privé n’est envoyé à une API.
- Preuve rendue : niveau **Abstract** sur chaque source et avertissement que le texte intégral n’a pas
  été vérifié.
- Budget : 20 notices locales au maximum, 10 abstracts présentés au modèle et une génération ARGO.
- Délai : cible interactive de deux minutes ; le travail reste durable et peut reprendre après
  fermeture.
- Sortie : prose par défaut, références reconstruites depuis SQLite et limites explicites.
- Abstention : aucune source qualifiée, information absente des abstracts, contradiction non résolue,
  citation non traçable ou quota non repris dans le délai du travail.

La réponse rapide ne peut présenter une valeur, une condition expérimentale, un tableau ou une
figure comme vérifié dans le corps du document.

## Analyse approfondie

- Sources : fragments full-text locaux des corpus commun et privé, lus dans leurs stockages séparés ;
  abstracts locaux seulement comme rappel. Une traversée bibliographique ne compte comme preuve que
  si le texte correspondant est effectivement accessible et ingéré.
- Preuve rendue : **Texte intégral** pour une affirmation seulement si un extrait verbatim et ses pages
  SQLite ont passé la vérification ; sinon la source reste **Abstract**.
- Budget de recherche : RRF sur 80 fragments au maximum, cross-encoder sur 40, 12 fragments conservés,
  deux itérations de recherche au total et une traversée bornée de 10 relations DOI.
- Budget ARGO : résumé contextuel facultatif sur les seuls 12 fragments, une vérification structurée
  et une génération finale ; chaque requête est comptée avant envoi et respecte le quota local.
- Délai : cible de 15 minutes, plafond de travail de 30 minutes hors attente de quota ; progression,
  annulation et reprise utilisent le même identifiant durable.
- Sortie : affirmations atomiques, niveau épistémique, citations/pages SQLite, contradictions et
  informations manquantes visibles.
- Abstention : preuves insuffisantes après deux recherches, implication ou pages invalides, conflit
  non arbitrable, information uniquement inaccessible, ou budget épuisé.

## Invariants communs

Les deux modes n’utilisent jamais les labels CiderQA à l’inférence, n’inventent ni DOI ni page, ne
mélangent pas stockage commun et privé et n’utilisent pas la mémoire du modèle pour combler une
lacune. Le cache est invalidé par toute variation de question, corpus, modèles, prompts ou paramètres.
Le mode visible et le niveau de preuve de chaque source restent persistés avec la réponse.
