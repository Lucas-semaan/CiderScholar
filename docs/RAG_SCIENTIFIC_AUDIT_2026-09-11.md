# Audit scientifique du RAG — 11 septembre 2026

## Périmètre et méthode

Premier rapport établi **avant modification du pipeline**, sur le workspace courant, y compris ses
modifications utilisateur préexistantes. Lecture du code, du guide méthodologique, des configurations
effectives et de compteurs SQLite en lecture seule ; tests locaux et contre-exemples synthétiques.
Aucun appel bibliographique, génération ARGO, téléchargement, réindexation ou modification du corpus.
Les références de fonctions permettent de retrouver le comportement malgré les décalages de lignes.

Critères utilisateur : exactitude et couverture avant latence ; aucune baisse arbitraire du rappel ;
diagnostic avant refonte ; distinguer observation, risque inféré et expérience nécessaire ; conserver
la provenance SQLite ; comparer chaque correction à une baseline et à un contrôle indépendant.
La seconde recherche reste une **expérimentation**, pas une décision de réactivation : le contrat
conversationnel accepté du 27 août impose une seule vague et des validations obligatoires.

## A. Architecture actuelle

- React → client HTTP → FastAPI (`app/api/chatbot.py`) → jobs persistés →
  `app/jobs/chat_handler.py` → `answer_chatbot` / `_answer_chatbot`
  dans `app/services/workflows.py`.
- SQLite est l'autorité pour articles, abstracts, chunks, pages, décisions, jobs et réponses.
  Qdrant local fournit identifiants et scores, dans des collections distinctes de chunks et abstracts.
- La génération est réalisée par le client configurable `ArgoClient`. Le nom de cette classe ne
  suffit pas à déduire le modèle réellement choisi par les réglages utilisateur.
- Le chatbot courant utilise `ArgoHypothesisPlanningService`, importé sous l'alias historique
  `ArgoQueryPlanningService`. Le vieux planificateur par axes n'est pas son point d'entrée.
- `app/deep_research/` contient un autre chemin, avec RRF, reranking, éventuel résumé contextuel,
  seconde itération plafonnée à deux et parcours de citations. Il est désactivé dans les deux
  configurations inspectées. `answer_effort=deep` n'active pas ce moteur.
- `rank_question`, extraction par article et synthèse hiérarchique constituent également des
  parcours distincts ; leur présence ne prouve pas qu'ils sont appelés par le chatbot.

**Configuration réellement résolue.** `load_settings('config.yaml')` et `load_settings()` pointent
vers le même corpus sous `%LOCALAPPDATA%/CiderScholar/UserData/data/common/database`, mais avec des
réglages différents : le workspace demande `qwen3-vl-embedding-8b`, batch 64, et autorise la découverte
externe ; le défaut desktop demande `intfloat/multilingual-e5-base`, batch 12, découverte désactivée.
Le dossier partagé contient E5 et le cross-encoder mMARCO, mais aucun dossier Qwen. Le cross-encoder
est **désactivé dans les deux cas**. Une simple lecture de `EmbeddingConfig` aurait manqué cet écart.
La version exacte du binaire installé et l'égalité physique SQLite/Qdrant n'ont pas été certifiées.

## B. Flux d'une requête

```text
Message et historique persistés
  → choix recherche / réutilisation conversationnelle
  → question courante + jusqu'à deux questions précédentes, 4 000 caractères
  → garde local de périmètre cidricole
  → wiki borné de raisonnement, sans autorité probante
  → plan ARGO mis en cache : hypothèse prudente + besoins atomiques
       ou plan déterministe de secours
  → une vague locale, sous verrou, une ouverture Qdrant partagée
       abstracts : titres d'articles + FTS chunks + FTS/métadonnées notices + dense notices
       acquisition facultative : au plus deux textes intégraux des notices locales trouvées
       textes intégraux : FTS + dense → RRF → agrégation par article
         → score scientifique heuristique + cross-encoder optionnel
         → présélection → navigation article/section/voisins → choix des passages
  → découverte externe facultative, séparée et non utilisée comme preuve
  → fusion articles/abstracts, DOI puis repli titre, sélection par facettes heuristiques
  → filtre ARGO global A–D, lots séquentiels ≤ 10, reprise par cache
  → déduplication exacte supplémentaire et extraits verbatim bornés
  → synthèse JSON unique sur les preuves SQLite A/B
  → contrôles citations/nombres/langue/causalité + vérification sémantique des affirmations
  → corrections bornées / réponse partielle / abstention
  → références et liens PDF assemblés par l'application, persistance et affichage
```

La réutilisation conversationnelle réhydrate les sources SQLite et refait le filtre obligatoire.
Elle n'effectue pas systématiquement une nouvelle recherche. Une relance courte demandant un nouvel
effet peut donc rester limitée aux anciennes sources : risque à tester dans le choix automatique du mode.

## C. Points forts à préserver

Recherche déjà multi-requêtes et hybride ; hypothèse jamais transmise comme preuve ; vocabulaire
cidricole bilingue et distinction matrice/procédé/résultat ; identifiants de preuve stables ;
réhydratation SQLite ; validation indépendante des affirmations ; échecs techniques reprenables ;
absence de recherche agentique illimitée ; chargement paresseux et fermeture explicite des ressources.
Le filtre global considère explicitement une contradiction pertinente comme A. Les sources externes
non ingérées et les observations visuelles générées n'alimentent pas la réponse scientifique.

## D. Problèmes identifiés

| Priorité | Composant / fonction | Problème observé ou risque | Impact qualité | Latence | Confiance |
|---|---|---|---|---|---|
| Critical | `EvidencePassageSelector.select`, `app/llm/article_evidence.py` | Jaccard ≥ 0,85 reporte des passages ; réintégration seulement sous le minimum de trois. Deux phrases opposées avec similarité 0,944 donnent trois passages retenus sur quatre, négation écartée | Contradiction perdue avant filtre global | Négligeable | Reproduit synthétiquement |
| Critical | `deterministic_hypothesis_plan` | Accepte une question de 4 000 caractères mais l'insère dans des champs limités à 500 et 2 000 | Le secours peut lui-même échouer ; défaut reproduit à 733 caractères | Échec plutôt que réponse | Reproduit |
| High | `search_common_corpus_abstracts` | Exclut les abstracts dont le DOI figure dans **tous** les articles locaux avant de savoir si un passage ou abstract local sera réellement retenu | Source trouvée potentiellement perdue | Lit `list_articles` et compte les chunks inutilement | Condition certaine, fréquence réelle non mesurée |
| High | `LexicalQueryBuilder.build` | Garde les 24 premiers termes distincts ; termes finaux et expansions ajoutées à la fin disparaissent | Rappel lexical des dimensions finales réduit | Borne utile, choix des termes inadéquat | Troncature reproduite ; impact corpus à mesurer |
| High | `search_common_corpus_full_text_evidence` | Question acceptée jusqu'à 4 000 caractères, builder lexical limité à 2 000 ; variantes et intent ont aussi leurs propres bornes | Dégradation/échec du chemin full text pour une question longue | Repli | Contrats observés |
| High | `HybridSearchService._dense_article_prefilter` | L'hypothèse dense cherche seulement dans 4–60 articles trouvés lexicalement ; seule l'originale reste globale | L'expansion sémantique ne peut sauver un article absent du pool lexical et du top dense original | Gain possible | Restriction certaine, perte empirique à mesurer |
| High | Reranking full text dans `workflows.py` | Notes A–D lexicales prioritaires, texte = abstract s'il existe, sinon chunks ; cross-encoder désactivé ; son rang ne vaut ensuite que 5 % | Résultat Methods/Results absent de l'abstract pénalisé ; filtre ARGO ne voit jamais les exclus | Économie sur petits pools | Code certain ; ampleur à mesurer |
| High | Configurations workspace/desktop | Modèles et options divergents sur le même corpus ; modèle workspace absent | Repli lexical ou voie dégradée, mesures non comparables | Échecs/replis | Configurations et dossiers vérifiés |
| Medium | `BibliographicHybridSearchService._response` | RRF notice fixe 0,4/0,6 + bonus thème 0,02 ; maximum RRF au rang 1 = 1/61 ≈ 0,0164 | Bonus thématique domine tout le RRF ; calibration des priorités nécessaire | Faible | Calcul exact ; impact à tester |
| Medium | Fusion des résultats bibliographiques | Meilleur score par DOI, pas une RRF globale entre toutes les variantes ; métadonnées ajoutées après FTS | Favorise certains canaux, provenance de sous-requête incomplète | Faible | Code observé |
| Medium | `distinct_evidence` / `focused_excerpt` | Fenêtres lexicales successives de 2 400 caractères puis adaptation au prompt ; identités conservées mais contexte tronquable | Condition/limite éloignée potentiellement perdue | Réduit le prompt | Risque inféré |
| Medium | Traces et caches | Compteurs agrégés sans trajectoire d'identifiants ; signature retrieval n'inclut pas tout `article_ranking` / `evidence` ; résultats dégradés peuvent être cachés | Diagnostic et invalidation incomplets | Cache rapide mais incomplet possible | Code observé |
| Low | `workflows.py`, `pilot_rag.py` | Plusieurs milliers de lignes, chemins historiques et réglages retirés encore présents | Maintenance et lecture ambiguës | Indirect | Observé ; pas de refactor massif proposé |

Les points « Critical » décrivent des modes de défaillance possibles, pas une preuve que toutes les
réponses actuelles sont fausses. Les tests existants passent malgré ces contre-exemples.

## E. Analyse spécifique des requêtes longues

`contextualize_retrieval_query` concatène les questions utilisateur récentes. Le plan génère une
hypothèse de 80/140/250 mots selon l'effort et 1–3/5/8 besoins ; chaque requête de besoin peut
atteindre 600 caractères. « Courte » est principalement une instruction du prompt, pas un plafond
de mots. Les contradictions sont facultatives et intercalées par besoin dans `lexical_queries`.

Le chemin courant transmet au dense l'originale et l'hypothèse ; au lexical l'originale et toutes
les requêtes/contradictions produites. Il adapte `maximum_variants` à leur nombre : les budgets
historiques 5/7/10 ne sont **pas** une suppression des dernières contradictions dans ce chemin.
`retrieval_queries(..., limit=...)` existe, mais ce n'est pas la méthode appelée ici.

FTS consomme en mode `any` une disjonction **OR**, pas un AND général trop restrictif. Les guillemets
d'une question ne préservent pas automatiquement les expressions ; tokenizer, filtre de mots
fonctionnels et arrêt à 24 termes dominent la requête. Le chatbot désactive le préfixage malgré sa
valeur globale vraie. `expand_cider_query` ajoute les expansions après l'original, ce qui peut les
rendre invisibles lorsque les 24 places sont prises. Le test à 733 caractères perd le terme final.
Le [contrat officiel FTS5](https://www.sqlite.org/fts5.html) confirme la distinction OR/AND/phrases.

La découverte externe reçoit en revanche une seule question contextualisée, sans les besoins courts,
et refuse plus de 2 000 caractères. C'est donc principalement cette voie qui ressemble au modèle
« grande question unique ». Elle ne doit pas être assimilée à la collecte thématique du corpus.

Proposition : conserver l'original pour classement/validation, isoler les requêtes par moteur,
garantir les termes discriminants de chaque besoin, mesurer les termes effectivement consommés et
les tokens tronqués. Comparer cette stratégie à l'existante avant de modifier la largeur des pools.

## F. Retrieval, représentation et chunking

| Budget courant | concise | balanced | deep |
|---|---:|---:|---:|
| Besoins maximum | 3 | 5 | 8 |
| Requêtes lexicales théoriques avec toutes les contradictions | 7 | 11 | 17 |
| Requêtes denses | 2 | 2 | 2 |
| Candidats par recherche abstract, avant limites internes | 40 | 45 | 96 |
| Pool d'articles full text avant classement scientifique | 18 | 24 | 48 |
| Limite fusionnée de chunks full text avec 8 ancrages/article | 144 | 192 | 384 |
| Articles full text finalement présélectionnés | 6 | 8 | 16 |
| Candidats de contexte par article | 12 | 16 | 20 |
| Passages finaux par article | 3 | 4 | 6 |
| Abstracts retenus | 12 | 15 | 32 |
| Notices/preuves après fusion, avant ARGO A–D | 12 | 16 | 36 |
| Affirmations du générateur mono-synthèse | 4 | 8 | 12 |

Les limites de chunks peuvent évincer un article avant agrégation si quelques articles occupent tout
le haut du classement. Pas de seuil de similarité dur ajouté par le chatbot ; Qdrant permet un seuil
configurable. Les filtres d'admission SQLite, le DOI obligatoire pour les abstracts de notices et les
exclusions de titres non identifiables restent des règles scientifiques explicites.

RRF full text : poids lexical 0,35 et dense 0,45, répartis entre variantes, constante 60. Le 0,20
« réservé au reranker » n'est pas un troisième signal calculé à cette étape. L'agrégation article
combine meilleur fragment 0,40, moyenne des trois meilleurs 0,25, titre 0,15, abstract 0,10 et concepts
0,10. Le chatbot passe `diversity_mode='none'` ; il conserve ensuite une sélection par facettes
heuristiques malgré l'absence d'axes LLM et de vague de couverture. Aucune diversité garantie par
équipe indépendante, type d'étude ou résultat contradictoire.

Le chunking **est déjà structurel et par phrases** (`ScientificChunker`), avec comptage réel du
tokenizer en ingestion, cible 420, maximum 512, chevauchement 80, pages persistées. Reconnaissance par
regex de grands titres de sections, faible prise en charge des sous-sections, titres combinés et
formats atypiques. Les références ne constituent pas une section explicitement reconnue. Le schéma
SQLite possède `subsection`, mais ce chunker ne l'alimente pas. Figures et tableaux ont des structures
séparées ; leurs captions peuvent contribuer au lexical, sans constituer une compréhension visuelle.

Embedding full text = `passage: ` + chunk seul ; abstract bibliographique = titre + abstract borné à
12 000 caractères. Titre/section du chunk ne sont pas inclus dans son vecteur. E5, normalisation,
cosinus et dimension contrôlée au chargement/collection ; la fiche
[E5 officielle](https://huggingface.co/intfloat/multilingual-e5-base) indique 768 dimensions et une
troncature à 512 tokens. Les longs abstracts ne passent pas le même contrôle de longueur explicite
que les chunks. Ajouter titre/section exige un nouveau budget tokenizer et une génération d'index
candidate ; aucun gain ne peut être annoncé sans ablation.

## G. Bibliographie scientifique

Dans le corpus desktop consulté : 7 193 articles (7 074 indexed, 119 rejected), 323 247 chunks marqués
indexed ; 3 058 articles possèdent un abstract. Notices : 6 693 accepted, 4 017 review, 13 166 rejected.
Parmi accepted, 3 553 sont marquées indexed et 3 140 not_applicable. Ces comptes ne sont ni une
mesure de pertinence ni une certification de l'index Qdrant. Deux requêtes diagnostiques plus lourdes
ont été interrompues après huit secondes ; pas de conclusion quantitative sur leurs résultats.

La collecte dispose de familles thématiques focused/expanded/specialized/materials/microbiology,
pagination, quotas, checkpoints et règles d'admission (`harvest.py`, `harvest_queries.py`, clients
fournisseurs). DOI d'abord ; versions portant des DOI différents ne sont pas fusionnées sur le titre.
Les replis titre sans DOI restent moins robustes et ne réunissent pas forcément du contenu
complémentaire. Métadonnées disponibles : année, revue, type, citations, auteurs, fournisseur ; pas
de mesure structurée universelle de taille d'échantillon, qualité méthodologique ou indépendance.

Le chatbot ne recherche pas systématiquement reviews, études primaires, travaux pivots et études
négatives comme catégories distinctes. Les contradictions dépendent du plan. Les parcours de
citations existent dans les outils de collecte et deep research, sans expansion du graphe activée par
défaut dans le chatbot. La découverte live séquence les fournisseurs et ne demande que quatre notices
par source. Les notices découvertes ne sont pas ingérées par cette étape et ne changent pas la synthèse
en cours. L'acquisition des deux full texts intervient auparavant sur les résultats locaux.

Proposition : mesurer la couverture par question/strate ; exploiter les types d'études comme signaux
transparents et non comme exclusions universelles ; traiter l'expansion de citations comme campagne
bornée et traçable, distincte d'une relance implicite du chatbot.

## H. Construction du contexte

Le filtre sémantique lit des extraits sélectionnés, pas nécessairement l'article complet. Puis
`distinct_evidence` retire seulement des sous-ensembles exacts de phrases dans un même article.
Cela ne répare pas une contradiction supprimée **plus tôt** par le sélecteur Jaccard.

`CiderEvidenceRagService._bounded_evidence` alterne les passages des articles et conserve leurs
identités. `_fit_prompt_payload` conserve tous les identifiants essentiels et ajuste uniformément
les fenêtres verbatim par recherche dichotomique sous 64 000 caractères moins la réserve de reprise.
Le wiki peut occuper 12 000 caractères. Certains champs de budget d'effort sont historiques et ne
doivent pas être présentés comme des plafonds réellement appliqués à cette étape.

Pas de résumé intermédiaire utilisé comme preuve dans le chemin normal. Une fenêtre textuelle peut
néanmoins couper une condition, une unité ou une limite ; ajouter des compteurs de caractères avant/
après et tester les informations éloignées. L'obligation de citer tous les passages A/B peut entrer en
tension avec les plafonds de 4/8/12 affirmations et multiplier les corrections : hypothèse à mesurer.

## I. Citations et traçabilité

Identité `common:<article>:chunk:<id>`, pages et texte issus de SQLite ; abstracts sans fausse page.
Les schémas de génération énumèrent les IDs autorisés. Validation des nombres, langue, pertinence,
causalité et champs visibles ; `ChatAnswerVerifier` décompose les assertions, mécanismes, définitions
et limitations, puis les confronte à leurs seuls passages cités via `ClaimVerifier`. Erreur technique
du validateur obligatoire = reprise, jamais contournement silencieux. Le rendu bibliographique et
les liens PDF sont construits par l'application.

Ce dispositif est solide mais ne prouve pas automatiquement l'implication scientifique. Un juge LLM
peut se tromper ; l'identité persistée ne garantit ni l'adéquation du passage sélectionné ni
l'exactitude de son extraction. L'évaluation experte indépendante reste nécessaire.

## J. Performance et observabilité

Neuf réponses historiques trouvées entre le 1er et le 11 septembre, six avec les principaux timings :
cinq `partial_generated`, une `abstained`, trois sans statut moderne. Versions/configurations mixtes,
pas de paire avant/après et aucune inférence causale sur les patches actuels.

| Étape historique | n | Médiane (s) | Min–max (s) |
|---|---:|---:|---:|
| Planification | 6 | 9,056 | 1,811–14,785 |
| Attente verrou | 6 | 0,000 | 0,000–0,000 |
| Recherche abstracts | 6 | 336,813 | 222,925–841,713 |
| Recherche full text | 6 | 89,053 | 47,772–274,186 |
| Fusion preuves | 6 | 0,089 | 0,035–0,269 |
| Filtre sémantique | 6 | 22,203 | 0,391–61,958 |
| Génération et contrôles associés | 6 | 169,830 | 36,791–172,024 |

Une seule trace détaillée : acquisition Qdrant 429,737 s, chargement embedding 63,213 s,
encodage 65,759 s, FTS session 88,224 s, dense batch 37,963 s. **Timings imbriqués : ne pas additionner
ces valeurs aux étapes parentes, ni additionner les médianes pour inventer un total.** Aucun timing
live externe représentatif ; aucune mesure actuelle permettant de prédire un gain de latence.

Caches : plans par question/historique/effort/modèle/wiki ; retrieval par empreinte de révision SQLite,
configuration et manifestes ; vecteurs de questions en mémoire côté full text ; lots de validation
sémantique et de claims persistés. La voie abstracts appelle directement `encode_queries`, donc elle
ne partage pas entièrement le cache vectoriel du full text. Plusieurs sessions FTS relisent le même
corpus pour abstracts d'articles et chunks. `list_articles()` agrège tous les chunks pour obtenir des
DOI. `_response` bibliographique chronomètre seulement son assemblage, pas la recherche précédente.

Les traces du chatbot agrègent les nombres par étape ; elles n'enregistrent pas la trajectoire complète
des IDs, rangs, exclusions et sous-requêtes consommées. Ajouter une trace locale opt-in contenant IDs,
hashes et raisons, sans PDF ni secrets dans les logs ; réhydrater le texte seulement pour l'audit.
Optimiser d'abord ces redondances et chargements. Ne pas ouvrir plusieurs clients Qdrant concurrents
ou paralléliser sans contrôle les fournisseurs devant rester séquentiels.

## K. Recommandations et plan d'intervention

**P0 — corrections de forte confiance.** Protéger résultats contradictoires, valeurs et conditions
contre la déduplication approximative ; rendre le plan de secours compatible avec toutes les
longueurs autorisées ; déplacer l'exclusion des doublons abstracts vers une comparaison des preuves
effectivement disponibles. Tests généraux avant correction, puis contrôle indépendant. Les deux
premières causes sont déjà reproduites ; la troisième exige son test d'intégration avant modification.

**P1 — fort gain attendu, à mesurer.** Rendre la configuration effective visible dans tout rapport ;
instrumenter chaque réduction de pool ; comparer dense global pour les deux requêtes au préfiltrage ;
préserver les concepts finaux dans le lexical ; rendre le classement moins dépendant de l'abstract
et des grades lexicaux ; compléter la signature des caches. Ne pas modifier arbitrairement le modèle
choisi dans un fichier utilisateur.

**P2 — expérimentations.** Cross-encoder actif/inactif ; poids/position des heuristiques ; hybrid vs
lexical vs dense ; original seul vs original + besoins + hypothèse ; représentation chunk + titre +
section ; couverture des Methods/Results et contradictions ; diversité indépendante des études.
Une seconde vague reste une proposition expérimentale hors défaut conversationnel.

**P3 — performance.** Réutilisation des vecteurs encodés entre collections, regroupement des lectures
SQLite et métadonnées, remplacement du scan d'articles pour dédupliquer, mesure cold/warm Qdrant,
nettoyage limité des paramètres historiques après démonstration de non-utilisation.

## L. Plan d'expérimentation et baseline

Baseline technique du workspace avant patch : Ruff format **506 fichiers conformes**, Ruff check
conforme, pytest **1 348 passed en 123,79 s**, frontend **101 tests / 29 fichiers**, lint, types et
build conformes. Cela établit l'état logiciel, pas l'exactitude scientifique des réponses.

Le projet possède déjà CiderQA : splits development/validation/final_test, gel et hashes, garde contre
fuite de labels, métriques notice/article/fragment, bootstrap, exactitude, complétude, citations,
pages, nombres, abstention et politiques de promotion. Aucun fichier de jeu réel `*ciderqa*.json`
n'a été trouvé dans les dossiers runtime/tests/docs inspectés ; `artifacts/ciderqa` est absent.
Cela ne prouve pas qu'aucun jeu externe n'existe. Ne pas inventer un score CiderQA.

Deux niveaux de comparaison :

1. **Régressions synthétiques hors réseau**, gelées avant patch : contradiction par négation,
   changement numérique, changement de condition, répétition exacte, question de 501/2 001/4 000
   caractères, et abstract pertinent doublonné avec article non sélectionné. Mesurer conservation des
   preuves attendues et succès du fallback ; ne pas appeler ces tests un benchmark scientifique.
2. **Panel scientifique initial de 12–16 questions**, preuves annotées depuis SQLite : factuelle,
   sigle ambigu, synonymes FR/EN, mécanistique, comparative, spécialiste, multi-articles,
   contradiction, récente, résultat absent du titre/abstract, Methods, Results, abstention et suivi.
   Pas de catégorie clinique artificielle hors périmètre cidricole. Constituer ensuite les 100 cas
   et l'annotation experte indépendante exigés par le protocole CiderQA existant.

Pour chaque ablation : corpus et modèle gelés, même ordre et effort, un seul facteur modifié,
cache froid/chaud séparé, récupération notice/article/chunk **avant et après chaque filtre**.
Calculer Recall/Precision@K, MRR, nDCG, hit rate, documents distincts, duplication, récupération
des contradictions ; ne pas déduire la couverture du nombre de citations. Annoter ensuite exactitude,
complétude, citations impliquantes, assertions sans preuve et limites reconnues. Mesurer temps par
étape, durée totale, RAM, tokens et requêtes fournisseur séparément.

Réutiliser `scripts.evaluate_ciderqa`, `scripts.compare_ciderqa_baselines` et les outils d'ablation
existants ; leur matrice actuelle cible deep research et doit être étendue explicitement pour les
variantes du chatbot. Le comparateur historique de baselines impose le même code : les comparaisons
de code candidat doivent enregistrer les deux empreintes et utiliser le protocole de promotion
approprié, sans maquiller deux révisions en une seule.

Aucun gain de latence ne compense une régression de rappel, d'abstention ou de fidélité des citations.
Les réglages expérimentaux ne deviennent pas les valeurs par défaut sur la seule foi de tests simulés.

## Mise en œuvre après remise du premier rapport

Le rapport A–L ci-dessus décrit la baseline. Après sa remise, quatre corrections ciblées ont été
appliquées, sans réécriture du pipeline, sans mutation du corpus et sans changement des modèles,
top-K, préfiltre dense ou budgets d'effort :

1. `app/retrieval/evidence_selection.py` expose une comparaison de phrases exactes, conservant casse,
   négation, chiffres et conditions. `EvidencePassageSelector` réutilise ce contrat au lieu du
   Jaccard. Les différences scientifiques ne sont plus considérées comme redondantes sur le seul
   vocabulaire. Le contrôle de répétition exacte reste passant.
2. Les champs internes du plan de secours supportent la question complète jusqu'à 4 000 caractères.
   Le schéma fournisseur **et** la validation locale des plans générés conservent 500 caractères
   pour une proposition et 2 000 pour l'interprétation. Cela corrige le crash du fallback, pas encore
   la sélection des termes lexicaux ni toutes les bornes du retrieval full text.
3. La recherche d'abstracts ne supprime plus une notice parce que son DOI figure ailleurs dans
   `articles`. Elle conserve les candidats retrouvés pour la déduplication et la comparaison des
   preuves disponibles. La lecture exhaustive `list_articles()` de cette étape disparaît également.
4. Version retrieval `rag-v3-conservative-evidence-selection` : les anciens résultats de sélection
   ne sont pas réutilisés. La signature inclut maintenant les paramètres `article_ranking` et
   `evidence`, avec deux tests d'invalidation ciblés. Les autres limites de cache du diagnostic
   demeurent des travaux distincts.

Le guide méthodologique consigne les critères explicites de la mission et distingue ces corrections
de résultats validés par experts. Ses 13 références d'empreinte dans `knowledge/` ont été actualisées
après vérification ; le contenu des fiches et leur niveau d'autorité n'ont pas été modifiés.

### Comparaison locale avant/après

Les 19 cas initiaux de `tests/test_scientific_recall_regressions.py` ont été exécutés avant patch :
**14 échecs, 5 succès**. Après patch, ces 19 cas passent ; deux contrôles supplémentaires du cache
portent le fichier à 21 cas. Ces données synthétiques ne sont pas des publications scientifiques.

| Contrôle synthétique | Avant | Après |
|---|---:|---:|
| Préservation de quatre passages distincts, dans chacun des quatre cas négation/résultat/condition/nombre | 3/4 | 4/4 |
| Plan de secours conservant intégralement les questions aux quatre longueurs × trois efforts | 3/12 | 12/12 |
| Abstract retrouvé conservé malgré l'identité d'un article sans preuve retenue | 0/1 | 1/1 |
| Contrôles indépendants : répétition exacte et rejet de longueur hors contrat | 2/2 | 2/2 |

La suite ciblée élargie compte **89 tests passants**. Les valeurs ci-dessus mesurent les modes de
défaillance corrigés ; aucun Recall@20 scientifique, gain de latence ou score d'exactitude globale
n'est revendiqué. Les coûts des tests ne sont pas des latences du RAG. La suppression du scan de DOI
est une réduction de travail certaine, mais son gain en secondes n'a pas été isolé.

Commande de reproduction hors réseau :

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_scientific_recall_regressions.py
```

Les prochaines ablations prioritaires restent : deux recherches denses globales vs préfiltre lexical,
préservation des concepts en fin de question, contribution du cross-encoder et poids des heuristiques.
La configuration Qwen du workspace est signalée, conservée et non déployée. Aucun index n'a été
reconstruit ; la version installée n'a pas été remplacée.

### Panel scientifique proposé pour l'annotation

Ces formulations sont des **candidats**, sans réponse de référence ni répondabilité présumée.
Pour chacune, un expert doit attribuer les identifiants SQLite, extraits, conditions, pages et
affirmations attendues avant l'inférence ; les réponses absentes du corpus deviennent des cas
d'abstention. Les catégories « récent » et « contradiction » ne garantissent pas que de telles
preuves existent. Répartir les familles entre les splits avant de créer des paraphrases.

| ID | Question candidate | Défaut ou capacité mesuré |
|---|---|---|
| Q01 | Quel acide est transformé pendant la fermentation malolactique du cidre, et quel produit est observé dans les études disponibles ? | Fait précis, fidélité chimique et citations |
| Q02 | Dans le contexte d'un moût de pomme, que signifie FML et quels critères permettent de distinguer cette transformation de la fermentation alcoolique ? | Sigle, désambiguïsation |
| Q03 | Quels effets du cuvage de la pulpe avant pressurage sont documentés sur le rendement en jus et les composés phénoliques ? | Variantes terminologiques, comparaison, plusieurs résultats |
| Q04 | Par quels mécanismes la disponibilité en azote peut-elle modifier les composés soufrés pendant la fermentation du cidre, et quelles conditions expérimentales limitent ces conclusions ? | Mécanismes vs observations, conditions |
| Q05 | Comment les études sur jus de pomme comparent-elles pectinase, filtration membranaire et collage pour réduire la turbidité ? | Comparaison de procédés, absence dans le titre |
| Q06 | Quelles publications apportent des résultats divergents sur l'effet de la température de fermentation sur les esters du cidre ? | Contradictions et différences de protocole |
| Q07 | Quelles méthodes permettent de mesurer l'azote assimilable dans un moût de pomme, et quelles interférences ou limites sont documentées ? | Passages Methods, limites analytiques |
| Q08 | Quelles études publiées entre janvier 2024 et septembre 2026 documentent les levures non-Saccharomyces dans le cidre ? | Contrainte temporelle explicite, disponibilité du corpus |
| Q09 | How do apple juice studies distinguish natural occurrence of Alicyclobacillus from inoculation, survival and inactivation experiments? | Distinction entre protocoles et occurrence |
| Q10 | Which measured changes during oak maturation of apple brandy are directly supported, and which conclusions rely on other spirits? | Matrice exacte, transposition et travail fondateur |
| Q11 | What do cider studies report about the interaction between yeast strain and fermentation temperature on aroma, including results absent from the article title? | Résultats dans le corps, interactions |
| Q12 | Compare analytical methods for patulin in apple juice, including sample preparation, detection limits and matrix effects reported by each study. | Methods, chiffres, unités et comparaison |
| Q13 | Across primary studies and reviews, what evidence links apple cultivar, pressing conditions and phenolic composition of cider? | Synthèse multi-articles et diversité des sources |
| Q14 | What exact fermentation protocol guarantees the same aroma profile for every apple cultivar and yeast strain? | Abstention ou réfutation étayée, éviter une règle universelle inventée |
| Q15 | What evidence shows that a reported effect on cider turbidity persists during storage rather than only immediately after treatment? | Temporalité et conditions finales discriminantes |
| Q16 | And what changes if the temperature is lower? | Suivi : historique autorisé = question Q11, recherche vs réutilisation |

Q11 et Q16 appartiennent à une même famille et au même split. Le panel est équilibré FR/EN ; il sert
au développement du protocole et ne remplace pas le minimum de 100 questions CiderQA ni sa double
annotation indépendante. Pour l'ablation « requête longue », ajouter aux questions de développement
un contexte pertinent et placer alternativement une même contrainte discriminante au début et à la
fin, sans changer leurs preuves attendues. Ne pas utiliser le test final pour créer ces variantes.

### Validation finale

- Python : **1 369 tests passants**, sans avertissement, en 286,70 s ; sortie complète conservée
  dans `data/exports/rag-audit-20260911/pytest-final.txt`.
- Ruff : vérification du format des 507 fichiers et analyse statique passantes.
- Frontend : **101 tests passants dans 29 fichiers** ; lint, types et build de production passants.
- Provenance du guide : les 28 tests du seed passent avec les empreintes actualisées ; ils sont
  également inclus dans la suite complète ci-dessus.
- Comparaison synthétique et empreintes des fichiers candidats :
  `data/exports/rag-audit-20260911/regression-comparison.json` (artefact local non versionné).

La durée de pytest n'est pas une mesure de performance du retrieval. Aucun gain de latence RAG
ni gain global de qualité scientifique n'est déduit de ces validations techniques.
