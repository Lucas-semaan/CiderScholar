# Construction et réinstallation Windows 0.2.4

Date de validation : 2026-08-05.

## Artefacts vérifiés

- `installer/output/CiderScholar-0.2.4-windows-x64.exe` ;
- `installer/output/CiderScholar-0.2.4-windows-x64.exe.sha256` ;
- `installer/output/latest.json`.

L'exécutable mesure 2 751 258 333 octets. Son SHA-256 est :

```text
3c65895d74aa395d83dd65633060acdd08d50798ffddfe987474329494b5faf0
```

Le hash et la taille ont été recalculés après la compilation et correspondent au sidecar et à
`latest.json`.

## Compatibilité de données

La release comprend les migrations SQLite 27 et 28. Elle peut donc reprendre un `UserData`
conservé par la 0.2.3 et mettre à niveau les bases principale et commune vers le schéma 28 sans
supprimer les conversations ni les preuves. L'installation remplace seulement le répertoire
applicatif sous `%LOCALAPPDATA%\Programs\CiderScholar` ; `UserData` reste séparé.

## Validation exécutée

- `ruff format --check` et `ruff check` réussis ;
- 680 tests Python réussis ;
- CI frontend réussie : formatage, ESLint, TypeScript, 61 tests Vitest et build Vite ;
- vérification post-install du runtime et des modèles réussie ;
- API locale démarrée et endpoint `/health` validé ;
- les deux bases SQLite locales sont au schéma 28 et exposent
  `native_full_text_assets` ;
- une recherche hybride E5/Qdrant sur le runtime installé a retourné des fragments hydratés depuis
  SQLite.

La génération d'une réponse complète dépend toujours d'une clé ARGO personnelle valide ; le
contrôle `/health/llm` doit être vert avant le test d'un chat de production.
