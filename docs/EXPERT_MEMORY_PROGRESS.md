# Mémoire experte — progression et reprise

Mise à jour : 24 septembre 2026. Révision de départ : `676684a`.

Demande utilisateur : roadmap détaillée, puis « commence à appliquer ce que tu peux sans supervision ».
Cette autorisation couvre le développement local et les vérifications autonomes. Elle ne constitue
pas une revue scientifique des nouvelles règles. Aucune règle nouvelle n'a été présentée comme une
consigne utilisateur acceptée dans le guide méthodologique.

## Résultat de cette étape

Le socle hors ligne comprend les contrats fermés, la configuration désactivée, le stockage de versions
candidates, le parseur borné, le graphe de dépendances, 13 fiches initiales et un routeur de
prévisualisation. Trois commandes permettent de vérifier et d'inspecter ce résultat ; voir
[knowledge/README.md](../knowledge/README.md).

Le chatbot n'est pas encore branché sur cette mémoire. `off`, `shadow` et `active` sont des valeurs
de configuration reconnues ; aucune ne déclenche actuellement l'injection de fiches en production.
Le routeur autonome refuse `active`. Aucun modèle, corpus, index, conversation ou fournisseur réel
n'a été utilisé pour les nouvelles fonctionnalités. Aucun appel Argo, entraînement, promotion ou
activation n'a été effectué sur une base ou un corpus réel ; les transitions d'activation sont
couverts uniquement par des bases temporaires de test.

## Audit des prochains blocages — 24 septembre 2026

La prochaine tranche de code autonome n'est pas autorisée par les contrats actuels :

- le lot 12 attend encore l'exécution d'un pilote shadow réel, des observations expertes et une
  adjudication externe liée au hash exact de l'audit ;
- les lots 14 (`evidence_note`) et 15 (clarification bornée) sont conditionnels au résultat de ce
  pilote et, pour le lot 15, à une décision de méthode explicite ; ils ne doivent pas être inventés
  depuis des fixtures synthétiques ;
- l'activation de la mémoire reste refusée en production : le routeur autonome refuse `active`, et
  les activations précédemment testées concernent uniquement des bases temporaires ;
- les validations externes de la roadmap générale (ARGO réel, SharePoint, profils Windows 8/16 Go,
  groupe pilote et CiderQA réel) restent des actions d'équipe, non exécutables depuis ce dépôt sans
  créer de fausse preuve.

La prochaine action admissible est donc de recueillir ces rapports datés et leurs hashes, puis de
reprendre les corrections de code uniquement sur les défauts observés. Les commandes techniques et
le parcours local de distribution sont prêts pour cette reprise.

Pour rendre cette reprise opérable, `scripts/import_expert_pilot_observations.py` valide un lot JSON
fourni par l'équipe et, avec `--apply`, persiste les observations une par une en réutilisant les
cas déjà enregistrés. La commande ne lance ni LLM, ni recherche, ni activation ; elle constitue un
adaptateur technique, pas une source d'observations ou d'autorité.

## Reprise du 24 septembre 2026

Le lot 05 est commencé avec un premier socle isolé : `app/knowledge/trace.py` définit un manifeste
fermé et borné pour une tentative, ses candidats, budgets, preuves référencées par hashes, liens
affirmation → preuve, sortie et coût. Les migrations 47 à 53 ajoutent respectivement
`expert_run_manifests`, `expert_corrections`, leur historique de révisions et les diagnostics
persistés dans la base
applicative, sans modifier la migration 35 ni activer la mémoire. Des tests couvrent le hash canonique,
le refus d'identifiants de preuve dupliqués et la création de la table.

Ce socle est maintenant branché à l'enqueue, aux checkpoints du worker, à
`persist_result_and_succeed`, aux échecs et aux annulations. Il reste toutefois une traçabilité
partielle : les frontières de candidats sont couvertes progressivement et le routage/budget
complet de la mémoire experte n'est pas encore activé. Les détails de schéma et les identifiants
restent locaux ; aucun texte scientifique n'est copié dans le manifeste.

Validation de cette reprise : Ruff format/lint sur `app`, `scripts` et `tests` conformes ; 1 582 tests
backend réussis ; CI frontend réussie avec 108 tests et build de production. Ces résultats incluent
les changements déjà présents dans le dépôt et ne constituent pas une mesure CiderQA.

Le repository expose désormais `save_trace_checkpoint` : le checkpoint est refusé si le worker, le
lease ou la tentative ne correspondent pas. `persist_result_and_succeed` accepte aussi un manifeste
final et lie son identifiant au message assistant dans la transaction de succès. Le test ciblé couvre
checkpoint valide, worker périmé et clôture atomique ; le résultat est 28 tests réussis avec le socle
de migration.
Le contexte `JobProgressContext` expose désormais ce checkpoint sans ouvrir de connexion SQLite dans
un handler.

La frontière d'autorité du lot 11 est maintenant persistée : une revue humaine doit correspondre au
hash exact du candidat et au hash exact d'un rapport `passed`, puis fait passer le candidat vers
`approved`, `rejected` ou `needs_expert`. La migration 54 ajoute les revues append-only et les reçus
d'activation. L'activation vérifie la décision explicite, la génération active et la filiation avec
la release courante, met à jour le pointeur et les états dans une transaction, et est idempotente.
Un rapport `inconclusive` ne peut pas être approuvé ni activé. La migration 55 ajoute un retour
arrière explicite et idempotent vers l'unique release parente de la candidate active ; la génération,
les hashes, la raison et les états `superseded`/`eligible` sont enregistrés dans un reçu append-only.

La revue locale dispose maintenant d'une projection API bornée (`GET /api/expert-memory/candidates`)
et d'un écran frontend dédié. L'écran n'affiche pas les conversations, exige le hash du rapport
`passed`, sépare la décision de l'activation et ne propose cette dernière qu'après une approbation
persistée. Les erreurs, l'état vide et le rechargement sont explicités ; le serveur reste l'autorité
pour les contrôles de hash et de génération.

Le handler de chat crée maintenant le manifeste initial sous lease et construit le manifeste final à
partir des citations effectivement produites. Les preuves de citation portent les hashes du texte
source et du texte présenté ; une réponse héritée sans ces hashes est marquée `incomplete` plutôt que
reconstituée. Une reprise recharge le même `run_id` pour la tentative, et 150 tests ciblés couvrant
worker, repository, chatbot, RAG et mémoire experte sont verts.
Les frontières retrieval, fusion et filtre exposent maintenant un flux privé de candidats identifiés
(hash, rang, score borné, étape, décision et motif) vers le manifeste ; les éléments effectivement
présentés au contexte final restent également enregistrés. Ce flux est borné à 300 éléments, exclu
des réponses HTTP et conservé dans le checkpoint de reprise. Les candidats rejetés dans les moteurs
lexical/vectoriel sont maintenant conservés lorsqu'ils dépassent la limite de fusion ; les résultats
dépassent la limite de fusion ou sont écartés par le filtre de variante lexicale ; ces deux voies
portent un motif stable dans le manifeste privé.
Le ranking hybride expose désormais les chunks entrants avec leur hash de texte et leur décision ;
la course de démarrage entre le scheduler bibliographique et la première configuration admin a aussi
été supprimée par un délai initial conforme à son intervalle périodique.
Le manifeste reçoit également les empreintes du corpus avant et après l'exécution lorsqu'elles sont
disponibles ; leur absence est conservée comme telle et ne bloque pas une réponse scientifique.
Il enregistre aussi le hash du manifeste latéral d'index lorsqu'il est présent, sans ouvrir l'index
vectoriel ; hors mode `shadow`, les budgets et éléments de routage restent volontairement absents
tant que la mémoire experte n'est pas injectée par le lot 06.
Le mode `shadow` calcule désormais la décision déterministe de la release épinglée et persiste ses
éléments sélectionnés, les décisions de routage (identifiant et motif) ainsi que les caractères de
contexte par étape ; il n'injecte aucune instruction dans le prompt et ne change pas le retrieval.
Le mode `active` reste désactivé pour ce chemin tant que l'intégration du lot 06 n'est pas validée.
Les transitions `failed` et `cancelled` clôturent maintenant le manifeste et son `result_message_id`
dans la même transaction que le job ; ce contrôle est couvert par les tests de repository.
Le checkpoint initial peut aussi être écrit sous lease après une demande d'annulation, avant que la
borne sûre ne la reconnaisse ; l'annulation ne laisse donc plus un job de chat sans manifeste.
Les jobs de chat portent aussi un `ExpertMemoryPin` interne ; l'enqueue résout l'eligible active et
révérifie son ID/hash et la recette dans la transaction, tandis que les anciens payloads utilisent
implicitement `off`.

Le lot 07 est maintenant amorcé côté backend : `POST /api/chatbot/messages/{id}/expert-corrections`
valide que la cible est une réponse assistant et, si fourni, que le manifeste, le hash de réponse,
les claims, les preuves et le texte sélectionné correspondent aux données persistées. La soumission
est privée, idempotente par `client_request_id`, et reste `diagnosis_incomplete` sans manifeste.
Les routes locales de liste et détail sont disponibles ; `PATCH /api/expert-memory/corrections/{id}`
ajoute une nouvelle révision avec contrôle optimiste `expected_revision`, retrait explicite et replay
idempotent. La table `expert_correction_revisions` conserve les payloads et statuts de chaque version.
Le chatbot affiche maintenant « Proposer une correction » après chaque réponse assistant. Le dialogue
frontend est accessible, valide les champs et le passage sélectionné, distingue portée ponctuelle et
réutilisable, neutralise le double clic pendant l'envoi et expose les états erreur/succès. L'appel
reste centralisé dans `frontend/src/lib/api.ts`; les erreurs structurées du backend conservent leur
message français. Le lot reste volontairement non génératif : aucun diagnostic, appel LLM, candidat
de connaissance ou activation n'est déclenché par ces routes.

Le lot 08 est maintenant livré techniquement par une grille D1 déterministe : manifeste absent ou invalide →
`insufficient_trace`, hash de réponse divergent → `source_changed`, exécution interrompue →
`runtime_failure`, rejet sémantique ou omission précoce → signal incertain à revoir. Les diagnostics
sont immuables, liés à la révision de correction, hashés, rejouables sans doublon et visibles dans le
détail local d'une correction. Ils ne concluent jamais à une lacune de corpus sur la seule absence d'un
candidat et n'appellent aucun LLM.

Le diagnostic déterministe est maintenant exposé par un job durable `expert_improvement` : la
migration 51 étend le contrat fermé des jobs sans perdre les jobs bibliographiques existants, l'API
`POST /api/expert-memory/corrections/{id}/diagnosis-jobs` met en file une opération idempotente sans
appel LLM (`max_llm_requests=0`), et le worker persiste le diagnostic sans créer de message de
conversation. Les conflits de `client_request_id`, la reprise, l'absence de manifeste et le replay
immuable sont couverts par des tests ciblés. Le job reste privé et aucun diagnostic ne conclut
automatiquement à une lacune du corpus.

Le lot 09 est maintenant livré techniquement par la migration 52 et `ExpertCandidateCompiler`. Un `CandidatePatch` ne peut
être compilé que depuis un diagnostic `supported` dont le hash et les cibles correspondent ; les
opérations sont limitées à trois éléments, vérifient les hashes de base et passent par la validation
du paquet existante. Le résultat est une release `candidate` immuable, idempotente et séparée du
pointeur actif ; aucune compilation ne modifie le corpus, le code Python ou la release active. La
compilation est aussi disponible comme opération durable `expert_improvement`, avec replay par
`client_request_id`, et la route `POST /api/expert-memory/diagnoses/{id}/candidate-jobs` renvoie
un job `202` sans appel LLM. La lecture locale d'un candidat expose ses métadonnées et les éléments
de sa release candidate ; la transition vers `evaluating` est atomique et un double démarrage est
refusé. Les états `inconclusive` et `evaluation_failed` restent réservés aux étapes d'évaluation.
Les routes d'enqueue candidat et évaluation exigent désormais le profil local administrateur côté
serveur, comme les routes de revue, activation et rollback ; un appel direct sous profil utilisateur
est refusé avant toute lecture métier.

Le lot 10 ajoute maintenant le manifeste C8 et son rapport borné : les entrées sont fixées par
hashes de corpus, configuration, release et questions, avec deux contrôles indépendants ; le split
`final_test` est impossible dans le contrat. Un rapport séparé vérifie les hashes du manifeste et
des sorties base/candidate, exige les validateurs déterministes pour l'état `passed`, puis place le
candidat en `awaiting_review` sans activation. Le worker possède maintenant une primitive de report
normal : `JobHandlerDeferred` remet un job en `queued`, libère la lease, restitue la tentative
consommée par le claim et conserve un événement technique, sans le classer en échec. Cette primitive
est testée indépendamment. Le runner de campagne sait aussi avancer et observer une cellule à la fois
(`advance_one`/`observe_one`) sans bloquer le worker. `build_campaign_pair_specs` construit désormais
deux campagnes isolées à partir du manifeste hash-only et d'un jeu de cas séparé, avec pin exact des
releases base/candidate ; la CLI `run_expert_memory_evaluation` écrit ces spécifications et un
checkpoint privé reprenable, puis peut avancer ou observer une cellule via la file durable, sans
appel LLM implicite. `EvaluationCampaignRunner.finalize` clôt désormais idempotemment une campagne
non bloquante quand toutes ses cellules sont terminales, écrit l'audit et le rapport, puis émet un
événement de fin. `EvaluationCampaignPairCoordinator` avance automatiquement base puis candidate
et expose `comparison_ready` lorsque les deux états sont complets. L'enqueue du job comparatif est
maintenant porté par un parent `expert_improvement` différable qui exclut explicitement sa propre
ligne de la gate d'exclusivité ; un cycle complet synthétique valide désormais la reprise parent →
cellule base → cellule candidate → comparateur. L'annulation du parent propage aussi une clôture
immédiate aux cellules en attente et une demande coopérative aux cellules en cours, sans enfant
orphelin. Un comparateur déterministe lit maintenant deux
`state.json` terminés, vérifie la couverture et l'identité des contrôles, hash les sorties et calcule
les taux d'exécution ; l'opération durable `evaluate` et sa route privée persistent ce rapport. Un
jeu complet reste `inconclusive` tant qu'aucune adjudication scientifique autorisée ne fournit de
critères de qualité.

### Blocages et risques conservés

- Le lot 05 reste partiel : les paramètres détaillés de certaines requêtes internes restent agrégés
  par compteurs ; le handler crée désormais un manifeste minimal avant ses contrôles de conversation
  et d'intégrité, de sorte que ces échecs peuvent aussi être clôturés et repris.
- Les chemins d'échec et d'annulation sont maintenant clôturés sous le même lease par le repository ;
  les erreurs survenant avant la création initiale du manifeste restent à traiter comme trace absente.
- Les migrations 47–55 n'ont été vérifiées que sur bases temporaires par les tests ; aucune base réelle ne
  doit être ouverte sans sauvegarde SQLite cohérente et contrôle de version, comme indiqué plus bas.
- Les lots 06 et 12–13 restent limités par la validation d'autorité humaine, le pilote shadow réel,
  la publication et l'installation réelles ; ces contrôles ne peuvent pas être remplacés par des
  fixtures synthétiques sans falsifier l'état de la roadmap.

## Optimisation du RAG : présélection dense par article

La demande utilisateur du 9 septembre 2026 est appliquée dans le chemin hybride existant. FTS5 lit
toujours l'ensemble du corpus local, puis recueille jusqu'à 60 articles distincts. Si au moins quatre
articles sont trouvés et qu'il y a plusieurs variantes denses, la première reste une requête Qdrant
globale et chaque variante dense suivante est filtrée sur ces articles. Les vecteurs des variantes
sont encodés ensemble et les requêtes Qdrant restent dans un seul batch, avec leur filtre propre.

Cette borne réduit l'exploration dense de l'index complet sans remplacer le retrieval hybride par une
recherche séquentielle. Le chemin global est conservé lorsque le pool FTS5 est pauvre, lorsque le
demandeur a déjà fourni des `article_ids`, lorsqu'il n'y a qu'une variante dense, ou lorsque
`dense_article_prefilter_enabled=false`. Les résultats lexicaux globaux restent toujours fusionnés.
Le premier vecteur global protège la découverte d'une terminologie absente de FTS5 ; les niveaux A–D,
le reranking global et les validateurs de preuve ne changent pas.

`ChatbotRetrievalTrace` enregistre désormais le nombre d'articles dans ce pool et le nombre de
requêtes denses réellement globales. Les paramètres `dense_article_prefilter_*` vivent dans
`retrieval` et sont inclus dans la signature de cache existante, car la configuration entière y est
hashée. Les tests couvrent les filtres distincts d'un même batch Qdrant, le groupe de quatre articles,
le premier vecteur global et le repli.

## État des lots de la roadmap

| Lot | État | Réalisé / reste à faire |
|---|---|---|
| 00 | Baseline technique réalisée ; inventaire scientifique partiel | Révision, environnement et tests relevés. Aucune attestation de version installée ni de jeu expert complet disponible. |
| 01 | Réalisé techniquement, validation scientifique externe requise | C1, correction, diagnostic, patch candidat, manifeste C3, budgets, configuration et contrats de rapport sont implémentés et testés ; l'autorité humaine et les résultats réels restent hors de l'attestation locale. |
| 02 | Réalisé techniquement, publication réelle restante | Migration 35, tables de paquets, import atomique/idempotent, relecture vérifiée, pointeur actif, épinglage, feedback persistant, revue, activation CAS et retour arrière sont implémentés et testés sur bases temporaires. |
| 03 | Réalisé pour le paquet V1 | Manifest JSON, YAML/Markdown borné, provenance, graphe, dépendances inverses, lint déterministe et CLI. Ce contrôle est structurel, pas une validation d'autorité humaine. |
| 04 | Partiel : paquet et prévisualisation réalisés | 13 fiches, sélection déterministe et budgets. La sélection est testable en CLI ; le mode shadow dans les jobs et la comparaison scientifique à l'ancien comportement attendent les lots 05–06. |
| 05 | En cours, traçabilité durable raccordée | Contrat de manifeste, table `expert_run_manifests`, épinglage, checkpoints sous lease, clôture atomique, échecs, annulations et candidats omis sont raccordés ; seuls certains paramètres internes restent ouverts. |
| 06 | Bloqué par validation d'autorité et pilote | Le chemin d'intégration est préparé derrière le mode inactif ; activation réelle, pilote et comparaison scientifique attendent les décisions et observations externes. |
| 07 | Partiel : soumission, lecture, révisions et dialogue privé | Migrations 48–49, validation serveur, soumission idempotente, liste/détail, `PATCH` avec `expected_revision`, historique, retrait explicite et dialogue frontend testé ; aucun diagnostic ni workflow génératif. |
| 08 | Réalisé techniquement, audit externe restant | Migration 50–51, grille D1, persistance immuable par révision, replay idempotent, API de mise en file sans appel LLM, worker sans message de conversation et CLI `diagnose_expert_feedback` sont implémentés et testés avec budget nul ; l'audit réel du corpus et l'autorité experte restent externes. |
| 09 | Réalisé techniquement, revue experte restante | Migration 52, compilation structurée d'un `CandidatePatch`, validation des hashes/cibles, release candidate immuable, replay idempotent, CLI, jobs de diagnostic/compilation et projection revue sont implémentés et testés ; la décision humaine reste obligatoire. |
| 10 | Réalisé techniquement, adjudication scientifique restante | Migration 53, contrat C8 sans labels, contrôles bornés, runner non bloquant, campagnes base/candidate orchestrées, comparateur déterministe, rapport hashé et jobs/API `evaluate` sont implémentés et testés ; le cycle synthétique complet est validé, mais aucun résultat réel ne vaut adjudication. |
| 11 | Partiel : revue, activation, rollback et UI explicites | Migrations 54–55, revue humaine append-only, projection API, écran frontend, contrôle des hashes, transitions candidat, activation atomique/idempotente et rollback vers la release parente ; les mutations d'activation/rollback sont désormais protégées par le profil administrateur côté serveur, avec la route canonique `releases/{id}/rollback`, compatibilité de l'ancien chemin et CLI administratives à dry-run prévalidé ; audit externe reste à faire. |
| 12 | Partiel : cycle de pilote shadow mesurable | Contrat borné validation-only, persistance/API idempotentes du plan, de l'audit, de l'attestation et des observations (migrations 56–57), agrégats p50/p95, seuils d'intégrité et activation explicitement interdite ; exécution réelle du pilote, adjudication externe et comparaison avant généralisation restent à faire. |
| 13 | Réalisé techniquement, publication et installation réelles restantes | `app/knowledge/package.py` construit uniquement depuis une candidate `approved`/`activated` et une revue `approve` exacte ; paquet sans conversation ni identifiant de job, compatibilité, hashes, signature OpenSSH Ed25519, staging content-addressed, import idempotent, proposition locale, approbation, activation CAS, retour arrière explicite avec événements, API, écran, reprise idempotente et garde administrateur sont testés. |
| 14–15 | Non commencés | Distribution approuvée complète et extensions optionnelles. |

## Fichiers et décisions techniques

- `app/knowledge/contracts.py`, `models.py` : objets Pydantic figés, champs inconnus refusés,
  identifiants stables, versions et hashes canoniques. Collections immuables ; revalidation des
  objets fabriqués via `model_copy` en mode Python pour ne pas convertir un booléen en entier.
- `app/expert_feedback/models.py` : contrat d'une correction, références de preuve sans texte
  intégral, diagnostic et patch limité. Une source changée, une trace insuffisante ou une ambiguïté
  experte bloque la proposition automatique, même comme cause contributive. Aucun endpoint associé.
- `app/config.py`, `config.example.yaml`, `installer/config.runtime.yaml` : `expert_memory.mode`
  vaut `off`. Plafond de 12 éléments, enveloppes de 2 000/1 600/1 600 caractères selon l'étape,
  trois éléments modifiés et deux tentatives de compilation au maximum. Budgets d'amélioration
  par défaut nuls. Aucune variable d'environnement ni dépendance externe ajoutée.
- `app/database/expert_memory_migration.py` et migration 35 : `expert_releases`,
  `expert_release_items`, `expert_release_dependencies`, `expert_active_release`,
  `expert_release_events`. Une transaction contient la migration et son numéro. Les contenus
  importés sont immuables ; l'événement d'import scelle les éléments et liens.
- `app/knowledge/repository.py` : un import est candidat, adressé par hash, avec contrôle de base
  attendue, compatibilité, FK, graphe et hashes avant commit et lors de la relecture. Un import
  concurrent identique ne crée qu'une version. Les bases absentes, anciennes ou futures sont refusées.
- `app/knowledge/loader.py`, `validation.py`, `graph.py` : manifeste `package.json` explicite,
  100 éléments/2 Mio, 32 Kio par fichier, profondeur YAML limitée. Pas de scan libre du disque,
  source PDF ni chargement d'un modèle. Provenance limitée à `AGENTS.md`, `docs/*.md`, `app/*.py`.
- `app/knowledge/routing.py` : normalisation Unicode et frontières de tokens, critères fermés,
  priorités puis IDs, dépendances sélectionnées en groupe, exclusion et passerelles. Le coût inclut
  le JSON exact des trois contextes. Une enveloppe insuffisante écarte le groupe entier ; une
  exigence globale trop volumineuse produit un repli sans contexte.
- `knowledge/` : cinq politiques dérivées du guide accepté ; huit propositions de taxonomie,
  passerelle, routes et recettes. `--include-proposals` permet seulement leur inspection hors ligne.

Le plan initial prévoyait les schémas et tables de plusieurs workflows dès les lots 01–02. Ils sont
créés ici avec leur premier cas d'usage réel, pour éviter de figer des structures non exercées. C'est
un affinage technique de l'ordre des travaux, pas une suppression des exigences de traçabilité,
d'évaluation ou de revue. Les lots incomplets restent explicitement ouverts.

### Frontière d'autorité à compléter avant intégration

Le chargeur vérifie que les documents de provenance existent et que leurs octets correspondent aux
hashes déclarés. Il ne vérifie pas qu'une nouvelle instruction reformule fidèlement ces documents,
ni qu'un fichier marqué `reviewed_method` a reçu une revue. La prévisualisation interprète ces
étiquettes déclaratives ; elle n'a aucun effet de production et annonce `scientific_approval=false`.

Avant toute injection active, ajouter une validation d'autorité issue d'une liste initiale revue ou
d'un événement de revue authentifié, lié au hash exact de la version. Un hash de contenu ou un champ
`authority` ne doit jamais servir seul de justificatif d'activation. Cette dépendance bloque la mise
en production du lot 06 et la promotion du lot 11.

Le lot 12 dispose maintenant d'un contrat de pilote `shadow` borné : il reprend un manifeste C8 de
validation, produit un plan et un audit hashés, exige les contrôles d'exécution et signale les
blocages sans jamais autoriser l'activation. Le plan, l'audit et l'attestation sont maintenant
persistés dans la migration 56, exposés par l'API privée et raccordés à l'écran de revue ; les
écritures sont idempotentes et liées aux hashes exacts du candidat, de l'évaluation et de l'audit.
Même un audit
`ready` signifie seulement « prêt pour une approbation humaine et un audit externe » ; il ne
constitue pas une décision scientifique. Une attestation externe explicite peut être liée au hash
exact de cet audit avec un identifiant de relecteur, une référence externe et une raison ; l'objet
d'attestation conserve lui aussi `activation_allowed=false` et refuse l'acceptation d'un audit
bloqué.
Le registre d'observations du pilote est maintenant ajouté par la migration 57 : chaque cas est
référencé par hash, sans contenu de conversation, avec protocole de mesure constant, temps expert,
tokens, correction humaine, effet utile, faux gain et retours arrière. Les agrégats p50/p95 sont
calculés par rang déterministe ; le pilote n'est complet qu'à partir de 10 cas et reste borné à 20.
Le store refuse désormais aussi tout `case_sha256` absent des questions ou contrôles gelés dans le
plan du pilote ; ce contrôle est couvert par une régression dédiée.

Le lot 13 commence avec `app/knowledge/package.py` : une candidate ne peut être exportée que si
son état et sa revue humaine concordent avec le hash de la release. Le fichier transportable ne
contient que la `KnowledgePackage`, ses versions minimales, le hash de la release et un reçu de
revue hashé ; il exclut conversation, feedback privé, secrets et identifiants de jobs. Le
vérificateur contrôle l'intégrité, la compatibilité application/schéma et, si demandé, une
signature OpenSSH Ed25519 dans un namespace distinct de celui du corpus. Le staging est
content-addressed, rejouable et recopié par fichiers vérifiés ; l'import cible utilise le dépôt
existant pour créer une candidate inactive, sans modifier le pointeur actif. Un registre local
impose ensuite `imported → proposed → approved → activated`, avec décision humaine, génération
attendue, transaction atomique et événement idempotent. Une migration 59 ajoute le retour arrière
vers l'ancêtre immédiat, avec hash cible, CAS et reçu append-only. Les routes privées et l'écran
`distribution-experte` exposent ces transitions, y compris les paramètres CAS. L'écran conserve les
identifiants de requête d'activation et de rollback dans un stockage local, réutilise un identifiant
après une erreur réseau et relit l'état serveur avant de permettre une nouvelle action.

## Validation

La baseline avant implémentation comporte 1 089 tests backend réussis. Les nouveaux modules ont
d'abord été vérifiés par suites ciblées, avec avertissements traités en erreurs.

| Contrôle final | Résultat observé |
|---|---|
| `.venv` : Ruff format sur `app scripts tests` | 586 fichiers correctement formatés |
| `.venv` : Ruff check sur `app scripts tests` | Réussi |
| `.venv` : `python -m pytest -q` | 1 582 tests réussis en 215,20 s ; environnement Python 3.14 local hors plage supportée, conservé pour la commande obligatoire |
| Python 3.12.14 isolé : `python -m pytest -q -W error` | Non relancé dans cette itération |
| Python 3.12.14 isolé : `python -m pip check` | Aucune dépendance incompatible |
| Frontend : `npm.cmd --prefix frontend run ci` | Format, lint, typage, 31 fichiers / 108 tests, build de production réussis |
| Linter du paquet réel | 13 éléments valides, zéro diagnostic, aucune approbation scientifique |
| Import dry-run avec configuration runtime distribuée | `applied=false`, `activated=false`, 13 éléments, aucune ouverture SQLite |
| Prévisualisation FML/cidre avec propositions | Route malolactique retenue, 8 éléments, 726/344/700 caractères par étape |

Empreinte du paquet initial vérifié :
`8546f1019cd33ac4ad32ed1aba19c3afb41647cdc08a3dcfee6602cad93ebac7`.

L'environnement `.venv` préexistant utilise Python 3.14.6, alors que le projet exige Python 3.12.
Un environnement de validation isolé Python 3.12.14 a été créé sous `tmp/p` avec les dépendances de
`requirements.txt`, sans remplacer `.venv`. Un premier essai sous `outputs/expert-memory-python312`
a atteint la limite Windows de longueur des chemins lors de l'installation de PyTorch. Ces dossiers
et les dépendances frontend restent ignorés par Git.

Pour rendre la comparaison reproductible, la pile de test de `tmp/p` a été alignée sur `.venv` :
`starlette==1.3.1`, `httpx2==2.7.0` (et son `httpcore2==2.7.0`), `anyio==4.14.2`,
`pillow==12.3.0`. Ces ajustements concernent uniquement
l'environnement isolé ; `requirements.txt` reste inchangé. Sans HTTPX2, absent de ce fichier mais
présent dans `.venv`, Starlette émet un avertissement de dépréciation lors de l'import de TestClient ;
les premières collectes Python 3.12 avec `-W error` ont donc été refusées. AnyIO 4.15.1 déprécie
aussi un alias utilisé par ce TestClient, d'où l'alignement avec 4.14.2. Aucun avertissement n'a été
filtré pour contourner ces erreurs. Les futures installations de l'environnement de test devront
tenir compte de ces écarts. Pillow, également absent des requirements, est importé par les tests
existants d'analyse de figures ; sa version est celle de `.venv`. Pour reproduire la pile testée
après installation des requirements :

```powershell
.\tmp\p\Scripts\python.exe -m pip install starlette==1.3.1 httpx2==2.7.0 anyio==4.14.2 pillow==12.3.0
.\tmp\p\Scripts\python.exe -m pip check
.\tmp\p\Scripts\python.exe -m pytest -q -W error
```

Le contrôle supplémentaire `.venv` Python 3.14 avec `-W error` a produit 17 échecs et 1 251 succès :
ses échecs signalent des connexions SQLite non fermées au ramasse-miettes dans les parcours existants.
Ils ne constituent pas une validation du runtime 3.14, hors version supportée ; la commande standard
du dépôt et le contrôle strict sous Python 3.12 sont consignés séparément dans le tableau ci-dessus.

Ces résultats sont des contrôles techniques. Aucun score scientifique CiderQA nouveau, aucune
baseline de réponses Argo, aucun accord expert et aucun gain mesuré ne sont revendiqués.

L'essai manuel du dry-run a révélé une incohérence préexistante de `config.example.yaml` :
`ingestion.max_tokens=750` dépasse `embeddings.max_sequence_length=512`. Les nouvelles options
de mémoire sont valides, mais le chargement de ce fichier complet est refusé par le validateur
existant. Les exemples d'essai utilisent donc `installer/config.runtime.yaml`, vérifié avec succès.
Le réglage d'ingestion n'a pas été modifié dans cette tâche de mémoire experte.

## Migration réelle à préparer

Aucune base réelle n'a été ouverte pour migration/import dans cette tâche. La migration 35 est
cependant enregistrée dans le schéma commun : le prochain démarrage de l'API ou du worker avec ce
code l'appliquera, même si la mémoire est `off`. `Database.initialize` n'effectue pas de sauvegarde.

Avant ce démarrage :

1. Résoudre les chemins de la base applicative et du corpus commun depuis la configuration réelle,
   et relever la version des processus API/worker installés.
2. Arrêter les processus écrivains et vérifier l'absence de job en cours d'écriture.
3. Produire une sauvegarde SQLite cohérente et vérifier son intégrité pour chaque cible distincte ;
   si les chemins coïncident, une seule sauvegarde suffit. Ne pas copier seulement le fichier `.db`
   en ignorant un éventuel WAL actif.
4. Appliquer la migration par l'initialisation normale, vérifier schéma 35, intégrité/FK et comptes
   des articles, preuves et conversations, puis redémarrer API et workers avec la même version.
5. Effectuer l'import du paquet candidat seulement après ces contrôles. Vérifier que le pointeur
   actif reste vide et que `activated=false` est retourné.

Lorsque les bases sont distinctes, le schéma partagé ajoute aussi les cinq tables expertes vides à
la base corpus ; le script d'import cible uniquement la base applicative. Une ancienne application
limitée au schéma 34 refusera ensuite une base 35 : le retour arrière du logiciel exige une procédure
coordonnée avec la sauvegarde, pas une suppression manuelle des tables. L'intégration réelle reste
une étape distincte de ces tests sur bases temporaires.

## Prochaine tâche exécutable par un agent

Poursuivre le lot 05 avant de modifier les prompts. Relire les contrats C2–C3 et le lot 05 complet,
puis travailler dans cet ordre :

1. Créer `app/knowledge/trace.py` avec modèles fermés du manifeste : version/mode épinglés,
   identités et hashes des candidats par étape, décisions A–D, contextes réellement présentés,
   liens affirmation–preuve, budgets et statut de tentative. Ne pas copier le PDF.
2. Ajouter une nouvelle migration après 35 pour le manifeste et les références de job. Ne pas
   modifier la migration 35 après déploiement. Préserver les anciens jobs en les considérant `off`.
3. Implémenter l'épinglage immuable dans le repository et persister manifeste/résultat/succès dans
   la transaction existante sous contrôle du lease ; tracer aussi échec, annulation et reprise.
4. Brancher seulement la collecte de traces sur `_answer_chatbot` et ses frontières déjà existantes.
   Garder la vague groupée unique, le filtrage global et les validateurs actuels.
5. Ajouter les tests de version modifiée pendant un job, ancienne tentative, annulation, crash et
   réduction du prompt. Vérifier que les IDs tracés correspondent aux preuves effectivement envoyées.
6. Lancer les tests proches, puis les quatre commandes obligatoires de `AGENTS.md`. Consigner les
   résultats ici. Ensuite seulement entreprendre le lot 06 et son contrôle d'autorité.

Pour toute tâche suivante, les commandes copiables et critères d'acceptation de
[la roadmap](EXPERT_MEMORY_ROADMAP.md), des [contrats](EXPERT_MEMORY_CONTRACTS.md) et de
[la validation](EXPERT_MEMORY_VALIDATION.md) restent le cadre ; ce document décrit ce qui existe
réellement et les écarts connus, sans transformer une étape partielle en fonctionnalité terminée.
