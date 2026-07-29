# Construction Windows 0.2.0

Date de construction : 2026-07-27

## Artefacts prêts à publier

- `installer/output/CiderScholar-0.2.0-windows-x64.exe`
- `installer/output/CiderScholar-0.2.0-windows-x64.exe.sha256`
- `installer/output/latest.json`

L’exécutable mesure 1 166 919 178 octets. Son SHA-256, recalculé indépendamment après la
construction, est :

```text
d0ce39e050feb13c77c9ed9a9c76275aa8cd56c4e7430d4c405ba03a0a145989
```

Cette valeur est identique dans le sidecar et dans `latest.json`.

## Vérifications automatiques réalisées

- runtime CPython 3.12.10 recréé depuis les dépendances verrouillées hors ligne ;
- import de l’application et de toutes les dépendances majeures depuis le runtime élagué ;
- intégrité des deux modèles vérifiée avant et après copie ;
- manifeste de payload généré pour 12 998 fichiers, avec taille et SHA-256 individuels ;
- version applicative `0.2.0` présente dans le manifeste ;
- nouveaux workers durables et validateur CiderQA présents dans le payload ;
- compilation Inno Setup réussie.

Le manifeste de payload contient notamment les modèles E5 et cross-encoder complets. Le journal brut
est conservé dans `artifacts/build/windows-release-0.2.0.stdout.log`; le journal d’erreur est vide.

## Validation encore externe

Cette construction n’atteste pas une installation réelle sur un autre profil Windows, ni les profils
physiques 8 Go et 16 Go. Elle est prête pour la publication SharePoint et les essais décrits dans
[`USER_ACTION_CHECKLIST.md`](USER_ACTION_CHECKLIST.md).
