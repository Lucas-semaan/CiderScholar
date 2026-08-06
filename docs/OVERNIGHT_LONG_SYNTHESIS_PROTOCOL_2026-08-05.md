# Protocole nocturne de synthèses scientifiques longues

Identifiant : `CS-LONG-INTRO-01`
Fenêtre : nuit du 5 au 6 août 2026, arrêt strict à `09:00 Europe/Paris`
Planification : Sol
Exécution recommandée : `gpt-5.6-terra`, raisonnement `medium`
Statut : prêt pour un lancement manuel ; aucune boucle n'est activée par ce document

## 1. Objectif

Cette campagne ne cherche pas à optimiser séparément douze réponses. Elle doit identifier les défauts
transversaux qui empêchent CiderScholar de construire une bonne synthèse scientifique longue, puis
tester des corrections générales et réversibles.

Une bonne synthèse longue doit pouvoir :

1. commencer, lorsque cela aide réellement, par un cadrage technique sourcé ;
2. définir les termes ambigus, la matrice, le procédé et le résultat étudié ;
3. répondre directement à tous les axes documentés de la question ;
4. conserver les conditions expérimentales nécessaires à l'interprétation ;
5. distinguer observation, mécanisme démontré, hypothèse, analogie et recommandation ;
6. croiser les résultats convergents ou contradictoires ;
7. relier chaque affirmation scientifique à une preuve persistée ;
8. rester plus courte lorsque la documentation ne justifie pas un développement long.

Le résultat attendu à 09:00 est un profil de réponse général mieux étayé, ou la démonstration que le
principal défaut se situe avant la génération : planification, récupération ou sélection des preuves.

## 2. Portée et interdictions

Conditions communes à tous les profils :

- corpus local inchangé ;
- sources externes désactivées ;
- analyse de figures désactivée, sauf décision séparée et explicitement journalisée ;
- mode `quick` pour le chatbot ;
- une conversation neuve par couple `question × profil` ;
- un seul job utilisateur actif ;
- température initiale `0.1` ;
- aucun secret dans YAML, les prompts, les commandes ou les journaux ;
- aucun contenu intégral de PDF copié dans le journal ;
- aucune migration, réindexation, dépendance, suppression de données, publication, merge ou push ;
- aucun réglage spécifique à une question dans un prompt candidat ;
- aucune consultation des labels du jeu CiderQA final.

Les modifications ciblées du backend nécessaires à l'instrumentation ou à l'exposition de profils
expérimentaux sont autorisées. Elles doivent préserver tous les changements déjà présents dans le
workspace, rester non commitées et être intégralement décrites dans le rapport final.

## 3. Définition d'un cadrage technique autorisé

Une introduction est utile lorsqu'elle accomplit au moins une fonction scientifique précise :

- définir un terme potentiellement ambigu ;
- délimiter la matrice, l'étape du procédé, les conditions et le résultat étudié ;
- distinguer deux phénomènes voisins qu'il serait dangereux de confondre ;
- fournir le cadre mécanistique indispensable à la lecture des résultats ;
- distinguer preuve directe, mécanisme plausible et analogie ;
- annoncer une lacune documentaire structurante.

Toute affirmation factuelle de l'introduction doit être reliée à une preuve fournie à ARGO. Une
définition non démontrée par le corpus peut seulement être présentée comme définition opérationnelle
du terme employé dans la réponse, jamais comme fait scientifique universel.

L'introduction représente normalement 10 à 20 % de la réponse et ne dépasse jamais 25 %. Elle peut
être réduite à deux phrases ou omise lorsque les preuves sont insuffisantes.

Sont interdits ou pénalisés :

- histoire générale du cidre, du Calvados ou de la fermentation ;
- importance culturelle, économique ou sanitaire générique ;
- définitions de manuel absentes des preuves ;
- listes de microorganismes, molécules ou procédés sans lien démontré avec la question ;
- préambule qui reformule longuement la question sans la préciser ;
- analogie avec vin, bière, whisky ou autre matrice non signalée comme indirecte ;
- mécanisme plausible présenté comme démontré ;
- recommandation pratique ou sanitaire prématurée ;
- mention d'ARGO, du RAG, du validateur, des identifiants internes ou du prompt ;
- texte ajouté uniquement pour atteindre une longueur cible.

Question de contrôle : si le paragraphe d'introduction disparaît, la compréhension scientifique de la
réponse devient-elle moins précise ? Si non, il est probablement décoratif.

## 4. Panel et axes de notation

Les axes ci-dessous servent uniquement à noter la couverture. Ils ne sont jamais injectés dans les
questions envoyées à CiderScholar.

| ID | Question | Axes attendus pour la couverture |
| --- | --- | --- |
| Q1 | Impact du cuvage des pommes broyées avant pressurage | définition du cuvage ; conditions ; extraction et oxydation ; propriétés du jus ; fermentation et cidre final |
| Q2 | Microorganismes les plus thermorésistants à la pasteurisation | organisme et état physiologique ; matrice ; paramètres thermiques ; mesure de résistance ; portée sanitaire |
| Q3 | Température, durée de stockage et stabilité protéique | température ; durée ; dénaturation ou agrégation ; turbidité ; facteurs confondants |
| Q4 | Extraction des polyphénols au pressurage | variété et maturité ; broyage ou cuvage ; oxygène et enzymes ; température et durée ; pression ou cycle ; rendement et composition |
| Q5 | Oxygénation du moût, fermentation et arômes | dose et moment ; levures ; cinétique ; redox ; familles aromatiques ; risques d'oxydation |
| Q6 | Trouble protéique des jus clarifiés | protéines ; polyphénols ; pH, ions et chaleur ; agrégation ; distinction d'autres troubles |
| Q7 | Souches de *S. cerevisiae* et composés volatils | effet souche ; familles de volatils ; conditions de fermentation ; interactions matrice × souche ; reproductibilité |
| Q8 | Fermentation malolactique | transformation malique/lactique ; acidité ; arômes ; microorganismes ; stabilité et compromis |
| Q9 | Limitation des amines biogènes | précurseurs ; microorganismes ; conditions ; prévention ; suivi ; prudence sanitaire |
| Q10 | Grain, chauffe et âge du bois | propriétés du bois ; extraction et réactions ; oxygénation ; effets sensoriels ; preuve directe versus analogie |
| Q11 | Micro-oxygénation du Calvados | dose et durée ; ellagitanins ; couleur ; arômes ; oxydation ; preuve Calvados directe |
| Q12 | Diagnostic différentiel des troubles | hypothèses concurrentes ; tests orthogonaux ; témoins ; séquence décisionnelle ; interprétation causale |

### Répartition figée

- Lot découverte : `Q1`, `Q2`, `Q4`, `Q5`, `Q10`, `Q12`.
- Lot confirmation : `Q3`, `Q6`, `Q7`, `Q8`, `Q9`, `Q11`.
- Sentinelles de répétabilité : `Q2`, `Q4`, `Q10`, `Q12`.

Terra peut lire les réponses détaillées du lot découverte. Avant le choix d'un candidat, il ne doit
consulter que les scores agrégés du lot confirmation.

## 5. Questions originales

Les formulations suivantes sont immuables :

1. Quel est l'impact du cuvage des pommes broyées avant pressurage sur le jus et le cidre final ?
2. Quels microorganismes sont les plus thermorésistants lors de la pasteurisation du jus de pomme ?
3. Comment la température et la durée de stockage modifient-elles la stabilité protéique du jus de pomme ?
4. Quels facteurs déterminent l'extraction des polyphénols pendant le pressurage des pommes à cidre ?
5. Comment l'oxygénation du moût influence-t-elle la fermentation alcoolique et les arômes du cidre ?
6. Quels mécanismes provoquent le trouble protéique dans les jus de pomme clarifiés ?
7. Quel est l'effet des différentes souches de *Saccharomyces cerevisiae* sur les composés volatils du cidre ?
8. Comment la fermentation malolactique modifie-t-elle l'acidité, les arômes et la stabilité microbiologique du cidre ?
9. Quels paramètres permettent de limiter la production d'amines biogènes pendant la fermentation cidricole ?
10. Comment le grain, la chauffe et l'âge du bois influencent-ils l'élevage des eaux-de-vie de cidre ?
11. Quels effets la micro-oxygénation produit-elle sur les ellagitanins, la couleur et les arômes du Calvados ?
12. Comment distinguer expérimentalement un trouble causé par les protéines, les polyphénols, les pectines ou les microorganismes ?

## 6. Profils expérimentaux

L'ordre est imposé. Un profil n'est exécuté sur le lot confirmation que s'il franchit les seuils du
lot découverte.

### P0 — Baseline

- question originale seule ;
- réponse simple : 4 096 tokens ;
- brouillon de facette : 4 096 tokens ;
- assemblage facetté : 6 144 tokens ;
- limites actuelles de statements et prompt actuel.

### P1 — Cadrage technique seul

Budgets identiques à P0. Ajouter exactement :

> Si les preuves le permettent, commence par un bref cadrage technique directement utile à la
> question : définis les termes ambigus, précise la matrice et l'étape du procédé, et distingue les
> mécanismes démontrés, les hypothèses et les analogies. Chaque affirmation factuelle de ce cadrage
> doit être soutenue par les sources fournies. N'ajoute aucune généralité encyclopédique, historique
> ou contextuelle non nécessaire. Si le cadrage n'est pas documenté, indique sobrement cette limite
> puis réponds directement.

Seul le cadrage change entre P0 et P1.

### P2 — Demande de synthèse longue

Reprendre mot pour mot P1 et ajouter :

> Après ce cadrage, développe une synthèse scientifique approfondie couvrant tous les axes réellement
> documentés, les conditions expérimentales, les résultats convergents ou contradictoires et les
> limites de transposition. Vise environ 900 à 1 400 mots seulement si les preuves permettent au moins
> six affirmations distinctes et utiles. Sinon, reste plus court : ne répète pas, ne dilue pas et ne
> complète jamais la longueur par des connaissances non sourcées.

Les budgets restent ceux de P0. Seule la demande de développement long change entre P1 et P2.

### P3 — Budget adaptatif

Conserver mot pour mot P2 et modifier uniquement les plafonds :

- question mono-axe : 4 096 tokens, au plus 8 statements ;
- deux axes : 6 144 tokens, au plus 12 statements ;
- trois ou quatre axes : 8 192 tokens, au plus 16 statements ;
- brouillon de facette : toujours 4 096 tokens.

P3 n'est testé que si P2 montre au moins un des signaux suivants :

- `finish_reason=length` ;
- relance demandant une réponse plus brève ;
- consommation supérieure à 85 % du plafond ;
- limite de statements atteinte ;
- preuves et axes présents mais réponse finale incomplète.

Augmenter seulement `argo.max_output_tokens` est interdit : le plafond global vaut déjà 8 192 et les
appels du chatbot sont bornés séparément.

### P4 — Cadrage général candidat

P4 est facultatif. Il remplace uniquement le texte de cadrage de P1/P2 par une règle générale plus
courte, proposée après l'analyse transversale.

P4 n'est admissible que si le même défaut apparaît sur au moins trois questions appartenant à deux
familles. Le texte ne doit contenir aucun terme propre à l'une des douze questions.

### Ordre des variables après P3

Si le défaut principal n'est pas la longueur, tester une seule famille à la fois :

1. prompt de synthèse finale si les preuves sont présentes mais mal assemblées ;
2. prompt de planification si des axes sont oubliés avant la recherche ;
3. fenêtre de preuves si un passage utile est évincé ;
4. nombre d'articles ou `passages_per_article` si la couverture documentaire manque ;
5. poids lexical, vectoriel et reranker si une erreur de récupération est démontrée ;
6. température en dernier.

Ne jamais changer récupération et génération dans le même candidat.

## 7. Taxonomie des défauts

### Introduction

- `I-NONE` : cadrage utile et documentable absent ;
- `I-ENCYCLO` : généralités encyclopédiques ;
- `I-UNSOURCED` : définition ou fait non soutenu ;
- `I-SCOPE` : matrice, procédé ou temporalité mal délimités ;
- `I-MECHANISM` : mécanisme supposé présenté comme établi ;
- `I-ANALOGY` : preuve indirecte non signalée ;
- `I-DUPLICATE` : introduction répétée dans le corps ;
- `I-DISPROPORTIONATE` : introduction trop longue ;
- `I-DECORATIVE` : préambule exact mais inutile.

### Synthèse

- `S-MISSING-AXIS` : axe documenté omis ;
- `S-CONDITION-LOSS` : température, durée, dose, souche ou matrice perdue ;
- `S-CAUSALITY` : causalité excessive ;
- `S-CONTRADICTION` : désaccord documentaire masqué ;
- `S-TRANSFER` : transposition abusive ;
- `S-CITATION` : citation inadéquate ou affirmation sans preuve ;
- `S-PADDING` : remplissage ou répétition ;
- `S-POOR-ABSTENTION` : lacune masquée ou refus excessif ;
- `S-SAFETY` : recommandation sanitaire non étayée.

### Pipeline

- `P-PLAN-MISSING-AXIS` ;
- `P-RETRIEVAL-MISS` ;
- `P-SEMANTIC-FALSE-REJECT` ;
- `P-EVIDENCE-WINDOW-LOSS` ;
- `P-TRUNCATED` ;
- `P-INVALID-JSON` ;
- `P-VALIDATION-RETRY` ;
- `P-QUOTA` ;
- `P-TIMEOUT` ;
- `P-UNSTABLE`.

## 8. Notation sur 100

| Dimension | Points |
| --- | ---: |
| Fidélité aux preuves et aux citations | 25 |
| Couverture des axes documentés | 20 |
| Qualité du cadrage technique | 20 |
| Conditions, causalité, incertitude et transposition | 15 |
| Organisation et densité scientifique | 10 |
| Abstention et limites | 5 |
| Robustesse opérationnelle | 5 |

Sous-score cadrage, 20 points :

- pertinence technique : 0–4 ;
- ancrage dans les preuves : 0–4 ;
- délimitation du périmètre : 0–4 ;
- distinction mécanisme, hypothèse et analogie : 0–4 ;
- proportion et transition vers la réponse : 0–4.

Mesures séparées :

- pourcentage d'axes couverts ;
- affirmations distinctes et étayées ;
- affirmations non étayées ;
- proportion de preuves directes ;
- citations traçables ;
- longueur en mots ;
- prompt et completion tokens ;
- introduction en pourcentage de la réponse ;
- répétitions substantielles ;
- durée et nombre réel d'appels ARGO ;
- erreurs, retries et attente de quota.

Le nombre de mots n'apporte aucun point. Calculer aussi la densité : affirmations distinctes et
étayées pour 1 000 tokens de sortie.

### Garde-fous éliminatoires

Rejeter immédiatement un candidat qui produit :

- citation, page, DOI, chiffre ou résultat inventé ;
- affirmation sanitaire non étayée ;
- transposition non signalée entre matrices ou procédés ;
- fait central non sourcé dans l'introduction ;
- baisse de la traçabilité des citations ;
- fuite de données, secret ou contenu intégral de PDF ;
- deux échecs de validation consécutifs.

Renforcer la vérification de Q2, Q9, Q10 et Q11.

## 9. Promotion et arrêt d'un profil

### Accès au lot confirmation

Exécuter le lot confirmation seulement si, sur le lot découverte :

- gain moyen d'au moins 5 points contre le profil précédent ;
- au moins quatre questions sur six non dégradées ;
- gain de cadrage sur au moins trois questions ;
- aucun garde-fou violé ;
- pas de hausse supérieure à 30 % des tokens sans gain de couverture.

### Promotion finale

- gain médian global d'au moins 5 points ;
- au moins huit questions sur douze non dégradées ;
- aucune famille scientifique en baisse de plus de 2 points ;
- cadrage ≥ 16/20 sur au moins neuf questions où il est utile ;
- zéro `I-UNSOURCED`, `S-SAFETY` ou `S-TRANSFER` grave ;
- couverture en hausse d'au moins 10 points ou affirmations utiles en hausse d'au moins 15 % ;
- répétition substantielle inférieure à 15 % ;
- coût supplémentaire proportionné au gain.

P2 ou P3 est rejeté si la longueur augmente sans gain de couverture, d'affirmations utiles ou de
discussion des limites.

## 10. Journal persistant

Racine recommandée :

`data/exports/overnight-long-synthesis-20260805/`

Contenu :

- `protocol-copy.md` : copie exacte du présent protocole ;
- `state.json` : état atomique et budget restant ;
- `events.jsonl` : journal append-only canonique ;
- `night-report.md` : rapport relu avant chaque décision ;
- `profiles/` : profils et hash de prompts ;
- `responses/` : capture JSON complète de chaque conversation ;
- `logs/` : sorties API, worker et tests ;
- `patches/` : diff initial et diff des changements nocturnes.

Chaque cycle enregistre :

- `run_id`, profil, QID et timestamps UTC/local ;
- commit Git et état initial des fichiers modifiés ;
- conversation, job et `client_request_id` ;
- question et instruction de profil exactes ;
- réponse complète et métadonnées `messages[].response` ;
- sources, pages, avertissements et brouillons de facettes ;
- tokens, durée, nombre d'appels et erreurs ;
- scores bruts, tags, décision et hypothèse suivante.

Le GET conversation est la capture d'autorité. L'export Markdown est destiné à la lecture humaine et
ne remplace pas le JSON, car il omet les métadonnées structurées de `response_json`.

## 11. Préflight opérationnel

Dans `C:\Users\lsemaan\Documents\ciderscholar v2` :

```powershell
$cfg = (Resolve-Path .\config.yaml)
.\.venv\Scripts\python.exe -c "from app.config import load_settings; s=load_settings(r'$cfg'); print(s.paths.database_path); print(s.paths.common_database_path); print(s.argo.model); print(s.argo.max_output_tokens)"
```

Vérifier manuellement que les chemins affichés désignent le corpus voulu. Ne jamais imprimer la clé
ARGO. Enregistrer `git status --short --branch` et `git rev-parse HEAD` dans le journal, sans reset,
stash, checkout destructif ou réécriture des changements existants.

Avant de démarrer :

1. fermer le CiderScholar de bureau ou arrêter son worker ;
2. vérifier qu'aucun autre worker ne peut prendre les jobs avec un profil différent ;
3. créer un `config.user.yaml` minimal et réversible à côté de `config.yaml` ;
4. fixer `app.chat_worker_concurrency: 1`, `argo.temperature: 0.1` et
   `argo.max_output_tokens: 8192` ;
5. vérifier que les profils nocturnes ne contiennent aucun secret ;
6. tester `GET /health`, `GET /health/llm` et `GET /api/diagnostics/readiness`.

Exemple minimal :

```yaml
app:
  chat_worker_concurrency: 1

argo:
  temperature: 0.1
  max_output_tokens: 8192
  request_timeout_seconds: 300
```

## 12. Lancement contrôlé

Créer deux stop-files distincts. Dans un premier terminal :

```powershell
$cfg = (Resolve-Path .\config.yaml)
$apiStop = (Join-Path (Split-Path $cfg) "data\runtime\overnight-api.stop")
Remove-Item -LiteralPath $apiStop -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m scripts.run_api --config $cfg --stop-file $apiStop
```

Dans un second terminal :

```powershell
$cfg = (Resolve-Path .\config.yaml)
$workerStop = (Join-Path (Split-Path $cfg) "data\runtime\overnight-worker.stop")
Remove-Item -LiteralPath $workerStop -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m scripts.run_job_worker --config $cfg --chat-concurrency 1 --stop-file $workerStop
```

Contrôler :

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/health/llm
Invoke-RestMethod http://127.0.0.1:8000/api/diagnostics/readiness
```

Le changement de profil se fait uniquement lorsqu'aucun job n'est actif. Arrêter proprement le
worker, modifier le profil local, puis le relancer. Redémarrer aussi l'API lorsque sa vue des réglages
doit rester cohérente. Aucun rebuild frontend et aucune réindexation ne sont requis.

## 13. Soumission, attente et capture

Créer une conversation neuve par couple profil/question, soumettre le job avec
`use_external_sources=false`, `analyze_figures=false`, `mode=quick` et
`interaction_mode=research`.

Attendre le job avec le backoff 1 s, 1,5 s, 2,5 s, 4 s, puis 5 s jusqu'à un état terminal. Après une
erreur réseau, rechercher le job existant avec le même identifiant ; ne jamais soumettre un doublon
avec un nouveau `client_request_id` par réflexe.

Après succès, appeler :

`GET /api/chatbot/conversations/{conversation_id}`

et enregistrer le JSON avec une profondeur suffisante. Capturer notamment `messages[].response`, les
sources, warnings, tokens, durée et `facet_drafts`.

Deux suivis diagnostiques peuvent être envoyés dans la même conversation en
`interaction_mode=conversation` :

> À partir uniquement des mêmes sources, vérifie si la réponse distingue correctement le périmètre,
> les conditions, les mécanismes, les résultats et les limites. Réécris-la sans ajouter de nouvelle
> source et sans généralité non étayée.

> Indique les dimensions de la question que les sources disponibles ne permettent pas de couvrir.
> N'extrapole pas pour combler ces lacunes.

Ces suivis servent à localiser le défaut. Ils ne remplacent pas la réponse initiale pour la notation
du profil.

## 14. Discipline d'amélioration

Après chaque lot, Terra produit uniquement :

1. les trois défauts transversaux les plus fréquents ;
2. leur répartition entre les familles ;
3. l'étape probable du pipeline ;
4. une hypothèse unique ;
5. la prochaine variable autorisée par le protocole.

Avant d'appliquer un candidat :

- enregistrer le diff et le hash du profil ;
- modifier une seule variable conceptuelle ;
- exécuter les tests ciblés ;
- confirmer qu'aucun fichier utilisateur sans rapport n'a changé ;
- ne jamais modifier un profil pendant un job actif.

Comparaisons A/B : présenter les réponses à Terra sous des labels aléatoires `A` et `B` sans exposer
le profil, puis révéler les profils après la notation.

## 15. Tests

Pour un changement de configuration uniquement :

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_config.py tests/test_argo_client.py tests/test_job_worker_cli.py tests/test_job_worker.py tests/test_chatbot.py
```

Pour un prompt, un plafond codé, la planification ou la validation :

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_query_planning.py tests/test_semantic_filter.py tests/test_pilot_rag.py tests/test_chatbot.py
```

Ne pas reconstruire le frontend à chaque cycle. À partir de 08:15, exécuter les validations finales :

```powershell
.\.venv\Scripts\python.exe -m ruff format --check app scripts tests
.\.venv\Scripts\python.exe -m ruff check app scripts tests
.\.venv\Scripts\python.exe -m pytest -q
npm.cmd --prefix frontend run ci
```

Un candidat dont les tests ciblés échouent n'est pas exécuté contre ARGO.

## 16. Quotas et circuit breakers

Limites locales : 20 requêtes/minute, 120/heure, 200/3 heures.

Garde-fous nocturnes :

- une seule question utilisateur active ;
- au plus huit questions par heure ;
- au plus dix-huit questions sur trois heures ;
- respecter `retry_at` sans relance prématurée ;
- au plus une relance externe pour un même objectif ;
- arrêter un profil après deux violations scientifiques éliminatoires ;
- arrêter après deux familles en régression ;
- arrêter après deux réponses consécutives en échec de validation ;
- arrêter l'exploration après deux candidats sans gain transversal ;
- ne pas lancer une confirmation qui ne peut finir avant 08:15.

La délégation MCP ARGO n'est pas une dépendance. Si elle devient disponible, une seule délégation peut
être active dans le workspace et une seule relance est permise pour un même objectif.

## 17. Cadence

- 19:30–21:00 : P0 sur les douze questions.
- 21:00–21:30 : score et taxonomie P0.
- 21:30–22:30 : P1 sur le lot découverte.
- 22:30–23:00 : décision P1.
- 23:00–00:15 : P1 sur le lot confirmation si admissible.
- 00:15–00:45 : synthèse transversale et refroidissement quota.
- 00:45–02:15 : P2 sur le lot découverte.
- 02:15–02:45 : décision P2.
- 02:45–04:15 : P2 sur le lot confirmation si admissible.
- 04:15–05:00 : refroidissement et choix du meilleur profil.
- 05:00–06:15 : P3 ou P4 sur le lot découverte, uniquement si justifié.
- 06:15–08:15 : répétition des quatre sentinelles avec le meilleur profil et consolidation du rapport.
- 08:15 : gel absolu des candidats ; terminer seulement le travail déjà engagé.
- 08:30 : aucune nouvelle question ; validations, bilan et arrêt coopératif.
- 08:55–09:00 : vérification terminale, stop-files et confirmation qu'aucun job nocturne ne reste actif.

En cas de démarrage tardif, supprimer P3/P4 en premier, puis réduire les répétitions. Ne jamais
supprimer P0 ni le lot confirmation du profil retenu.

## 18. Livrables à 09:00

- configuration et profils exacts ;
- journal JSONL et captures structurées ;
- tableau P0/P1/P2/P3/P4 par question et famille ;
- taxonomie et fréquence des défauts ;
- comparaison tokens, durée, couverture, citations et densité ;
- meilleur profil reproductible ou conclusion explicite qu'aucun profil n'est promouvable ;
- diff des changements et résultats des tests ;
- incidents, consommation ARGO et raison de l'arrêt ;
- recommandation humaine `GO`, `NO-GO` ou `À REVOIR`.

À 09:00 au plus tard, créer les stop-files attendus, attendre l'arrêt propre des processus et ne laisser aucun job
actif. Aucun merge, commit, push ou déploiement automatique.

## 19. Prompt de lancement manuel pour Terra medium

Créer une tâche locale dans ce projet avec `gpt-5.6-terra`, raisonnement `medium`, puis envoyer :

> Lis intégralement `docs/OVERNIGHT_LONG_SYNTHESIS_PROTOCOL_2026-08-05.md` et exécute le protocole
> `CS-LONG-INTRO-01` jusqu'à son état terminal ou jusqu'à 09:00 Europe/Paris. Commence par le préflight
> et ne lance aucune requête ARGO avant d'avoir vérifié la configuration, l'absence de worker
> concurrent, les journaux et les tests ciblés. Préserve toutes les modifications utilisateur déjà
> présentes. Tu peux effectuer les changements backend ciblés et réversibles explicitement autorisés
> par le protocole, mais tu ne dois ni réindexer, ni migrer, ni supprimer, ni merger, ni committer, ni
> pousser. Une seule variable conceptuelle change par candidat. Respecte les quotas et les circuit
> breakers. Journalise chaque réponse et relis le journal avant toute décision. Gèle les changements à
> 08:15, n'envoie plus de nouvelle question après 08:30, exécute les validations finales, arrête
> proprement les processus et livre le rapport avant 09:00. Si un blocage empêche un profil, continue
> avec le meilleur profil testable et consigne la
> limite ; ne demande une intervention humaine que pour une action qui élargirait réellement le
> périmètre autorisé.
