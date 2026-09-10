# Mémoire experte — contrats techniques proposés

Complément normatif **pour l’implémentation proposée** de la
[roadmap](EXPERT_MEMORY_ROADMAP.md), daté du 7 septembre 2026. Les contrats ci-dessous décrivent la
cible complète ; [la progression](EXPERT_MEMORY_PROGRESS.md) précise ceux déjà implémentés.
Si une contrainte du code rend un contrat impossible, consigner le conflit avant de changer le plan.

Le socle initial implémente un manifeste disque `package.json` avec les métadonnées C1 et une liste
`files` de Markdown, sans champ `items` sur disque. Le chargeur construit ensuite `KnowledgePackage`.
Les recettes ajoutent `interaction_mode=research|conversation` pour fermer leur graphe respectif.
`required=true` est réservé à `method_policy`. La provenance vérifie les octets exacts de sources
`AGENTS.md`, `docs/*.md` ou `app/*.py` ; elle ne prouve pas une validation d'autorité. Cette validation
reste obligatoire avant intégration active. Les commandes effectivement utilisables sont documentées
dans [knowledge/README.md](../knowledge/README.md) ; les commandes et endpoints ultérieurs ci-dessous
restent des spécifications tant que la progression ne les indique pas réalisés.

## C0. Règles communes

- Pydantic : `extra="forbid"`, enums fermés ; objets de version immuables.
- UUID pour les objets de travail ; IDs sémantiques pour les éléments de connaissance.
- Dates UTC avec fuseau. Hash SHA-256 du JSON canonique UTF-8, clés triées, séparateurs fixes.
- Distinguer hash d’intégrité et signature d’auteur ; un hash ne prouve pas une approbation humaine.
- Numéro de schéma explicite dans fichiers, payloads, manifestes et rapports.
- Pas de chargement de modèle, Qdrant ou client LLM dans les imports, migrations ou démarrage API.
- Les fixtures contenant de la science sont synthétiques et signalées comme telles ; aucun DOI fictif
  n’est présenté comme une publication réelle. Aucun identifiant de corpus local n’est codé en dur.

## C1. Paquet de connaissances et recettes

### Fichiers et modèle `KnowledgeItem`

Un fichier Markdown contient un frontmatter YAML suivi d’un corps UTF-8. Le corps est soumis aux
mêmes limites que les champs structurés ; il ne peut pas neutraliser les invariants de l’application.

| Champ | Type / validation |
|---|---|
| `schema_version` | entier littéral `1` |
| `id` | chaîne, regex `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){1,5}$`, 120 caractères maximum |
| `revision` | entier ≥ 1 ; nouvelle révision à chaque changement de contenu |
| `kind` | `taxonomy`, `method_policy`, `gateway`, `route`, `recipe` ; `evidence_note` réservé au lot 14 |
| `title` | chaîne non vide, ≤ 160 caractères |
| `language` | `fr`, `en`, `multilingual` |
| `authority` | `accepted_user_method`, `reviewed_method`, `proposal` |
| `provenance` | liste de `{document, section, revision_sha256}` ; document relatif autorisé, section non vide |
| `depends_on` | liste unique d’IDs, ≤ 20 |
| `supersedes` | ID/révision précédente facultatifs ; ne remplace pas une activation |
| `stage` | `routing`, `planning`, `semantic_filter`, `generation` ; une étape par élément |
| `required` | booléen ; réservé aux éléments initiaux approuvés, non modifiable par compilation automatique |
| `data` | union discriminée par `kind`, décrite ci-dessous |
| `body` | partie Markdown, ≤ 6 000 caractères pour V1 |

Le hash porte sur l’objet parsé normalisé, corps compris. `referenced_by` et `content_sha256` sont
calculés par le compilateur de paquet, jamais acceptés comme assertions du modèle.
Un fichier importé ne peut s’auto-attribuer une approbation : `authority` est vérifiée contre une
provenance initiale revue ou un événement de revue stocké. `proposal` reste inactif.

Un paquet a `schema_version`, `package_id`, `version`, `minimum_app_version`,
`minimum_schema_version`, `items` triés avec leurs hashes et un `package_sha256` calculé.
Importer tous les éléments ou aucun. Fixer V1 à 100 éléments/2 Mio par paquet ; dépassement explicite,
jamais troncature. La hausse ultérieure doit être mesurée et documentée.

### Données par type

| Kind | Champs de `data` | Restrictions |
|---|---|---|
| `taxonomy` | `canonical_term`, `aliases_fr`, `aliases_en`, `ambiguity_terms`, `facet_kind` | Vocabulaire et sens opérationnel, aucune conclusion scientifique à citer. |
| `method_policy` | `instruction`, `applies_to` | Consigne de méthode pour une étape ; ne désactive aucun contrôle Python. |
| `gateway` | `criteria`, `on_uncertain` | Délimite l’application d’une règle spécialisée ; ne bloque pas l’accès au RAG général. |
| `route` | `priority`, `match`, `exclude`, `target_ids` | Opérateurs fermés, cibles typées, aucun code ni requête SQL libre. |
| `recipe` | `recipe_version`, `steps` | Ordre fermé du workflow ; aucune connaissance factuelle ou outil arbitraire. |

`facet_kind` ∈ `matrix`, `process`, `outcome`, `condition`, `term`.
`match` et `exclude` : objets de listes `all_terms`, `any_terms`, `facet_ids`, `language` facultative.
Chaque liste est bornée à 20 chaînes de 120 caractères. Aucune expression régulière fournie par fichier.
Les termes sont normalisés par une fonction commune déterministe, comparés comme tokens/séquences
de tokens complets ; tester accents, apostrophes, tirets et sigles courts sans collision de sous-chaîne.
`on_uncertain` ∈ `keep_general_route`, `record_ambiguity`. Pas de rejet de question automatique en V1.
Les `target_ids` d’une route et `knowledge_ids` d’une recette figurent aussi dans `depends_on` ; le
linter vérifie cette inclusion pour que l’analyse d’impact couvre toutes les références opérationnelles.

Chaque étape de recette contient `id`, `handler`, `knowledge_ids`, `input_contract`, `output_contract`.
Les handlers autorisés sont une liste Python : `plan_hypothesis`, `retrieve_grouped`,
`select_global_evidence`, `synthesize_validated`. Le contexte hiérarchique et la fusion restent dans
leurs services existants. Les chemins conversationnels sont explicitement déclarés séparément et
réutilisent les passages SQLite selon le contrat existant.

La validation refuse un second `retrieve_grouped`, un saut de validation, une branche inconnue,
un `eval`, un outil réseau, un shell ou un handler chargé depuis une chaîne de chemin Python.
Une évolution du graphe d’exécution relève d’un changement de code revu ; le compilateur automatique
ne modifie que les références à des instructions optionnelles à l’intérieur du graphe approuvé.

### Exemple de fichier initial

Exemple de structure, à compléter avec la vraie empreinte du guide au lot 04 ; il ne constitue pas
un paquet importable tant que sa provenance n’est pas renseignée.

```yaml
schema_version: 1
id: taxonomy.malolactic_fermentation
revision: 1
kind: taxonomy
title: Fermentation malolactique — vocabulaire
language: multilingual
authority: accepted_user_method
provenance:
  - document: docs/HOW_TO_WORK_ON_CIDERSCHOLAR.md
    section: '4.1'
    revision_sha256: '<empreinte réelle à calculer>'
depends_on: []
stage: planning
required: false
data:
  canonical_term: fermentation malolactique
  aliases_fr: [FML, TML]
  aliases_en: [MLF, malolactic fermentation]
  ambiguity_terms: []
  facet_kind: process
```

Associer cette entrée à un routage contextualisé, sans transformer toute occurrence de trois lettres
en intention cidricole. Créer aussi un exemple synthétique sans ces termes pour tester le moteur.

### Routage et budgets

`route(question, deterministic_intent, release) -> RoutingDecision` retourne IDs/révisions/hashes,
raisons de matching, exclusions, ambiguïtés et éléments omis par budget. Tri : priorité décroissante,
puis ID croissant ; calcul de la fermeture `depends_on` avant validation du budget.

Configuration proposée :

```yaml
expert_memory:
  mode: off
  max_selected_items: 12
  planning_max_characters: 2000
  semantic_max_characters: 1600
  generation_max_characters: 1600
  max_candidate_items_changed: 3
  max_compile_attempts: 2
```

Les caractères sont mesurés sur le payload assemblé, séparateurs compris ; les tokens réels sont
mesurés à l’exécution. Ces bornes ne s’ajoutent pas au plafond fournisseur existant.
Retirer par priorité les groupes optionnels complets, dépendances comprises ; si les instructions
indispensables ne tiennent pas, repli vers les instructions Python initiales et trace `budget_fallback`.
Les règles critiques A–D, preuves, citations, nombres et bornes d’exécution restent en Python.

Chargeur : refuser chemins absolus, `..`, liens symboliques/jonctions sortant de la racine, fichiers
non réguliers, clés YAML dupliquées, tags personnalisés, alias/ancres, encodage invalide et profondeur
> 10. Limite 32 Kio par fichier avant parsing. `safe_load` seul ne suffit pas à détecter tous ces cas.
Une dépendance manquante ou cyclique invalide le paquet entier.

## C2. Stockage, autorités et activation

### Placement

Les tables ci-dessous vivent dans la **base applicative résolue** déjà utilisée par `JobRepository`.
Ne pas créer un nouveau fichier SQLite par défaut. La base peut coïncider avec le corpus selon la
configuration ; les tests doivent couvrir les deux topologies. Si elle est distincte, les références
vers le corpus se vérifient dans le service, pas par une fausse clé étrangère interbase.

Les contenus de mémoire sont des instructions ou annotations dérivées. Ils ne rejoignent ni FTS des
articles, ni collections Qdrant, ni tables bibliographiques. Les originaux scientifiques restent
uniquement dans le corpus commun. Les fichiers `knowledge/` servent à proposer/revoir un paquet ;
une modification sur disque ne change jamais la version runtime importée.

### Tables à créer

| Table | Colonnes et contraintes minimales |
|---|---|
| `expert_releases` | `id` PK, `package_sha256` UNIQUE, `schema_version`, `app_min_version`, `created_at`, `base_release_id` FK nullable, `manifest_json`, `state` CHECK `candidate/eligible/retired/revoked` |
| `expert_release_items` | FK `release_id`, `item_id`, `revision`, `kind`, `content_sha256`, `payload_json`, PK `(release_id,item_id)` |
| `expert_release_dependencies` | `release_id`, `item_id`, `dependency_id`, PK du triplet ; deux FK composites vers les éléments de la même release |
| `expert_active_release` | une seule ligne `singleton=1`, `release_id` FK nullable, `generation` entier croissant, `updated_at` |
| `expert_release_events` | `id` PK, `from_release_id`, `to_release_id`, `event_type`, `review_id` nullable, `reason`, `created_at` ; journal append-only |
| `expert_run_manifests` | `id` PK, `job_id` FK, `attempt`, `release_id` nullable, `payload_json`, `manifest_sha256`, `state`, `result_message_id` FK nullable, UNIQUE `(job_id,attempt)` |
| `expert_corrections` | `id` PK, `message_id` FK, `manifest_id` FK nullable, `client_request_id`, `payload_json`, `status`, `revision`, dates ; UNIQUE `(message_id,client_request_id)` |
| `expert_diagnoses` | `id` PK, FK `correction_id`, `correction_revision`, `payload_json`, `diagnosis_sha256`, date |
| `expert_candidates` | `id` PK, FK `diagnosis_id`, `base_release_id`, `candidate_release_id`, `diff_sha256`, `state`, `attempt`, dates |
| `expert_evaluations` | `id` PK, FK `candidate_id`, `report_json`, `report_sha256`, `state`, date ; conserver tous les essais |
| `expert_reviews` | `id` PK, FK `candidate_id`, `candidate_sha256`, `evaluation_sha256`, `decision`, `reviewer_label`, `reason`, date |
| `expert_regression_cases` | `id` PK, `source_correction_id` nullable, `case_sha256` UNIQUE, `split`, `payload_json`, `introduced_release_id` FK, date |
| `expert_improvement_checkpoints` | `job_id` PK/FK, `revision`, `payload_json`, `updated_at` ; cellules/enfants, budget consommé et progression, écriture sous lease |

`reviewer_label` désigne l’acteur local déclaré, pas une identité authentifiée par un annuaire inexistant.
Toutes les transitions s’exécutent dans des transactions courtes avec paramètres SQL, index sur FK,
contraintes d’état et contrôle optimiste `revision`. Les contenus des releases restent immuables ;
une correction d’élément crée une nouvelle release. Ne pas supprimer physiquement une release référencée.

### Atomicité et reprise

À l’enqueue d’un chat, résoudre mode/release dans la transaction du job ; stocker cette identité dans
le payload interne. Ce champ n’est pas modifiable librement par le client public. Le producteur
d’évaluations peut désigner un candidat explicitement pour une cellule isolée.

À l’activation : vérifier `expected_active_generation`, release de base, hashes candidat/évaluation,
revue `approve`, politique et compatibilité. Insérer le cas de développement validé, l’événement
d’activation et déplacer le pointeur dans la même transaction. Un rollback SQL laisse tout inchangé.
Deux demandes concurrentes : une seule réussit ; l’autre reçoit `409 active_release_changed`.
Même requête idempotente déjà appliquée : retourner le résultat antérieur, sans nouvelle activation.

Une release retirée des nouveaux jobs peut finir les jobs déjà épinglés. Une release `revoked`,
distincte d’un simple retour arrière, est interdite à toute nouvelle étape de génération : annuler
explicitement les jobs concernés avec notice, puis proposer un nouveau job. Ne pas changer leur
release silencieusement. Contrôler la révocation aux frontières d’étape et avant persistance finale.

Si le corpus change, ne pas tenir de transaction SQLite ouverte pendant un appel LLM : enregistrer
empreintes avant/après et hashes de passages réellement vus. Marquer le run non comparable pour
évaluation ; le validateur final décide toujours sur les sources autorisées réellement persistées.

Suppression d’une conversation : supprimer ses manifestes/corrections/diagnostics privés dépendants,
annuler ses candidats non promus et nettoyer les exports privés gérés. Une règle durable déjà
approuvée et son cas de régression ne survivent que sous forme **explicitement revue et dépersonnalisée**,
avec rupture des FK privées (`SET NULL` quand approprié). Prévenir l’utilisateur de cette conservation
au moment de la promotion, pas en détournant la suppression de conversation. Ajouter un test dédié.

## C3. Manifeste `ExpertRunManifest`

| Groupe | Champs obligatoires |
|---|---|
| Identité | schéma, UUID run/job, tentative, mode, release/hash nullable, recette/version/hash, révision code |
| Entrées | hash question originale, hash contexte utilisateur, effort, interaction_mode, hash configuration expurgée |
| Environnement | empreintes corpus avant/après, schéma SQL, index et modèles, fournisseur/modèle, templates de prompt |
| Routage | IDs/hashes retenus, règles matchées/rejetées, motifs, exclusions, repli et budget par étape |
| Retrieval | variantes sous forme de hashes + paramètres reconstructibles privés, IDs/rangs/scores dans les pools bornés |
| Décisions | par candidat : grade local/global, besoins concernés, motif contrôlé, stage de rejet ou rétention |
| Preuves | références typées, hash texte original, hash texte présenté, pages réellement persistées, section/contexte |
| Sortie | statut, codes de validation, IDs stables des affirmations et associations aux evidence_ids, hash réponse |
| Coût | nombre d’appels par étape, tokens, durée, attente quota/verrou, cache hit/miss, état final |

Référence scientifique : union `chunk` (`article_id`, `chunk_id`, pages), `bibliographic_abstract`
(`record_id`, `content_hash`) ou `article_abstract` (`article_id`, hash abstract). Inclure identité
du corpus ; un `evidence_id` temporaire seul ne permet pas de retrouver un abstract de manière sûre.
Les métadonnées sont résolues côté serveur. Une figure générée ne devient pas un quatrième type
admissible pour la génération V1.

Pour rejouer le prompt sans copier le texte scientifique : conserver IDs, hashes, template/recette
immuables et description déterministe de réduction (`prefix_characters`, normalisation/version).
Vérifier que la reconstruction retrouve exactement le hash présenté. Les besoins structurés et
sorties de planning utiles au diagnostic peuvent être conservés comme artefacts privés typés
`retrieval_only`, jamais comme preuve ; aucune chaîne de raisonnement libre n’est exigée ni publiée.

Un petit manifeste public contient seulement mode, version lisible, disponibilité du diagnostic et
état dégradé éventuel. Les listes de candidats, questions, prompts et contenus privés restent hors
logs, job events, exports par défaut et paquets distribués. Les logs utilisent des codes, compteurs
et IDs techniques. Un export de diagnostic est explicite, expurgé et prévisualisable.

Les manifestes sont incrémentalement checkpointés par tentative avec vérification worker/lease ;
la clôture et le lien au message sont atomiques avec `persist_result_and_succeed`. Un crash conserve
la trace partielle. Sans trace complète, diagnostic `insufficient_trace`, jamais manifeste reconstitué
présenté comme observation originale. Limite payload : 2 Mio ; si dépassée, conserver un état
`incomplete` explicitement non évaluable plutôt qu’une troncature cachée.

## C4. Correction et diagnostic

`ExpertCorrectionCreate` : `schema_version=1`, `client_request_id` UUID, `manifest_id` facultatif,
`claim_id` facultatif, `selected_text` ≤ 2 000, `problem` de 10 à 4 000 caractères,
`proposed_correction` de 10 à 4 000, `scope` = `this_answer` / `reusable_method`,
`evidence_refs` liste ≤ 10, `suggested_category` facultative. Le serveur déduit `message_id` du chemin,
vérifie rôle assistant, appartenance du manifeste et hash de la réponse avant de valider un claim.

Les textes sont des données non fiables : jamais concaténés au system prompt du chatbot. Les références
absentes sont refusées pour un support revendiqué ; une URL externe peut être notée comme piste séparée,
sans téléchargement ni statut de preuve. Une correction peut ne contenir aucun support si elle est
purement méthodologique. Une correction factuelle non étayée reste une proposition.

États correction : `submitted`, `diagnosis_incomplete`, `diagnosed`, `needs_expert`,
`candidate_ready`, `resolved`, `rejected`, `withdrawn`. Chaque mise à jour utilisateur crée une révision
et invalide diagnostic/candidat/évaluation/revue en aval ; ne pas écraser un historique approuvé.

### D1. Diagnostic causal

Lire le manifeste puis contrôler les couches suivantes, sans déduire la cause du ton de l’expert.

| Observation vérifiable | Cause principale proposée | Destination |
|---|---|---|
| Pas de manifeste, entrées non reconstructibles | `insufficient_trace` | Compléter l’observation ; aucune modification compilable. |
| Hash de preuve différent ou source exclue depuis le run | `source_changed` | Nouveau cas explicitement daté ; ancien replay non comparable. |
| Source pertinente absente du corpus contrôlé | `corpus_gap` | Proposition d’acquisition séparée et autorisée. |
| Source persistée mais absente des index attendus | `index_gap` | Correction d’indexation, hors mémoire. |
| Sens/critère approprié absent de la mémoire chargée | `knowledge_gap` ou `routing_error` | Candidat de vocabulaire/routage, après revue de la portée. |
| Source indexée non retenue dans le pool | `retrieval_error` | Examiner requêtes/reranking ; code ou règle selon preuve du diagnostic. |
| Source admissible classée C/D à tort | `semantic_filter_error` | Candidat de méthode ou bug du filtre ; jamais promotion forcée d’une source. |
| Source retenue mais non présentée à la génération | `context_budget_error` | Allocation du contexte ou bug ; pas déclaration de lacune. |
| Preuves suffisantes présentées, conclusion erronée/omise | `generation_method_error` | Instruction/recette optionnelle, ou bug si invariant en cause. |
| Sortie étayée rejetée par un contrôle erroné | `validator_bug` | Ticket/test Python ; compilateur interdit sur validateurs. |
| Experts en désaccord ou conditions manquantes | `expert_ambiguity` | Discussion humaine, conserver les interprétations. |
| Quota, réseau, worker, lease ou erreur modèle | `runtime_failure` | Reprise/exploitation ; pas apprentissage scientifique. |

`Diagnosis` contient cause principale, causes contributives, `observed_ids`, hashes vérifiés,
`missing_information`, `confidence` = `supported` / `uncertain`, `target_item_ids`,
`proposed_action` = `knowledge_candidate` / `engineering_issue` / `acquisition_proposal` /
`expert_review` / `no_change`. Seul un diagnostic `supported` avec cibles autorisées est compilable.

Vérifier une absence dans le corpus exige un audit borné explicite par identités ou recherches locales
contrôlées ; l’absence parmi les seuls candidats récupérés ne démontre pas une lacune documentaire.
Ce diagnostic hors ligne n’est pas une vague supplémentaire cachée de la réponse utilisateur.

## C5. Candidats, revue et API

`CandidatePatch` contient `base_release_id`, `diagnosis_sha256`, opérations `replace_item` /
`add_item` / `retire_item`, chacune avec ID, hash attendu, objet complet proposé et justification.
`retire_item` signifie omission dans la nouvelle release, pas suppression de l’historique.
Une opération ne peut toucher les fichiers Python, instructions projet, secrets, jeux réservés ou
rapports de validation. Les seuls éléments éditables automatiquement sont les données lexicales,
routages et instructions optionnelles ; `required`, gates critiques et graphe des handlers sont verrouillés.

États : `draft` → `structurally_valid` → `evaluating` → `awaiting_review` → `approved` → `activated`.
Branches : `needs_expert`, `evaluation_failed`, `rejected`, `superseded` ; erreur technique d’évaluation
= `inconclusive`, jamais succès. Les transitions sont calculées côté service, pas reçues telles quelles.

API à créer (préfixes complets ; aucune route lourde ne travaille dans le processus HTTP) :

| Route | Contrat et résultat |
|---|---|
| `POST /api/chatbot/messages/{id}/expert-corrections` | C4 ; `201` correction, replay idempotent `200` ; aucun appel LLM. |
| `GET /api/expert-memory/corrections?status=&cursor=&limit=` | Liste locale paginée, limit 1–100 ; résumés sans prompts. |
| `GET /api/expert-memory/corrections/{id}` | Détail local et versions de diagnostic ; pas de texte intégral. |
| `PATCH /api/expert-memory/corrections/{id}` | Nouvelle révision avec `expected_revision`, ou retrait explicite. |
| `POST /api/expert-memory/corrections/{id}/diagnosis-jobs` | `client_request_id`, `expected_revision`, budget d’appels explicite ; `202` job public. |
| `POST /api/expert-memory/diagnoses/{id}/candidate-jobs` | Hash diagnostic + budget ; `202`, administration locale. |
| `GET /api/expert-memory/candidates/{id}` | Diff borné, dépendances, état, rapports et prochaines actions. |
| `POST /api/expert-memory/candidates/{id}/evaluation-jobs` | Manifest évaluation validé + budget ; `202`, administration locale. |
| `POST /api/expert-memory/candidates/{id}/reviews` | `approve/reject/needs_changes`, raison, hashes candidat/rapport ; `201`, administration locale. |
| `POST /api/expert-memory/candidates/{id}/activate` | Review ID, hashes et génération active attendue ; `200` transition, administration locale. |
| `GET /api/expert-memory/releases` | Historique paginé et version active. |
| `POST /api/expert-memory/releases/{id}/rollback` | Cible compatible, raison et génération attendue ; `200`, administration locale. |

Toute mutation reçoit une clé d’idempotence UUID ; même clé avec autre contenu : `409`.
Réponses d’erreur : `404` objet absent ; `403` profil local insuffisant ; `409` état/version
incompatible ; `422` schéma/référence invalide. Détail = code stable + message français sans exception
brute, chemin local ou secret. Désactiver un bouton ne remplace jamais un contrôle serveur.
Les lectures/décisions locales ne requièrent pas de LLM. Retour à la version précédente demande
une action explicite dans l’interface ; ce n’est pas une restauration destructive du corpus.

Pour les opérations lourdes, ajouter **un type** `expert_improvement`, payload discriminé
`diagnose/compile/evaluate`. Mettre à jour enum, SQL CHECK via migration, parsing, registry, worker,
producteurs, projections API/TypeScript et tests. Réutiliser étapes durables existantes pertinentes
(`planning`, `verification`, `validation`, `persistence`) ; conserver les mêmes transitions/leases.

Le travail d’évaluation orchestre des cellules `chat_answer` isolées. Ajouter un résultat interne
`JobHandlerDeferred(available_at, checkpoint)` au worker et une méthode repository de report sous
lease, sur le modèle de `defer_for_quota` : `running -> queued`, libération du propriétaire, date
`available_at` future et restitution de la tentative consommée par cette attente normale. Le
checkpoint et le report sont atomiques. Ce n’est ni un échec ni un nouveau job ; les étapes publiques
ne régressent pas. Chaque passage enfile/retrouve au plus une cellule idempotente puis rend le slot.
Tester `chat_worker_concurrency=1`. À l’annulation, annuler les enfants actifs, attendre leur
terminalité via les mêmes reports bornés et conserver leurs rapports ; aucun enfant orphelin.
Étendre aussi `app/api/jobs.py` et le service d’annulation : un parent momentanément `queued` ne
doit pas emprunter la suppression immédiate ordinaire sans propager l’annulation aux enfants.
L’exclusivité des cellules CiderQA distingue le parent orchestrateur de ses propres cellules ;
elle continue de refuser une autre campagne concurrente. Extraire de `EvaluationCampaignRunner`
une primitive commune « avancer une cellule sans attendre », appelée par la CLI et le handler.

## C6. CLI réutilisant les mêmes services

Toutes les commandes suivantes sont **futures**. Parseurs bornés, `--help`, code retour `0` succès,
`1` validation refusée, `2` erreur/incomplet ; `--output`/`--run-dir` sous le dossier d’exports résolu
pour les artefacts privés. Les tests peuvent utiliser un répertoire temporaire injecté.

| Module `scripts.*` | Arguments à implémenter | Effet |
|---|---|---|
| `lint_expert_knowledge` | `--knowledge-dir`, `--output` | Lecture seule ; pas de réseau ni accès aux bases. |
| `import_expert_knowledge` | `--knowledge-dir`, `--config`, `--apply`, `--output` | Dry-run par défaut ; importe une candidate validée, jamais activation. |
| `diagnose_expert_feedback` | `--correction-id`, `--expected-revision`, `--config`, `--run-dir`, `--max-llm-requests` | Crée/reprend le même travail ; défaut 0 appels, diagnostic déterministe. |
| `compile_expert_candidate` | `--diagnosis-id`, `--config`, `--run-dir`, `--max-llm-requests` | 0 par défaut ; patch fourni/revu ou génération explicitement budgétée. |
| `run_expert_memory_evaluation` | `--manifest`, `--config`, `--run-dir` | Planifie/reprend les cellules ; le manifest fixe autorisation/budget et snapshots. |
| `promote_expert_memory` | `--candidate-id`, `--review-id`, `--expected-active-generation`, `--config`, `--apply`, `--output` | Dry-run par défaut ; mêmes gates que l’API, aucune auto-approbation. |
| `rollback_expert_memory` | `--release-id`, `--expected-active-generation`, `--reason`, `--config`, `--apply`, `--output` | Dry-run puis bascule atomique explicite. |

`--run-dir` contient `manifest.json`, `checkpoint.json`, `report.json` et un diff sans données privées
quand applicable ; fichiers écrits atomiquement. Une reprise avec entrées/hashes différents échoue
plutôt que mélanger deux campagnes. Les commandes ne réimplémentent pas le SQL ou l’orchestration.

Pour l’assistance LLM, séparer budgets diagnostic, compilation, revue et évaluation ; défaut total 0.
Le budget évaluation inclut planning, filtre, génération, repasses, juge et réessais fournisseur.
Réserver chaque appel dans le gestionnaire de quota existant avant de l’émettre ; séquencer les
opérations coûteuses locales. Un quota indisponible rend le travail différé/incomplet, pas validé.

## C7. Extension `evidence_note`, lot 14 seulement

Champs supplémentaires : `statement`, matrice/procédé/conditions/résultat, `support_refs`,
`contradiction_refs`, `limits`, `reviewed_at`, `review_state`, `source_hashes`, `recheck_after`.
Une date de recontrôle est un signal de revue, pas une expiration scientifique universelle.
Changement de source, exclusion définitive ou hash non résolu → note `stale` inutilisable jusqu’à revue.

Le routeur peut produire des termes de recherche et des identifiants de supports candidats **avant**
la vague unique. Ces supports prennent place dans les pools/budgets normaux, puis subissent le filtre
global ; ne pas ajouter une deuxième récupération après la synthèse. Le texte de la note n’est jamais
inséré dans `ChatEvidencePassage`, ni dans la liste des preuves autorisées du générateur.

## C8. Manifeste d’évaluation et identité des cellules

Créer `ExpertMemoryEvaluationManifest` avec les champs suivants ; aucune réponse de référence ne
figure dans le payload transmis au chatbot.

| Champ | Contrat |
|---|---|
| `schema_version`, `run_id` | littéral 1, identifiant stable selon les contraintes de campagne existantes |
| `candidate_id`, `base_release_id` | UUID, vérifiés contre la relation de base enregistrée ; hash candidat également obligatoire |
| `dataset_manifest`, `dataset_sha256` | chemin local résolu par le service d’évaluation uniquement, hash du jeu gelé |
| `split`, `purpose` | règles CiderQA existantes ; `final_test` interdit à la compilation et au diagnostic |
| `case_ids` | liste unique non vide, ≤ 500 ; inclusion obligatoire des contrôles de développement pour le replay ciblé |
| `evidence_mode` | `abstract_only` ou `full_text`, même valeur pour les deux bras |
| `answer_effort`, `prompt_profile` | effort existant, profil existant fixé identique dans les deux bras |
| `corpus_snapshot` | emplacement de lecture contrôlé, empreinte et manifestes des index compatibles |
| `code_revision`, `model_versions`, `configuration_sha256` | identités comparables ; configuration détaillée expurgée attachée au run |
| `repetitions`, `seeds` | entier 1–5, seeds explicites ou indication fournisseur non reproductible ; règle V5 pour cas ciblés |
| `argo_authorized`, `max_llm_requests` | faux/0 par défaut ; le budget inclut les deux bras, répétitions, juge et corrections |
| `judge_model`, `judge_prompt_sha256`, `rubric_sha256` | juge indépendant du contexte de compilation ; identité de la grille experte |
| `policy_version`, `policy_sha256` | politique figée avant lancement |

Identité de cellule : `(run_id, arm, repetition, question_id)` ; `arm=base/candidate` reste dans
l’orchestrateur, jamais dans le prompt du répondant ou du juge. Chaque cellule a une conversation
vierge et un job `chat_answer` épinglé ; son UUID d’idempotence dérive de l’identité complète.
Pour conserver les invariants historiques `(evaluation_run_id, profile, question_id)`, créer un
sous-run distinct par bras/répétition et garder le même `profile` scientifique. Ne pas renommer la
candidate `p1` : ces profils portent déjà un sens expérimental distinct.

La projection répondant contient question, effort, source snapshot, version épinglée et paramètres
autorisés seulement. La projection juge contient question, réponse, preuves originales admissibles
et grille attendue, sans diff, diagnostic ou bras. La projection compilateur n’inclut que le cas et
les contrôles de développement ; jamais labels ou corrections dérivées du test final.

Sorties : réponses individuelles et manifestes privés, observations du juge, matrice de comparabilité,
rapports CiderQA base/candidate et `gate.json` avec état `passed/failed/inconclusive`, motifs,
hashes de tous les artefacts et budget réellement consommé. Des métriques fabriquées par le même
processus proposant la correction ne constituent jamais des observations de validation.
