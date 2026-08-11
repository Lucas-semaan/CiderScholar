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
  les preuves le permettent, sans remplir artificiellement la réponse.

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
- écrire en prose naturelle par défaut ;
- utiliser des puces uniquement lorsqu’une liste est explicitement demandée ;
- ne pas employer d’émoticône ou d’emoji ;
- éviter les fioritures, formules enthousiastes, superlatifs et exagérations ;
- éviter les introductions creuses et les conclusions répétitives ;
- ne pas masquer un résultat négatif derrière une formulation positive ;
- distinguer observation, interprétation, hypothèse et recommandation ;
- ne pas transformer une association en causalité ;
- ne pas produire de recommandation normative sans preuve explicite dans les sources.

## Structure d’une réponse en prose

1. Répondre directement à la question.
2. Exposer les résultats soutenus par les sources.
3. Présenter les contradictions, limites, biais ou erreurs potentielles pertinents.
4. Indiquer les améliorations possibles seulement si elles découlent clairement des constats.
5. Terminer par les limites documentaires utiles, sans formule décorative.

Les cinq éléments ne deviennent pas automatiquement cinq sections. Ils doivent former un texte
naturel et concis. Un élément absent des sources est omis ou explicitement déclaré non documenté.

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

Les brouillons sont persistés dans `facet_drafts` avec leur requête, leurs preuves et leurs sources.
Ils facilitent l’audit, mais ne constituent jamais eux-mêmes une preuve scientifique.
Si le plan ARGO est indisponible ou invalide, un plan déterministe borné prend le relais et un
avertissement est ajouté à la réponse.

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
transmis au modèle. Les retraits sont attribués à des codes stables tels que
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

- le style attendu est calculé par l’application et non choisi par ARGO ;
- la langue attendue est calculée depuis le dernier message utilisateur, jamais depuis l’historique
  ni depuis une requête interne d’axe ; chaque champ rédactionnel est validé séparément et une
  correction ARGO doit traduire tout champ rejeté avant rendu ;
- une réponse en prose ne commence aucun paragraphe par un marqueur de liste ;
- une réponse en liste n’est permise que sur demande explicite ;
- les citations rendues correspondent aux identifiants de sources validés ;
- chaque quantité générée correspond dans la preuve citée par sa valeur, son signe ou comparateur,
  son unité, son intervalle ou incertitude et son contexte scientifique ;
- chaque page affichée provient du chunk full-text effectivement fourni à ARGO ;
- les références finales proviennent uniquement de SQLite ;
- aucun emoji n’est présent ;
- les phrases interdites connues déclenchent un test de non-régression ciblé.
