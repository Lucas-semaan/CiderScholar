# Rapport d'incident — filtre sémantique du chat

Date : 20 septembre 2026.

## Observation reproductible

La dernière réponse enregistrée le 17 septembre 2026 à la question sur les opérations de production de cidres thiolés a suivi cette trajectoire :

1. la recherche a sélectionné 31 passages de texte intégral et 8 notices avec abstract, soit 39 passages dans 16 documents ;
2. le filtre sémantique global a écarté 13 documents comme `C` ou `D` ; les huit documents texte intégral ont tous été écartés ;
3. il ne restait que trois abstracts ;
4. la génération a effectué dix requêtes, puis a produit `status=insufficient` sans citation ;
5. le validateur a rejeté cette abstention (`unjustified_abstention`, `missing_required_evidence`) et le rendu final a été une réponse vide sans référence.

Le corpus n'était donc pas vide. La perte de sortie est due à l'enchaînement sélection sémantique → contrat de génération, pas à RAGFlow.

## Causes établies

### Filtre global trop opaque pour être piloté

Le filtre ne conserve que les décisions A/B du modèle pour la génération. Il n'enregistre dans la trace agrégée que le nombre de rejets C/D ; ni les identifiants concernés, ni le besoin de vérification, ni le motif court de chaque décision ne sont disponibles dans la réponse persistée. Il est donc impossible de distinguer une exclusion scientifiquement justifiée d'un faux négatif sans rejouer le cas.

L'incident montre aussi une concentration anormale : 100 % des preuves full-text ont disparu tandis que trois abstracts ont été gardés. Ce signal doit déclencher un diagnostic, non une rétrogradation silencieuse en réponse sans sources.

### Contrat de génération contradictoire dans ce cas

Lorsqu'il existe des preuves A/B obligatoires, le validateur interdit `status=insufficient` et exige que toutes les preuves obligatoires soient citées. Si le modèle s'abstient, aucune réponse partielle n'est conservée ; si une réponse n'en couvre qu'une partie, elle est aussi rejetée. Le mécanisme de récupération ne peut aider que si au moins une tentative a déjà généré une affirmation validée.

Cette règle protège contre une réponse sélective trompeuse, mais elle ne prévoit pas le cas sûr où le modèle ne sait pas assembler les preuves restantes malgré leur admission. Le résultat n'est alors ni une réponse citée ni une abstention qui expose les sources.

## Correction requise, sans assouplissement aveugle

1. Persister, pour chaque élément filtré, son identifiant de preuve, sa note A–D, le besoin de vérification associé et un motif borné. Le texte source, la question et le prompt ne doivent pas être journalisés.
2. Ajouter une garde d'incident : lorsque le filtre exclut toutes les preuves full-text mais conserve des abstracts, produire un diagnostic `semantic_filter_full_text_exhausted` et conserver les identifiants candidats pour revue. Cela ne promeut aucune preuve rejetée.
3. Distinguer deux ensembles à la synthèse : les preuves admissibles et un sous-ensemble représentatif obligatoire, borné à une preuve par article/axe documenté. Toutes les affirmations affichées restent vérifiées par `ChatAnswerVerifier`; une preuve non citée ne devient jamais une affirmation implicite.
4. Après l'épuisement des reprises, si des affirmations individuellement validées existent, rendre cette réponse partielle avec une limite explicite et ses sources. Si aucune affirmation n'est validée, rendre une abstention accompagnée des références des preuves A/B récupérées et du diagnostic technique — sans formuler de conclusion scientifique.
5. Ajouter des tests couvrant : rejet de tous les full-text, abstention modèle malgré A/B, réponse partielle avec une seule affirmation validée, et interdiction persistante d'afficher une affirmation non étayée.

## Critères d'acceptation

- Une incompatibilité modèle/index bloque la recherche vectorielle avec un code exploitable ; elle ne se dégrade pas silencieusement.
- Les traces permettent d'expliquer pourquoi une preuve a été exclue, sans enregistrer le contenu scientifique ni la question.
- Une réponse partielle ne contient que des affirmations individuellement citées et validées.
- Une abstention n'est jamais présentée comme une absence de références lorsque des preuves récupérées existent ; elle n'affirme aucun résultat scientifique.
- Le cas du 17 septembre est rejouable hors réseau avec des sorties de filtre et de génération simulées.
