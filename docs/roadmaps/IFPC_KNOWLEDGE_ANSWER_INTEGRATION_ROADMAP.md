# Intégration du modèle de réponse « Livre de connaissances »

Statut : roadmap incrémentale, première tranche livrée le 22 septembre 2026.

## 1. Référence observée

L'écran authentifié <https://ifpc.vercel.app/assistant> a été examiné directement le 22 septembre
2026 avec la question « À quelle température conduire la fermentation d'un cidre ? ».

Les éléments utiles observés sont :

- des appels de source numérotés dans le fil de la réponse ;
- un panneau progressif « sources du Livre » donnant accès aux fiches complètes ;
- des planches ou illustrations mêlées à la restitution lorsque le moteur les juge pertinentes ;
- des étapes visibles d'analyse, recherche et rédaction ;
- des questions suggérées et des actions de copie ou d'évaluation en fin de réponse.

Deux limites observées ne doivent pas être reproduites : le panneau peut présenter davantage de
sources que la réponse n'en cite réellement, et l'ouverture d'une fiche renvoie au début du document
plutôt qu'au passage qui soutient l'affirmation. Une illustration de conduite du verger était aussi
présente sans relation évidente avec la température de fermentation.

## 2. Décision de conception CiderScholar

CiderScholar conserve la divulgation progressive et la lisibilité de cette expérience, mais les
subordonne au contrat scientifique existant :

1. un appel de citation est généré exclusivement depuis des `evidence_ids` validés et persistés ;
2. son activation ouvre le passage exact, ses localisateurs et sa famille de source ;
3. une liste nommée « Sources citées » ne contient que les sources effectivement appelées dans le
   texte ; les autres résultats éventuels sont séparés sous « Sources consultées » ;
4. une figure n'est montrée que si une relation persistée la rattache à une preuve citée ;
5. le Livre AsCoCid et les publications scientifiques restent visuellement identifiables sans créer
   deux systèmes de preuve concurrents.

## 3. Roadmap d'intégration

### Tranche 1 — Citations ancrées et preuve exacte — livrée

- Produire côté backend des ancres stables à partir des preuves réellement citées.
- Conserver le libellé bibliographique visible existant et le rendre interactif.
- Ouvrir un dialogue accessible contenant le ou les extraits exacts, la section, la page ou le
  localisateur structurel et les liens vers le PDF local ou la source distante.
- Distinguer explicitement « Livre AsCoCid » et « Publication scientifique ».
- Ajouter l'action de copie de la réponse.

Critères vérifiables : aucune ancre sans preuve validée ; aucune preuve non citée injectée dans le
dialogue ; ouverture du PDF local à la première page citée ; fonctionnement au clavier et fermeture
avec Échap.

### Tranche 2 — Panneau de sources progressif

- Remplacer la liste actuelle par deux groupes non ambigus : « Sources citées » puis, si utile,
  « Sources consultées ».
- Afficher le nombre d'affirmations soutenues par chaque source et permettre d'aller vers chacune.
- Conserver le résumé bibliographique compact, avec accès secondaire aux métadonnées détaillées.
- Ne jamais présenter le nombre de résultats récupérés comme un nombre de références utilisées.

Critères vérifiables : bijection testée entre appels dans le texte et groupe cité ; ordre stable par
première apparition ; états vide, incomplet et erreur explicités.

### Tranche 3 — Figures et planches reliées aux preuves

- Introduire un manifeste persistant `figure -> evidence_ids` au lieu d'associer une image par simple
  proximité lexicale.
- Afficher une figure uniquement lorsqu'au moins une preuve citée de la réponse lui est reliée.
- Montrer légende, provenance, page et raison de sa présence ; permettre d'ouvrir son contexte.
- Évaluer séparément la pertinence documentaire et la qualité visuelle.

Critères vérifiables : zéro illustration orpheline ; aucune image issue d'un document seulement
consulté ; test de non-régression sur une question de fermentation qui exclut les visuels de verger.

### Tranche 4 — Parcours de réponse réutilisable

- Exposer des états honnêtes et courts : compréhension, recherche locale, validation des preuves,
  rédaction. Un état n'est affiché que si l'étape a réellement été exécutée.
- Générer les questions suivantes à partir du périmètre de la réponse et des lacunes explicites, sans
  les présenter comme des conclusions scientifiques.
- Ajouter un retour structuré : utile, preuve inadéquate, passage introuvable, réponse incomplète.
- Réutiliser ces primitives dans les synthèses longues et les écrans de détail plutôt que créer une
  variante propre au chatbot.

Critères vérifiables : état terminal systématique ; annulation et erreur récupérables ; feedback lié à
la version de réponse et à ses preuves.

### Tranche 5 — Évaluation scientifique et déploiement

- Constituer un jeu de questions couvrant AsCoCid seul, littérature seule, mode mixte, contradiction,
  absence de preuve, localisateurs structurels et figures.
- Mesurer précision des citations, couverture des affirmations, exactitude du passage ouvert,
  pertinence des figures et taux de sources affichées mais non citées.
- Activer progressivement les tranches 2 à 4 derrière des options indépendantes et comparer les
  résultats avant généralisation.

Seuil de sortie recommandé : 100 % des appels résolus vers une preuve persistée, 0 source non citée
dans « Sources citées », 0 figure orpheline et aucune régression sur la suite CiderScholar.

## 4. Ordre de réalisation

La tranche 2 est la prochaine priorité : elle corrige l'ambiguïté la plus visible tout en s'appuyant
sur les ancres déjà livrées. La tranche 3 vient ensuite, car elle exige un nouveau contrat de données
et ne doit pas être simulée depuis les seuls résultats de recherche. Les tranches 4 et 5 généralisent
les bons motifs d'interface au reste de l'outil et sécurisent leur déploiement.
