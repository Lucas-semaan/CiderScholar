# FUS-004 — audit licences et sécurité des parsers RAGFlow

Statut : **décision technique conditionnelle — aucun adapter ne doit être intégré ni activé sur la base de cet audit seul.**

Date d'observation : 14 septembre 2026. Périmètre lu : clone local RAGFlow
`C:\Users\lsemaan\AppData\Local\Temp\codex-ragflow-compare-20260913`, commit
`b19a10fb70c64a2a0e29c4a3d20eaa90b969c3b9` (décrit `nightly`, 13 septembre
2026), et sources officielles indiquées ci-dessous. Cette note n'est **pas un
avis juridique**. Une validation humaine des licences, notices et droits de
redistribution est un prérequis G0.

## Conclusion

Recommandation : **Docling comme bibliothèque locale, dans un environnement
Python séparé et épinglé, est le seul candidat à prototyper.** Il doit recevoir
un fichier contrôlé, fonctionner hors réseau avec des poids préinstallés,
hashés et montés en lecture seule, et retourner seulement le contrat
CiderScholar. Son lancement par sous-processus local est acceptable si
l'isolation de dépendances est nécessaire ; il ne doit pas devenir un service
HTTP permanent par défaut.

**Rejeter pour l'instant DeepDoc/RAGFlow direct library et le sidecar
RAGFlow/DeepDoc.** Le premier hérite d'une application RAGFlow complète,
d'une lockfile très large et de téléchargements de poids à l'absence de
fichiers. Le second ajoute Docker, un port HTTP et des surfaces de requêtes ;
il ne devient envisageable qu'après une image minimale reconstruite, SBOM,
digest d'image, authentification locale et tests d'isolation. Ne pas lancer la
pile RAGFlow (MySQL, Elasticsearch/Infinity, MinIO, Redis, chat) pour extraire
un PDF.

| Option | Décision | Raisons principales |
| --- | --- | --- |
| Docling direct library | expérimental, après gate | MIT, Windows/Python supportés ; mais dépendances/modèles à verrouiller et mémoire à mesurer. |
| Docling local subprocess | **préféré** si env isolé | frontière de processus, timeout/limites/effacement des temporaires plus contrôlables. |
| DeepDoc direct library | rejet provisoire | couplage aux imports RAGFlow, poids implicites et énorme graphe de dépendances. |
| DeepDoc local sidecar | rejet provisoire | HTTP local, image Linux, téléchargement au build et pas de contrôle d'accès visible. |

## Provenance, versions, SBOM et licences

Le `LICENSE` racine du clone est Apache-2.0 ; `pyproject.toml` déclare RAGFlow
`0.27.2`, Python `>=3.13,<3.14`, et `uv.lock` comme lockfile globale. Ce
manifeste est celui de toute l'application, pas un manifeste minimal de
DeepDoc. Il inclut notamment clients cloud, navigateurs, connecteurs,
stockages et LLM, donc ne constitue pas une dépendance admissible pour
CiderScholar.

| Élément observé | Version/digest stable observé | Licence déclarée / conséquence |
| --- | --- | --- |
| RAGFlow et DeepDoc embarqué | commit `b19a10fb70c64a2a0e29c4a3d20eaa90b969c3b9`; branche `nightly` | Apache-2.0 ([LICENSE du clone](https://github.com/infiniflow/ragflow/blob/b19a10fb70c64a2a0e29c4a3d20eaa90b969c3b9/LICENSE)). Préserver licence, copyrights et NOTICE éventuels à l'extraction/redistribution ; revue humaine requise. |
| DeepDoc OSS server | `0.1.0`, Python `>=3.11,<3.13` | Pas de lockfile spécifique. Dépend de LitServe, ONNX Runtime, OpenCV-headless, NumPy, Pillow, pyclipper, multipart, Shapely, six : leurs licences transitives doivent être produites par SBOM avant livraison. |
| Docling appelé par RAGFlow | `==2.71.0` seulement dans `docker/entrypoint*.sh`; absent de `uv.lock` | Code Docling MIT ; RAGFlow l'installe à l'exécution depuis un miroir Tsinghua puis PyPI, ce qui n'est pas acceptable. Le paquet courant doit être revalidé, résolu dans un lock séparé et accompagné de ses notices. |
| Docling / docling-core | source officielle : Python `>=3.10,<4`; code MIT | Les modèles ne suivent pas automatiquement MIT : Docling le précise dans son [README officiel](https://github.com/docling-project/docling). |
| Docling PDF | `docling-parse`, `pypdfium2`, et les extras modèles/OCR choisis | Licences transitives et binaires natifs à inventorier dans le lock réellement retenu, pas supposées d'après le package parent. Les extras non retenus (VLM, audio, HTML/Playwright, remote) restent exclus. |
| Modèles Docling | dépôt `docling-project/docling-models`, révision observée `2199320848bb9a8a519d22e4b528185a4f9a6f64`, ~358 MB | La fiche officielle affiche CDLA-Permissive-2.0 et Apache-2.0 : identifier fichier par fichier la licence applicable et obtenir validation humaine, en particulier pour redistribution. Exemple : `tableformer_accurate.safetensors`, 212 758 388 octets, SHA-256 `2a7c…374d`; `fast`, 145 453 276 octets, SHA-256 `3119…74d9`. |
| Modèles DeepDoc | dépôt `InfiniFlow/deepdoc`, révision observée `9c7aa2c730d7a242d7f04cf6109a6a239aefc717` | Apache-2.0 selon la [fiche officielle](https://huggingface.co/InfiniFlow/deepdoc). Voir hashes complets ci-dessous. |

Les références Docling officielles confirment les extras PDF/OCR et l'exécution
hors ligne avec `artifacts_path` : [paquet et extras](https://github.com/docling-project/docling/blob/main/pyproject.toml),
[FAQ offline](https://github.com/docling-project/docling/blob/main/docs/faq/index.md).

### Inventaire DeepDoc des poids et téléchargement

Le serveur OSS annonce ces cinq fichiers, soit environ **102,5 MB décimaux** :

| Fichier | Taille | SHA-256 de la révision observée |
| --- | ---: | --- |
| `layout.onnx` | 75 726 930 | `de401c03ee30b1c120416dc06f0705237f0c36d3cdb692c9bfefe8a8f98a4b70` |
| `det.onnx` | 4 745 517 | `30a86f5731181461d08021402766601e4302a9b9b9666be8aff402696339cdff` |
| `rec.onnx` | 10 826 336 | `1c7cf60de2afd728d512f4190cf37455092b45f06175365c6fc58d8cd7e2a68b` |
| `tsr.onnx` | 12 243 033 | `1585f88015c60209f16a079a26d944afca790ab7022fe7d0574113ccb9a6f9b4` |
| `ocr.res` | 26 249 | non-LFS ; hacher le fichier acquis avant admission |

`deepdoc/server/download_deps.py` télécharge ces fichiers de
`InfiniFlow/deepdoc`, via `HF_ENDPOINT` ou `https://huggingface.co`. Le
`Dockerfile_deepdoc_oss` les télécharge aussi pendant le build et utilise, par
défaut, des miroirs Aliyun/HF Mirror. Plus critique, `pdf_parser.py` et les
recognizers OCR/layout/table appellent `snapshot_download()` lorsque
`rag/res/deepdoc` manque ; le parser télécharge également
`InfiniFlow/text_concat_xgb_v1.0` si le modèle XGBoost est absent. Les poids
ne sont donc ni complets ni hermétiques dans le snapshot.

**Contrôle exigé :** acquisition séparée et approuvée, révision + SHA-256
complet dans un manifeste CiderScholar, miroir interdit, `HF_HUB_OFFLINE=1`
ou équivalent testé, egress bloqué au runtime. La taille disque doit prévoir
poids, caches de wheels/images et pages rasterisées, pas seulement les 102,5
MB ci-dessus.

## Exigences d'exploitation

| Sujet | Observation vérifiée | Politique requise avant essai |
| --- | --- | --- |
| OS/Python | RAGFlow demande Python 3.13 ; DeepDoc OSS 3.11–3.12 et son Dockerfile Ubuntu 24.04/Python 3.12 ; Docling annonce Python 3.10+. | Ne pas installer dans l'environnement applicatif. Utiliser un env dédié compatible Windows pour Docling ; DeepDoc sidecar est Linux/Docker seulement à ce stade. |
| RAM/CPU/GPU | DeepDoc serveur force CPU, un worker par device ; images jusqu'à 4096×4096. Aucun minimum RAM officiel trouvé dans le snapshot. | Mesurer p50/p95 et pic RAM sur FUS-003 ; fixer limites pages, pixels, taille, durée et concurrence. Pas de promesse RAM avant mesure. |
| Disque | DeepDoc ~102,5 MB de poids déclarés ; Docling models ~358 MB à la révision vue, auxquels s'ajoutent wheels, cache et temporaires. | Réserver au moins le poids installé + double de la taille maximale d'entrée par job ; seuil final à mesurer/documenter, avec quota par job. |
| Réseau sortant | `uv pip install` dynamique, Hugging Face/miroirs, `snapshot_download`; Docling RAGFlow effectue HEAD/GET d'une URL de serveur et POST vers elle. | Egress deny-by-default. Autoriser seulement une acquisition administrative explicite, journalisée sans contenu. Runtime : aucune connexion. |
| Temporaires | Le chemin Docling local écrit les bytes à `cwd/.docling_tmp/<nom>` si aucun `output_dir`; il tente ensuite `unlink`. PDFPlumber rend des pages/images ; Hugging Face et pip gardent aussi des caches. | Créer un répertoire par job hors corpus, permissions utilisateur, nom non contrôlé par l'entrée, nettoyage dans `finally`, balayage de reprise et test d'absence après succès/échec. |
| Logs | DeepDoc journalise du texte de boîtes (`TABLE`, `FIGURE`, `WASTE`, `REMOVED`). Docling logge chemin, URL et erreurs ; le client RAGFlow peut enregistrer réponses HTTP tronquées. | Niveau production ≥ WARNING, logger filtrant sans contenu scientifique, chemin local, PDF, URL signée ni réponse distante. Tests de fuite obligatoires. |
| Exécution de code | Aucun `eval`/`exec` trouvé dans le chemin PDF/DeepDoc ciblé ; `ast.literal_eval` est utilisé dans les opérateurs. Les parseurs et bibliothèques natives traitent toutefois des PDF/images non fiables. | Considérer l'extraction comme traitement de contenu hostile : processus à privilèges minimaux, sans secrets/réseau, limites OS et mise à jour CVE. L'absence d'`eval` ne prouve pas l'absence de vulnérabilité native. |

## Surface de données et d'attaque

Le chemin `DoclingParser._parse_pdf_remote` RAGFlow charge tout le PDF en
mémoire, l'encode en base64 puis le `POST` aux endpoints configurés
`/v1[/alpha]/convert/source`; il essaie d'abord le chunking et peut réessayer
jusqu'à quatre fois. Une variable `DOCLING_SERVER_URL` suffit à changer la
destination. Ne jamais réutiliser cette implémentation pour CiderScholar.

Le serveur DeepDoc expose `/health`, `/model`, et trois `POST` de prédiction
multipart sur le port 9390. Le Dockerfile expose ce port sans mécanisme
d'authentification visible et redémarre les workers. Même lié à `127.0.0.1`,
il faut limiter taille de requête/réponse, durée, format MIME, nombre de
pages et concurrence ; sinon une autre application locale peut soumettre des
images ou lire les résultats. Une sidecar future doit écouter un socket
local/port éphémère protégé, refuser les hôtes non loopback et ne recevoir que
un temporaire contrôlé ou octets bornés.

Les formats zip/EPUB et les adaptateurs cloud présents sous `deepdoc/parser/`
ne font pas partie du candidat PDF. Ils sont exclus : ne pas importer le
package parser général ni activer les fournisseurs Tencent/MinerU/Docling
remote. Les PDF pathologiques, bombes de décompression, images très grandes,
XML hostile et sorties JSON trop grandes restent des cas de test FUS-702.

## Gates avant FUS-202/FUS-203

1. Un propriétaire humain valide les licences des packages exacts, licences
   fichier-par-fichier des modèles, obligations de redistribution et marques ;
   l'équipe produit conserve licences et notices distribuées.
2. Produire une SBOM CycloneDX/SPDX du **seul** environnement Docling retenu,
   lock avec hashes, scan de vulnérabilités daté et procédure de mise à jour.
3. Préinstaller modèles et binaires depuis une source approuvée ; inscrire
   version, révision, taille et SHA-256 complets. Refuser modèle absent au
   lieu de télécharger.
4. Test automatisé : aucune connexion sortante, aucun log de texte/PDF,
   temporaires supprimés après succès, timeout et crash, et échec contrôlé sur
   fichier trop grand/corrompu.
5. Mesurer RAM, disque, durée et qualité sur FUS-003 avant toute sélection de
   parser. SQLite reste l'autorité : la sortie parser est dérivée, traçable et
   jamais une preuve synthétique.

## Limites et points à faire valider humainement

Les licences déclarées par des dépôts ne suffisent pas à conclure à une
compatibilité de distribution de CiderScholar. À faire vérifier par un humain :
licence exacte de chaque artefact Docling, licences et avis de tous binaires
ONNX/ORT/OpenCV/PDFium/XGBoost, droit de mettre les modèles dans un installateur
offline, conséquences de l'Apache-2.0 pour une adaptation DeepDoc, et politique
de sécurité/CVE des wheels et images gelées. Cet audit n'a exécuté aucun
parser, téléchargé aucun poids, ni produit de SBOM : ce sont des gates de
validation, non des résultats acquis.
