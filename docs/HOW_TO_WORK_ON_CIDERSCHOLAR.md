# Comment travailler sur CiderScholar

Statut : guide méthodologique accepté.
Dernière consolidation : 6 août 2026.

Ce guide transforme les consignes méthodologiques dispersées dans les conversations CiderScholar en
règles durables et vérifiables. Il complète `AGENTS.md`, le contrat rédactionnel du chatbot et les
protocoles spécialisés. Une instruction explicite de la tâche courante reste prioritaire.

## 1. Transformer une demande en critères vérifiables

Au début d’une tâche scientifique ou documentaire :

1. relever les contraintes de méthode, de périmètre, de modèle, de délégation, de durée et de sortie ;
2. les reformuler en critères observables avant les appels réseau ou les écritures ;
3. conserver ces critères lors des reprises, délégations et changements de contexte ;
4. vérifier le résultat contre eux avant livraison ;
5. ajouter ici toute nouvelle règle explicitement destinée aux travaux futurs.

Une conversation antérieure sert de contexte, mais une proposition de l’agent ne devient pas une règle
utilisateur par simple répétition. En cas de doute, l’inscrire comme « à confirmer » au lieu de la
présenter comme acquise.

Un exemple qui révèle un défaut doit conduire à une règle générale et à un test représentatif. Il ne
faut pas coder une exception limitée au terme, à la question ou au DOI de l’exemple.

## 2. Choisir les publications admises dans le corpus

### 2.1 Périmètre éditorial

Le corpus scientifique est volontairement plus large que les seules publications contenant le mot
`cidre`. Une publication scientifique ou technique peut être admise si elle relève d’au moins une des
catégories suivantes :

- filière cidricole directe : cidre, hard cider au sens de cidre alcoolisé, fermentation cidricole,
  pomme à cidre, jus ou moût destiné au cidre, pressurage, clarification, stabilisation, élevage ou
  qualité du cidre ;
- matières et coproduits directement utiles : pomme, jus de pomme, moût, marc, pulpe, peau, pépins,
  gâteau de presse, pectines, polyphénols, protéines, azote, microorganismes ou composés pertinents ;
- produits dérivés cidricoles : Pommeau, Calvados, eau-de-vie de cidre, cider brandy, apple brandy,
  apple spirit et autres produits explicitement issus de la pomme ou du cidre ;
- filières connexes : brassicole, viticole et distillation/spiritueux, lorsque la publication étudie
  une matrice, un procédé, un mécanisme, une méthode ou un résultat transférable à une question
  cidricole ;
- matrice proche ou matrice modèle : jus de pomme standard, solution modèle de jus de pomme, autre
  jus ou fermentation de fruit, lorsqu’elle documente un mécanisme pertinent absent ou mal couvert
  dans la matrice cidricole exacte.

L’admission d’une filière connexe n’autorise pas à présenter ses résultats comme démontrés dans le
cidre. L’utilité pour le corpus et la force de preuve pour une question sont deux décisions distinctes.

### 2.2 Décision `accepted`, `review` ou `rejected`

Classer `accepted` une publication dont le lien scientifique ou technique avec le périmètre ci-dessus
est explicite et justifiable. La justification nomme autant que possible la matrice, le procédé ou
mécanisme, et le résultat utile.

Classer `review` lorsque le transfert paraît plausible mais n’est pas démontrable avec le titre,
l’abstract et les métadonnées disponibles, ou lorsque l’identité bibliographique reste incertaine.
Une décision humaine motivée peut corriger un faux négatif du filtre automatique.

Classer `rejected` notamment :

- une occurrence incidente du mot cidre, pomme, Calvados, Pommeau ou d’un acronyme homonyme ;
- une espèce portant `apple` dans son nom vernaculaire sans être la pomme pertinente ;
- une publication clinique, nutritionnelle, marketing, historique ou économique sans objet
  scientifique ou technique transférable à la filière ;
- une autre matrice alimentaire sans mécanisme, procédé, méthode ou résultat transférable explicite ;
- des métadonnées manifestement incohérentes, un document non scientifique hors périmètre ou un titre
  inexploitable.

La précision prime sur le volume, mais un petit plafond arbitraire ne doit pas arrêter une collecte qui
peut produire des milliers de notices pertinentes. Paginer jusqu’à saturation utile, quota ou plafond
de sécurité explicitement annoncé. Auditer régulièrement un échantillon des admissions et arrêter ou
resserrer les requêtes si la précision se dégrade.

### 2.3 Identité, déduplication et niveau de contenu

- Normaliser et vérifier le DOI avant insertion. Comparer le DOI à l’ensemble du corpus actif, pas à
  une seule table ou un seul ancien chemin.
- À DOI normalisé identique, conserver une seule entrée documentaire et privilégier le texte intégral.
- Deux DOI différents ne sont jamais fusionnés sur le seul titre. Sans DOI, SHA-256 et métadonnées
  contrôlées servent de replis prudents ; un titre proche n’est qu’un candidat à revue.
- Un abstract accepté, non vide et associé à un DOI valide reste consultable et recherchable avec le
  niveau `Abstract only` si aucun texte intégral n’est disponible.
- Le niveau `Full article` désigne un contenu intégral réellement acquis et persisté. Il ne faut jamais
  présenter un abstract, un XML partiel ou une position de fragment comme un PDF paginé.
- Un article légalement acquis au cours d’une requête utilisateur et admis éditorialement rejoint
  définitivement le corpus commun ; il n’est pas un cache temporaire propre à la conversation.
- Une décision manuelle d’admission ou de rejet conserve sa raison et ne doit pas être silencieusement
  annulée par une nouvelle collecte automatique.

## 3. Acquérir et ingérer les contenus

Préférer le texte intégral légalement accessible, quel que soit son format pris en charge : PDF, JATS
XML, TEI XML ou texte nettoyé déclaré par le fournisseur. Utiliser l’abstract lorsque le texte intégral
n’est pas disponible.

Pour chaque campagne :

1. inspecter les bases, index, chemins actifs, jobs et verrous réels ;
2. créer une sauvegarde SQLite cohérente avant la première mutation importante ;
3. dédupliquer DOI d’abord, puis SHA-256, avant téléchargement ou insertion ;
4. télécharger atomiquement, calculer le hash, conserver fournisseur, licence ou droit d’accès connu,
   URL et état de reprise ;
5. écrire dans SQLite avec un orchestrateur unique ; les explorations parallèles restent en lecture
   seule jusqu’à consolidation ;
6. traiter les acquisitions et indexations par lots reprenables ; ne pas recalculer un index complet si
   les vecteurs compatibles existent et qu’une migration vérifiée suffit ;
7. contrôler les comptes SQLite, FTS et index, puis produire un rapport `accepted/review/rejected`,
   `Full article/Abstract only`, erreurs et reprises possibles.

Ne pas contourner un paywall, un CAPTCHA ou une restriction d’accès. Un contenu structuré n’est admis
comme tel que si le fournisseur déclare explicitement son type ; ne pas inférer du XML ou un texte
intégral depuis une URL ambiguë.

Pour des fichiers locaux, tenter d’abord l’extraction native. Déclencher l’OCR seulement si aucun texte
exploitable n’est extrait, puis vérifier le résultat. Les fichiers illisibles ou corrompus sont signalés
et exclus de l’index ; ils ne sont supprimés que sur demande explicite, avec une opération récupérable
quand elle est possible.

## 4. Rechercher et classer les preuves pour une question

### 4.1 Comprendre l’intention avant le retrieval

Représenter la question par au moins :

- la matrice ou population ;
- l’étape du procédé ou le mécanisme ;
- les résultats demandés ;
- les conditions qui changent l’interprétation, par exemple souche, température, durée, dose, état
  physiologique, méthode de mesure ou temporalité.

Résoudre les sigles dans le contexte métier avant la recherche. Dans un contexte cidricole, `FML`,
`TML` ou `MLF` désigne par défaut la fermentation malolactique, sauf indice contraire. Les requêtes
doivent inclure les développements français et anglais sans abandonner les termes scientifiques
discriminants de la question.

Lorsqu’un terme est ambigu, formuler l’interprétation retenue et les exclusions. Par exemple, le cuvage
cidricole désigne par défaut le maintien des pommes broyées ou de la pulpe avant pressurage ; il ne se
confond pas automatiquement avec le stockage du jus, le chauffage, le transport, une fermentation
inoculée ou la macération alcoolique du raisin.

### 4.2 Élargissement progressif des matrices

Chercher dans cet ordre :

1. matrice, procédé et résultats exacts ;
2. synonymes et matrice cidricole très proche ;
3. matrice modèle ou filière connexe avec même mécanisme et mêmes résultats ;
4. matrice distante uniquement comme analogie explicitement incertaine.

Exemples validés :

- Calvados + élevage bois → apple brandy/apple spirit/cider brandy → autres eaux-de-vie de fruits →
  cognac ou brandy → vin en dernier recours ;
- occurrence dans du jus de pomme pasteurisé → jus de pomme standard → solution modèle de jus de
  pomme, en distinguant toujours occurrence naturelle, détection, inoculation, croissance et
  inactivation ;
- cidre exact → brassicole, viticole ou distillation seulement si le mécanisme étudié est réellement
  transférable et si la différence de matrice reste visible dans le classement et la réponse.

Un terme commun, une similarité générale ou une mention incidente de la matrice ne suffit pas. Le
reranking porte sur la combinaison `matrice + procédé/mécanisme + résultat + conditions`.

### 4.3 Niveaux de preuve A à D

Attribuer les niveaux relativement à chaque question ou axe :

- `A — direct` : matrice, procédé et résultat correspondent directement ;
- `B — supportive` : mécanisme ou méthode transférable, avec différence explicitement bornée ;
- `C — peripheral` : contexte utile mais ne répond pas à l’effet demandé ;
- `D — irrelevant` : hors sujet ou homonyme.

Les niveaux A et B peuvent alimenter la synthèse. C et D peuvent aider au diagnostic du retrieval mais
ne deviennent pas des preuves directes. Sans preuve A, la réponse doit annoncer la portée indirecte ou
s’abstenir ; elle ne comble pas la lacune avec une analogie.

Le texte intégral ne prime sur un abstract que s’il est au moins aussi pertinent. Un abstract A peut
donc précéder un texte intégral B ou C. À pertinence comparable, le texte intégral paginé reste
préférable.

Une question réellement multi-axes peut être décomposée en un à quatre axes. Chaque axe conserve sa
propre requête et un quota équilibré de preuves. Les brouillons d’axes restent reliés aux preuves
originales mais ne sont jamais eux-mêmes une preuve.

## 5. Produire une réponse scientifique

Appliquer `docs/CHATBOT_RESPONSE_CONTRACT.md`. Pour une synthèse longue, appliquer aussi
`docs/OVERNIGHT_LONG_SYNTHESIS_PROTOCOL_2026-08-05.md`.

Les règles consolidées les plus importantes sont :

- répondre dans la langue de la question ;
- définir les termes ambigus et délimiter matrice, procédé, conditions et résultats avant de
  synthétiser ;
- répondre directement à chaque axe, puis présenter mécanismes, conditions, contradictions et
  limites réellement documentés ;
- distinguer observation, interprétation, hypothèse et recommandation ;
- signaler une preuve issue d’une autre matrice comme indirecte et ne pas l’intégrer comme résultat
  démontré dans la matrice cible ;
- construire citations, pages, auteurs, année, titre et DOI exclusivement depuis les données
  persistées et validées ;
- ne pas inventer une page à partir d’un rang de fragment ;
- ne pas afficher les explications internes du modèle à la place d’une limite scientifique précise ;
- s’abstenir clairement lorsque les documents récupérés ne répondent pas directement à la question.

La longueur suit la complexité et la couverture documentaire, pas un objectif de remplissage. Une
introduction scientifique utile définit le périmètre et les distinctions nécessaires ; elle ne devient
pas une généralité encyclopédique non sourcée.

## 6. Diagnostiquer et améliorer le système

Ne pas conclure « manque de sources » avant d’avoir séparé :

1. interprétation ou planification de la requête ;
2. disponibilité réelle dans le corpus actif ;
3. indexation FTS/vectorielle ;
4. retrieval et reranking ;
5. filtre sémantique ;
6. allocation de la fenêtre de preuves ;
7. génération et assemblage ;
8. validation des citations ou nombres ;
9. différence entre le workspace et la version installée.

Le cas FML a montré qu’une réponse affichée comme sans source pouvait en réalité avoir trouvé les
preuves puis échouer à la validation d’un nombre. Le diagnostic doit donc relire l’état persistant et
les journaux avant de modifier le retrieval.

Pour une boucle d’amélioration :

- figer une baseline, les questions, les critères et un lot de contrôle ;
- journaliser réponses brutes, sources, réglages, versions, coûts, latences et erreurs ;
- modifier une seule famille de paramètres par cycle ;
- rejouer le cas révélateur et au moins un contrôle indépendant ;
- généraliser la correction, sans introduire dans le prompt la réponse particulière du benchmark ;
- augmenter le budget de tokens seulement si des axes déjà présents dans les preuves sont omis ou si
  une troncature est observée ;
- rejeter une réponse plus longue qui n’améliore pas la couverture, la précision des citations ou la
  densité scientifique ;
- ne promouvoir aucun candidat qui dégrade la traçabilité, l’abstention ou la fidélité aux preuves.

## 7. Traçabilité des décisions consolidées

Les règles ci-dessus proviennent notamment des conversations suivantes :

- **Clarifier la fusion des notices PDF** : base documentaire unique, DOI vérifié, `Full article` ou
  `Abstract only` ;
- **Planifier collecte bibliographique** : collecte de plusieurs milliers de notices, pertinence avant
  petit plafond arbitraire, acquisition full text puis repli abstract ;
- **Analyser le workflow de collecte** : textes intégraux natifs autres que PDF, reprise, hash et
  généralisation aux fournisseurs ;
- **Vérifier et indexer les articles** : DOI d’abord, admission manuelle motivée, sources légales et
  distinction notice/PDF ;
- **Élargir la recherche sur les jus de pomme** : élargissement progressif aux matrices proches et
  distinction occurrence/inoculation/inactivation ;
- **Améliorer le retrieval RAG Calvados** : hiérarchie des matrices et classement par matrice, procédé
  et résultats ;
- **Corriger la recherche FML cidre** : interprétation métier des sigles et diagnostic séparant corpus,
  retrieval et validation ;
- **Corriger le pipeline RAG Argo** : généralisation des défauts, niveaux A–D et abstention ;
- **Planifier boucles d’amélioration** : baseline, une variable par cycle, gain scientifique plutôt que
  longueur ;
- **Indexer les fichiers Biblio HG** : extraction native, OCR de repli, reprise et rapport des fichiers
  illisibles.

## 8. Checklist de livraison

Avant de conclure une tâche concernée par ce guide, vérifier :

- les consignes de méthode de la demande ont été reformulées et respectées ;
- chaque admission/rejet important possède une raison inspectable ;
- DOI, doublons, niveau de contenu et provenance ont été contrôlés ;
- les filières connexes n’ont pas été transformées en preuves directes sans justification ;
- les états `accepted/review/rejected` et `Full article/Abstract only` sont rapportés séparément ;
- les écritures importantes ont une sauvegarde et une stratégie de reprise ;
- un changement de comportement possède un test de non-régression représentatif ;
- toute nouvelle règle durable explicitement demandée a été ajoutée à ce fichier.
