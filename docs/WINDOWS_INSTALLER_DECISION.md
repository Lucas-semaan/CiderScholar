# Décision d’installateur Windows

## Choix retenu

Le pilote utilise **Inno Setup 6.5 en installation par utilisateur**, avec un payload hors ligne :
CPython 3.12.10 x64 embarqué, wheels Windows figées, frontend Vite précompilé et modèle E5 inclus.
L’application s’installe sous `%LOCALAPPDATA%\Programs\CiderScholar`; les données durables restent sous
`%LOCALAPPDATA%\CiderScholar\UserData`.

| Option | Taille et PyTorch/E5 | Droits | Mise à jour | Décision |
| --- | --- | --- | --- | --- |
| Inno Setup 6.5 + CPython embarqué | LZMA2, exécutable jusqu’à 4 Go, payload CPU et E5 inclus | `lowest`, aucun UAC | remplace le programme, conserve `UserData` | Retenu |
| MSIX | Gros payload et fichiers mutables plus contraignants | sans admin possible | excellent versionnement, signature obligatoire | Écarté pour le pilote non signé |
| Archive portable | simple mais volumineuse | aucun admin | pas de désinstallation, raccourcis ni arrêt intégrés | Écartée |
| PyInstaller one-folder | runtime autonome mais analyse dynamique fragile pour Torch/Transformers | aucun admin | payload opaque et plus difficile à auditer | Écarté |

Le build refuse un runtime, un modèle ou un installateur dont le SHA-256 diffère. Inno Setup vérifie
son archive pendant la copie, puis `scripts.verify_desktop_install` revérifie l’intégralité du modèle
avant la fin de l’installation. Le SHA-256 de l’installateur et `latest.json` sont publiés à côté de
l’exécutable. La licence d’usage du compilateur Inno Setup doit être vérifiée par l’organisation avant
une compilation institutionnelle ; cela ne concerne pas l’exécution de l’installateur généré.

## Matrice reproductible

La matrice machine est dans `installer/versions.json`, les dépendances Python d’usage dans
`requirements-runtime.txt` et l’arbre npm complet dans `frontend/package-lock.json`. Node et npm ne
sont utilisés qu’au build. Le poste utilisateur n’a besoin ni de Python système, ni de Node, ni d’un
terminal.

