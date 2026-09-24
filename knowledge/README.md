# Paquet initial de mémoire de méthode

Ce dossier contient 13 fiches locales de méthode, vocabulaire et procédures déclaratives. Il ne
contient aucune preuve scientifique. Le chatbot ne charge pas encore ces fiches. L'état exact des
travaux et la prochaine tâche figurent dans [la progression](../docs/EXPERT_MEMORY_PROGRESS.md).

## Contenu et autorité

`package.json` énumère explicitement les fichiers admis. Les sous-dossiers suivent les valeurs de
`kind` : `taxonomy`, `method_policy`, `gateway`, `route`, `recipe`.

- Cinq fiches `policy.*` reformulent les consignes déjà acceptées du guide méthodologique ; leur
  champ `authority=accepted_user_method` décrit cette origine. Trois sont des exigences globales.
- Les deux taxonomies, la passerelle, les trois routes et les deux recettes sont des propositions
  d'implémentation. Elles restent `proposal`, y compris lorsque leur vocabulaire vient du code.
- Une empreinte SHA-256 prouve l'identité du document de provenance, pas une revue humaine.
  L'import ne vérifie pas encore une autorisation d'activation : il conserve uniquement un candidat.
  Aucun fichier ne peut activer la mémoire ou devenir une preuve par ce mécanisme.

## Vérifier sans base ni modèle

Depuis la racine du dépôt, avec un environnement Python 3.12 contenant `requirements.txt` :

```powershell
.\.venv\Scripts\python.exe -m scripts.lint_expert_knowledge --knowledge-dir knowledge
.\.venv\Scripts\python.exe -m scripts.import_expert_knowledge `
  --knowledge-dir knowledge --config installer/config.runtime.yaml
.\.venv\Scripts\python.exe -m scripts.preview_expert_routing `
  --knowledge-dir knowledge --question "Quel rôle de la FML dans le cidre ?" --language fr
```

Le fichier `installer/config.runtime.yaml` fournit une configuration valide pour cet essai sans
écriture. Le linter retourne `structurally_valid=true`, `scientific_approval=false`, un hash et le graphe.
L'import sans `--apply` est une prévisualisation : il n'ouvre ni ne crée de base SQLite. Le routeur
affiche seulement des identifiants, empreintes, motifs et tailles de contexte ; la question et les
instructions ne sont pas recopiées dans sa sortie. La question passée en argument peut rester dans
l'historique du terminal : utiliser ces exemples synthétiques pour les essais.

Par défaut, les routes candidates ne sont pas sélectionnées. Pour tester explicitement leur
comportement hors ligne :

```powershell
.\.venv\Scripts\python.exe -m scripts.preview_expert_routing `
  --knowledge-dir knowledge --question "Quel rôle de la FML dans le cidre ?" `
  --language fr --include-proposals
```

Cette option ne change aucune autorité, aucun prompt et aucune version active. La décision reste
`preview_only=true` et `scientific_approval=false`. Une FML sans contexte cidricole explicite échoue
à la passerelle ; la recherche générale reste conceptuellement disponible. La prévisualisation
n'exécute aucune recherche et ne traite pas encore la recette conversationnelle.

## Importer un candidat dans une base préparée

`--apply` est la seule option d'écriture du script d'import. Elle exige une base applicative existante
et déjà migrée en version 35 par le workflow normal. L'opération est transactionnelle et idempotente :
un paquet identique ne crée pas de deuxième version. Elle ne modifie jamais le pointeur actif.

Avant une migration réelle, appliquer la procédure de sauvegarde et d'arrêt coordonné décrite dans
[la progression](../docs/EXPERT_MEMORY_PROGRESS.md#migration-réelle-à-préparer). Ne pas utiliser
`config.example.yaml` comme configuration de déploiement. Après préparation seulement, remplacer
le chemin d'exemple entre guillemets par le chemin absolu de la configuration effectivement vérifiée :

```powershell
.\.venv\Scripts\python.exe -m scripts.import_expert_knowledge `
  --knowledge-dir knowledge --config "C:\chemin\configuration-locale-validee.yaml" --apply
```

Le linter accepte facultativement `--output data/exports/expert-memory-lint.json` ; il écrit le
rapport JSON atomiquement et refuse d'écraser une fiche. Le script d'import et le routeur écrivent
uniquement sur stdout et n'acceptent pas `--output`.

## Reprendre les observations d'un pilote

Les observations humaines peuvent être préparées dans un JSON sans texte de conversation ni contenu
scientifique. Le fichier doit contenir le `pilot_id` exact et une liste bornée de mesures :
`observation_id`, `case_sha256`, `expert_time_seconds`, `diagnosis_human_corrected`, `useful_effect`,
`false_gain`, `rollback_count`, `prompt_tokens`, `completion_tokens` et `recorded_by`. Le protocole
est repris depuis le plan SQLite ; il ne doit pas être copié manuellement dans le fichier.

La validation est sans écriture par défaut :

```powershell
.\.venv\Scripts\python.exe -m scripts.import_expert_pilot_observations `
  --input "C:\chemin\observations.json" --pilot-id "00000000-0000-0000-0000-000000000000" `
  --config "C:\chemin\configuration-locale-validee.yaml"
```

Après vérification du rapport et de la cible, `--apply` persiste chaque observation dans une
transaction séparée. Un arrêt peut donc être repris avec le même fichier : les cas déjà enregistrés
sont rejoués sans créer de doublon. Cette commande ne lance ni LLM, ni recherche, ni activation.

## Modifier une fiche

1. Partir d'une copie candidate ; conserver l'original et la différence lisible.
2. Incrémenter `revision` si son contenu change et nommer la révision antérieure avec `supersedes`.
3. Maintenir `depends_on` pour toutes les cibles de route et références de recette.
4. Pour une nouvelle décision ou une traduction non revue, conserver `authority=proposal` et
   `required=false`. Seules les politiques de méthode peuvent être globalement requises.
5. Ajouter des cas positifs, voisins et ambigus, puis relancer linter et tests pertinents.

La provenance utilise les octets exacts du document source, fins de ligne comprises. Un changement
du guide ou une conversion de fins de ligne invalide volontairement l'empreinte. Examiner le diff
avant d'actualiser la provenance et la révision ; ne pas recalculer les hashes aveuglément pour
faire passer le linter. Pour un paquet placé ailleurs, fournir `--source-root <racine-des-sources>`.

Les champs inconnus, références manquantes, cycles, doublons, chemins sortants, liens symboliques,
jonctions traversées, tags/alias YAML et dépassements de taille sont refusés. Une recette décrit
uniquement les handlers fermés du workflow actuel ; elle n'exécute ni code ni outil arbitraire.
