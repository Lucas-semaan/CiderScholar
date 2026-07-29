# Installation et exploitation sous Windows 11

Ce document décrit l’environnement de **développement**. Pour un poste utilisateur, télécharger
l’installateur Windows et son fichier SHA-256 depuis SharePoint, lancer l’exécutable puis suivre
l’assistant : aucun Python, Node, Git ou terminal n’est requis. Voir aussi
`SHAREPOINT_INSTALLATION.md` et `WINDOWS_TROUBLESHOOTING.md`.

## Prérequis

- Windows 11 x64 ;
- Python 3.12 x64 ;
- Node.js 20+ et npm ;
- Git ;
- 12 à 20 Go libres selon les modèles et le corpus.

Vérifier les outils :

```powershell
py -3.12 --version
node --version
npm --version
```

## Installation

```powershell
Set-Location "C:\chemin\vers\local-science-rag"
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm.cmd --prefix frontend ci
Copy-Item config.example.yaml config.yaml
```

L’activation de la venv est facultative : toutes les commandes de ce guide appellent directement son interpréteur.

## Modèles d’inférence locaux

```powershell
.\.venv\Scripts\python.exe -m scripts.prepare_embedding_model --allow-network
.\.venv\Scripts\python.exe -m scripts.prepare_reranker_model --allow-network
```

E5 et le cross-encoder multilingue sont copiés sous `data\models`, accompagnés d’un manifest SHA-256,
puis toujours chargés avec `local_files_only=True` et `trust_remote_code=False`. Le cross-encoder
reste désactivé jusqu’à l’activation scientifique du mode approfondi. Les modèles, le RAG et les
documents restent locaux ; seuls les passages bornés nécessaires à la génération sont envoyés à ARGO.

## Clé ARGO et secrets optionnels

```powershell
[Environment]::SetEnvironmentVariable("LOCAL_SCIENCE_RAG_ARGO_API_KEY", "<clé>", "User")
[Environment]::SetEnvironmentVariable("OPENALEX_KEY", "<clé-optionnelle>", "User")
[Environment]::SetEnvironmentVariable("CLARIVATE_API_KEY", "<clé>", "User")
[Environment]::SetEnvironmentVariable("ELSEVIER_KEY", "<clé>", "User")
```

Fermer puis rouvrir le terminal. Ne jamais ajouter ces valeurs à `config.yaml`.

## Build et lancement

```powershell
npm.cmd --prefix frontend run build
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Ouvrir `http://127.0.0.1:8000`. Arrêter avec `Ctrl+C`. Ne pas écouter sur `0.0.0.0` lorsque le corpus est privé.

## Développement frontend

Garder FastAPI actif sur le port 8000 puis, dans un second terminal :

```powershell
npm.cmd --prefix frontend run dev
```

Ouvrir `http://127.0.0.1:5173`.

## Import et index

L’interface Corpus permet l’import et la réindexation. En ligne de commande :

```powershell
.\.venv\Scripts\python.exe -m scripts.ingest_folder "D:\Articles scientifiques" --recursive
.\.venv\Scripts\python.exe -m scripts.rebuild_index
```

Un seul processus doit ouvrir un stockage Qdrant embarqué donné. Arrêter l’application avant une reconstruction complète avec `--recreate`.

## Sauvegarde

Arrêter FastAPI et les scripts, puis copier au minimum :

- `data\pdf` ;
- `data\database` ;
- `data\qdrant` ;
- `data\models` ;
- `config.yaml`.

Ne jamais copier `data\qdrant` pendant qu’un processus l’utilise.

## Diagnostic et validation

```powershell
.\.venv\Scripts\python.exe -c "from app.config import load_settings; from app.llm.argo_client import ArgoClient; s=load_settings(); c=ArgoClient(s); print(c.health().model_dump_json(indent=2)); c.close()"
.\.venv\Scripts\python.exe -m pytest -q
npm.cmd --prefix frontend run ci
```

Les journaux FastAPI signalent les erreurs d’API sans inclure le texte intégral des PDF ni les secrets.
