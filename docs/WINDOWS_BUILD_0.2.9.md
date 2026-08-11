# Build et installation CiderScholar 0.2.9

Date de validation : 11 août 2026, Europe/Paris.

## Artefact final

- Installeur : `installer/output/CiderScholar-0.2.9-windows-x64.exe`
- Taille : 2 930 852 929 octets
- SHA-256 : `5310ca1557795dd5709f93c969748ac0fadc711d32cb0e6bd942abba29d8fd15`
- Empreinte identique dans le sidecar et `latest.json`
- Payload : 13 048 fichiers contrôlés avant compilation

## Validations source

- Ruff format : 406 fichiers conformes
- Ruff lint : aucun défaut
- Backend : 830 tests réussis
- Frontend : 28 fichiers de tests et 93 tests réussis, typage, lint et build Vite réussis
- Dépendances frontend : `npm audit` à zéro vulnérabilité ; script d'installation
  `esbuild@0.25.12` explicitement approuvé dans le manifeste npm

## Validation installée

- Installation silencieuse terminée avec le code 0
- Version Python et registre Windows : `0.2.9`
- Vérification embarquée du runtime et des deux modèles réussie
- Configuration locale et base du corpus commun préservées dans `UserData`
- Parité application/runtime : 13 017 fichiers attendus et installés, aucun ajout, manque ou
  changement de taille ou de SHA-256

Le contrôle post-installation utilise Python avec l'option `-B`. Il vérifie donc le runtime et les
modèles sans créer de `__pycache__` dans les répertoires applicatifs fraîchement installés.
