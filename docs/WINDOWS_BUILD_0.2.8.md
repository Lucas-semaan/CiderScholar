# Build et installation CiderScholar 0.2.8

Date de validation : 10 août 2026, Europe/Paris.

## Artefact final

- Installeur : `installer/output/CiderScholar-0.2.8-windows-x64.exe`
- Taille : 2 754 172 326 octets
- SHA-256 : `1eff8add69f05772a321be7cc97b5338ba5aabbc8fcf26c91af3cd89f3aae47e`
- Empreinte identique dans le sidecar et `latest.json`
- Payload : 13 040 fichiers contrôlés, sans écart entre le workspace, le staging et
  l'application installée

## Validations

- Ruff format : 392 fichiers conformes
- Ruff lint : aucun défaut
- Backend : 778 tests réussis
- Frontend : 19 fichiers de tests et 71 tests réussis, typage, lint et build Vite réussis
- Runtime installé : vérificateur d'intégrité réussi
- Installation réelle : version Python `0.2.8`, API saine, worker sain, aucun travail actif et
  aucun avertissement de diagnostic

Le build npm a signalé six vulnérabilités de dépendances, une modérée et cinq élevées.
Aucun correctif automatique des dépendances n'a été appliqué pendant la release.

## Durcissement de la mise à niveau

Le premier contrôle strict de l'installation 0.2.8 a détecté quatre anciens fichiers Python
absents du payload courant mais conservés par l'installation précédente. La cause a été
généralisée : l'installeur supprimait l'ancien build frontend, mais pas tous les répertoires
applicatifs remplaçables.

Le contrat Inno supprime désormais `app`, `frontend`, `runtime` et `scripts` avant de recopier le
payload vérifié. `UserData`, qui contient la configuration, le corpus, les modèles, les
conversations et les secrets, reste hors de cette cible et a été conservé pendant le test réel.
Un test de contrat couvre cette propriété.

Après recompilation et réinstallation corrective, les comparaisons strictes donnent :

- application : 205 fichiers attendus et installés, aucun ajout, manque ou changement ;
- scripts : 62 fichiers attendus et installés, aucun ajout, manque ou changement ;
- frontend : 25 fichiers attendus et installés, aucun ajout, manque ou changement ;
- runtime : 12 718 fichiers attendus et installés, aucun ajout, manque ou changement.
