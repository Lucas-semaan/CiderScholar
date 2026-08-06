# Build et test installé CiderScholar 0.2.7

Date de validation : 6 août 2026, Europe/Paris.

## Artefact final recompilé

- Installeur : `installer/output/CiderScholar-0.2.7-windows-x64.exe`
- Taille : 2 754 132 608 octets
- SHA-256 : `038c520d6c13508585676157769b12dd7801b420fc6ccea2818c171117b86041`
- Empreinte identique dans le sidecar et `latest.json`
- Build complet : frontend, runtime CPython, corpus commun et modèles reconstruits puis vérifiés
- Validation du workspace : Ruff format/check, 720 tests backend et 68 tests frontend réussis

Les anciens installateurs 0.2.3 à 0.2.6 ont été supprimés du répertoire de sortie local après
vérification que `latest.json` désignait la 0.2.7.

## Matrice E2E reproductible

La matrice `DEM-007` à `DEM-014` a été rejouée après installation :

- backend : 85 tests réussis ;
- frontend : 3 tests réussis dans 2 fichiers ;
- aucun appel ARGO réel n'est utilisé par cette matrice.

Les contrats couverts comprennent la prose scientifique, le format liste explicite, la persistance
dans le chat d'origine, la reprise après redémarrage, les origines commune/privée, la mise à jour du
corpus, la suggestion PDF et la reprise après quota.

## Parcours réel sur l'installation 0.2.7 avant la recompilation finale

Ce parcours a été validé sur l'artefact 0.2.7 précédent. La recompilation finale conserve la
même version et ajoute la suppression du bloc redondant `Définition retenue`; elle a été validée
par les suites automatisées et le smoke test du runtime emballé, sans nouvelle installation locale.

Le job utilisateur déjà en file `5130ef94-aa25-4e40-a79d-f73e1eecd679` a été conservé puis repris par
le worker installé. Aucun second job et aucune nouvelle question n'ont été soumis pendant son
exécution.

Séquence persistée :

1. `waiting` ;
2. `planning` ;
3. `search` ;
4. `reranking` ;
5. `evidence_selection` ;
6. `generation` ;
7. `validation` ;
8. `persistence` avec état `succeeded`.

Résultat observé dans l'interface :

- l'étape visible a d'abord été « Recherche locale dans le corpus », puis « Génération de la réponse
  finale » ;
- la réponse finale est visible dans le chat d'origine ;
- elle contient 2 685 caractères et une section de sources/références ;
- la carte de travail a disparu après la persistance ;
- aucune erreur ou alerte n'a été relevée dans la console du navigateur ;
- le diagnostic final indique ARGO prêt, worker prêt, 0 travail actif et une profondeur de file à 0.

## Observations à conserver

- La recherche initiale sur 237 455 fragments a duré environ dix minutes et a utilisé jusqu'à
  environ 5,6 Go de mémoire worker. Pendant une section CPU synchrone, le heartbeat a brièvement été
  qualifié d'ancien, puis il est redevenu sain sans intervention. La réponse n'a pas été perdue, mais
  le heartbeat devrait à terme rester indépendant des longues sections de calcul.
- Deux copies de la même question étaient déjà visibles dans cette conversation historique avant la
  fin du job. La reprise 0.2.7 n'a créé aucune question supplémentaire. Ce reliquat antérieur doit
  rester distinct du résultat de ce test.
- Le build npm a signalé quatre vulnérabilités de dépendances (une modérée et trois élevées). Aucun
  `npm audit fix` automatique n'a été appliqué afin de ne pas modifier les dépendances sans revue.

## Conclusion

La version installée produit désormais une sortie visible et persistée sur le parcours réel testé.
Les états techniques exposés correspondent aux principales phases réellement exécutées, et la file
revient proprement à zéro après succès. Les deux observations ci-dessus sont des axes de durcissement,
pas des blocages de sortie pour ce build.
