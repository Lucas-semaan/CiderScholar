# Choix techniques des premiers jalons

## Backend ARGO et découverte bibliographique explicite

Le client ARGO est l’unique moteur de génération des workflows de preuves et de synthèse. Il utilise exclusivement
`https://chatbot.argo.inrae.fr/api`, un jeton Bearer lu depuis l'environnement,
des requêtes non streamées et séquentielles, TLS obligatoire, aucun proxy
d'environnement et aucune redirection. Les sorties JSON restent contraintes par
schéma puis validées par Pydantic ; le contenu de raisonnement éventuel retourné
par le serveur n'est ni exposé ni persisté.

## Réutilisation DPAPI pour la clé ARGO

Le stockage des identifiants éditeur s'appuie déjà sur les primitives Windows DPAPI de
`app/secrets.py`. Les fonctions de protection et déprotection en mémoire, l'interdiction d'interface
Windows et la libération explicite des buffers natifs sont réutilisables. Les helpers du registre et
`PublisherCredentialStore` restent propres aux identifiants éditeur : ils associent nom d'utilisateur
et mot de passe, utilisent des messages spécialisés et stockent le ciphertext dans l'environnement
utilisateur.

La clé ARGO réutilise donc les primitives DPAPI communes derrière `LocalSecretStore`, avec son propre
fichier de ciphertext versionné. Elle ne duplique pas les appels `CryptProtectData` et
`CryptUnprotectData`, et ne réutilise ni le registre éditeur ni ses contrats API.

Le fichier ARGO est fixé à `data/secrets/argo-key.dpapi`, dans les données locales du poste. Le
constructeur refuse que ce chemin appartienne à `paths.exports_dir`. Les exports, sauvegardes de
corpus et futurs paquets reposent sur leurs répertoires dédiés et n'incluent jamais `data/secrets` ;
le dossier SharePoint synchronisé est sélectionné séparément et ne peut pas devenir ce stockage.

La découverte bibliographique est déclenchée uniquement par une action de
l'utilisateur. Les connecteurs officiels sont exécutés séquentiellement, avec
limites, temporisation et reprise bornée. Une panne de source n'annule pas les
autres résultats. Les notices sont normalisées puis dédupliquées par DOI ou par
titre. Les clés OpenAlex, Clarivate et Elsevier restent dans l'environnement ;
Crossref et Europe PMC fonctionnent sans secret. Le profil Clarivate actif est
Web of Science Expanded, car la clé migrée répond sur cet endpoint tandis que
l'endpoint Starter la refuse.

La collecte récurrente utilise quatre vagues de requêtes par thème. Après un cycle complet, les cinq
connecteurs passent à la page suivante plutôt que de répéter les premiers résultats. Les DOI acceptés
sans abstract sont résolus par lots de cent au maximum via un filtre OpenAlex, conformément à la
possibilité de regrouper les identifiants. Les échecs sont mémorisés trente jours. Les questions
françaises sont enrichies par un lexique cidricole bilingue déterministe avant FTS5 et E5 ; aucun LLM
n’est appelé pour cette expansion.

## SQLite et FTS5

SQLite conserve les métadonnées, l’état des traitements et le texte des fragments. FTS5 est une
table dédiée synchronisée par triggers. Ce choix duplique le texte entre `chunks` et `chunks_fts`,
mais rend le comportement explicite, simple à inspecter et indépendant d’un framework RAG.

Les insertions d’un article et de tous ses fragments utilisent `BEGIN IMMEDIATE` et une transaction
unique. Un échec ne laisse donc pas un article partiellement visible. Le mode WAL améliore la
robustesse, et chaque connexion est fermée explicitement pour éviter les verrous Windows.

## Identifiants et DOI

Les articles reçoivent un UUID local ; les fragments utilisent un entier SQLite, utile comme `rowid`
FTS5 et futur identifiant de point Qdrant. Le DOI est facultatif, normalisé seulement lorsqu’une chaîne
conforme apparaît littéralement dans les métadonnées ou les premières pages. Aucun modèle ne participe
à cette extraction.

## Extraction et cache

PyMuPDF parcourt les pages une à une. Un seul article est traité à la fois ; le corpus complet n’est
jamais chargé. Le résultat page/texte est enregistré par hash dans `data/extracted`. Si une panne
survient après extraction, une nouvelle tentative reprend ce cache au lieu de relire le PDF.

Le cache est écrit dans un fichier temporaire, puis déplacé atomiquement. Le texte extrait n’est pas
écrit dans les logs ou rapports JSON.

## Détection OCR

Le pipeline mesure le nombre de pages contenant une quantité minimale de texte. Si le ratio est trop
faible, l’état devient `ocr_required`. Il ne lance aucun traitement OCR de masse. L’interface
`PdfExtractor` permettra d’ajouter plus tard GROBID, Tesseract ou OCRmyPDF sans modifier la base.

## Découpage

Le découpage détecte les sections scientifiques usuelles, segmente les paragraphes en phrases et
conserve les bornes de pages en base 1. Il n’assemble que des pages consécutives et ne mélange pas des
sections différentes. Une phrase n’est coupée que lorsqu’elle dépasse seule le plafond configuré.

Le nombre de tokens est une estimation lexicale déterministe. Le tokenizer du modèle d’embeddings
n’est pas chargé pendant l’extraction textuelle : il intervient seulement lorsque le processeur
d’embeddings reçoit un lot borné.

## Embeddings locaux

`SentenceTransformerBackend` charge tardivement une copie locale de
`intfloat/multilingual-e5-base`. Les passages reçoivent le préfixe `passage:`, les questions le préfixe
`query:`, les vecteurs sont normalisés et la longueur maximale est limitée à 512 tokens. Ces paramètres
restent configurables pour permettre ultérieurement `BAAI/bge-m3`.

Le processus applicatif impose `local_files_only=True` et `trust_remote_code=False`. Un script séparé
exige `--allow-network` pour préparer le modèle ; il n’importe aucun composant de la base ou du corpus.

`EmbeddingBatchProcessor` lit les fragments par clé croissante avec `LIMIT batch_size`. Il passe au
stockage vectoriel les identifiants, sections, pages et vecteurs, mais jamais le texte. Un lot n’est
marqué `indexed` qu’après `upsert` réussi. Une interruption laisse au pire `processing`, état remis à
`pending` au démarrage suivant.

## Qdrant embarqué

`QdrantLocalIndex` instancie exclusivement `QdrantClient(path=...)`. Aucun hôte, port, jeton ou service
d’inférence distant n’est configuré. La collection utilise la distance cosinus et `on_disk=True` pour
laisser le système d’exploitation charger les pages vectorielles à la demande plutôt que de conserver
le corpus en RAM.

Les métadonnées de collection enregistrent le nom du modèle et la dimension. Une collection créée pour
un autre modèle ou une autre dimension est refusée, ce qui empêche de mélanger silencieusement des
espaces vectoriels incompatibles.

Le payload Qdrant contient seulement `chunk_id`, `article_id`, section, pages et nom du modèle. Il ne
contient aucun texte scientifique. Après la recherche, `VectorSearchService` relit le fragment dans
SQLite et vérifie que son article correspond au payload Qdrant.

Les identifiants de points sont les identifiants entiers des fragments SQLite. Les `upsert` sont donc
idempotents. Une reconstruction supprime seulement la collection configurée ; toutes les sources
restent présentes et permettent de recalculer l’index.

`qdrant-client==1.18.0` crée aussi une connexion SQLite `:memory:` temporaire pour inspecter le schéma
d’une collection. Son context manager ne ferme pas explicitement cet objet sous Python 3.14, alors
que la cible 3.12 ne signale pas ce comportement. Après fermeture normale du client durable,
`QdrantLocalIndex.close()` force donc la collecte de ce temporaire sous un filtre strictement limité
à son `ResourceWarning`. Les tests exécutés avec les avertissements de ressources transformés en
erreurs confirment qu’aucune connexion applicative ne reste ouverte.

## Recherche lexicale

La saisie utilisateur n’est jamais passée directement à l’opérateur SQLite `MATCH`.
`LexicalQueryBuilder` applique NFKC et `casefold`, extrait uniquement des tokens Unicode, retire une
liste courte de mots fonctionnels français/anglais, borne la requête à 24 termes et reconstruit une
expression FTS5 entièrement entre guillemets. Les préfixes sont activés à partir de quatre caractères.

Le mode par défaut relie les concepts par `OR` et laisse BM25 ordonner les fragments. Les modes `all`
et `phrase` sont disponibles pour les usages précis. Le BM25 donne un poids de 1,5 à la section et de
1,0 au texte. La fusion hybride utilisera les rangs plutôt que de comparer directement le score BM25
au cosinus Qdrant.

Les filtres par article et section sont ajoutés avec des paramètres SQL liés. Seuls les articles
`validated` ou `indexed` sont interrogeables. Le `LIMIT` est appliqué dans SQLite : aucun résultat
intermédiaire complet n’est chargé en mémoire.

## Fusion hybride RRF

Les valeurs BM25 et cosinus n’ont pas la même échelle. Le système ne les additionne donc pas
directement : `reciprocal_rank_fusion` utilise `poids / (k + rang)` avec `k=60`. Les poids initiaux sont
0,35 pour FTS5 et 0,45 pour Qdrant. Le poids 0,20 du reranker reste explicitement réservé jusqu’à ce
que ce composant fournisse réellement un score. `reranker.enabled` vaut donc `false` par défaut :
activer ce réglage ne doit pas être présenté comme supporté avant l’implémentation du cross-encoder.

Chaque source conserve son rang et sa contribution. Les égalités sont résolues de manière stable par
meilleur rang individuel, puis identifiant de fragment. Un doublon interne à une liste ne contribue
qu’une fois.

Le service accepte déjà l’original et jusqu’à cinq variantes dédupliquées. Le poids total de chaque
canal est divisé entre les variantes, afin qu’ajouter une reformulation n’augmente pas artificiellement
le poids du canal. Les traitements restent séquentiels et les candidats sont bornés avant fusion.

Le texte final n’est repris ni du résultat FTS ni du payload Qdrant : tous les candidats fusionnés sont
réhydratés une dernière fois depuis SQLite. Les filtres article et section sont appliqués aux deux
canaux avant la RRF.

## Classement distinct des articles

Le classement n’assimile jamais un fragment à un article. Il regroupe les candidats hybrides par
`article_id`, trie leurs fragments puis calcule cinq composantes normalisées : meilleur fragment,
moyenne des trois meilleurs, recouvrement lexical du titre, recouvrement du résumé et présence
littérale des concepts centraux. Les poids initiaux respectifs sont 0,40, 0,25, 0,15, 0,10 et 0,10.

Une sélection gloutonne applique ensuite, si demandé, une pénalité maximale de 0,15 contre la
redondance avec les articles déjà retenus. Le mode thématique utilise Jaccard sur les termes du titre,
du résumé et des meilleurs fragments ; les modes année et revue utilisent uniquement une égalité de
métadonnées. Le mode `balanced` moyenne ces trois signaux. La pénalité diffère les quasi-doublons mais
ne les supprime pas : le service peut donc toujours remplir vingt places si vingt articles sont
disponibles.

Les auteurs, titre, DOI, revue, année et langue sont relus dans SQLite au moment du classement. Les
valeurs transportées par FTS5 ou Qdrant ne peuvent ni remplacer ni inventer ces métadonnées. Le
classement mesure uniquement la pertinence pour la question ; aucun signal de qualité méthodologique
n’entre dans le score.

## Connexion ARGO

`ArgoClient` appelle directement l’API officielle compatible OpenAI avec `httpx`, sans framework RAG
ni SDK intermédiaire. La configuration impose l’endpoint HTTPS INRAE, TLS, l’absence de proxy
d’environnement et le refus des redirections. La clé Bearer est lue uniquement depuis la variable
d’environnement configurée.

Les appels sont synchrones, non streamés et protégés par un verrou : un seul travail génératif
s’exécute à la fois. Le nombre maximal de tokens produits et le nombre de caractères du prompt sont
bornés. Avant une génération, la liste des modèles doit confirmer que le modèle configuré est
accessible au compte.

Pour une sortie structurée, le schéma JSON est transmis dans `response_format`, puis le contenu
retourné est validé une seconde fois par Pydantic. Cette double contrainte complète les vérifications
métier portant sur l’identité des fragments, les pages sources et les preuves persistées.

La sonde `/health/llm` vérifie la clé et le modèle sans produire de texte. Le client HTTP est fermé et
la clé effacée de l’instance après chaque workflow.

## Extraction de preuves traçables

`EvidencePassageSelector` ne relit qu’une fenêtre bornée d’un article à la fois. Il combine rang du
fragment, recouvrement avec la question, section scientifique, contenu quantitatif et marqueurs de
contraste. Les sections Results, Discussion et Conclusion sont favorisées ; Materials and Methods
est différée lorsque la question ne demande pas de protocole. La déduplication Jaccard évite de
remplir le contexte avec des fragments presque identiques. Le lot final contient trois à huit
passages et au plus 32 000 caractères.

Le texte complet du PDF n’est jamais envoyé à ARGO. Pour chaque passage retenu, le code extrait
localement deux phrases candidates courtes. Le schéma JSON transmis à ARGO utilise des énumérations
dynamiques pour l’`article_id`, les `chunk_id`, les bornes de pages et les extraits exacts autorisés.
Cette contrainte empêche le modèle de paraphraser une citation tout en lui laissant la rédaction des
constats et l’évaluation de la pertinence.

Le schéma distant n’est pas considéré comme une preuve suffisante. La réponse est d’abord validée
par `ArticleEvidence`, qui interdit les champs supplémentaires — donc notamment un DOI — puis le code
vérifie l’appartenance de chaque chunk à l’article, l’égalité des pages et la présence verbatim de
l’extrait dans le texte SQLite. `Database.save_article_evidence` répète ces contrôles au sein de la
transaction qui remplace les preuves et marque la fiche terminée. Une sortie invalide est réessayée
une seule fois ; sinon l’article passe à `failed` sans preuve partielle.

La migration SQLite v2 ajoute `article_evidence_runs`. Elle conserve l’état, les passages retenus, le
nombre de lancements et la dernière erreur pour chaque couple question/article. Une reprise recharge
les fiches `completed` directement depuis SQLite avant d’instancier E5, Qdrant ou ARGO. Pour une fiche
manquante, le classement et les embeddings sont libérés avant le chargement du générateur. Les
traitements lourds restent ainsi séquentiels sur la machine 16 Go.

## Synthèse hiérarchique et citations applicatives

`HierarchicalSynthesisService` consomme uniquement les fiches `completed` et les lignes `evidence`
de la question. La fenêtre est bornée à vingt articles et quatre-vingts preuves, sélectionnées en
tourniquet afin de conserver au moins une preuve par article avant d’ajouter les suivantes. Un thème
reçoit au plus vingt-quatre preuves ; les extraits et énoncés transmis sont tronqués séparément pour
respecter le contexte local sans charger le corpus.

Avec plusieurs articles, un prompt dédié crée un plan de thèmes. Le plan est refusé s’il ne couvre pas
chaque article porteur de preuve exactement une fois ou si ses identifiants ne sont pas contigus. Avec
un seul article, le plan est déterministe et évite un appel ARGO inutile. Chaque thème fait ensuite
l’objet d’une génération structurée et d’une transaction indépendante. La migration SQLite v3
conserve le plan, l’état et la sortie de chaque thème ; une interruption de la synthèse finale ne
recalcule donc pas les thèmes terminés.

ARGO ne produit jamais le texte des citations. Chaque énoncé factuel est un `CitedStatement` contenant
un ou plusieurs UUID de la table `evidence`. Les schémas JSON bornent ces UUID par énumération, puis
Pydantic et SQLite vérifient à nouveau leur appartenance à la question et au thème. Les champs de
consensus, convergence et contradiction exigent au moins deux articles distincts. Les DOI et chaînes
de citation écrites par le modèle sont interdits.

Le rendu Markdown transforme ensuite les UUID en `[ArticleID, p. X]` ou
`[ArticleID, pp. X–Y]` à partir des pages SQLite. La bibliographie est reconstruite après génération
avec le titre, les auteurs, la revue, l’année et le DOI relus dans `articles`. Une reprise complète
recalcule aussi ce rendu depuis SQLite sans contacter ARGO, de sorte qu’aucune métadonnée mémorisée
par le modèle ne puisse devenir une référence.

## Évaluation reproductible

Chaque cas JSON contient une question, au moins un `expected_article_id`, des alternatives
`acceptable_article_ids` et des concepts attendus. La précision et le MRR considèrent les deux
ensembles comme pertinents, alors que le rappel porte uniquement sur les articles obligatoires. Le
nDCG est gradué : gain 2 pour `expected`, gain 1 pour `acceptable`. Les doublons de la liste classée
sont ignorés après leur première occurrence.

Le benchmark réutilise une seule instance E5/Qdrant pour tous les cas et les exécute
séquentiellement. Un fil léger échantillonne toutes les 100 ms le RSS du processus et la mémoire
système utilisée. La version du corpus est le SHA-256 de la liste ordonnée des identifiants, hashes
PDF et états de validation SQLite ; le rapport peut donc être comparé uniquement à corpus identique.

La traçabilité ne juge pas le texte avec un second modèle. Pour une synthèse terminée explicitement
associée au cas — ou une question identique trouvée dans SQLite — chaque `evidence_id` de chaque
affirmation finale est comparé aux preuves autorisées de la question. Le rapport donne le taux de
références traçables et le taux d’affirmations contenant une référence absente. Le benchmark ne
lance jamais ARGO automatiquement et indique `non évalué` lorsqu’aucune synthèse persistée ne
correspond.

Les résultats complets sont sérialisés en JSON Pydantic et rendus dans un rapport Markdown. Les deux
fichiers sont écrits dans un temporaire du répertoire cible puis remplacés atomiquement afin qu’une
interruption ne laisse pas un rapport partiel.

## Interface React/Tailwind et API FastAPI

L’interface produit est une SPA React/TypeScript construite par Vite. Tailwind CSS est son unique
système visuel : les primitives partagées vivent sous `frontend/src/components/ui` et les pages sont
isolées par domaine sous `frontend/src/features`. Le client HTTP typé centralise tous les échanges
avec FastAPI.

Les opérations testables — import, indexation, classement, preuves, synthèse, suppression,
réindexation et exports — vivent dans `app/services/workflows.py`. Les routes de `app/api` valident les
entrées, délèguent aux services et sérialisent les sorties. Elles ne chargent jamais un modèle lourd
au démarrage. Après `npm run build`, FastAPI sert aussi la SPA compilée sur le même port local.

Un PDF envoyé est écrit par blocs dans un fichier temporaire situé sous `data/pdf/uploads`, haché en
SHA-256, renommé avec un nom nettoyé puis déplacé atomiquement. Le nom fourni par le navigateur ne
peut pas sortir de ce répertoire. La suppression d’un article retire d’abord ses points Qdrant,
puis ses métadonnées et analyses SQLite dépendantes ; le PDF source est conservé.

Les réglages d’administration sont fusionnés dans une copie Pydantic de `Settings` et vivent dans le
processus FastAPI. Ils ne réécrivent jamais le YAML et aucune valeur de secret n’est renvoyée au
navigateur. L’architecture complète est décrite dans `WEB_ARCHITECTURE.md`.

## Mémoire et exécution

L’ingestion et les embeddings sont séquentiels. `psutil` avertit lorsque la mémoire système utilisée
dépasse 13 Go et arrête proprement le traitement si le processus atteint sa limite ou si moins de
512 Mo sont disponibles. `multilingual-e5-base` a atteint 1 077,26 Mo de pic pendant un encodage réel
CPU ; après fermeture des poids, le processus conservait environ 439 Mo, principalement pour les
bibliothèques PyTorch chargées. Une recherche réelle avec E5 et Qdrant a atteint 1 111,23 Mo. Le
chemin FTS5 seul a atteint 33,20 Mo et n’importe ni Qdrant ni Sentence Transformers. La génération
ARGO n’ajoute aucun poids de modèle au processus local ; sa consommation locale se limite au contexte
borné, à la validation et au client HTTP. La reprise SQLite d’une synthèse terminée ne contacte pas
ARGO.
Le reranker, lorsqu’il sera implémenté, devra avoir sa propre mesure avant activation sur la machine
16 Go. La recherche hybride chaude prend environ 53 ms sur le corpus de démonstration ; son pic de
1 112,29 Mo est dominé par E5. Les autres limites fonctionnelles et leur effet sur l’acceptation sont
inventoriés dans [`ACCEPTANCE_AUDIT.md`](ACCEPTANCE_AUDIT.md).

En mode Qdrant embarqué, l’application sérialise ses opérations et crée le client avec
`force_disable_check_same_thread=True`. Ce réglage évite aussi la connexion SQLite temporaire utilisée
par `qdrant-client==1.18.0` pour sonder le mode de compilation : son context manager ne la ferme pas
correctement sous Python 3.14. Les connexions persistantes des collections restent fermées
explicitement par `QdrantLocalIndex.close()`.
