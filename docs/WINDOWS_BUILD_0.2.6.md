# Construction et réinstallation Windows 0.2.6

Date de validation : 2026-08-05.

## Artefacts vérifiés

- `installer/output/CiderScholar-0.2.6-windows-x64.exe` ;
- `installer/output/CiderScholar-0.2.6-windows-x64.exe.sha256` ;
- `installer/output/latest.json`.

L'exécutable mesure 2 751 242 627 octets. Son SHA-256 est :

```text
5086bcd9353cdaa5082950cba676544e3747497d4b4f1f72fa5f7c18ea30540d
```

Le hash recalculé correspond au sidecar et à `latest.json`.

## Contenu de la version

La version 0.2.6 ajoute le diagnostic local des travaux durables : fraîcheur du worker,
travaux actifs, étapes et mémoire. La réponse ne contient ni question, ni réponse, ni clé,
ni identifiant de processus.

La vue documentaire unifiée `Corpus` n'est pas exposée dans cette version. Les tâches
`COR-032` à `COR-035` restent bloquées par `COR-031`, c'est-à-dire la validation d'une
réponse chatbot traçable.

## Validation exécutée

- `ruff format --check` et `ruff check` réussis ;
- CI frontend réussie : formatage, ESLint, TypeScript, 65 tests Vitest et build Vite ;
- tests ciblés du diagnostic validés avant l'empaquetage ; la suite Python complète n'a pas
  été rejouée pendant cette livraison, afin d'éviter une validation redondante ;
- installateur compilé, manifeste et empreinte SHA-256 vérifiés ;
- installation silencieuse réussie sur le profil existant, sans suppression des données ;
- application 0.2.6 relancée, `/health` = `ok` et
  `/api/system/diagnostics` répond avec un worker sain et sans travail actif.
