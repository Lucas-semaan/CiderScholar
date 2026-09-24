# Comment travailler sur CiderScholar

Statut : guide méthodologique accepté.
Dernière consolidation : 27 août 2026.

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

### 1.1 Veille et densité rédactionnelle — décision utilisateur du 10 septembre 2026

La veille hebdomadaire est activable dans les réglages administrateur, avec des thèmes modifiables
initialisés aux huit thèmes existants. Elle privilégie les nouveautés et complète le fonds ancien,
acquiert le texte intégral accessible légalement puis utilise l'abstract admissible en repli. Une
échéance manquée est rattrapée au prochain lancement. Le budget validé est d'environ une heure,
1 000 notices examinées et 100 acquisitions tentées ; le reste est reprenable. Le bilan est présenté
dans l'application, sans notification Windows. Voir `BIBLIOGRAPHIC_WATCH.md` pour le contrat opérationnel.

Les réponses du chatbot commencent par le contenu scientifique. La mini-introduction et les préfixes
« preuve directe » ou « preuve indirecte » ne sont plus imposés et ne sont pas affichés. Nommer
naturellement la matrice réellement étudiée dans le résultat cité ; préciser les différences de
procédé et les limites de transposition seulement lorsqu'elles sont utiles à l'interprétation. Ne
jamais inventer une matrice inconnue. Le classement interne A–D et les validations scientifiques sont
conservés. Une clarification brève reste possible lorsqu'une ambiguïté réelle l'exige. Supprimer les
reformulations de la question, les annonces de plan et les conclusions répétitives ; conserver les
résultats, conditions, mécanismes, contradictions et limites documentés. Les budgets des efforts ne
sont pas diminués : l'économie porte sur le remplissage, pas sur la richesse scientifique.

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

- Les termes « bibliographie principale », « base documentaire » et « corpus commun » désignent la
  même source d’autorité scientifique : la base SQLite du corpus sous `data/common/database`. Les
  notices bibliographiques, abstracts, textes intégraux, chunks, preuves et états d’acquisition y
  sont consolidés ; une base applicative ou un ancien chemin de migration ne reçoit jamais une
  collection bibliographique scientifique parallèle.
- Les nouveaux PDF, caches d'extraction et index du corpus sont écrits sous `data/common/`
  (`pdf`, `extracted`, `qdrant` et `database`). Les anciens chemins restent lisibles uniquement
  pendant une migration explicite, sauvegardée et additive : elle copie les fichiers vérifiés,
  conserve les originaux et ne remplace jamais un fichier existant.
- Normaliser et vérifier le DOI avant insertion. Comparer le DOI à l’ensemble du corpus actif, pas à
  une seule table ou un seul ancien chemin.
- À DOI normalisé identique, conserver une seule entrée documentaire et privilégier le texte intégral.
- Deux DOI différents ne sont jamais fusionnés sur le seul titre. Sans DOI, SHA-256 et métadonnées
  contrôlées servent de replis prudents ; un titre proche n’est qu’un candidat à revue.
- Deux conteneurs PDF de SHA-256 différents ne sont reconnus comme une même publication qu’après
  confirmation par une empreinte exactement identique du texte intégral normalisé, page par page,
  sur un candidat bibliographique suffisamment spécifique. Le titre ne déclenche jamais seul la
  fusion.
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

Un PDF ajouté explicitement depuis l’interface est ingéré puis indexé automatiquement en ciblant
uniquement les nouveaux articles ou les fragments en échec à reprendre. L’opération n’est présentée
comme réussie que si l’indexation correspondante est complète ; un échec conserve les données
persistées et un chemin de reprise, sans transformer un doublon déjà indexé en nouveau document.

Instruction utilisateur explicite et durable du 13 août 2026 : un document ajouté par les parcours
d’import locaux est adopté définitivement dans le corpus, sans circuit séparé de proposition ni
évaluation préalable de confiance. Les contrôles de format, de déduplication, d’extraction et
d’indexation restent obligatoires ; un fichier en échec ou nécessitant un OCR demeure reprenable et
n’est pas présenté comme ajouté tant que ces contrôles ne sont pas satisfaits.

Un rapport d’import distingue toujours les fichiers effectivement ajoutés, déjà présents, à vérifier
par OCR et en échec. Le nombre de fichiers sélectionnés ou copiés ne doit pas être présenté comme un
nombre de nouveaux articles.

Ne pas contourner un paywall, un CAPTCHA ou une restriction d’accès. Un contenu structuré n’est admis
comme tel que si le fournisseur déclare explicitement son type ; ne pas inférer du XML ou un texte
intégral depuis une URL ambiguë.

Pour des fichiers locaux, tenter d’abord l’extraction native. Déclencher l’OCR seulement si aucun texte
exploitable n’est extrait, puis vérifier le résultat. Les fichiers illisibles ou corrompus sont signalés
et exclus de l’index ; ils ne sont supprimés que sur demande explicite, avec une opération récupérable
quand elle est possible.

Une année future repérée automatiquement dans un fichier local n’est jamais acceptée comme année de
publication sans métadonnée bibliographique validée. Elle est laissée vide ou envoyée en revue afin
d’éviter de confondre un objectif, un numéro de page, un ISSN ou un autre identifiant avec une année.

Instruction utilisateur explicite et durable du 1er septembre 2026 : le titre visible d'un PDF local
est accepté seulement lorsqu'il est identifiable dans les métadonnées natives, le texte extrait ou un
nom de fichier descriptif. Une étiquette générique, un numéro, un DOI tronqué, une diapositive ou un
hash de stockage ne constitue jamais un titre. L'extraction native précède toujours l'OCR, et le texte
OCR n'est utilisé que s'il a été admis par son contrôle de qualité. Si aucune source ne permet
d'identifier le document, utiliser exactement `fichier local`. Les corrections du corpus existant
commencent par un audit de prévisualisation en lecture seule ; elles ne sont appliquées qu'après revue
et sauvegarde SQLite vérifiée.

Cette revue porte sur toute manifestation documentaire, pas seulement sur les articles scientifiques :
livre, chapitre, thèse, rapport, présentation, texte réglementaire, fiche technique, bulletin, brevet ou
autre document. Pour chaque PDF, conserver une décision structurée contenant le titre, les personnes ou
organismes responsables, l'année, le type, la confiance et la preuve exacte (métadonnée ou page). Une
décision automatique n'est applicable qu'avec une preuve explicite et une confiance suffisante ; un
paragraphe d'introduction, un nom de logiciel producteur ou une liste d'auteurs agrégée avec ses variantes
ne remplace jamais une métadonnée existante.

Instruction utilisateur explicite et durable du 2 septembre 2026 : ne jamais créer, résumer ni
reformuler le titre d'un fichier local. Transcrire le titre principal réellement imprimé sur la première
page ou, si elle est distincte, sur la page de titre immédiatement suivante ; réunir uniquement les
lignes qui composent visuellement ce même titre. La grande majorité des documents possèdent ce titre
principal. Le repli exact `fichier local` est réservé au cas où aucun titre n'est réellement lisible après
extraction native puis OCR contrôlé.

Instruction utilisateur explicite et durable du 2 septembre 2026 : un PDF local dont le titre reste
exactement `fichier local` après cette revue est conservé avec son fichier, ses fragments et la raison
d'exclusion, mais sort de l'index lexical et vectoriel ainsi que des nouvelles références et citations.
Une reconstruction ou une réconciliation d'index ne doit pas le réintroduire. Cette règle ne s'applique
pas aux titres seulement ambigus ou encore à auditer : ceux-ci restent dans le corpus tant qu'aucune
décision définitive ne les classe comme non identifiables. Une correction explicite des métadonnées est
requise avant toute réactivation.

Instruction utilisateur explicite et durable du 1er septembre 2026 : lorsqu'une source du corpus local
est citée et que son PDF persisté existe encore, la citation et la carte de source permettent de l'ouvrir
à la demande. Le client ne transmet qu'un identifiant d'article ; l'API résout le chemin dans SQLite,
sert uniquement un PDF existant et ne révèle jamais le chemin local. Une source sans fichier disponible
reste citée sans produire de lien cassé.

### 3.1 Mener les campagnes Aureli authentifiées

Instruction utilisateur explicite et durable du 12 août 2026 : pour les futures campagnes Aureli,
reprendre le processus validé lors de la collecte `cider`.

1. L’utilisateur ouvre lui-même sa session Aureli dans le navigateur ; ne jamais lui demander de
   transmettre son mot de passe dans la conversation.
2. Réutiliser la session uniquement pour la campagne autorisée. Si un jeton doit alimenter le client
   local, le fournir par une variable d’environnement au seul processus de collecte ; ne jamais
   l’afficher, l’écrire dans le dépôt, un point de reprise, une sauvegarde ou un rapport. Le supprimer
   de l’environnement de campagne à la fin ou à l’expiration de la session.
3. Interroger en priorité l’API Primo officielle d’Aureli, séquentiellement et avec temporisation.
   Paginer avec des points de reprise et respecter les limites différentes des sessions invitées et
   identifiées ; une connexion autorisée n’est pas un droit de contourner un autre contrôle d’accès.
4. Faire repasser chaque notice par la déduplication DOI et le classement
   `accepted/review/rejected`. Une recherche plein texte sur `cider` contient des homonymes, des
   occurrences incidentes et des publications cliniques ou marketing qui ne rejoignent pas le RAG.
5. Pour les seules notices admises, utiliser « Obtenir PDF » ou un lien fournisseur direct lorsque
   la session donne légalement accès au document. Vérifier le type PDF réel, calculer SHA-256,
   rapprocher le DOI et ingérer atomiquement comme `Full article`.
6. Si le texte intégral n’est pas directement disponible, conserver l’abstract validé avec DOI au
   niveau `Abstract only`. Ne jamais transformer une page HTML, un extrait tronqué ou une simple
   notice en texte intégral.
7. Le rapport final indique séparément candidats vus, doublons, décisions, PDF tentés, PDF validés,
   `Full article`, `Abstract only`, erreurs et point exact de reprise.

### 3.2 Enrichir les métadonnées du corpus existant

Pour compléter DOI, auteurs, année, type et éditeur sans confondre publications et documents
internes, exécuter d’abord l’enrichisseur sans `--apply`. Il charge automatiquement le dernier audit
`local-non-article-audit-*` disponible ; un audit précis peut être imposé avec `--curation-audit`.

```powershell
.\.venv\Scripts\python.exe -m scripts.enrich_corpus_metadata `
  --run-dir data\exports\metadata-enrichment\<campagne> `
  --fallback-sources
```

Contrôler `accepted-updates.jsonl`, `review-candidates.json` et `report.json`. Les recherches par titre
ne deviennent applicables qu’après accord entre fournisseurs ; les DOI déjà présents sont résolus par
identifiant exact. Les entrées marquées `skip_external_lookup` restent hors recherches et mutations.
Une entrée `validate_and_correct_year` peut remplacer l’année erronée uniquement si une source
bibliographique validée fournit l’année corrigée. Après contrôle, relancer exactement le même dossier
avec `--apply` ; le script crée alors une sauvegarde SQLite cohérente avant la transaction.

Une métadonnée confirmée sur le site officiel d’un éditeur, d’une institution ou d’un dépôt peut être
consignée dans `web-validations.jsonl` dans le dossier de campagne. Chaque ligne conserve au minimum
`record_id`, `provider`, `source_url` HTTPS et `fields`. Ce fichier est relu avec les mêmes bornes DOI et
année que les API ; une page de résultats générique ou un extrait non attribuable ne suffit pas.

Un poster, un diaporama ou un supplément local n’hérite jamais automatiquement du DOI et du type de
l’article parent sur la seule égalité du titre. Conserver le type de la manifestation locale et relier
l’article parent séparément lorsque cette relation est établie.

### 3.3 Découvrir des publications connexes à partir des DOI du corpus

Les articles du corpus pertinents pour le sujet étudié et dotés d’un DOI validé peuvent servir de
points de départ à une collecte bibliographique connexe. Interroger les API bibliographiques
autorisées pour explorer notamment leurs références, les publications qui les citent et les relations
ou recommandations déclarées par les fournisseurs. Une relation bibliographique ou algorithmique
n’établit pas à elle seule la pertinence : chaque publication découverte repasse par le périmètre
éditorial et la décision `accepted`, `review` ou `rejected`.

Une campagne peut enchaîner ces collectes via API et suivre les nouveaux DOI pertinents, à condition
de rester bornée en profondeur, volume et durée. Pour chaque candidat, conserver au minimum le DOI
source, le type de relation, le fournisseur, le DOI candidat normalisé, la date de collecte et la
décision motivée. Normaliser et dédupliquer avant tout téléchargement ou insertion, ne pas réexaminer
silencieusement une décision manuelle, et rendre la campagne reprenable par pagination et points de
contrôle.

Respecter les conditions d’utilisation, quotas et limites de débit de chaque API ; borner les délais,
réessais et erreurs, et séquencer les appels lorsque le fournisseur l’exige. Cette autorisation vise
la collecte par API documentée : elle n’autorise ni contournement de paywall ou de contrôle d’accès,
ni extraction HTML non permise. L’acquisition éventuelle du texte intégral reste soumise aux règles
de provenance, de licence et de niveau de contenu de la section 3.

### 3.4 Étendre durablement tous les thèmes par plusieurs fournisseurs

Instruction durable issue de la campagne multi-sources du 12 août 2026 : pour une expansion large,
utiliser le processus reprenable décrit dans `docs/CORPUS_EXPANSION.md`. Couvrir les huit thèmes avec
les familles focused, expanded, specialized, materials et microbiology ; séquencer les fournisseurs,
faire tourner les familles page par page lorsqu'un budget fournisseur est partagé, conserver un
checkpoint par couple source/famille et laisser la saturation utile, un quota documenté
ou le timeout explicite décider de l’arrêt. Un petit plafond arbitraire ne remplace pas ces critères.

La collecte intermédiaire diffère normalisation globale, reclassement et purge. L’indexation
incrémentale des seuls abstracts acceptés peut tourner en parallèle avec le harvest SQLite via
`scripts.run_incremental_abstract_indexer` : elle borne chaque passe à quelques lots, libère le verrou
Qdrant entre les passes et ne marque `indexed` qu’un contenu dont le hash est toujours celui encodé.
Une notice enrichie pendant l’encodage reste donc `pending` et est reprise au passage suivant. Il ne
faut jamais lancer simultanément deux workers Qdrant ni une reconstruction `--recreate`; la
commande est lancée comme compagnon de l’orchestrateur au début de chaque campagne d’enrichissement,
avec le même timeout et un journal JSONL de campagne. La
consolidation finale reste la vérification exhaustive qui refuse tout harvest actif. Elle vérifie obligatoirement la sauvegarde
pré-campagne, refuse tout harvest encore actif, réapplique le filtre éditorial à tous les hits, place
les abstracts automatiques sans DOI en revue, archive les rejets et vérifie l’égalité SQLite/Qdrant.
Si un processus interrompu a laissé un run `running`, ne le clôturer qu’après vérification explicite
de l’absence de writer et en nommant exactement tous les identifiants concernés ; conserver les hits,
recalculer leurs compteurs et journaliser la raison au lieu de modifier directement la base.
Une limite 403/429, une clé absente, un budget insuffisant et une vraie absence d’ajout sont consignés
séparément et ne bloquent pas les autres fournisseurs.

Instruction utilisateur explicite du 13 août 2026 : lorsqu'un couple `source × famille de tags` ne
produit aucun nouvel abstract pertinent pendant deux rotations thématiques complètes, le marquer
`closed_weekly` pendant sept jours. Cette fermeture opérationnelle est persistée entre campagnes et
évite les requêtes trop fréquentes, mais ne ferme pas les autres familles de la même source. À
l'échéance, autoriser une nouvelle rotation ; tout nouveau gain efface la fermeture. Ne jamais assimiler
à une absence scientifique de gain une clé manquante, un quota, un 403/429 ou une erreur fournisseur.

Instruction durable complémentaire du 13 août 2026 : une campagne « toutes sources » énumère et
essaie les API de découverte configurées Crossref, Europe PMC, OpenAlex, Clarivate, Elsevier/Scopus,
HAL, CORE, DOAJ, Semantic Scholar, ISTEX, DataCite, OpenAIRE, Zenodo, PubMed via les E-utilities
officielles du NCBI et USDA PubAg via son endpoint Primo public. Tester d'abord le mode public
documenté lorsqu'il existe ;
une limite publique devient un état différé et non une fausse saturation. Unpaywall intervient au
stade de résolution DOI/full-text, car il ne fournit pas de recherche thématique. Les clés API
durables autorisées sont conservées uniquement dans le coffre DPAPI administrateur et hydratées dans
le processus de campagne ; les jetons de session courts, cookies et valeurs d'authentification ne
sont jamais consignés dans SQLite, YAML, checkpoints, rapports ou journaux.
Respecter les limites documentées par endpoint : en particulier, une recherche Zenodo anonyme est
bornée à 25 notices et à 30 requêtes par minute. Paginer sans trou à partir de la page logique du
checkpoint et espacer les appels d'au moins 2,1 secondes. Un HTTP 400 systématique est un défaut de
requête à diagnostiquer, jamais une preuve d'absence de publications.

Pour ces campagnes, ajouter en parallèle une exploration OpenCitations des citations entrantes et
références sortantes de DOI acceptés, équilibrée entre les huit thèmes. Cette phase reste en lecture
seule, bornée en graines, arêtes, candidats et temps, et conserve DOI graine, relation, DOI candidat,
fournisseur et date. Le titre OpenCitations ne sert qu’au préfiltrage : chaque candidat repasse ensuite
par une résolution DOI exacte et le filtre éditorial avant insertion.

Lorsqu'une clé d'API bibliographique devient disponible après le démarrage du writer SQLite, ne pas
lancer un second writer et ne pas interrompre brutalement le premier. Conserver la clé dans le coffre
DPAPI administrateur puis, si le fournisseur le permet, collecter dans un staging reprenable en lecture
seule avec la même échéance. Importer ce staging seulement après l'arrêt vérifié du writer, avec la
résolution d'identité, la déduplication DOI et le filtre éditorial ordinaires.

Instruction utilisateur explicite du 13 août 2026 : les opérateurs des moteurs sans API ont autorisé
une collecte HTML pour cette campagne et les campagnes futures menées selon ce processus. Conserver
la preuve d'autorisation sous forme d'un identifiant non secret dans le checkpoint. Utiliser ces
moteurs seulement pour découvrir URL, titre, extrait et DOI éventuel ; aucune affirmation ni aucun
abstract du corpus ne repose directement sur un snippet. Avant insertion, confirmer le candidat par
un DOI exact auprès d'une API bibliographique ou par une page institutionnelle attribuable, puis
réappliquer le filtre éditorial. Temporiser et rendre la pagination reprenable. Un 403, 429, CAPTCHA,
défi anti-bot ou résultat scientifiquement inutilisable est consigné comme limite ; ne pas le
contourner, changer d'identité réseau ou déguiser le client.

### 3.5 Importer des exports texte Scopus localement

Instruction utilisateur explicite et durable du 28 août 2026 : les exports Scopus fournis localement
sont traités en priorité hors réseau. Le champ Scopus `Source title` désigne la revue, l'ouvrage ou les
actes ; il ne doit jamais être confondu avec l'organisation `publisher`. En l'absence d'un champ
éditeur explicite, ne pas inférer cette organisation depuis le DOI, le copyright de l'abstract ou le
titre de source sans validation bibliographique attribuable.

Le format texte concatène `Source title`, volume, numéro, pages et citations sur une ligne. Le parseur
retire les suffixes bibliographiques depuis la droite et conserve toutes les virgules internes du
titre de source ; il ne coupe jamais à la première virgule. Chaque valeur observée est persistée avec
son EID Scopus dans `bibliographic_record_sources.source_title` pour le corpus actif ou dans
`rejected_bibliographic_record_sources.source_title` pour l'archive, même lorsque plusieurs EID d'une
même identité portent des variantes. Le champ scalaire `bibliographic_records.journal` n'est corrigé
automatiquement que s'il est vide ou correspond exactement à l'ancien préfixe tronqué ; une valeur
canonique différente provenant d'une autre source est conservée.

Toute correction commence par un audit local sans mutation, refuse un harvest actif, crée une
sauvegarde SQLite vérifiée avant la migration ou la mise à jour, puis contrôle SQLite, les clés
étrangères et l'égalité FTS/base. Le rapport distingue notices brutes, EID uniques, sources actives,
EID archivés ou absents, titres par source persistés, journaux scalaires corrigés, variantes et
conflits. Les pourcentages par `Source title` utilisent un dénominateur explicitement nommé ; ils ne
sont jamais présentés comme une distribution d'éditeurs.

Instruction utilisateur explicite et durable du 28 août 2026 : la disponibilité d'un élément
bibliographique est indépendante de son statut éditorial et de son état vectoriel. Une identité ayant
un texte intégral persisté est classée `Full article`; sinon, toute identité ayant un abstract non vide
est classée `Abstract only`, avec ses métadonnées, y compris sans DOI vérifié. Le statut
`accepted/review/rejected` reste une dimension distincte et ne doit jamais faire disparaître un
abstract de la base documentaire. Une référence qui ne possède ni texte intégral ni abstract n'est
pas faussement appelée `Abstract only` : elle demeure une notice d'acquisition séparée jusqu'à
l'obtention d'un contenu scientifique.

### 3.6 Séparer le wiki de raisonnement et les sources RAG

Instruction utilisateur explicite et durable du 10 septembre 2026 : les sources denses, fréquemment
consultées et qui décrivent la manière de raisonner sur le domaine sont distillées dans un wiki local
versionné. Ce wiki contient notamment les cadres de décision, les distinctions, les compromis, les cas
frontières et les interprétations stratégiques. Le chatbot charge son cœur pour toute question
scientifique cidricole acceptée, puis un petit nombre de pages thématiques déterminées par la question.
Le contexte est borné et son empreinte participe aux clés de cache concernées.

Le wiki oriente l'interprétation, la formulation des hypothèses de recherche et l'organisation de la
réponse, mais ne constitue jamais une preuve scientifique. Chaque affirmation affichée reste fondée sur
les passages persistés et validés du corpus SQLite. Une contradiction documentaire ne doit pas être
masquée par le wiki : elle reste pertinente pour le retrieval et déclenche une revue de la page concernée.

Les sources rares ou utiles seulement dans certaines situations restent dans le RAG : spécifications
détaillées, valeurs et seuils, protocoles complets, documents réglementaires, décisions historiques,
résultats particuliers et connaissances externes de niche. Leur absence du wiki n'est jamais interprétée
comme une absence du corpus. Pour les fiches AsCoCid, conserver l'empreinte du document, un repère local
dans l'original et les réserves documentaires ; ne pas présenter une distillation éditoriale comme une
validation experte sans événement de revue lié à la version exacte.

Instruction utilisateur explicite et durable du 22 septembre 2026 : les documents textuels déposés
dans le dossier `wiki/` sont aussi des sources Ascocid interrogeables. Ils sont extraits localement,
persistés dans le corpus SQLite et reliés à leur chemin et empreinte d'origine ; un fichier déjà présent
dans le corpus reçoit un alias Ascocid sans duplication du contenu. En mode `concise`, lorsque les
documents Ascocid classés A/B couvrent l'ensemble des besoins de vérification de la question, la réponse
utilise exclusivement ces documents. Si cette couverture est incomplète, ou en mode `balanced`/`deep`,
le RAG scientifique complète les documents Ascocid. Chaque citation issue de cette collection est rendue
exactement sous la forme `Ascocid — <nom du fichier>` ; les pages éventuelles peuvent être ajoutées sans
remplacer cette mention. Le cœur Markdown distillé reste un cadre organisationnel non citable tant
qu'une affirmation n'est pas reliée à un document Ascocid persisté.

## 4. Rechercher et classer les preuves pour une question

### 4.1 Comprendre l’intention avant le retrieval

Instruction utilisateur explicite et durable du 10 septembre 2026 : toute question conversationnelle
manifestement hors du périmètre scientifique et technique cidricole est refusée localement avant toute
ouverture SQLite/Qdrant, recherche lexicale ou vectorielle, réutilisation de sources, acquisition ou
enrichissement externe. Le garde de périmètre accepte les formulations cidricoles directes, les matières,
produits dérivés et procédés réellement transférables ; il conserve les relances courtes qui se réfèrent à
une question cidricole précédente. Il ne transforme jamais une absence de résultats RAG en rejet de
périmètre. Le refus explique brièvement le domaine couvert et possède un test qui prouve l'absence d'appel
au retrieval.

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
Pour tout procédé cidricole contrôlé ainsi désambiguïsé, injecter dans la première vague au moins une
requête matrice-procédé avec ses synonymes scientifiques français et anglais ; un plan généré ne peut
pas remplacer entièrement ce socle par des procédés voisins.

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

### 4.3 Niveaux de pertinence A à D

Attribuer les niveaux relativement à chaque question ou axe :

- `A — exact` : matrice, procédé et résultat correspondent à la question ;
- `B — transposable` : mécanisme ou méthode applicable, avec différence explicitement bornée ;
- `C — peripheral` : contexte utile mais ne répond pas à l’effet demandé ;
- `D — irrelevant` : hors sujet ou homonyme.

Les niveaux A et B peuvent alimenter la synthèse. C et D peuvent aider au diagnostic du retrieval mais
ne répondent pas à la question. Sans niveau A, la réponse nomme la matrice et les bornes de
transposition du niveau B, ou s’abstient ; elle ne comble pas la lacune avec une analogie.

Le texte intégral ne prime sur un abstract que s’il est au moins aussi pertinent. Un abstract A peut
donc précéder un texte intégral B ou C. À pertinence comparable, le texte intégral paginé reste
préférable.

Une question réellement multi-axes peut être décomposée en un à quatre axes. Chaque axe conserve sa
propre requête et un quota équilibré de preuves. Les brouillons d’axes restent reliés aux preuves
originales mais ne sont jamais eux-mêmes une preuve.

Lorsqu’un fragment classé A ou B est retenu dans un texte intégral, analyser un contexte intra-article
borné avant la synthèse : voisins du fragment, résultats, méthodes ou conditions et discussion ou
limites complémentaires. Cette expansion reste plafonnée en nombre de chunks et en distance ; elle
ne charge pas automatiquement tout le corpus ni un article sans lien avec la question. Les passages
complémentaires conservent leur rôle (`anchor`, résultat, méthode/conditions, discussion/limite ou
contexte) et leurs pages persistées. Leur provenance commune n’augmente jamais à elle seule leur
pertinence scientifique.

## 5. Produire une réponse scientifique

Appliquer `docs/CHATBOT_RESPONSE_CONTRACT.md`. Pour une synthèse longue, appliquer aussi
`docs/OVERNIGHT_LONG_SYNTHESIS_PROTOCOL_2026-08-05.md`.

Les règles consolidées les plus importantes sont :

- produire exclusivement dans la langue de la question chaque élément rédactionnel visible :
  définition, affirmation, libellé de mécanisme, limitation, message d’abstention, brouillon d’axe
  et assemblage final. Si une preuve est rédigée dans une autre langue, ARGO traduit son contenu
  scientifique au lieu de recopier sa formulation. Les extraits de preuve verbatim, titres,
  auteurs, taxons, symboles et autres métadonnées bibliographiques restent dans leur forme originale ;
  ils ne justifient jamais un mélange de langues dans la prose générée ;
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

Instruction utilisateur explicite et durable du 21 septembre 2026 : les appels ARGO sont soumis aux
fenêtres glissantes personnelles suivantes : **20 requêtes par minute**, **120 requêtes par heure** et
**200 requêtes par période de 180 minutes**. Toute requête, y compris la découverte de modèles, une
reprise et un appel qui échoue, réserve atomiquement son créneau persistant avant l'envoi ; si un plafond
est atteint, ne pas émettre d'appel de sondage ni de nouvelle tentative avant l'instant de reprise
calculé. Les boucles d'évaluation et de correction s'arrêtent alors proprement avec un diagnostic
reprenable, sans jamais tenter de contourner ces plafonds.

Instruction utilisateur explicite et durable du 31 août 2026 : la réponse conversationnelle d'Argo
est une synthèse du sujet construite à partir des fragments pertinents, pas une succession de résumés
de fragments. Elle commence par une mini-introduction utile qui situe la matrice, le procédé, les
distinctions nécessaires et l'angle de la synthèse, sans généralité encyclopédique non étayée. En
l'absence de demande de forme explicite, Argo choisit la typologie qui sert le mieux la question et les
preuves : prose continue, sections thématiques, comparaison, déroulé de processus ou liste. Une forme
explicitement demandée par l'utilisateur reste prioritaire.

Instruction utilisateur explicite et durable du 1er septembre 2026 : la fenêtre de génération
conversationnelle ne retranche pas arbitrairement les preuves pertinentes retenues par le RAG. Tous
les éléments A/B présentés à la génération contribuent à au moins une affirmation citée ; plusieurs fragments
convergents ou complémentaires peuvent soutenir une même idée synthétique. Une réponse qui en omet un,
ou qui réduit un ensemble riche à quelques phrases anormalement courtes, est validée dans une enveloppe
globale d’une requête de génération au plus, réponse initiale comprise. Tout rejet résiduel est traduit en
diagnostic scientifique structuré plutôt qu'en erreur interne, sans repasse ARGO. Si
le contexte doit être ajusté à la borne d'entrée du fournisseur, raccourcir le
texte de chaque passage sans supprimer son identité ni sa provenance ; la sélection RAG en amont reste
le lieu où sont écartés les doublons ou éléments non pertinents. Cette exigence n'autorise ni citation
décorative, ni répétition, ni utilisation d'un élément C/D.

Le filtre sémantique global ne peut pas vider une vague locale classée sur la foi d'une seule sortie
Argo. Un verdict où tous les candidats deviennent C/D reçoit jusqu'à deux repasses explicites
(`semantic_filter_empty`) rappelant notamment la portée des preuves B sans imposer de promotion
artificielle. Si les trois évaluations rejettent encore toute la vague, conserver les candidats SQLite
comme non évalués par ce filtre et leurs niveaux locaux déterministes, afficher un avertissement de
dégradation et laisser la génération ainsi que les validateurs affirmation-par-affirmation statuer. Ce
repli n'autorise jamais une preuve externe, une citation inventée ou le contournement des contrôles
numériques, causaux, normatifs et de sécurité.

Après épuisement de l’unique requête, distinguer les blocages scientifiques des avertissements de qualité.
Une référence de preuve inexistante, une preuve C/D utilisée comme appui, une affirmation numérique,
causale, normative, évaluative ou de sécurité non étayée, une fuite d'identifiant ou du processus interne,
un schéma inutilisable et une question altérée restent bloquants. Une introduction trop courte, une
synthèse trop brève, une typologie imparfaite, une facette ou une preuve A/B omise et un écart de langue ou
de structure, ainsi que l'absence de libellé explicite sur la portée indirecte d'une preuve B, peuvent
devenir des avertissements seulement si toutes les affirmations affichées restent scientifiquement sûres.
Une abstention générée alors que le filtre global a présenté des preuves A/B est elle-même traitée comme
une omission signalée par le diagnostic. Si l’unique requête reste une abstention, la plus précise peut
être rendue sans citation plutôt qu'une erreur interne. Dès qu'une
version answerable sûre existe, le meilleur candidat privilégie d'abord le nombre de preuves A/B réellement
citées, puis la qualité formelle et la longueur étayée. Cette version est rendue avec
`partial_generated`, les codes d'avertissement et une limitation lisible ; aucune affirmation bloquée
n'est exposée.

Chaque tentative answerable est aussi évaluée affirmation par affirmation dès sa réception. Si un bloc
numérique, causal, interne ou autrement bloquant est rejeté, les autres paragraphes indépendamment
validés et leurs citations alimentent un pool cumulatif sûr ; les variantes de correction portant sur les
mêmes preuves sont ramenées à la formulation étayée la plus développée. L'assemblage cumulatif privilégie
la couverture des preuves et des axes encore absents, puis repasse intégralement dans les validateurs avant
d'être admissible. Une définition de repli contextualisée et strictement fondée sur la question remplace
les champs globaux invalides. Une tentative ultérieure au schéma inutilisable ou une abstention ne peut
donc plus effacer des affirmations scientifiques valides déjà obtenues, ni forcer le choix d'un seul essai
quand plusieurs repasses apportent des paragraphes complémentaires.

Chaque affirmation rendue est un paragraphe scientifique développé lorsque ses passages le permettent :
elle expose le constat, le replace dans la matrice et les conditions étudiées, rapproche les sources
convergentes ou complémentaires, puis précise sa portée ou sa limite documentée. La densité minimale de
chaque paragraphe et la longueur totale sont proportionnées au nombre et à la richesse textuelle des
preuves citées. Avec un corpus riche, une formulation télégraphique déclenche une correction ; avec une
preuve pauvre, le système reste bref plutôt que d'extrapoler. Les citations doivent être plus nombreuses
quand plusieurs sources soutiennent effectivement le même développement, jamais ajoutées pour décorer.

Le contrôle public d’effort (`concise`, `balanced`, `deep`) module conjointement la largeur bornée
du retrieval, la fenêtre de preuves et la taille maximale de la synthèse. Il ne désactive jamais le
filtre sémantique, la validation des citations et des nombres, les niveaux A–D ni l’abstention. Le mode
approfondi développe seulement les axes réellement documentés ; le mode concis conserve les nuances
nécessaires à la fidélité scientifique.

Instruction utilisateur explicite et durable du 24 août 2026 : une réponse approfondie à une question
comparative ou réellement multi-dimensionnelle est pilotée par la couverture scientifique, pas par un
simple plafond de tokens. Le plan comporte trois à quatre axes utiles au plus lorsqu'ils exigent des
preuves distinctes ; chaque axe final affiche au moins une affirmation validée ou une lacune précise,
avec un état documenté, partiel ou non documenté. Développer les mécanismes, conditions,
contradictions, compromis et limites seulement lorsqu'ils sont étayés. Si au moins six affirmations
distinctes sont validables mais que l'assemblage approfondi reste anormalement court, autoriser une
seule relance d'expansion strictement fondée sur les mêmes preuves. Toute récupération qui retire des
affirmations propage le statut public `partial_generated`.

Instruction utilisateur explicite et durable du 24 août 2026 : les dimensions explicitement demandées
et les facettes déterministes indispensables à leur interprétation priment sur les axes périphériques
proposés par le planificateur, même lorsque le nombre maximal d'axes est déjà atteint. Cette priorité
s'applique à toute question et à tout domaine : aucune liste d'agents, de procédés ou de substances
n'est codée comme exception de génération. Un élément précis, comme la bentonite dans le collage des
jus, sert uniquement de cas de non-régression pour cette règle générale. Si le corpus contient des
résultats de niveau A sur une dimension prioritaire, leur absence de la synthèse est un défaut de
planification, de retrieval ou d'assemblage, pas une lacune documentaire. Lorsque le nombre minimal
d'affirmations distinctes défini par l'effort est déjà validé dans les axes, toute récupération qui
fait repasser l'assemblage sous ce seuil déclenche une unique relance fondée sur les mêmes preuves ou
un repli sur les affirmations d'axes déjà validées.

Instruction utilisateur explicite et durable du 24 août 2026 : une synthèse ne doit pas devenir
artificiellement courte parce que la fenêtre de génération, l'assemblage ou le rendu n'exploite qu'une
fraction des preuves A/B déjà retenues. Le nombre de résultats RAG n'est pas un quota de citations :
une source redondante ne doit pas être ajoutée pour faire nombre. En revanche, chaque résultat qui
apporte un mécanisme, une condition, un effet, une contradiction ou une limite distincte doit pouvoir
atteindre un brouillon d'axe, puis être conservé dans l'assemblage s'il est validé. Le contrôle de
profondeur combine nombre d'affirmations, axes documentés et densité rédactionnelle soutenue par les
brouillons ; il ne s'arrête pas au premier petit seuil d'affirmations. L'appartenance d'une
affirmation à un axe est explicite et validée : elle n'est jamais déduite du seul partage d'un passage
avec un autre axe. Un ouvrage généraliste riche, tel qu'un traité de procédés vinicoles utilisé par
analogie contrôlée, reste un cas de non-régression et ne devient pas une exception codée.

Pour un grand index vectoriel local, séparer la largeur lexicale de la largeur dense. Toutes les
variantes scientifiques utiles peuvent rester interrogées par FTS, tandis qu'un nombre réduit et
représentatif de variantes déclenche la recherche dense coûteuse ; les recherches propres aux axes
réutilisent le pool vectoriel global et complètent par le lexical. Cette optimisation doit être
testée sur le nombre d'appels denses et sur la conservation de la couverture par axe.

Sur un index Qdrant local volumineux, une opération native atomique peut retarder les threads Python
de heartbeat pendant plusieurs minutes, en particulier lorsque Windows pagine. Le lease par défaut
du worker couvre trente minutes et reste renouvelé toutes les trente secondes dès que l'ordonnanceur
Python reprend la main. Cette marge évite qu'une lecture de l'API remette en file et duplique une
recherche encore active ; elle ne constitue ni un délai global de réponse ni une autorisation de
laisser expirer silencieusement un job.

Instruction utilisateur explicite et durable du 25 août 2026 : le mode `deep` doit pouvoir conserver
plus de vingt candidats lorsque les preuves distinctes le justifient. Son pool borné retient jusqu'à
32 résultats d'abstract, 36 enregistrements de preuve et 40 éléments dans le contexte scientifique ;
le filtre sémantique et l'évaluation de couverture s'appliquent à tout le pool, par lots, sans coupe
silencieuse à vingt. La première vague `balanced` ou `deep` utilise une requête exacte par axe, des
candidats bornés et un coût dense réduit. Elle n'est définitive que si chaque axe est couvert par des
preuves admissibles ; sinon une vague d'élargissement rétablit les variantes, le préfixage et les
budgets configurés. L'optimisation ne constitue donc jamais un motif pour déclarer une lacune.

Les variantes lexicales d'une même vague partagent une session SQLite en lecture seule, et les
encodages ainsi que les requêtes Qdrant compatibles sont groupés. Un résultat de retrieval peut être
réutilisé uniquement par un cache local adressé par le contenu : empreinte du corpus SQLite,
configuration complète du retrieval, modèles et manifestes, filtres et limites. Toute différence ou
entrée corrompue produit un cache miss sûr ; SQLite reste l'autorité pour le texte et les preuves.
Tracer séparément hits et misses du cache, candidats lexicaux/denses et temps des vagues avant de
conclure à un gain de performance.

Une exécution partielle conserve le contrat rédactionnel normal. Les affirmations déjà validées et
citées peuvent former une réponse `partial_generated`, avec une limite localisée sur l’axe manquant.
Si aucune affirmation n’est validée, produire une abstention ou un diagnostic structuré dans la même
forme que la réponse attendue. Ne jamais remplacer la synthèse par une succession d’extraits ou de
sources brutes.

Instruction utilisateur explicite et durable du 26 août 2026 : la recherche conversationnelle ne
possède pas de délai global fixe ni de réserve de temps qui permettrait d’omettre le contrôle
sémantique ou la synthèse. Les appels distants restent individuellement temporisés et les pools de
preuves sont bornés par l’effort choisi ; l’amélioration de la durée repose sur le cache, le
groupement des requêtes et la réduction du travail redondant, jamais sur la suppression silencieuse
d’une étape de validation.

Pour le mode `balanced`, quinze minutes constituent l’objectif de performance de référence, pas une
limite d’exécution. Mesurer cet objectif sur la durée complète du job et optimiser d’abord les pools
intermédiaires, les ouvertures d’index, les encodages et les recherches répétées. La sélection finale
reste fixée par les exigences scientifiques du mode et une couverture insuffisante déclenche toujours
la vague complémentaire prévue, même si l’objectif de quinze minutes est dépassé.

Décision utilisateur validée le 26 août 2026 : chaque vague locale partage une seule ouverture
Qdrant paresseuse entre les collections d'abstracts et de texte intégral, y compris lorsqu'une
acquisition incrémentale écrit de nouveaux chunks pendant la vague ; le propriétaire est fermé à la
fin de la vague. Lorsque l'évaluation de couverture est fiable et que des preuves filtrées existent,
la vague complémentaire relance uniquement les axes `partial`, `missing` ou `indeterminate` ; les
axes `covered` ne sont pas recherchés de nouveau s'ils possèdent aussi le nombre minimal de candidats
sémantiques A/B exigé par l'effort. Un contrôle en repli ou une sélection vide conserve le comportement
prudent qui élargit tous les axes. Les plafonds finaux et les validations restent inchangés.

### 5.1 Pipeline conversationnel sans axes ni contrôle de couverture

Instruction utilisateur explicite et durable du 27 août 2026 : les axes de travail, les quotas par
axe, l'évaluation séparée de couverture et la vague complémentaire automatique sont retirés du chemin
de production du chatbot. Cette décision remplace, pour ce chemin, les règles antérieures qui
imposaient des brouillons par axe ou une seconde recherche lorsque leur couverture était jugée
insuffisante. Les anciennes structures peuvent rester temporairement lisibles pour les migrations et
les audits, mais elles ne pilotent plus la recherche ni la génération courantes.

Le chemin de production suit désormais ce contrat vérifiable :

1. Argo produit d'abord une **réponse hypothétique prudente**, jamais affichée et jamais considérée
   comme une preuve, ainsi que des propositions atomiques à vérifier. Cette hypothèse simule la
   structure et le vocabulaire d'une réponse experte afin d'orienter le retrieval ; elle ne contient
   ni citation, ni DOI, ni page, ni valeur numérique absente de la question. Elle distingue
   observation attendue, mécanisme encore hypothétique, recommandation éventuelle et limites de
   transposition.
2. Le nombre de vérifications s'adapte à l'effort sans devenir un quota de remplissage : `concise`
   autorise au plus trois besoins et une hypothèse de 80 mots ; `balanced`, cinq besoins et 140 mots ;
   `deep`, huit besoins et 250 mots. Une question simple peut conserver un seul besoin. Toutes les
   dimensions explicitement demandées restent obligatoires même en mode concis. La brièveté réduit le
   nombre d'affirmations finales, jamais le niveau de validation.
3. Une **seule vague groupée** interroge localement l'original et l'hypothèse pour le dense, puis
   l'original, les requêtes courtes des vérifications et une contradiction par besoin pour le lexical.
   Les variantes partagent la
   session SQLite, l'encodage est groupé, les collections réutilisent une seule ouverture Qdrant, puis
   l'union dédupliquée subit une fusion et un reranking globaux. Aucun besoin ne devient une unité de
   recherche successive. Pour réduire le coût des variantes denses sans réduire la vague, la première
   requête dense reste globale ; lorsque FTS5 a trouvé un nombre minimal d'articles distincts, les
   variantes denses suivantes sont filtrées sur ce pool borné d'articles. Un pool lexical trop pauvre,
   un filtre explicite déjà fourni ou une seule requête dense désactive cette optimisation : toutes les
   requêtes restent globales. Le filtre est tracé, borne des candidats et ne constitue jamais une
   décision de pertinence ni une raison d'exclure une preuve A/B.

   Lorsqu'une entité scientifique nommée possède des synonymes, traductions ou sigles non ambigus
   dans son contexte, la vague lexicale conserve aussi une requête contrôlée avec son libellé
   canonique. Une planification générée ne peut donc pas reconnaître l'entité puis perdre sa forme
   canonique avant le retrieval. Ces candidats ne sont pas des preuves automatiques : ils restent
   soumis au classement et au filtre scientifique A–D. Cas de non-régression : dans un contexte
   boissons/défaut de bouchon, `TCA`, `trichloroanisole` et `goût de bouchon` conduisent à la forme
   canonique `2,4,6-trichloroanisole`.
4. Le contexte intra-article utilise un index logique `article -> section -> chunk` construit sur les
   colonnes SQLite persistées. Il part des chunks d'ancrage, lit leurs voisins bornés, puis seulement
   quelques chunks des mêmes sections ou de `Results`, `Discussion`, `Conclusion`, `Abstract` et,
   lorsque la question le demande, `Materials and Methods`. Les fenêtres candidates par article sont
   respectivement bornées à 12, 16 et 20 chunks pour `concise`, `balanced` et `deep`; les passages
   finaux restent bornés à 3, 4 et 6. Le chatbot ne lit donc pas automatiquement un article complet.
5. Les passages et abstracts originaux relus depuis SQLite constituent la seule autorité scientifique.
   Qdrant ne fournit que des identifiants et scores. Une notice externe découverte ne devient une
   preuve qu'après ingestion, validation et persistance dans SQLite. La réponse hypothétique ne peut
   jamais atteindre le prompt de synthèse finale. Une analyse visuelle générée peut être persistée
   pour revue, mais elle n'alimente pas cette synthèse tant qu'un contrat distinct ne l'a pas promue
   comme preuve validée.
6. Argo qualifie globalement les candidats A à D par rapport à la question complète et aux besoins de
   vérification. Une contradiction qui étudie directement la question reste A ; elle n'est pas
   rejetée parce qu'elle contredit l'hypothèse. Ce filtre n'évalue aucune couverture et ne déclenche
   aucune nouvelle recherche. Ce contrôle est obligatoire, travaille par lots séquentiels de dix
   candidats au maximum et respecte aussi la taille du message fournisseur. Un verdict C ou D valide
   reste un rejet scientifique valide. Chaque lot terminé est mis en cache par question, preuves,
   modèle et version du validateur ; une reprise ne rejoue que les lots manquants. Une erreur
   d'authentification, de quota, de délai, de contexte ou de JSON produit un état technique reprenable
   et aucune synthèse ne contourne le filtre.
7. Avant la génération, les passages strictement redondants sont regroupés au sein d'un même article.
   Chaque résultat, condition, contradiction et limite distincts est conservé ; une similarité seule
   ne peut jamais supprimer une contradiction. Les exclusions pour redondance sont tracées et chaque
   preuve finalement transmise doit être exploitable et citée. Les extraits restent verbatim, centrés
   sur l'information utile, avec leur contexte et leur provenance.
8. Argo produit enfin une synthèse unique à partir des seuls passages SQLite retenus. Chaque
   affirmation cite un identifiant de preuve autorisé ; valeurs numériques, causalité, pages,
   métadonnées, langue et pertinence A/B repassent par les validateurs applicatifs. Une lacune donne
   une abstention localisée ou `partial_generated`, jamais une recherche implicite ni une affirmation
   complétée par la mémoire du modèle. Les affirmations atomiques des résultats, mécanismes,
   définitions et limitations sont comparées uniquement à leurs passages cités sur l'implication, la
   négation, la population ou matrice, les conditions, la temporalité et les unités. Les éléments non
   étayés sont retirés ou corrigés, puis la réponse assemblée est revalidée.
9. Une relance conversationnelle recharge abstracts, passages et métadonnées depuis SQLite, applique
   le budget de l'effort demandé et repasse par le filtre obligatoire. Une décision n'est réutilisée
   que si la question, les preuves, le modèle et les versions du pipeline sont identiques.

Le retriever et le reranker peuvent être spécialisés progressivement hors ligne. CiderQA
`development` fournit les positifs traçables ; les décisions A–D validées fournissent positifs,
cas transposables et négatifs difficiles, dont le texte est toujours réhydraté depuis SQLite. Les
labels `validation` et `final_test` ne servent jamais à l'entraînement. Chaque entraînement continue
depuis un modèle local signé vers un nouveau répertoire candidat, sans écraser ni activer le modèle
courant. La promotion exige les gates CiderQA de retrieval, exactitude, citations, nombres et
abstention. Un fine-tuning ultérieur du générateur peut améliorer style, abstention et respect du
schéma de citations, mais ne sert jamais à mémoriser les connaissances scientifiques du corpus.

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
- mesurer séparément attente du verrou local, recherche abstracts, recherche texte intégral,
  enrichment, filtre sémantique global et génération, sans journaliser le contenu scientifique ;
- modifier une seule famille de paramètres par cycle ;
- rejouer le cas révélateur et au moins un contrôle indépendant ;
- généraliser la correction, sans introduire dans le prompt la réponse particulière du benchmark ;
- augmenter le budget de tokens seulement si des dimensions déjà présentes dans les preuves sont omises ou si
  une troncature est observée ;
- rejeter une réponse plus longue qui n’améliore pas la complétude utile, la précision des citations ou la
  densité scientifique ;
- ne promouvoir aucun candidat qui dégrade la traçabilité, l’abstention ou la fidélité aux preuves.

Les échecs de planification structurée distinguent désormais `invalid_json` et
`schema_validation`. Le diagnostic persistant peut contenir le chemin et le type de validation
Pydantic, mais jamais la question, la sortie générée, une valeur fautive ou le texte d'une preuve.
Les budgets de caractères des filtres sémantiques s'appliquent au payload assemblé, séparateurs
compris ; un extrait multi-passages ne doit jamais dépasser silencieusement la borne du modèle.

### 6.1 Complément d'audit du 11 septembre 2026

Instruction utilisateur explicite de la mission d'audit RAG : l'exactitude, la couverture, la
pertinence et la traçabilité scientifiques priment sur le coût et la latence. Ne pas réduire fortement
le rappel pour accélérer la recherche. Distinguer défaut observé, conséquence inférée et hypothèse à
tester ; reconstruire le chemin réellement appelé avant de modifier l'architecture. Une technologie
ou une deuxième recherche proposée par l'audit reste expérimentale tant que son bénéfice n'est pas
mesuré, sans réactivation implicite des axes conversationnels retirés.

Corrections techniques appliquées dans le workspace, et non réglages scientifiques validés par un
benchmark expert : les sélecteurs de passages utilisent une redondance textuelle exacte plutôt qu'une
similarité de vocabulaire pour ne pas écarter négations, valeurs et conditions distinctes. La présence
d'un DOI dans les articles du corpus ne suffit pas à supprimer un abstract retrouvé : comparer les
preuves effectivement disponibles. Le plan de secours conserve la question originale jusqu'à la
borne autorisée de 4 000 caractères ; les champs générés par le planificateur restent plus courts.
Toute évolution de sélection invalide le cache retrieval, dont la signature inclut aussi les réglages
de classement des articles et de sélection des passages. Instruction utilisateur explicite du 22
septembre 2026 : un repli lexical causé par une marge mémoire insuffisante n'est pas un résultat
hybride complet et ne doit jamais être réutilisé depuis ce cache ; une demande ultérieure réessaie
la recherche vectorielle lorsque les ressources sont redevenues disponibles.

Le rapport `RAG_SCIENTIFIC_AUDIT_2026-09-11.md` distingue les contre-exemples synthétiques reproductibles
des futures mesures CiderQA sur preuves expertes. Un test synthétique réussi ne démontre ni une
amélioration globale des réponses ni un gain de latence sur le corpus installé.

## 7. Traçabilité des décisions consolidées

Les règles ci-dessus proviennent notamment des conversations suivantes :

- **Clarifier la fusion des notices PDF** : base documentaire unique, DOI vérifié, `Full article` ou
  `Abstract only` ;
- **Unifier bibliographie et corpus** : « bibliographie principale », « base documentaire » et
  « corpus commun » sont un seul concept et une seule source d’autorité scientifique ;
- **Planifier collecte bibliographique** : collecte de plusieurs milliers de notices, pertinence avant
  petit plafond arbitraire, acquisition full text puis repli abstract ;
- **Analyser le workflow de collecte** : textes intégraux natifs autres que PDF, reprise, hash et
  généralisation aux fournisseurs ;
- **Vérifier et indexer les articles** : DOI d’abord, admission manuelle motivée, sources légales et
  distinction notice/PDF ;
- **Étendre la bibliographie depuis le corpus** : DOI validés comme points de départ, exploration des
  relations via API et collecte connexe bornée, traçable et reprenable ;
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

Instruction utilisateur explicite et durable du 24 septembre 2026 : l'édition interne cible une VM
Linux unique de 32 Go de RAM, CPU sans GPU et environ 200 Go de stockage. Toute l'application, les
PDF, SQLite, Qdrant et E5 restent sur cette VM ; E5 s'exécute localement sur CPU. La seule IA de cette
édition est l'API INRAE ARGO avec `chat-gpt-oss-120b` : aucun autre LLM local, payant ou de repli ne
doit être activé. La fiabilité mémoire prime sur le débit ; modèles, lots, contextes, caches et
concurrence restent bornés, mesurés et libérés explicitement. Une future édition producteurs utilisera
un sous-corpus et un fournisseur encore indécis ; elle n'est pas à construire avant décision, mais les
contrats scientifiques ne doivent pas dépendre directement d'ARGO.

Avant de conclure une tâche concernée par ce guide, vérifier :

- les consignes de méthode de la demande ont été reformulées et respectées ;
- chaque admission/rejet important possède une raison inspectable ;
- DOI, doublons, niveau de contenu et provenance ont été contrôlés ;
- toute exploration de publications connexes conserve le DOI source, la relation, le fournisseur et
  des bornes explicites de profondeur, volume et durée ;
- les résultats de filières connexes conservent leur matrice et leurs bornes de transposition ;
- les états `accepted/review/rejected` et `Full article/Abstract only` sont rapportés séparément ;
- les écritures importantes ont une sauvegarde et une stratégie de reprise ;
- un changement de comportement possède un test de non-régression représentatif ;
- chaque champ rédactionnel visible a été contrôlé séparément dans la langue de la question, y compris
  les limitations et abstentions ;
- la réponse hypothétique n'a été utilisée que pour le retrieval, et aucune recherche complémentaire
  automatique ni preuve externe non persistée n'a atteint la synthèse ;
- la fenêtre intra-article est hiérarchique et bornée, sans lecture complète par défaut ;
- toute nouvelle règle durable explicitement demandée a été ajoutée à ce fichier.
