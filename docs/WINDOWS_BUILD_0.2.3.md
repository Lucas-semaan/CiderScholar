# Construction et réinstallation Windows 0.2.3

Date de validation : 2026-08-03.

## Artefacts vérifiés

- `installer/output/CiderScholar-0.2.3-windows-x64.exe` ;
- `installer/output/CiderScholar-0.2.3-windows-x64.exe.sha256` ;
- `installer/output/latest.json`.

L'exécutable mesure 1 583 482 243 octets. Son SHA-256, recalculé indépendamment après la
construction, est :

```text
3f1a3519352d0dc65f8534572c54f1b3597365fb62223b4de60ac3b315bea58a
```

Cette valeur est identique dans le sidecar et dans `latest.json`.

## Contenu et validation automatisée

- payload recréé sans réutiliser le staging 0.2.2 ;
- runtime CPython 3.12.10 et dépendances hors ligne vérifiés ;
- modèles E5 et cross-encoder vérifiés avant compilation ;
- manifeste de 13 011 fichiers en version applicative 0.2.3 ;
- modules de filtrage sémantique, contrôle de couverture, contrats visuels et analyse locale des
  figures présents dans le manifeste ;
- Ruff format et lint réussis sur 365 fichiers ;
- 663 tests Python réussis ;
- formatage, ESLint, TypeScript, 19 fichiers/61 tests Vitest et build Vite réussis ;
- compilation Inno Setup réussie.

## Réinstallation locale et smoke test

L'application 0.2.2 a été arrêtée par son endpoint coopératif alors qu'aucun job n'était actif.
L'installation silencieuse de la 0.2.3 s'est terminée avec le code retour 0, sans supprimer
`UserData`.

Après relance :

- l'API publie la version 0.2.3 et `/health` répond `ok` ;
- la page principale et son bundle JavaScript répondent HTTP 200 ;
- le worker publie un heartbeat récent ;
- le schéma SQLite commun est en version 26 ;
- 2 409 articles, 59 470 fragments tous indexés et 2 253 notices bibliographiques sont conservés ;
- `qwen3-vl:8b-instruct` est annoncé disponible avec une limite de cinq figures.

Cette validation locale ne remplace pas les essais sur profils Windows distincts, les postes 8 Go
et 16 Go, la distribution SharePoint ni la validation CiderQA et experte des observations visuelles.
