# CiderScholar

CiderScholar est une application scientifique locale pour construire et interroger une base documentaire sur le cidre : biochimie, microbiologie, polyphénols, protéines, jus de pomme, cidres, Pommeau, Calvados et eaux-de-vie.

L’interface est une SPA React/TypeScript entièrement bâtie avec Tailwind CSS. FastAPI expose les cas d’usage du corpus, de la bibliothèque, du RAG et des synthèses. SQLite reste la source d’autorité ; Qdrant et E5 fonctionnent localement, tandis que la génération passe exclusivement par INRAE ARGO.

## État actuel et cible pilote

Le dépôt se lance actuellement comme un projet de développement. La cible pilote est une application
Windows installable sans terminal sur une dizaine de postes personnels. Chaque utilisateur conservera
ses conversations, ses travaux et ses documents privés sur son poste. Le corpus commun sera préparé
sur la machine administrateur, versionné, puis distribué par SharePoint.

La cible complète et son découpage atomique sont décrits dans
[`docs/ROADMAP.md`](docs/ROADMAP.md). Les décisions d’accès et de stockage sont dans
[`docs/ACCESS_MODEL.md`](docs/ACCESS_MODEL.md). Le futur parcours utilisateur pour enregistrer une clé
ARGO sans modifier de fichier est défini dans [`docs/ARGO_KEY_SETUP.md`](docs/ARGO_KEY_SETUP.md).

## Fonctionnalités

- chatbot scientifique principal en langage naturel, avec historique conversationnel borné, RAG
  local, génération ARGO et citations consultables ;
- tableau de bord de la base documentaire unifiée et de son indexation ;
- import de PDF, import récursif d’un dossier, ingestion, réindexation et suppression confirmée ;
- base filtrable réunissant articles complets et abstracts associés à un DOI vérifié ;
- recherche locale hybride dans les textes extraits et les abstracts, extraction de preuves et réponse ARGO ;
- synthèses hiérarchiques reprenables, citations dérivées de SQLite et exports ;
- paramètres de session validés sans réécriture de `config.yaml` ;
- test optionnel d’acquisition authentifiée auprès de publishers explicitement autorisés, documenté
  dans [`docs/AUTHORIZED_PUBLISHER_TEST.md`](docs/AUTHORIZED_PUBLISHER_TEST.md) ;
- collecte cidricole manuelle, bornée à une cadence de sept jours, et déduplication systématique par
  DOI normalisé ; son automatisation est reportée dans la [`roadmap`](docs/ROADMAP.md).

## Architecture

```text
Navigateur React/Tailwind
        │ HTTP JSON
        ▼
FastAPI ── routes minces (`app/api`)
        ▼
Services applicatifs (`app/services`)
        ▼
Ingestion · recherche · LLM · collecte
        ▼
SQLite (autorité) · Qdrant local · fichiers locaux

L'acquisition DOI-first des textes intégraux (Europe PMC, ISTEX et replis OA), son ordre de priorité,
la variable d'environnement ISTEX et la commande administrateur sont décrits dans le
[guide d'acquisition full text](docs/FULL_TEXT_ACQUISITION.md).
```

Le détail de l’arborescence est dans [`docs/PROJECT_TREE.md`](docs/PROJECT_TREE.md) et les conventions de contribution dans [`AGENTS.md`](AGENTS.md).

## Prérequis de développement

- Windows 11 et Python 3.12 64 bits ;
- Node.js 20 ou plus récent ;
- environ 12 à 20 Go libres avec les modèles et les données.

## Installation de développement actuelle

Dans PowerShell, depuis la racine du projet :

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm.cmd --prefix frontend ci
Copy-Item config.example.yaml config.yaml
```

`config.example.yaml` est le profil sûr : écoute sur `127.0.0.1`, génération ARGO et API bibliographiques désactivées. Seuls les passages bornés nécessaires sont envoyés à ARGO.

## Lancer l’application

Construire l’interface, puis lancer FastAPI qui sert à la fois l’API et la SPA :

```powershell
npm.cmd --prefix frontend run build
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Ouvrir [http://127.0.0.1:8000](http://127.0.0.1:8000). La documentation technique de l’API est disponible sur [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

Pour développer l’interface avec rechargement automatique, lancer FastAPI sur le port 8000 et, dans un second terminal :

```powershell
npm.cmd --prefix frontend run dev
```

Vite ouvre l’interface sur `http://127.0.0.1:5173` et relaie `/api` et `/health` vers FastAPI.

## Configuration actuelle d’ARGO et des sources bibliographiques

Dans l’état actuel du dépôt, les secrets ne sont jamais écrits dans YAML. Ils sont lus depuis les
variables utilisateur Windows :

```powershell
[Environment]::SetEnvironmentVariable("LOCAL_SCIENCE_RAG_ARGO_API_KEY", "<clé>", "User")
[Environment]::SetEnvironmentVariable("OPENALEX_KEY", "<clé-optionnelle>", "User")
[Environment]::SetEnvironmentVariable("CLARIVATE_API_KEY", "<clé>", "User")
[Environment]::SetEnvironmentVariable("ELSEVIER_KEY", "<clé>", "User")
```

Ouvrir un nouveau terminal après modification. ARGO est l’unique moteur génératif. Pour les sources bibliographiques externes, activer `app.allow_bibliographic_apis` ainsi que `bibliographic.enabled`. OpenAlex reste configuré en mode gratuit pour la collecte pilote.

Cette procédure est réservée au développement. Dans la version pilote distribuée, chaque utilisateur
collera uniquement sa propre clé ARGO dans l’écran d’accueil. L’application la vérifiera, puis la
chiffrera pour son compte Windows avec DPAPI. Aucun utilisateur final ne devra ouvrir PowerShell,
modifier `.env` ou recevoir les clés bibliographiques de l’administrateur.

ARGO ne reçoit que la question et les passages bornés nécessaires à la génération. Les fournisseurs bibliographiques ne reçoivent que la requête de recherche, jamais un PDF local.

## Chatbot scientifique

La page d’accueil est l’interface conversationnelle principale. Pour chaque message, CiderScholar :

1. complète la requête de recherche avec les deux dernières questions utilisateur lorsque la
   conversation contient une relance ;
2. recherche dans les chunks des articles complets avec FTS5, agrège les résultats par article et
   sélectionne des passages pertinents avec leurs pages ;
3. complète ces preuves par les abstracts qualifiés uniquement lorsque le texte intégral manque ;
4. transmet à ARGO un ensemble borné de passages full-text et d’abstracts de repli, avec un historique
   conversationnel limité ;
5. rejette toute réponse qui cite une preuve absente, invente une valeur numérique ou transforme un
   résultat expérimental en recommandation non étayée ;
6. affiche le niveau de preuve, les pages, DOI, fournisseurs et limites avec la réponse.

La réponse est rédigée en prose par défaut. Pour obtenir des puces, demandez explicitement une
liste, une checklist ou des étapes ; une consigne comme « sans puces » reste prioritaire. Les
citations auteur-date sont ajoutées par CiderScholar et une section `Références` finale rassemble les
notices APA 7 issues de la base locale. Une donnée bibliographique absente n’est jamais inventée.

L’option « Compléter avec les APIs bibliographiques » est désactivée par défaut. Lorsqu’elle est
activée, les fournisseurs officiels configurés sont interrogés séquentiellement avec une limite de
deux résultats par source. Les résultats passent le filtre cidricole et la déduplication DOI ; au
maximum deux sources externes sont ajoutées aux quatre sources locales prioritaires. Cette recherche
en direct n’ajoute pas automatiquement les notices au RAG. En revanche, lorsqu’un texte intégral
sélectionné est disponible auprès d’un fournisseur officiel, son acquisition est bornée à deux PDF
par question : le PDF, ses chunks et ses vecteurs sont alors conservés définitivement dans le corpus
commun, y compris après la fin de la conversation.

Le contrat HTTP est durable : `POST /api/chatbot/conversations/{id}/jobs` persiste la question et
retourne immédiatement `202`, puis `GET /api/jobs/{id}` suit le worker local. La réponse est relue
depuis la conversation SQLite ; aucune requête web ne reste ouverte pendant l’appel ARGO.

Le ton, la structure, la neutralité scientifique et le rendu APA 7 attendus sont définis dans
[`docs/CHATBOT_RESPONSE_CONTRACT.md`](docs/CHATBOT_RESPONSE_CONTRACT.md).

## Charger et collecter des données

La page « Base documentaire » est le chemin principal. Elle réunit dans une même liste les articles
complets (`Full article`) et les abstracts acceptés sans PDF (`Abstract only`). Un abstract seul
n’apparaît que s’il possède un DOI complet, valide et normalisé. Lorsqu’un PDF porte le même DOI, ses
métadonnées sont fusionnées et une seule fiche `Full article` subsiste. La recherche par mot-clé
couvre les métadonnées, les abstracts et le texte extrait des PDF. Seul un article complet propose
l’ouverture via `GET /api/corpus/{article_id}/pdf`. Les commandes d’exploitation restent disponibles :

```powershell
.\.venv\Scripts\python.exe -m scripts.ingest_folder "C:\chemin\vers\les\PDF" --recursive
.\.venv\Scripts\python.exe -m scripts.ingest_folder "C:\chemin\vers\les\PDF" --recursive --ocr --skip-known --wait-for-memory
.\.venv\Scripts\python.exe -m scripts.rebuild_index
.\.venv\Scripts\python.exe -m scripts.harvest_cider_pilot
.\.venv\Scripts\python.exe -m scripts.harvest_cider_bulk --target 1000 --page-size 50
.\.venv\Scripts\python.exe -m scripts.harvest_cider_bulk --target 500 --query-set materials
.\.venv\Scripts\python.exe -m scripts.harvest_cider_bulk --target 100 --query-set microbiology
.\.venv\Scripts\python.exe -m scripts.audit_microbiology_full_text --target 100
.\.venv\Scripts\python.exe -m scripts.import_ifpc_publications --bibliography-pages 2
```

La collecte est bornée, temporisée et normalement limitée à une exécution tous les sept jours. Elle
alterne quatre vagues thématiques, puis pagine les résultats à chaque nouveau cycle afin de ne pas
reprendre indéfiniment les premiers résultats. Un complément OpenAlex groupé tente de récupérer les
abstracts des DOI déjà retenus ; un échec n’est retenté qu’après trente jours. Un abstract accepté
avec DOI vérifié reste consultable et indexable même si aucun texte intégral n’est disponible. Si le
PDF est acquis plus tard, la déduplication DOI donne automatiquement priorité à l’article complet.

Cette cadence est actuellement appliquée lors du lancement manuel de la commande. Aucun ordonnanceur
hebdomadaire n’est activé ; les prérequis de cette évolution sont suivis dans la
[`roadmap`](docs/ROADMAP.md).

`--force` permet un essai explicite hors cadence et `--no-evaluate` évite les requêtes de contrôle
après une collecte. E5 n’est chargé que si au moins un abstract attend réellement son embedding.

## Moisson initiale massive et archive des rejets

`harvest_cider_bulk` est une opération explicite de constitution initiale, distincte de la collecte
hebdomadaire. Sa cible compte uniquement les nouveaux abstracts acceptés et indexables, pas les
résultats bruts ni les doublons multi-sources. Les requêtes sont paginées, tous les appels restent
séquentiels et le plafond OpenAlex gratuit reste appliqué à chaque cycle.

Les ensembles `focused`, `expanded`, `specialized` et `materials` permettent de choisir la largeur
scientifique de la moisson. `materials` couvre notamment la pomme, les cultivars, les pomaces, les
pelures, les pépins et les coproduits lorsqu’ils éclairent la composition ou la transformation
cidricole. `--sources` limite explicitement les fournisseurs et `--start-page` reprend une collecte
plus profondément sans rejouer les premières pages. Ces options ne désactivent jamais la
déduplication DOI, le filtre de pertinence ni l’archivage préalable des rejets.

Une notice classée `rejected` est d’abord copiée dans la table
`rejected_bibliographic_archive` avec au minimum son DOI et son titre, puis exportée en JSON sous
`data/exports`. Les références `review` restent dans la file technique de qualification ; elles ne
sont visibles ni dans la Base documentaire ni dans le RAG.

Après les tentatives d’enrichissement DOI, toute notice encore dépourvue d’abstract est elle aussi
classée non exploitable, archivée avec son DOI/titre puis retirée de la base active. L’invariant
d’exploitation est donc : une preuve locale provient soit des fragments d’un PDF ingéré, soit d’un
abstract accepté portant un DOI vérifié. Le niveau de preuve reste affiché pour ne pas confondre les
deux.

## Publications IFPC et auteurs ciblés

`import_ifpc_publications` importe le catalogue officiel des cahiers techniques IFPC, contrôle le
type PDF et limite les téléchargements aux hôtes autorisés. Les pages scannées sont OCRisées hors
ligne avec le moteur Windows français ; aucun document n’est envoyé à un service OCR distant. Les
métadonnées fiables du catalogue (titre, année et source IFPC) sont conservées séparément du texte
OCRisé.

La même commande effectue une collecte bibliographique bornée sur Pascal Poupard, Hugues Guichard et
Rémi Bauduin via les fournisseurs configurés. Le DOI reste la clé primaire de rapprochement. Un
repli sur le titre Unicode normalisé et l’année n’est utilisé que pour fusionner une ancienne notice
sans DOI avec son unique version enrichie ; deux notices portant des DOI différents ne sont jamais
fusionnées. `--skip-pdfs`, `--skip-bibliography`, `--bibliography-pages` et `--no-index` permettent une
reprise ciblée et reproductible.

## Qualité

```powershell
.\.venv\Scripts\python.exe -m ruff format --check app scripts tests
.\.venv\Scripts\python.exe -m ruff check app scripts tests
.\.venv\Scripts\python.exe -m pytest -q
npm.cmd --prefix frontend run ci
```

Les règles complètes sont dans [`AGENTS.md`](AGENTS.md). Les données locales, secrets, modèles, index, dépendances Node et builds générés sont exclus du versionnement.

## Confidentialité et sauvegarde

Ne jamais remplacer `127.0.0.1` par `0.0.0.0` sur une machine contenant des documents scientifiques. Le [guide du corpus commun](docs/CORPUS_ISOLATION.md) décrit son stockage et sa mise à jour. Le guide [OneDrive / SharePoint](docs/SHAREPOINT_DISTRIBUTION.md) explique la sélection du dossier utilisateur ainsi que la publication et le rollback administrateur ; le guide [proposer un document](docs/DOCUMENT_SUGGESTIONS.md) précise droits, données transmises et absence de suivi distant. Pour une sauvegarde vérifiée, arrêter les traitements lourds puis exécuter `python scripts/backup_corpus.py` ; la restauration correspondante remplace atomiquement `data/common` en conservant la version précédente.

Le projet est distribué sous licence AGPL-3.0-only.
