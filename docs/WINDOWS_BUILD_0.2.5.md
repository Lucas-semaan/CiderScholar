# Construction et réinstallation Windows 0.2.5

Date de validation : 2026-08-05.

## Artefacts vérifiés

- `installer/output/CiderScholar-0.2.5-windows-x64.exe` ;
- `installer/output/CiderScholar-0.2.5-windows-x64.exe.sha256` ;
- `installer/output/latest.json`.

L'exécutable mesure 2 751 240 950 octets. Son SHA-256 est :

```text
dc6b7a43f2662a8312130aeeee973d2ae53bed418427574e12a4404db6eda66f
```

Le hash et la taille ont été recalculés après la compilation et correspondent au
sidecar et à `latest.json`.

## Correctif de résilience

Après un upsert Qdrant réussi, une interruption du garde-fou mémoire ne reclasse
plus le lot durable comme `failed` dans SQLite. Les deux stores restent donc
cohérents et l'indexation peut reprendre sur le prochain lot lorsque la mémoire
est disponible.

## Validation exécutée

- `ruff format --check` et `ruff check` réussis ;
- 681 tests Python réussis ;
- CI frontend réussie : formatage, ESLint, TypeScript, 61 tests Vitest et build
  Vite ;
- installateur compilé, empreinte et manifeste vérifiés ;
- installation silencieuse sur le profil existant, avec runtime `0.2.5` ;
- vérification post-install réussie et API locale démarrée (`/health` = `ok`) ;
- corpus commun conservé au schéma 28, avec 234 464 fragments indexés et 2 991
  fragments en attente.

## Suite opérationnelle

Le retrieval local reste disponible. La génération d'une réponse complète dépend
d'une clé ARGO valide : lors de cette validation, `/health/llm` a répondu `503`.
Les fragments en attente peuvent être repris avec `scripts.rebuild_index
--retry-failed` après avoir libéré suffisamment de mémoire ; le correctif 0.2.5
préserve désormais tout lot déjà persistant avant de s'interrompre.
