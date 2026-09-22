# Roadmap de fusion sélective RAGFlow — CiderScholar

Statut : approuvée pour démarrage par l'utilisateur le 14 septembre 2026.

Point de départ CiderScholar : `e42b656fd2ab2929d7b9f5ec222a49fdc2659368`, complété par les changements non commités présents au démarrage. Ces changements doivent être préservés et attribués avant toute modification concurrente.

Référence RAGFlow inspectée : `b19a10fb70c64a2a0e29c4a3d20eaa90b969c3b9`. Pour un composant distribué, épingler une version stable et son digest après l'audit de dépendances ; ne jamais suivre `main` implicitement.

### Journal d'exécution

État au 14 septembre 2026 :

- FUS-001, FUS-101, FUS-102 et FUS-103 sont implémentés et revus ;
- FUS-004 est terminé au niveau technique, avec validation juridique humaine,
  SBOM verrouillée et contrôle des poids encore requis avant tout adapter externe ;
- FUS-003A fournit le manifeste strict, le validateur et les tests, mais les 60
  documents et leurs annotations humaines gelées restent à constituer ;
- FUS-002A a lancé la baseline technique locale sans ARGO ni CiderQA externe ;
  la baseline scientifique signée reste ouverte et `final_test` demeure fermé ;
- FUS-104A cartographie les consommateurs et les contrats à faire évoluer dans
  `docs/audits/FUS-104_DEPENDENCY_MAP.md` ; le raccordement applicatif FUS-104
  n'est pas commencé ;
- FUS-203 est suspendu : l'audit exclut une intégration DeepDoc/RAGFlow tant que
  ses dépendances, téléchargements implicites et contraintes d'isolation ne
  satisfont pas les gates ; Docling isolé est le seul prototype recommandé ;
- aucun adapter externe, modèle ou service RAGFlow n'a été installé ou activé ;
- la branche reste `main` car le workspace contenait des modifications utilisateur
  non attribuées. FUS-000 ne sera clos qu'après attribution ou sauvegarde de ces
  changements ; aucun reset, checkout destructif ou commit automatique n'a eu lieu.

## 1. Objectif et périmètre

La fusion est une intégration sélective, pas l'absorption de RAGFlow dans CiderScholar. CiderScholar reste l'autorité scientifique et le moteur de validation. Les capacités pertinentes à reprendre ou reproduire sont :

- parsers spécialisés et contrat d'extraction commun ;
- prise en charge de PDF complexes, JATS et TEI ;
- structure documentaire et navigation parent-enfant/PageIndex ;
- tableaux et contexte visuel traçables ;
- inspection des chunks et laboratoire de retrieval ;
- expérimentation bornée d'une recherche complémentaire, sans activation implicite en production.

RAGFlow ne reçoit aucun accès direct à SQLite ou Qdrant, ne construit pas les citations finales et ne transforme jamais un contenu synthétique ou externe non persisté en preuve.

Charge complète estimée : 90 à 120 jours-worker. Durée calendaire indicative avec trois workers et un intégrateur : 12 à 16 semaines. Une première version utile avec parsers alternatifs et inspection peut être livrée en 6 à 8 semaines.

## 2. Architecture cible

```text
PDF / JATS / TEI
  -> registre de parsers CiderScholar
       - PyMuPDF actuel
       - Docling optionnel
       - DeepDoc/RAGFlow optionnel et isolé
  -> contrat d'extraction normalisé
  -> admission CiderScholar : DOI, SHA, OCR, provenance, qualité, revue
  -> SQLite, autorité unique : articles, assets, chunks, localisateurs,
     outline, tableaux, figures, corrections
  -> index dérivés et reconstruisibles : FTS5 et Qdrant sans texte autoritaire
  -> retrieval CiderScholar + navigation outline/parent-enfant
  -> filtre sémantique obligatoire A-D
  -> génération structurée sur preuves SQLite
  -> validation des affirmations, nombres, unités, causalité et localisateurs
  -> citations construites par l'application
```

## 3. Invariants non négociables

Chaque worker lit intégralement `AGENTS.md`, `docs/HOW_TO_WORK_ON_CIDERSCHOLAR.md` et `docs/CIDERQA_PROTOCOL.md` avant toute action.

1. SQLite reste l'autorité du texte, des pages, des métadonnées et des preuves.
2. Tout résultat d'index ou de parser externe est validé puis persisté dans SQLite avant d'être probant.
3. Tout texte synthétique porte `citable=false`.
4. Aucun numéro de page fictif n'est attribué aux JATS ou TEI.
5. Aucun fallback de parser n'est silencieux.
6. Les résultats externes restent non probants avant admission et persistance.
7. Le pipeline conversationnel actuel à vague unique reste le défaut.
8. Une recherche complémentaire reste expérimentale jusqu'à promotion CiderQA et décision utilisateur explicite.
9. Les citations sont construites par l'application, jamais reprises aveuglément d'un modèle.
10. Parser, modèle, version et configuration entrent dans les empreintes de cache et d'index.
11. Aucun contenu de PDF, extrait scientifique, question ou réponse générée n'entre dans les logs techniques.
12. Toute migration de corpus est précédée d'une sauvegarde vérifiée et reste reprenable.
13. Une extraction, une structure ou un enrichissement généré ne peut écraser une source originale.
14. Les notices externes, réponses hypothétiques, résumés parents et légendes générées ne sont jamais citables.

## 4. Organisation du travail

Créer une branche d'intégration `codex/ragflow-fusion`. Chaque worker reçoit une seule tâche `FUS-xxx` dans une branche ou un worktree séparé.

Ne pas paralléliser :

- deux migrations SQLite ;
- deux modifications du modèle de localisateur ;
- deux changements de `app/services/workflows.py` ;
- un contrat backend et ses types TypeScript sans coordination ;
- une signature de cache et la logique qui la consomme.

Peuvent être parallélisés après stabilisation des contrats :

- Docling et DeepDoc ;
- JATS et TEI ;
- UI en lecture seule et traitement tableaux/figures ;
- packaging, audit sécurité et mesures de performance.

## 5. Phase 0 — Baseline et décisions

### [x] FUS-000 — Stabiliser le point de départ

Charge : 0,5 à 1 jour.

- relever `git status`, commit, fichiers non suivis et versions Python/Node ;
- attribuer ou intégrer les modifications existantes ;
- ne jamais réinitialiser un changement d'un autre auteur ;
- maintenir dans ce document le commit de base et les écarts intentionnels.

Acceptation : base reproductible, aucun changement perdu et aucun worker sur un fichier sale non attribué.

Réalisation : état relevé le 14 septembre 2026 sur le commit `e42b656fd2ab2929d7b9f5ec222a49fdc2659368` (Python 3.14.6, Node 24.14.1, npm 11.17.0) ; les écarts FUS-001 à FUS-004 et les changements parallèles sont attribués dans les audits, `git diff --check` et les 89 tests FUS ciblés passent.

### [x] FUS-001 — ADR d'architecture

Charge : 1 jour. Dépend de FUS-000.

Créer `docs/adr/ADR-00xx-ragflow-selective-integration.md` avec :

- frontières de confiance et flux de données ;
- classification `source`, `source-derived`, `synthetic`, `external-unpersisted` ;
- parser externe derrière un protocole CiderScholar ;
- chat RAGFlow exclu du chemin de production ;
- index hiérarchiques dérivés ;
- enrichissements générés non citables ;
- recherche complémentaire expérimentale ;
- timeout, taille maximale, version et politique d'échec de chaque appel externe ;
- désactivation et retour au parser actuel.

### [!] FUS-002 — Baseline scientifique signée

Charge : 2 jours. Dépend de FUS-000.

- exécuter CiderQA `development` en `concise`, `balanced` et `deep` ;
- enregistrer empreintes corpus/configuration/modèles/manifestes/commit ;
- conserver les rapports signés ;
- mesurer retrieval, filtre global, génération, validation, latence p50/p95, mémoire et appels ARGO ;
- ne pas ouvrir `final_test`.

Blocage : aucun jeu CiderQA réel gelé et validé par des experts n'est disponible ; une baseline simulée ne peut pas être publiée comme baseline scientifique.

### [ ] FUS-003 — Corpus de benchmark d'extraction

Charge : 2 à 3 jours plus validation humaine.

Préparer un manifeste de 60 documents : 10 PDF texte simple, 10 multi-colonnes, 10 riches en tableaux, 10 scannés/OCR, 10 riches en figures et 10 difficiles ou invalides.

Annoter sur des pages gelées : ordre de lecture, titres/niveaux, texte manquant/dupliqué, tableaux/cellules, figures/légendes, pages et résultat attendu. Ne versionner que les contenus légalement redistribuables ; pour les autres, conserver SHA-256 et instructions de montage.

### [x] FUS-004 — Audit licences et sécurité

Charge : 1 à 2 jours.

- licences RAGFlow, DeepDoc, Docling, OCR et poids ;
- version stable et digest ;
- SBOM et notices ;
- réseau sortant, fichiers temporaires, logs, modèles et exécution de code ;
- décision `direct library`, `local subprocess`, `local sidecar` ou rejet.

Gate G0 : ADR, corpus d'essai et option juridique validés avant les adapters externes.

## 6. Phase 1 — Contrats et provenance

### [x] FUS-101 — Contrat d'extraction normalisé

Charge : 2 jours. Dépend de FUS-001.

Étendre les contrats d'extraction avec :

```text
ParserIdentity:
  parser_id
  parser_version
  contract_version
  config_sha256
  model_name | null
  model_sha256 | null

ExtractionWarning:
  code
  severity: info | review | blocking
  page_number | null
  element_id | null

DocumentOutlineNode:
  node_id
  parent_node_id | null
  level
  kind
  title
  ordinal
  source_locator

ExtractedDocument:
  source_format
  parser_identity
  pages
  outline_nodes
  elements
  ocr_pages
  warnings
```

Exiger : champs inconnus refusés, identifiants déterministes, aucun mélange source/synthétique, anciens caches lisibles, cycles et nœuds orphelins rejetés.

### [x] FUS-102 — Persister les exécutions d'extraction

Charge : 2 jours. Dépend de FUS-101.

Ajouter une migration au prochain numéro réellement libre. Table `extraction_runs` : SHA de l'asset, parser/version/config/modèle, version de contrat, état, compteurs, hash normalisé, durée, article facultatif et diagnostic technique borné.

Tester base neuve, migration N-1, relance idempotente, interruption, cascade et absence de texte scientifique dans les erreurs.

### [x] FUS-103 — Registre et configuration des parsers

Charge : 2 jours. Dépend de FUS-101.

Créer `app/ingestion/parser_registry.py` et une configuration stricte avec les modes `pymupdf`, `docling`, `ragflow_deepdoc`, `auto_experimental`, plus limites fichier/pages/mémoire/timeout et politique `fail`, `review_required`, `explicit_retry_builtin`.

Le défaut reste `pymupdf`. Aucun composant lourd n'est chargé avant sélection explicite.

### [~] FUS-104 — Caches, jobs et API d'ingestion

Charge : 2 à 3 jours. Dépend de FUS-102 et FUS-103.

Faire dépendre le cache du SHA du fichier, parser/version/configuration, modèle OCR/layout et contrat. Exposer parser demandé/réel, réutilisation, warnings et revue dans les jobs/API/types frontend. Aucun fallback silencieux.

Réalisation partielle (14 septembre 2026) : les caches d'extraction identifiés
par parser sont séparés des caches historiques, les exécutions d'extraction sont
créées et finalisées pour une admission, un doublon DOI ou une extraction à
revoir par OCR, et la projection d'ingestion expose parser demandé/réel,
réutilisation, warnings et identifiant d'exécution. Les tests couvrent un
changement de configuration, une reprise, un échec, une déduplication DOI et
le passage OCR vers `review_required`. Le snapshot de sélection dans les jobs
durables et la sémantique explicite des politiques de repli restent ouverts.

## 7. Phase 2 — Adapters

### [x] FUS-201 — Adapter PyMuPDF de référence

Charge : 2 jours. Dépend de FUS-103.

Adapter l'extracteur actuel au registre sans changer ses sorties, ses pages, éléments, OCR ou comportement par défaut. Ajouter une identité complète et des golden tests avant/après.

Réalisation : le registre construit paresseusement l'adapter PyMuPDF avec la configuration active ; un golden test compare sa sortie complète à celle de l'extracteur de référence sur le même PDF, validé par Pytest.

### [ ] FUS-202 — Adapter Docling

Charge : 4 à 5 jours. Dépend de FUS-103 et FUS-004.

Créer un adapter local épinglé. Convertir pages, ordre, sections, tableaux, figures et légendes vers les types CiderScholar. Borner durée/mémoire, supprimer les temporaires et refuser tout téléchargement implicite.

Tester timeout, modèle absent, fichier corrompu, sortie incomplète, Unicode et déterminisme.

### [ ] FUS-203 — Prototype DeepDoc/RAGFlow isolé

Charge : 4 à 6 jours. Dépend de FUS-004 et FUS-103.

Statut : suspendu après audit FUS-004. Ne pas démarrer sans levée explicite des
gates licence, SBOM, poids, téléchargements implicites, ressources et isolation.

Préférer bibliothèque isolée, puis sous-processus, puis sidecar `127.0.0.1`. Rejeter l'option si elle exige la pile complète. Le sidecar éventuel reçoit des octets ou un temporaire contrôlé et renvoie uniquement le contrat CiderScholar. Aucun MySQL, Elasticsearch, MinIO, Redis ou chat RAGFlow.

### [ ] FUS-204 — Harness comparatif des parsers

Charge : 3 jours. Dépend de FUS-003 et des adapters candidats.

Mesurer rappel/précision caractères, lignes dupliquées, ordre de lecture, F1 sections, F1 cellules, figures/légendes, pages, erreurs, p50/p95, mémoire et cache.

Seuils préengagés : pages exactes à 100 % sur l'échantillon audité, aucune preuve critique omise, rappel texte PDF ≥ 98 %, F1 cellules ≥ 0,90 et réduction relative ≥ 20 % des erreurs d'ordre sur PDF complexes pour justifier un nouveau parser.

### [ ] FUS-205 — Politique de sélection

Charge : 1 à 2 jours. Dépend de FUS-204.

Décider parser par défaut, choix manuels, critères déterministes d'auto, types nécessitant revue, coût installation/mémoire et candidats rejetés.

Gate G1 : activation seulement après benchmark, licence, confidentialité et installation Windows acceptables.

## 8. Phase 3 — Assets natifs et localisateurs

### [x] FUS-301 — Autorité générique des assets sources

Charge : 3 jours.

Ajouter `article_source_assets` avec article, type, chemin contrôlé, SHA, média, taille, fournisseur, URL, licence, état primaire et lien vers `native_full_text_assets`. Migrer les `pdf_path` sans supprimer immédiatement la colonne historique.

Réalisation : migration 39 et accès SQLite ajoutent les assets PDF et natifs vérifiés avec hash, provenance, licence et primaire atomique ; les PDF historiques sont rétro-migrés sans suppression de `pdf_path`, validé par Pytest.

### [x] FUS-302 — Localisateur typé

Charge : 4 jours. Dépend de FUS-301.

Créer `chunk_locators` 1:1 :

```text
page: page_start, page_end
structural: section_path, paragraph_start/end, xml_id_start/end
```

Garantir exactement un type, aucun faux `page=1`, hash du span et asset obligatoire. Faire évoluer proprement les pages obligatoires des chunks/preuves sans perdre les anciennes données.

Réalisation : migration 40, rétro-migration PDF et méthodes SQLite ajoutent des localisateurs page ou structurels exclusifs avec asset et hash de span ; les deux voies sont validées par Pytest sans pagination inventée.

### [~] FUS-303 — Propager les localisateurs

Charge : 3 à 4 jours. Dépend de FUS-302.

Mettre à jour chunks, preuves, rehydration, checkpoints, validateurs, API, exports, synthèses et types TypeScript. Rendu PDF `p./pp.` ; rendu XML `§ section, par.` construit uniquement par l'application.

Réalisation partielle (14 septembre 2026) : les passages, sources et UI portent
déjà les localisateurs exclusifs et rendent les repères structurels sans lien
PDF fictif. Les checkpoints de retrieval conservent désormais le type, chemin,
bornes de paragraphes et `xml:id`, sans texte source ; à la reprise, la
réhydratation relit les coordonnées SQLite actuelles et efface explicitement
les pages historiques d'un chunk localisé structurellement. Les recherches
lexicale et hybride résolvent elles aussi le localisateur SQLite autoritaire,
et n'exposent donc plus une page historique lorsqu'un chunk est structurel.
Les tests couvrent la persistance sans texte, la réhydratation du texte et de
la position courants ainsi que l'absence de page pour une source XML.
Les preuves admettent désormais, valident contre SQLite et réhydratent un
localisateur structurel exclusif des pages ; migration 45 conserve les preuves
PDF existantes. L'admission native est couverte par FUS-305 ; la propagation
dans la synthèse longue reste à finaliser.

### [x] FUS-304 — Parsers JATS et TEI sécurisés

Charge : 4 à 5 jours. Dépend de FUS-101 et FUS-302.

Créer des parsers distincts, sans DTD, entités externes ou réseau. Extraire titre, abstract, sections, paragraphes, tableaux, figures et `xml:id`. Générer des identifiants stables quand nécessaire, sans modifier le texte source.

Réalisation : les adapters JATS et TEI lisent localement des XML bornés, refusent avant parsing les DTD et déclarations d'entités, et produisent titre, structure, paragraphes, tableaux, figures, `xml:id` et identifiants structurels déterministes sans numérotation de page fictive ; les cas JATS, TEI et XML dangereux sont couverts par Pytest.

### [ ] FUS-305 — Admission du texte natif

Charge : 3 à 4 jours. Dépend de FUS-301 à FUS-304.

Télécharger/vérifier, relier le DOI, admettre, parser, chunker, persister atomiquement, indexer et marquer `Full article` seulement si complet. Conserver le PDF prioritaire pour la pagination sans effacer l'XML. Relance idempotente.

Avancement (14 septembre 2026) : migration 44 rend `chunks.page_start` et
`page_end` conjointement nullables uniquement pour les contenus non paginés ;
un PDF conserve ses deux bornes valides. La reconstruction conserve les IDs,
les relations et FTS, contrôlés par test de migration. Les JATS et TEI vérifiés
sont désormais contrôlés par hash/taille, parsés, découpés en blocs
structurels et admis transactionnellement avec leurs chunks et localisateurs ;
la relance du même asset est idempotente. La collecte de texte intégral appelle
ce parcours paresseux pour les seuls formats explicitement JATS/TEI, sans
convertir les autres XML ou textes en preuves. Les chunks natifs sont indexés
immédiatement et ciblés à leur article après l'admission ; Qdrant ne reçoit
que des identifiants et SQLite réhydrate le localisateur structurel autoritaire.
L'outline extrait est admis et lié aux chunks dans cette même transaction. Un
viewer local affiche les passages comme données structurées sans servir le XML
comme HTML. Le marquage bibliographique `Full article` exige encore un contrôle
explicite de complétude de source.

### [ ] FUS-306 — Viewer de source native

Charge : 2 à 3 jours. Dépend de FUS-303 et FUS-305.

API et UI en lecture seule, navigation section/paragraphe, surlignage du span, provenance et hash. XML traité comme données, jamais comme HTML arbitraire.

Gate G2 : validation humaine de la retrouvabilité des citations structurelles.

## 9. Phase 4 — Outline, parent-enfant, tableaux et figures

### [x] FUS-401 — Persister l'outline

Charge : 3 jours. Dépend de FUS-101 et FUS-302.

Ajouter `document_outline_nodes` et la liaison chunks/nœuds. Interdire cycles, préserver ordre, asset, localisateur, extraction run et hash de structure. Sans outline, garder les sections actuelles.

Réalisation : migration 41 et accès SQLite persévèrent un outline source-dérivé par asset avec ordre, parent, localisateur, hash déterministe et exécution associée ; une table de liaison 1:1 rattache les chunks uniquement à un nœud du même article. Les validations rejettent les parents absents et liens inter-articles ; sans outline, les chunks existants restent inchangés.

### [x] FUS-402 — Nœuds de retrieval dérivés

Charge : 3 à 4 jours. Dépend de FUS-401.

Créer titres/chemins/résumés parents éventuels avec provenance, version et `citable=false` pour le généré. Index séparé ou `kind` distinct. Un hit parent est toujours développé en chunks SQLite citables.

Réalisation : migration 43 crée des nœuds titre/chemin dérivés des outlines persistés, avec hash de structure, asset d'origine et contrainte SQLite `citable=0`. Leur expansion contrôlée ne retourne que les chunks SQLite citables liés ; ils restent désactivés hors du chemin de recherche de production.

### [ ] FUS-403 — Intégrer l'outline au retrieval actuel

Charge : 3 à 4 jours. Dépend de FUS-402.

Conserver une seule vague : retrieval actuel, ancrage chunk, remontée outline, expansion bornée, sélection scientifique, filtre A-D. Garder 12/16/20 candidats intra-article et 3/4/6 passages. Ajouter des flags désactivés et leur signature de cache.

Avancement : le flag `outline_expansion_enabled`, désactivé par défaut, étend
désormais un résultat hybride avec des chunks SQLite du même nœud d'outline,
bornés par le nombre de candidats et de passages par article. Les compléments
restent des chunks source citables, jamais des nœuds dérivés. La remontée
parent-enfant complète et les signatures de cache restent à relier au workflow
scientifique.

### [~] FUS-404 — Tableaux comme preuve source

Charge : 3 à 4 jours. Dépend de FUS-302 et FUS-401.

Représentation déterministe des cellules, liaison aux chunks voisins et preuve `kind=table`. Vérifier les nombres contre les cellules. Tester unités d'en-tête, cellules fusionnées, décimales, pourcentages, notes, transpositions et tableaux multi-pages.

Avancement : une primitive de preuve tabulaire projette les cellules persistées de manière déterministe, relie les chunks voisins et vérifie les nombres contre les cellules source (décimales à virgule et pourcentages inclus). L'API locale et le dialogue du corpus exposent désormais ces projections pour inspection, sans les promouvoir en texte généré. La propagation vers les modèles de preuves et les cas avancés de tableaux reste à compléter.

### [~] FUS-405 — Figures et contexte visuel

Charge : 3 à 4 jours. Dépend de FUS-401.

Distinguer source citable — légende et texte voisin — et analyse générée non citable. Toute promotion visuelle future exige validation humaine, modèle/version, hash image, observation, limites et localisateur.

Avancement : la projection de preuve figure ne retient que la légende source et les chunks source voisins ; toute légende synthétique est structurellement exclue. L'API locale et le dialogue du corpus permettent de les inspecter sans jamais les confondre avec une analyse générée. La promotion d'analyse visuelle reste hors chemin de preuve et nécessite encore son workflow de validation dédié.

## 10. Phase 5 — Inspection et laboratoire de retrieval

### [x] FUS-501 — API d'inspection

Charge : 2 à 3 jours. Dépend de FUS-401.

Endpoints administrateur paginés pour provenance, outline, chunks, éléments, warnings, indexation, localisateurs et corrections. Aucun texte complet dans une réponse non explicitement demandée.

Réalisation : `GET /api/corpus/{article_id}/inspection` fournit une page bornée de provenance, outline, éléments, métadonnées de chunks, localisateurs, état d'indexation et exécutions/warnings d'extraction ; les contenus de chunks restent exclus et l'endpoint renvoie 404 hors corpus. Les limites et l'absence de fuite de texte sont couvertes par test HTTP.

### [x] FUS-502 — Explorateur documentaire

Charge : 3 jours. Dépend de FUS-501.

UI accessible : arbre sections, chunks, localisateurs, éléments, warnings, parser/version et état. Tous appels via `api.ts`, types dans `types/api.ts`, Tailwind uniquement, états complets et moins de 500 lignes par fichier.

Réalisation : le tableau du corpus ouvre un dialogue clavier-accessible d'inspection, avec état de chargement/erreur et arbre de sections, provenance, exécutions parser/version et warnings. Le client passe par `api.ts`, le contrat est typé dans `types/api.ts`, et aucun texte de chunk n'est rendu.

### [x] FUS-503 — Corrections curatoriales auditées

Charge : 3 à 4 jours. Dépend de FUS-501 et FUS-502.

Ajouter `chunk_corrections` avec hash original, correction, motif, reviewer, état, dates et remplacement. Une approbation invalide embeddings/caches/manifestes, réindexe et conserve l'original.

Réalisation : migration 42, API explicite de proposition/décision et accès SQLite conservent le hash source, le motif, reviewer et l'historique sans modifier le chunk original. Une approbation invalide uniquement ses index dérivés et rend le chunk à réindexer ; embedding, FTS lexical, recherche hybride, manifeste et réhydratation utilisent le texte approuvé.

### [ ] FUS-504 — API du laboratoire de retrieval

Charge : 2 à 3 jours. Dépend de FUS-403.

Retourner candidats FTS/denses, rangs RRF, score article, intention, expansion outline, passages et motifs d'exclusion. Filtre sémantique seulement sur action explicite. Aucune génération ni acquisition.

Avancement : `GET /api/retrieval-lab/inspect` lance explicitement une recherche hybride locale bornée et retourne l'intention déterministe, les candidats sans texte, les rangs et contributions FTS/denses/RRF, les localisateurs, compteurs, dégradation dense et expansion outline. Les scores article, passages sélectionnés et motifs d'exclusion restent à intégrer ; aucun filtre sémantique, LLM ou acquisition n'est déclenché.

### [ ] FUS-505 — Interface de comparaison

Charge : 2 à 3 jours. Dépend de FUS-504.

Comparer profil courant/candidat, preuves gagnées/perdues, temps et compteurs. Export sans texte par défaut, export complet seulement par action explicite.

## 11. Phase 6 — Recherche complémentaire expérimentale

### [ ] FUS-601 — Modèle de lacunes

Charge : 2 jours. Dépend de FUS-403 et FUS-504.

Créer en évaluation seulement un `EvidenceGap` par besoin de vérification, avec type, requête bornée, raison et historique. Pas d'axes conversationnels, maximum trois requêtes.

Avancement : les contrats `EvidenceGap` et `EvidenceGapPlan` sont isolés dans `app/evaluation/` : type fermé, requête et raison bornées, historique anti-répétition, unicité et plafond de trois lacunes sont validés avant tout retrieval. Ils ne sont exposés à aucune route conversationnelle ni acquisition.

### [ ] FUS-602 — Seconde vague bornée dans le harness

Charge : 2 à 3 jours. Dépend de FUS-601.

Une seule vague complémentaire, uniquement pour les lacunes, uniquement SQLite, sans acquisition externe. Réhydratation, déduplication, filtre A-D et fusion obligatoires. Ne pas brancher dans le chat de production.

Avancement : le harness `run_second_wave` n'accepte qu'un plan de lacunes validé et un récupérateur SQLite injecté ; il lance une requête par lacune, déduplique avec la première vague et ne conserve que les preuves A/B. Il reste sans route ni branchement au chat ; la réhydratation concrète depuis le retrieval et le checkpoint de reprise restent à raccorder.

### [ ] FUS-603 — Reprise, caches et traces

Charge : 2 jours. Dépend de FUS-602.

Checkpoint de première vague, lacunes, requêtes, seconde vague, décisions et versions. Une reprise ne rejoue que l'étape manquante ; toute différence pertinente invalide le checkpoint.

Avancement : `SecondWaveCheckpoint` porte la première vague, le plan de lacunes, la seconde vague, le hash du corpus et la version retrieval. Sa signature de contenu invalide toute différence pertinente et `next_stage()` détermine l'unique étape manquante. Le stockage durable du checkpoint et la décision finale restent à connecter au harness complet.

### [ ] FUS-604 — Ablation CiderQA

Charge : 2 à 3 jours. Dépend de FUS-603.

Comparer baseline, outline, seconde vague et combinaison, une variable à la fois. Appliquer tous les seuils absolus et budgets de régression de `ciderqa_promotion.py`.

Avancement : une matrice Fusion distincte et signée fige les quatre configurations d'évaluation : baseline, outline seul, seconde vague seule et combinaison. Les rapports d'exécution reliés aux seuils de promotion restent à brancher au runner CiderQA ; aucun flag de produit n'est activé par cette matrice.

### [ ] FUS-605 — Décision de promotion

Charge : 1 jour. Dépend de FUS-604.

Décision `rejet`, `expérimental` ou `proposition de promotion`. La promotion exige accord utilisateur, mise à jour méthodologique, flag désactivé dans la première release et retour immédiat possible à la vague unique.

## 12. Phase 7 — Exploitation et release

### [ ] FUS-701 — Packaging et capacités

Charge : 2 à 3 jours.

Dépendances parser séparées, installation standard légère, modèles préinstallés et hashés, diagnostic disponibilité/version et aucun téléchargement à la première requête.

### [ ] FUS-702 — Sécurité et confidentialité

Charge : 2 jours.

Tester XML hostile, PDF pathologique, fichier excessif, traversée de chemin, fuite logs, réseau, temporaires, injections et réponse sidecar surdimensionnée.

### [ ] FUS-703 — Sauvegarde et reconstruction

Charge : 2 à 3 jours.

Sauvegarder/restaurer SQLite, assets, caches, outlines, corrections, manifestes et Qdrant. Tester reconstitution complète et coupure pendant reindexation.

### [ ] FUS-704 — Observabilité

Charge : 1 à 2 jours.

Métriques non textuelles : parser/version, durées, pages/éléments, warnings, cache, mémoire, expansions, candidats, erreurs, reprises et fallback.

### [ ] FUS-705 — Résilience et performance

Charge : 2 à 3 jours.

Tester concurrence, parser bloqué, redémarrage, mémoire, Qdrant indisponible, index obsolète, migration interrompue, sidecar arrêté, source supprimée et corpus modifié entre checkpoint et reprise.

### [ ] FUS-706 — Pilote et release

Charge : 2 jours plus observation.

Déployer successivement : présent mais désactivé, activation interne manuelle, petit sous-groupe, observation, pilote complet, puis auto éventuel. Aucun retrieval complémentaire sans Gate G3.

Rollback immédiat en cas de localisateur faux, synthétique utilisé comme preuve, régression CiderQA, divergence SQLite/Qdrant, fuite dans les logs, réseau inattendu, mémoire incompatible ou ingestion non reprenable.

## 13. Chemin critique

```text
FUS-000
  -> FUS-001
  -> FUS-101
  -> FUS-102 / FUS-103
  -> FUS-104
  -> FUS-201 / FUS-202 / FUS-203
  -> FUS-204
  -> FUS-205
  -> FUS-301
  -> FUS-302
  -> FUS-303 / FUS-304
  -> FUS-305
  -> FUS-401
  -> FUS-402
  -> FUS-403
  -> FUS-504
  -> FUS-604
  -> FUS-706
```

## 14. Definition of Done commune

Une tâche fournit :

- code limité au périmètre ;
- tests représentatifs, dont un cas négatif ;
- migration ascendante et reprise lorsque nécessaire ;
- invalidation des caches concernés ;
- documentation des nouvelles variables, commandes et routes ;
- aucun secret, corpus, PDF, cache, modèle ou index versionné ;
- aucun changement scientifique silencieux ;
- rapport final des fichiers, tests, migration, rollback et risques.

Contrôles ciblés pendant le développement, puis suite complète à chaque fin de phase :

```powershell
.\.venv\Scripts\python.exe -m ruff format --check app scripts tests
.\.venv\Scripts\python.exe -m ruff check app scripts tests
.\.venv\Scripts\python.exe -m pytest -q
npm.cmd --prefix frontend run ci
git diff --check
```

## 15. Prompt standard worker Terra medium

```text
Implémente uniquement la tâche FUS-XXX de la roadmap RAGFlow/CiderScholar.

Avant toute modification, lis AGENTS.md, docs/HOW_TO_WORK_ON_CIDERSCHOLAR.md,
docs/CIDERQA_PROTOCOL.md et les fichiers cités par la tâche. Inspecte git status
et préserve tous les changements qui ne t'appartiennent pas.

Contraintes scientifiques : SQLite reste l'autorité ; aucun contenu synthétique
ou externe non persisté ne devient une preuve ; aucune citation ou page n'est
inventée ; aucun fallback n'est silencieux ; les versions et configurations
invalident leurs caches ; le pipeline conversationnel à vague unique ne change
pas sauf instruction explicite de la tâche.

Implémente code, tests, migrations et documentation demandés. N'élargis pas le
périmètre. Si un contrat préalable manque, rapporte le blocage plutôt que de
créer une interface concurrente.

Exécute les contrôles ciblés puis rapporte : fichiers modifiés, comportement,
tests et résultats, migration/reprise/rollback, risques ou décisions restantes.
```

## 16. Releases successives

- Release A — ingestion enrichie : FUS-000 à FUS-205 ; parsers alternatifs manuels, défaut inchangé.
- Release B — full text natif : FUS-301 à FUS-306 ; JATS/TEI citables par localisateur structurel.
- Release C — navigation scientifique : FUS-401 à FUS-405 ; outline, parent-enfant, tableaux et contexte visuel.
- Release D — outils curatoriaux : FUS-501 à FUS-505 ; inspection, corrections auditées et laboratoire.
- Release E — recherche complémentaire expérimentale : FUS-601 à FUS-605 ; aucune promotion implicite.
- Release F — généralisation pilote : FUS-701 à FUS-706.
