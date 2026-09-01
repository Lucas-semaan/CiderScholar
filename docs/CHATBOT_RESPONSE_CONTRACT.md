# Contrat rédactionnel du chatbot

Statut : accepté.

## Continuité entre recherche et conversation

Le champ `interaction_mode` d’une demande accepte trois valeurs :

- `auto` interprète la demande courante avec l’historique récent ;
- `research` force une nouvelle recherche bibliographique ;
- `conversation` réutilise les sources persistées avec la dernière réponse.

En mode automatique, une demande de détail, de reformulation, de traduction ou de changement de
format reste une conversation sur les résultats. Une demande explicite de nouvelles publications
ou d’une nouvelle recherche relance le RAG. Si aucune source réutilisable n’existe, le moteur revient
à la recherche afin de ne jamais produire une affirmation scientifique sans preuve.

## Effort de réponse

Le champ `answer_effort` accepte `concise`, `balanced` et `deep`, avec `balanced` par défaut. Ce
contrôle agit sur des plafonds cohérents de variantes de requête, d’articles, de passages, de contexte
intra-article, de preuves et de tokens. Il règle le niveau de détail, pas le niveau de vérité : les
contrôles de pertinence, de traçabilité, de nombres, de style et l’abstention restent identiques.

- `concise` répond directement avec les affirmations indispensables et évite une seconde recherche
  pour un axe seulement incomplet ;
- `balanced` fournit la synthèse scientifique usuelle et bornée ;
- `deep` élargit le contexte et développe les mécanismes, conditions, contradictions et limites quand
  les preuves le permettent, sans remplir artificiellement la réponse. Pour une demande réellement
  comparative ou multi-dimensionnelle, il établit trois à quatre axes scientifiques utiles au plus :
  finalité ou effets, mécanismes et conditions, comparaison demandée, puis compromis ou limites
  d'application. Chaque axe rendu contient au moins une affirmation validée ou une lacune documentaire
  explicite. Il peut conserver 32 candidats d'abstract, 36 enregistrements de preuve et jusqu'à 40
  éléments de contexte ; aucun traitement intermédiaire ne les tronque silencieusement à vingt. Un
  objectif indicatif de longueur ne remplace jamais ce contrôle de couverture.

Ces plafonds décrivent le pool de recherche et de validation. Après le classement sémantique global,
la génération finale reçoit tous les éléments pertinents retenus par le RAG. Chacun des éléments A/B transmis doit
contribuer à la synthèse et être cité au moins une fois ; plusieurs fragments peuvent étayer une même
idée lorsqu'ils sont réellement convergents ou complémentaires.

## Posture

Le chatbot est un agent scientifique froid, factuel et prudent. Il ne cherche pas à séduire,
encourager ou dramatiser. Il présente les résultats favorables et défavorables avec le même niveau
d’attention.

Il doit signaler, lorsque les sources le permettent :

- les résultats positifs et négatifs ;
- les biais et limites méthodologiques ;
- les erreurs ou incohérences potentielles ;
- les risques d’interprétation ;
- les informations absentes ou incertaines ;
- les améliorations envisageables, sans les présenter comme validées si elles ne le sont pas.

## Style

- produire exclusivement dans la langue du dernier message utilisateur chaque champ rédactionnel
  visible : définition, affirmation, mécanisme, limitation, abstention, brouillon d’axe et assemblage
  final. ARGO traduit le contenu scientifique des preuves rédigées dans une autre langue au lieu de
  recopier leur formulation ; les extraits verbatim, titres et métadonnées bibliographiques restent
  inchangés et ne comptent pas comme un mélange de langues dans la prose ;
- utiliser des phrases simples et un vocabulaire scientifique précis ;
- commencer par une mini-introduction utile qui situe le sujet, la matrice, le procédé et les
  distinctions nécessaires avant d'exposer les résultats ;
- en l'absence de contrainte explicite, laisser Argo choisir la typologie adaptée parmi la prose
  continue, les sections thématiques, la comparaison, le déroulé de processus et la liste ;
- respecter en priorité une forme explicitement demandée par l'utilisateur ;
- construire des paragraphes scientifiques riches lorsque les preuves le permettent : chaque paragraphe
  développe en trois à six phrases liées le constat, son contexte expérimental, les conditions ou la
  comparaison utiles, puis sa portée ou sa limite documentée ;
- croiser dans un même paragraphe plusieurs sources réellement convergentes ou complémentaires et rendre
  visibles leurs citations, sans citation décorative ni regroupement artificiel de résultats différents ;
- faire croître la longueur globale avec le nombre et la richesse des fragments retenus, sans répétition,
  remplissage ou connaissance externe ;
- ne pas employer d’émoticône ou d’emoji ;
- éviter les fioritures, formules enthousiastes, superlatifs et exagérations ;
- éviter les introductions creuses et les conclusions répétitives ;
- ne pas masquer un résultat négatif derrière une formulation positive ;
- distinguer observation, interprétation, hypothèse et recommandation ;
- ne pas transformer une association en causalité ;
- ne pas produire de recommandation normative sans preuve explicite dans les sources.

## Structure d’une réponse

1. Situer brièvement le sujet et les distinctions nécessaires.
2. Répondre directement à la question en synthétisant les fragments pertinents.
3. Exposer les résultats soutenus par les sources, en croisant les preuves complémentaires.
4. Présenter les contradictions, limites, biais ou erreurs potentielles pertinents.
5. Indiquer les améliorations possibles seulement si elles découlent clairement des constats.
6. Terminer par les limites documentaires utiles, sans formule décorative.

Ces éléments ne deviennent pas automatiquement six sections. Argo choisit une organisation naturelle
adaptée au contenu ; un élément absent des sources est omis ou explicitement déclaré non documenté.
La validation contrôle aussi la densité de chaque paragraphe et la longueur globale en fonction de la
matière disponible dans les passages cités. Une réponse télégraphique est régénérée dans l'enveloppe
globale de correction ; une source brève ne crée jamais une obligation de développer au-delà de ce
qu'elle permet d'étayer.

## Questions à plusieurs axes

Une question qui demande plusieurs résultats scientifiques distincts, par exemple les arômes et la
structure d’une eau-de-vie pendant l’élevage, suit une synthèse en deux étages :

1. un appel ARGO de planification comprend la matrice, le processus et les résultats demandés ;
2. ce plan conserve un seul axe pour une demande simple et ne crée de deux à quatre axes que si
   des besoins de preuve réellement indépendants le justifient ;
3. les requêtes courtes de chaque axe alimentent les recherches lexicales et vectorielles ; les
   matrices proches et distantes restent explicitement étiquetées pour le reranking ;
4. chaque axe reçoit un ensemble équilibré de preuves et produit un brouillon cité ;
5. un dernier appel assemble les brouillons en vérifiant leurs affirmations contre les passages
   originaux ;
6. seuls les identifiants des preuves originales peuvent apparaître dans la réponse finale.

Le rendu final reprend les libellés des axes validés au lieu de réduire toute question multi-axes aux
seules rubriques génériques « réponse » et « effets ». Pour chaque axe, l'application expose un état
documenté, partiellement documenté ou non documenté. Cet état est calculé depuis les affirmations
validées et les lacunes persistées ; il n'est pas une appréciation libre du modèle.

Les brouillons sont persistés dans `facet_drafts` avec leur requête, leurs preuves et leurs sources.
Ils facilitent l’audit, mais ne constituent jamais eux-mêmes une preuve scientifique.
Si le plan ARGO est indisponible ou invalide, un plan déterministe borné prend le relais et un
avertissement est ajouté à la réponse.

Pour toute question, les dimensions explicitement demandées et les facettes déterministes nécessaires
à leur interprétation remplacent les axes périphériques du planificateur si le plan a déjà atteint sa
taille maximale. La sélection de preuves réserve une représentation à chacun de ces axes prioritaires.
Cette règle est générique : les exemples de domaine, comme les auxiliaires minéraux dans une comparaison
de colles, restent des tests de non-régression et ne deviennent jamais des exceptions codées. Après une
récupération, le seuil minimal d'affirmations défini par l'effort est vérifié de nouveau ; s'il n'est
plus atteint malgré des brouillons validés suffisants, une unique relance ou un repli sur ces brouillons
est effectué, toujours à partir des preuves originales fournies.

Une preuve retenue n'est pas citée si elle ne fait que répéter une preuve meilleure. À l'inverse, les
preuves A/B apportant des résultats complémentaires ne sont pas écartées par un plafond fixe inférieur
au budget de l'effort demandé. La complétude finale se vérifie par axe explicite, nombre de résultats
distincts validés et densité rédactionnelle disponible dans les brouillons. Une affirmation finale
porte l'identifiant de son axe ; le renderer ne déduit pas cette appartenance du seul `evidence_id`,
car un même passage peut légitimement soutenir plusieurs dimensions.

Le coût dense est borné séparément du coût lexical. Les variantes non sélectionnées pour Qdrant restent
recherchées par FTS et les classements d'axes réutilisent le pool dense global. Le nombre d'appels
vectoriels dépend de l'effort public et est traçable sans réduire le nombre d'axes scientifiques.
Les variantes d'une vague partagent la même session SQLite et les recherches vectorielles compatibles
sont envoyées par lot. En `balanced` et `deep`, une première vague exacte et courte est acceptée
uniquement lorsque le filtre sémantique confirme une couverture suffisante de chaque axe ; sinon une
unique vague complémentaire emploie les variantes et bornes complètes. Les résultats peuvent provenir
d'un cache local seulement si l'empreinte du corpus, les modèles, leurs manifestes, la configuration,
les filtres et toutes les limites sont identiques ; une entrée invalide est traitée comme un miss.

Le filtrage sémantique traite au plus dix candidats par réponse structurée et enchaîne autant de lots
que nécessaire, jusqu'au plafond global de 48, tout en reconstituant l'ordre et l'ensemble exacts des
candidats persistés. Lors d'une génération facettée, chaque
brouillon reçoit d'abord les seules preuves jugées admissibles pour son axe ; l'assemblage final
reste fondé sur l'ensemble borné des preuves retenues. Un échec de schéma après correction est exposé
par le diagnostic stable `invalid_schema`, jamais par `unknown`.
L'échec d'un brouillon d'axe ne coupe pas les axes suivants : il devient une lacune explicite, les
autres brouillons sont encore validés, puis la réponse finale porte le statut `partial_generated`.

## Pipeline de production depuis le 27 août 2026

Le chatbot n'utilise plus d'axes de travail, de quotas par axe, de brouillons facettés par défaut ni
de contrôleur séparé de couverture. Argo prépare une hypothèse prudente non affichable et un nombre
adaptatif de vérifications (`concise` : 3 au plus, `balanced` : 5, `deep` : 8). L'original,
l'hypothèse et les requêtes de vérification alimentent une seule vague locale groupée. Un filtre
sémantique global A–D retient ensuite les candidats, sans déclencher de seconde recherche.

Le contexte suit `article -> section -> chunk` et relit uniquement les chunks d'ancrage, leurs voisins
et des sections ciblées. Les textes transmis à la synthèse finale sont toujours les passages ou
abstracts originaux persistés dans SQLite. Une notice externe n'est pas une preuve avant son ingestion
et sa validation. La synthèse finale est un workflow de génération unique, avec des repasses bornées
sur les mêmes preuves, soumis aux mêmes validations de citations, pages, nombres, causalité, langue et
abstention quel que soit l'effort choisi.
Une analyse visuelle générée reste hors de cette synthèse et n'est jamais substituée à un passage
original.

Le filtre global peut conserver un pool large pour l'évaluation et le diagnostic. La fenêtre
de synthèse finale conserve tous les éléments pertinents retenus dans l'ordre classé. Tous les éléments A/B de cette
fenêtre doivent être utilisés dans les affirmations citées. Le validateur refuse une omission et, pour
un ensemble riche, une densité rédactionnelle anormalement faible. La réponse initiale et ses corrections
se partagent une enveloppe globale de dix requêtes au plus ; chaque correction reçoit tous les codes encore
actifs et une action précise par code, en restant fondée sur les mêmes passages SQLite. La consigne active
remplace la précédente et reste bornée à la marge d'entrée réservée ; un dépassement résiduel produit un
diagnostic structuré et ne remonte jamais comme erreur interne du worker.

À l'épuisement de cette enveloppe, les atteintes à la fidélité scientifique restent bloquantes : référence
inexistante, niveau C/D, chiffre, causalité, norme, évaluation ou sécurité non étayés, fuite d'identifiant ou
du processus interne, schéma inutilisable ou altération de la question. Les défauts de couverture et de
rédaction, y compris un libellé indirect B manquant, peuvent être rendus comme avertissements lorsque la
version conservée ne contient plus aucun blocage scientifique. Une sortie `insufficient` qui omet des
preuves déjà classées A/B déclenche les mêmes corrections au lieu de valider immédiatement une abstention.
Si toutes les repasses restent des abstentions, la plus précise demeure un repli sûr ; sinon la sélection
du meilleur candidat privilégie la couverture citée des preuves A/B avant le nombre d'avertissements de
forme. Une synthèse sûre mais imparfaite prend `generation_status=partial_generated`, expose les codes de
qualité et signale précisément sa limite au lecteur.
À chaque tentative, les affirmations sont également validées séparément : les paragraphes sûrs et cités
sont mémorisés même si un autre bloc de la même sortie est rejeté. Une définition ou une limitation qui
fuit le processus interne n'invalide donc plus mécaniquement toutes les affirmations indépendantes ; les
champs globaux sont remplacés par un cadrage déterministe minimal avant de comparer ce candidat aux
repasses suivantes.

Les paragraphes historiques ci-dessus qui décrivent des axes ou une vague complémentaire restent des
éléments de migration et d'audit ; ils ne décrivent plus le chemin de production.

Si un fragment A ou B d’un texte intégral est retenu, le sélecteur peut rechercher dans le même
article des passages complémentaires bornés : voisins, résultats, méthodes/conditions et
discussion/limites. Chaque passage conserve sa page et son rôle contextuel. Cette expansion améliore
la compréhension de l’article mais ne transforme pas un passage périphérique en preuve directe.

## Trace de génération scientifique

Chaque phase rapide persistée peut exposer une entrée `generation_traces` sans question, preuve ni
texte généré. Elle enregistre uniquement la phase, l’issue, le nombre d’appels, les reprises de
validation ou de longueur, les tokens et la température de correction effectivement utilisée.
La température des corrections factuelles est configurée par
`argo.scientific_correction_temperature`, bornée entre `0` et `0,2`, avec `0,1` par défaut ; la
valeur historique `0,35` n’est plus forcée. Une campagne peut comparer `0` et `0,1`, mais aucune des
deux valeurs ne doit être déclarée supérieure avant mesure sur le développement CiderQA.

Les traces des phases échouées qui conduisent à une réponse facettée partielle sont conservées avec
`outcome=failed`. Elles permettent d’attribuer appels et tokens à l’étage réel sans exposer le contenu
scientifique ou les messages internes.

## Trace des pools de retrieval et des ressources

Chaque requête de recherche persiste une liste `retrieval_traces` strictement non textuelle. Elle
compte les variantes de requête, candidats lexicaux et denses, l’union soumise à la fusion RRF, les
candidats fusionnés, les entrées et sorties du reranker, puis les articles et passages effectivement
transmis au modèle, ventilés entre texte intégral et abstract, ainsi que les hits et misses du cache de
retrieval. Une dégradation de la voie des abstracts n'implique jamais l'échec de la voie distincte des
textes intégraux : le message public indique leurs nombres d'articles et de passages séparément. Les
documents transmis mais non cités après un échec de validation restent explicitement qualifiés de
« retrouvés, non cités » et ne sont pas affichés comme références. Les retraits sont attribués à
des codes stables tels que
`duplicate_across_query_pools`, `not_selected_after_scientific_ranking`,
`no_passage_selected` ou `semantic_or_scientific_grade_rejected`. La trace ne contient ni requête,
ni identifiant d’article, ni titre, ni DOI, ni extrait.

Les entrées `timings` conservent la durée et le nombre d’exécutions par étape, et ajoutent les tokens
d’entrée/sortie ainsi que la RAM du processus et du système observée aux bornes de l’étape. Ces
mesures avant/après ne constituent pas un pic mémoire échantillonné et ne doivent pas être présentées
comme tel. Si la mesure locale de RAM est indisponible, les valeurs restent nulles sans interrompre
la réponse scientifique.

## Réponses partielles et abstentions

Une synthèse dont certaines affirmations ou certains axes ont déjà passé les validations peut être
rendue avec `generation_status=partial_generated`. Elle utilise le même renderer, les mêmes citations
et la même structure que `generated`, puis décrit précisément ce qui n’a pas pu être établi.
Une récupération après rejet d'affirmations, dans un brouillon d'axe ou dans l'assemblage final,
propage obligatoirement `partial_generated` jusqu'à la réponse publique. Le statut `generated` est
réservé aux réponses dont toutes les phases utilisées sont intégralement validées.

Si aucune affirmation n’est validable, `abstained` conserve la structure rédactionnelle attendue et
n’invente aucune citation. Un problème technique sans synthèse prend le statut `diagnostic_only`,
également sous forme structurée. Dans aucun de ces cas l’application n’affiche les candidats de
retrieval ou une succession de sources comme s’il s’agissait de la réponse.

## Citations et références

- appliquer APA 7e édition ;
- construire toutes les références depuis les métadonnées persistées dans SQLite ;
- préférer un texte intégral à un abstract seulement lorsque sa matrice, son processus et son
  résultat sont au moins aussi pertinents ;
- permettre à un abstract directement pertinent de primer sur un texte intégral hors matrice ;
- élargir Calvados vers apple/cider brandy ou apple spirit avant les autres eaux-de-vie, et traiter
  le vin uniquement comme une analogie explicitement incertaine ;
- associer les citations full-text aux pages des chunks persistés dans SQLite ;
- signaler explicitement lorsqu’un énoncé ne repose que sur un abstract ;
- ne jamais accepter un auteur, une année, un DOI ou un titre inventé par ARGO ;
- citer uniquement les sources qui soutiennent réellement l’énoncé concerné ;
- conserver une bibliographie dédupliquée ;
- signaler clairement l’absence de DOI sans en inventer un.

Les citations utilisent le format auteur-date dans le texte. Une section `Références` placée en fin de
réponse contient les notices complètes au format APA 7. Le renderer applicatif, et non ARGO, produit
les citations et la bibliographie.

## Formulations interdites par défaut

- « excellente question » ;
- « résultat révolutionnaire », « remarquable » ou autre qualification non étayée ;
- « sans aucun doute » lorsque les sources comportent une incertitude ;
- émoticônes, emojis et interjections ;
- phrases promotionnelles ou encouragements génériques ;
- liste à puces lorsque l’utilisateur demande de la prose ;
- recommandation de sécurité, seuil ou norme absente des sources.

## Critères de validation automatisables

- une contrainte de forme explicite est calculée et imposée par l'application ; sans contrainte,
  la typologie est choisie par Argo dans l'ensemble fermé pris en charge par le renderer ;
- toute réponse scientifique générée contient une mini-introduction contextualisée ;
- la génération reçoit tous les éléments pertinents classés par le RAG et utilise chacun des éléments A/B transmis ;
- la langue attendue est calculée depuis le dernier message utilisateur, jamais depuis l’historique
  ni depuis une requête interne d’axe ; chaque champ rédactionnel est validé séparément et une
  correction ARGO doit traduire tout champ rejeté avant rendu ;
- une réponse en prose ne commence aucun paragraphe par un marqueur de liste ;
- une réponse en liste est interdite si l'utilisateur demande explicitement de la prose ou l'absence
  de liste ; sinon elle reste une typologie qu'Argo peut choisir lorsqu'elle est la plus claire ;
- les citations rendues correspondent aux identifiants de sources validés ;
- chaque quantité générée correspond dans la preuve citée par sa valeur, son signe ou comparateur,
  son unité, son intervalle ou incertitude et son contexte scientifique ;
- chaque page affichée provient du chunk full-text effectivement fourni à ARGO ;
- les références finales proviennent uniquement de SQLite ;
- aucun emoji n’est présent ;
- les phrases interdites connues déclenchent un test de non-régression ciblé.
