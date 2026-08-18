# Expansion locale multi-sources du corpus

Cette campagne couvre les huit thèmes du corpus avec cinq familles de requêtes : `focused`,
`expanded`, `specialized`, `materials` et `microbiology`. Elle interroge uniquement les API
bibliographiques configurées, séquentiellement, sans Argo, puis déduplique par DOI dans SQLite.

Ici, `materials` signifie **matières premières et coproduits cidricoles**, et non matériaux
industriels : pomme et fractions du fruit, jus ou moût, marc, pulpe, peau, pépins, gâteau de presse,
pectines, fibres, polyphénols, protéines et azote. Cette famille couvre leur composition, extraction,
valorisation et influence sur les procédés. `microbiology` approfondit les microorganismes ;
`focused` privilégie la filière exacte ; `expanded` élargit aux matrices transférables ;
`specialized` vise les mécanismes et méthodes plus discriminants.

## Collecte reprenable jusqu’à saturation

Créer d’abord une sauvegarde SQLite vérifiée. Lancer ensuite l’orchestrateur avec une durée maximale
explicite et un dossier stable :

```powershell
.\.venv\Scripts\python.exe -m scripts.expand_corpus_until_saturation `
  --sources crossref europe_pmc openalex clarivate elsevier hal core doaj semantic_scholar istex datacite openaire zenodo pubmed pubag `
  --query-sets focused expanded specialized materials microbiology `
  --timeout-hours 10 `
  --run-dir data\exports\corpus-expansion-all-themes
```

Chaque couple source × famille possède son profil et reprend au prochain couple vague/offset après une
interruption. Une page par famille est visitée en rotation afin de partager les petits budgets entre
les thèmes. Une famille est saturée après deux rotations thématiques complètes sans nouvel abstract
accepté. Elle est alors marquée `closed_weekly` pendant sept jours dans
`data/common/bibliographic-weekly-closures.json`, afin d'éviter des requêtes quotidiennes inutiles.
La fermeture porte uniquement sur cette source et cette famille de tags ; les autres familles de la
même source continuent. À l'échéance, une nouvelle rotation est autorisée et un nouveau gain efface la
fermeture. Un quota, une clé absente, un 403/429 ou une erreur fournisseur ne devient jamais une fausse
fermeture scientifique hebdomadaire. Deux lots entièrement vides et en erreur marquent la source comme limitée. Deux backfills DOI
consécutifs sans abstract suspendent seulement le backfill du cycle ; la pagination bibliographique
continue. Les réponses 429, budgets insuffisants, clés absentes et 403 restent distingués dans
`checkpoint.json` et les journaux. Un `limited_until_...` est automatiquement repris depuis le même
checkpoint après l'heure indiquée ; une source auparavant sans clé est aussi retestée au redémarrage.
Pour OpenAlex, un budget journalier insuffisant sans date explicite est repris au prochain minuit UTC.
Par défaut, l'orchestrateur attend ces fenêtres lorsqu'elles précèdent le timeout de campagne ;
`--no-wait-for-retries` permet seulement une passe non bloquante explicitement demandée.
Une reprise avec le même dossier conserve l'échéance initiale enregistrée dans le checkpoint.
Après une demande utilisateur explicite de prolongation ou de reprise après cette échéance,
`--reset-deadline` ouvre une nouvelle fenêtre `--timeout-hours` tout en conservant les profils, offsets
et l'historique des échéances dans le même checkpoint.

Pour une campagne « toutes les sources disponibles », la liste de découverte comprend Crossref,
Europe PMC, OpenAlex, Clarivate, Elsevier/Scopus, HAL, CORE, DOAJ, Semantic Scholar, ISTEX,
DataCite, OpenAIRE, Zenodo, PubMed via les E-utilities officielles du NCBI et USDA PubAg via son
endpoint Primo public.
HAL, CORE, DOAJ, DataCite, OpenAIRE, Zenodo, PubMed, PubAg et la recherche de notices ISTEX sont d'abord essayés en
mode public. Une clé CORE
ou Semantic Scholar, lorsqu'elle existe, augmente le quota et reste facultative ; un 429 public est
persisté avec son heure de reprise. Unpaywall n'est pas une source de découverte thématique : il est
utilisé ensuite comme résolveur DOI de texte intégral. Les clés CORE, Semantic Scholar et le jeton
ISTEX peuvent être conservés dans le coffre DPAPI administrateur avec
`scripts.set_admin_bibliographic_key`; leur valeur n'entre jamais dans un checkpoint ou un journal.
En mode anonyme, Zenodo limite une page de recherche à 25 notices et son endpoint de recherche à
30 requêtes par minute. L'adaptateur conserve donc les pages logiques du checkpoint, borne `size` à
25 et espace les appels d'au moins 2,1 secondes ; un HTTP 400 ne doit jamais être interprété comme une
absence de résultats pertinents.

Si une clé Semantic Scholar arrive pendant qu'un orchestrateur SQLite lancé sans cette clé est encore
actif, ne pas lancer un deuxième writer. Stocker la clé dans le coffre DPAPI puis lancer la découverte
officielle dans un staging en lecture seule ; ses résultats seront validés et importés après l'arrêt de
l'orchestrateur :

```powershell
$env:CIDERSCHOLAR_LOCAL_PROFILE = "admin"
.\.venv\Scripts\python.exe -m scripts.harvest_semantic_scholar_discovery `
  --query-sets specialized materials microbiology focused expanded `
  --pages-per-query 10 --page-size 100 --max-results 40000 `
  --timeout-hours 8 --run-dir data\exports\semantic-scholar-discovery
```

Le script exige le profil administrateur, hydrate la clé uniquement dans son processus, ignore les DOI
déjà présents d'après un instantané SQLite en lecture seule et conserve le record fournisseur dans
`results.jsonl`. Les 429 sont différés jusqu'à l'heure indiquée ; la clé n'apparaît jamais dans la
ligne de commande, le checkpoint ou les journaux.

Le même staging reprenable sert à rattraper Zenodo sans lancer un deuxième writer SQLite, notamment
après correction d'une limite de pagination. Il utilise l'API officielle publique, une page de 25 et
la même échéance que la campagne principale :

```powershell
.\.venv\Scripts\python.exe -m scripts.harvest_zenodo_discovery `
  --query-sets specialized materials microbiology focused expanded `
  --pages-per-query 10 --page-size 25 --max-results 40000 `
  --deadline <échéance ISO-8601 du checkpoint principal> `
  --run-dir data\exports\zenodo-discovery
```

Ajouter son `results.jsonl` aux mêmes entrées de validation exacte et d'import que Semantic Scholar,
uniquement après l'arrêt du writer principal.

Les résultats bruts ne sont pas le corpus final. Les admissions repassent par le filtre éditorial ; un
nom de lieu comme Calvados ou La Sidra, un homonyme, une métaphore et une orientation sociale,
historique, archéologique ou médicale ne suffisent pas. Un abstract automatique sans DOI reste en
`review`. Une notice sans abstract reste hors corpus abstrait après les tentatives d’enrichissement.

## Découverte connexe par graphe de citations

Pendant que l’orchestrateur SQLite tourne, OpenCitations peut explorer en lecture seule les citations
entrantes et références sortantes d’un échantillon équilibré de DOI acceptés dans les huit thèmes.
Les graines récentes et sous 250 citations sont prioritaires afin d’éviter les réponses monolithiques
des articles extrêmement cités :

```powershell
.\.venv\Scripts\python.exe -m scripts.harvest_citation_discovery `
  --relations citation reference --max-seeds 1000 --max-candidates 40000 `
  --timeout-hours 10 --run-dir data\exports\citation-discovery
```

Le collecteur respecte la limite publique OpenCitations de 180 requêtes par minute, reprend depuis
`checkpoint.json` et ne modifie ni SQLite ni Qdrant. `candidate-relations.jsonl` conserve pour chaque
arête le DOI graine, le type de relation, le DOI connexe, le fournisseur et la date. OpenCitations Meta
fournit ensuite le titre utilisé pour le filtre préliminaire ; seuls les candidats `accepted` ou
`review` alimentent `results.jsonl`. Un jeton OpenCitations facultatif peut être stocké sous le
fournisseur `opencitations` dans le coffre DPAPI, mais le mode public est essayé en premier.

Après l’arrêt de l’orchestrateur, ajouter ce dossier aux entrées de
`scripts.import_web_discovery`. Chaque DOI est alors résolu exactement par Crossref ou OpenAlex et la
pertinence est recalculée avant toute insertion. Une relation de citation ne vaut jamais admission.

## Indexation incrémentale pendant la collecte

Un seul worker local peut maintenir la collection Qdrant `bibliographic_abstracts` pendant que le
harvest écrit dans SQLite. Il ne modifie jamais les métadonnées scientifiques et travaille par petits
lots, ce qui libère le verrou Qdrant entre deux passes :

```powershell
.\.venv\Scripts\python.exe -m scripts.run_incremental_abstract_indexer `
  --timeout-hours 10 --poll-seconds 30 --max-batches-per-pass 1 `
  --log-path data\exports\corpus-expansion-all-themes\incremental-abstract-indexer.jsonl
```

Lancer cette commande juste après l'orchestrateur d'expansion, avec le même timeout et le même dossier
de campagne ; elle constitue la routine opérationnelle des enrichissements futurs. Le fichier JSONL
est reprenable et journalise une ligne par passe. `has_pending_after_pass` est une sonde booléenne
(`true`/`false`), non un compte exhaustif, afin de ne pas rallonger la fenêtre du verrou Qdrant.

SQLite reste l'autorité. Le worker lit en WAL et n’accuse réception d’un vecteur que si le hash du
contenu est inchangé après l'upsert Qdrant. Une notice enrichie concurremment reste donc `pending` et
est réencodée lors d’une passe ultérieure. En cas de verrou Qdrant déjà pris, le worker journalise
`deferred_qdrant_busy` et réessaie ; ne pas lancer un second worker ni une commande `--recreate`.
La consolidation finale demeure obligatoire pour le reclassement, la purge et la vérification exacte.

## Moteurs HTML explicitement autorisés

Lorsque l'opérateur des moteurs sans API a explicitement autorisé la collecte HTML, lancer la
découverte dans un dossier séparé. Cette phase ne touche pas SQLite et peut donc tourner pendant le
collecteur API :

```powershell
.\.venv\Scripts\python.exe -m scripts.harvest_web_discovery `
  --engines bing duckduckgo brave yahoo `
  --query-sets specialized materials microbiology focused expanded `
  --pages-per-query 3 --max-results 40000 --timeout-hours 3 `
  --run-dir data\exports\web-discovery
```

`results.jsonl` conserve URL, titre, extrait, DOI éventuel, requête et décision préliminaire ;
`pages.jsonl` et `checkpoint.json` rendent la collecte reprenable. Un résultat HTML reste un candidat
de découverte : il doit être confirmé par DOI exact via une API ou par une page institutionnelle
attribuable avant insertion. Le collecteur ne résout pas de CAPTCHA, ne contourne pas un 403/429 et
ne transforme jamais un extrait de moteur en abstract scientifique. Un moteur qui renvoie du bruit
non scientifique est mesuré puis écarté par le filtre, sans remplir artificiellement le corpus.

Après un arrêt forcé, une ligne de run peut rester `running` alors que son processus n'existe plus.
Vérifier d'abord dans le système qu'aucun collecteur de métadonnées n'est encore actif, puis récupérer
uniquement les identifiants interrompus explicitement constatés. La commande refuse d'agir si la
liste fournie ne correspond pas exactement à toutes les lignes encore `running`; elle conserve les
hits déjà persistés, recalcule les compteurs et journalise la raison :

```powershell
.\.venv\Scripts\python.exe -m scripts.recover_interrupted_harvest_runs `
  --run-id <run-id-1> --run-id <run-id-2> `
  --reason "processus de campagne interrompu et absence de writer vérifiée" `
  --apply
```

Ne jamais utiliser cette reprise pour faire disparaître un run lent ou un processus encore vivant.
Le rapport JSON produit doit être conservé avec les autres preuves de campagne.

Après l'arrêt du collecteur SQLite, valider puis importer les candidats. Lancer d'abord sans
`--apply` pour produire l'audit, puis reprendre exactement le même dossier avec `--apply` :

```powershell
.\.venv\Scripts\python.exe -m scripts.import_web_discovery `
  --input-run-dir data\exports\web-discovery-brave `
  --input-run-dir data\exports\web-discovery-yahoo `
  --input-run-dir data\exports\semantic-scholar-discovery `
  --input-run-dir data\exports\citation-discovery `
  --run-dir data\exports\web-discovery-validation

.\.venv\Scripts\python.exe -m scripts.import_web_discovery `
  --input-run-dir data\exports\web-discovery-brave `
  --input-run-dir data\exports\web-discovery-yahoo `
  --input-run-dir data\exports\semantic-scholar-discovery `
  --input-run-dir data\exports\citation-discovery `
  --run-dir data\exports\web-discovery-validation --apply
```

Un DOI de résultat est résolu exactement par Crossref, avec repli OpenAlex. Sans DOI, le titre doit
correspondre strictement à Crossref puis le DOI être confirmé par OpenAlex. L'insertion réapplique
encore le filtre de pertinence sur les métadonnées fournisseur ; le snippet ne devient jamais
l'abstract du corpus. La commande `--apply` refuse tout harvest SQLite encore actif.

## Consolidation unique après l’arrêt des collecteurs

La consolidation exige le manifeste de la sauvegarde pré-campagne et refuse de s’exécuter tant qu’un
harvest est `running` :

```powershell
.\.venv\Scripts\python.exe -m scripts.finalize_corpus_expansion `
  --apply `
  --backup-manifest C:\chemin\science_rag-before-campaign.manifest.json
```

Elle normalise les textes, réapplique le filtre courant à tous les hits, place les abstracts sans DOI
en revue, archive puis purge les rejets automatiques, met à jour l’index des abstracts et vérifie
exactement SQLite et Qdrant. La décision manuelle d’un utilisateur n’est pas annulée par cette passe.

## Texte intégral

Après consolidation, auditer puis acquérir légalement les contenus sur toutes les sources disponibles :

```powershell
.\.venv\Scripts\python.exe -m scripts.harvest_full_text `
  --audit-only --refresh-cache `
  --sources europe_pmc istex core hal semantic_scholar openalex unpaywall doaj crossref elsevier

.\.venv\Scripts\python.exe -m scripts.harvest_full_text `
  --refresh-cache --max-downloads 1000 --max-native-downloads 1000 `
  --sources europe_pmc istex core hal semantic_scholar openalex unpaywall doaj crossref elsevier
```

Les contenus HTML, les réponses non PDF et les accès refusés ne sont jamais promus en texte intégral.
Les états différés sont repris après le délai fournisseur ; les échecs permanents restent auditables.
