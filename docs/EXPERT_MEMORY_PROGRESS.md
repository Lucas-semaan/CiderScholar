# Mémoire experte — progression et reprise

Mise à jour : 9 septembre 2026. Révision de départ : `676684a`.

Demande utilisateur : roadmap détaillée, puis « commence à appliquer ce que tu peux sans supervision ».
Cette autorisation couvre le développement local et les vérifications autonomes. Elle ne constitue
pas une revue scientifique des nouvelles règles. Aucune règle nouvelle n'a été présentée comme une
consigne utilisateur acceptée dans le guide méthodologique.

## Résultat de cette étape

Le socle hors ligne comprend les contrats fermés, la configuration désactivée, le stockage de versions
candidates, le parseur borné, le graphe de dépendances, 13 fiches initiales et un routeur de
prévisualisation. Trois commandes permettent de vérifier et d'inspecter ce résultat ; voir
[knowledge/README.md](../knowledge/README.md).

Le chatbot n'est pas encore branché sur cette mémoire. `off`, `shadow` et `active` sont des valeurs
de configuration reconnues ; aucune ne déclenche actuellement l'injection de fiches en production.
Le routeur autonome refuse `active`. Aucun modèle, corpus, index, conversation ou fournisseur réel
n'a été utilisé pour les nouvelles fonctionnalités. Aucun appel Argo, entraînement, promotion ou
activation n'a été effectué.

## Optimisation du RAG : présélection dense par article

La demande utilisateur du 9 septembre 2026 est appliquée dans le chemin hybride existant. FTS5 lit
toujours l'ensemble du corpus local, puis recueille jusqu'à 60 articles distincts. Si au moins quatre
articles sont trouvés et qu'il y a plusieurs variantes denses, la première reste une requête Qdrant
globale et chaque variante dense suivante est filtrée sur ces articles. Les vecteurs des variantes
sont encodés ensemble et les requêtes Qdrant restent dans un seul batch, avec leur filtre propre.

Cette borne réduit l'exploration dense de l'index complet sans remplacer le retrieval hybride par une
recherche séquentielle. Le chemin global est conservé lorsque le pool FTS5 est pauvre, lorsque le
demandeur a déjà fourni des `article_ids`, lorsqu'il n'y a qu'une variante dense, ou lorsque
`dense_article_prefilter_enabled=false`. Les résultats lexicaux globaux restent toujours fusionnés.
Le premier vecteur global protège la découverte d'une terminologie absente de FTS5 ; les niveaux A–D,
le reranking global et les validateurs de preuve ne changent pas.

`ChatbotRetrievalTrace` enregistre désormais le nombre d'articles dans ce pool et le nombre de
requêtes denses réellement globales. Les paramètres `dense_article_prefilter_*` vivent dans
`retrieval` et sont inclus dans la signature de cache existante, car la configuration entière y est
hashée. Les tests couvrent les filtres distincts d'un même batch Qdrant, le groupe de quatre articles,
le premier vecteur global et le repli.

## État des lots de la roadmap

| Lot | État | Réalisé / reste à faire |
|---|---|---|
| 00 | Baseline technique réalisée ; inventaire scientifique partiel | Révision, environnement et tests relevés. Aucune attestation de version installée ni de jeu expert complet disponible. |
| 01 | Partiel | C1, correction, diagnostic, patch candidat, requêtes revue/activation, budgets et config implémentés. Manifeste C3 et contrats de rapports d'évaluation restent à implémenter avec leurs workflows. |
| 02 | Partiel | Migration 35, cinq tables de paquets, import atomique/idempotent, relecture vérifiée, pointeur actif initialement vide. Épinglage, feedback persistant, revue, activation et retour arrière restent à implémenter. |
| 03 | Réalisé pour le paquet V1 | Manifest JSON, YAML/Markdown borné, provenance, graphe, dépendances inverses, lint déterministe et CLI. Ce contrôle est structurel, pas une validation d'autorité humaine. |
| 04 | Partiel : paquet et prévisualisation réalisés | 13 fiches, sélection déterministe et budgets. La sélection est testable en CLI ; le mode shadow dans les jobs et la comparaison scientifique à l'ancien comportement attendent les lots 05–06. |
| 05–06 | Non commencés | Manifestes persistés, épinglage, contexte par étape, cache, intégration chatbot et shadow réel. |
| 07–12 | Non commencés | API/UI correction, diagnostic opérationnel, compilateur candidat, replay scientifique, revue, promotion et pilote. Les modèles seuls ne constituent pas ces workflows. |
| 13–15 | Non commencés | Distribution approuvée et extensions optionnelles. |

## Fichiers et décisions techniques

- `app/knowledge/contracts.py`, `models.py` : objets Pydantic figés, champs inconnus refusés,
  identifiants stables, versions et hashes canoniques. Collections immuables ; revalidation des
  objets fabriqués via `model_copy` en mode Python pour ne pas convertir un booléen en entier.
- `app/expert_feedback/models.py` : contrat d'une correction, références de preuve sans texte
  intégral, diagnostic et patch limité. Une source changée, une trace insuffisante ou une ambiguïté
  experte bloque la proposition automatique, même comme cause contributive. Aucun endpoint associé.
- `app/config.py`, `config.example.yaml`, `installer/config.runtime.yaml` : `expert_memory.mode`
  vaut `off`. Plafond de 12 éléments, enveloppes de 2 000/1 600/1 600 caractères selon l'étape,
  trois éléments modifiés et deux tentatives de compilation au maximum. Budgets d'amélioration
  par défaut nuls. Aucune variable d'environnement ni dépendance externe ajoutée.
- `app/database/expert_memory_migration.py` et migration 35 : `expert_releases`,
  `expert_release_items`, `expert_release_dependencies`, `expert_active_release`,
  `expert_release_events`. Une transaction contient la migration et son numéro. Les contenus
  importés sont immuables ; l'événement d'import scelle les éléments et liens.
- `app/knowledge/repository.py` : un import est candidat, adressé par hash, avec contrôle de base
  attendue, compatibilité, FK, graphe et hashes avant commit et lors de la relecture. Un import
  concurrent identique ne crée qu'une version. Les bases absentes, anciennes ou futures sont refusées.
- `app/knowledge/loader.py`, `validation.py`, `graph.py` : manifeste `package.json` explicite,
  100 éléments/2 Mio, 32 Kio par fichier, profondeur YAML limitée. Pas de scan libre du disque,
  source PDF ni chargement d'un modèle. Provenance limitée à `AGENTS.md`, `docs/*.md`, `app/*.py`.
- `app/knowledge/routing.py` : normalisation Unicode et frontières de tokens, critères fermés,
  priorités puis IDs, dépendances sélectionnées en groupe, exclusion et passerelles. Le coût inclut
  le JSON exact des trois contextes. Une enveloppe insuffisante écarte le groupe entier ; une
  exigence globale trop volumineuse produit un repli sans contexte.
- `knowledge/` : cinq politiques dérivées du guide accepté ; huit propositions de taxonomie,
  passerelle, routes et recettes. `--include-proposals` permet seulement leur inspection hors ligne.

Le plan initial prévoyait les schémas et tables de plusieurs workflows dès les lots 01–02. Ils sont
créés ici avec leur premier cas d'usage réel, pour éviter de figer des structures non exercées. C'est
un affinage technique de l'ordre des travaux, pas une suppression des exigences de traçabilité,
d'évaluation ou de revue. Les lots incomplets restent explicitement ouverts.

### Frontière d'autorité à compléter avant intégration

Le chargeur vérifie que les documents de provenance existent et que leurs octets correspondent aux
hashes déclarés. Il ne vérifie pas qu'une nouvelle instruction reformule fidèlement ces documents,
ni qu'un fichier marqué `reviewed_method` a reçu une revue. La prévisualisation interprète ces
étiquettes déclaratives ; elle n'a aucun effet de production et annonce `scientific_approval=false`.

Avant toute injection active, ajouter une validation d'autorité issue d'une liste initiale revue ou
d'un événement de revue authentifié, lié au hash exact de la version. Un hash de contenu ou un champ
`authority` ne doit jamais servir seul de justificatif d'activation. Cette dépendance bloque la mise
en production du lot 06 et la promotion du lot 11.

## Validation

La baseline avant implémentation comporte 1 089 tests backend réussis. Les nouveaux modules ont
d'abord été vérifiés par suites ciblées, avec avertissements traités en erreurs.

| Contrôle final | Résultat observé |
|---|---|
| `.venv` : Ruff format sur `app scripts tests` | 480 fichiers correctement formatés |
| `.venv` : Ruff check sur `app scripts tests` | Réussi |
| `.venv` : `python -m pytest -q` | 1 270 tests réussis en 147,26 s ; environnement Python 3.14 local hors plage supportée, conservé pour la commande obligatoire |
| Python 3.12.14 isolé : `python -m pytest -q -W error` | 1 270 tests réussis en 178,36 s, sans avertissement ; pile de test précisée ci-dessous |
| Python 3.12.14 isolé : `python -m pip check` | Aucune dépendance incompatible |
| Frontend : `npm.cmd --prefix frontend run ci` | Format, lint, typage, 28 fichiers / 97 tests, build de production réussis |
| Linter du paquet réel | 13 éléments valides, zéro diagnostic, aucune approbation scientifique |
| Import dry-run avec configuration runtime distribuée | `applied=false`, `activated=false`, 13 éléments, aucune ouverture SQLite |
| Prévisualisation FML/cidre avec propositions | Route malolactique retenue, 8 éléments, 726/344/700 caractères par étape |

Empreinte du paquet initial vérifié :
`8546f1019cd33ac4ad32ed1aba19c3afb41647cdc08a3dcfee6602cad93ebac7`.

L'environnement `.venv` préexistant utilise Python 3.14.6, alors que le projet exige Python 3.12.
Un environnement de validation isolé Python 3.12.14 a été créé sous `tmp/p` avec les dépendances de
`requirements.txt`, sans remplacer `.venv`. Un premier essai sous `outputs/expert-memory-python312`
a atteint la limite Windows de longueur des chemins lors de l'installation de PyTorch. Ces dossiers
et les dépendances frontend restent ignorés par Git.

Pour rendre la comparaison reproductible, la pile de test de `tmp/p` a été alignée sur `.venv` :
`starlette==1.3.1`, `httpx2==2.7.0` (et son `httpcore2==2.7.0`), `anyio==4.14.2`,
`pillow==12.3.0`. Ces ajustements concernent uniquement
l'environnement isolé ; `requirements.txt` reste inchangé. Sans HTTPX2, absent de ce fichier mais
présent dans `.venv`, Starlette émet un avertissement de dépréciation lors de l'import de TestClient ;
les premières collectes Python 3.12 avec `-W error` ont donc été refusées. AnyIO 4.15.1 déprécie
aussi un alias utilisé par ce TestClient, d'où l'alignement avec 4.14.2. Aucun avertissement n'a été
filtré pour contourner ces erreurs. Les futures installations de l'environnement de test devront
tenir compte de ces écarts. Pillow, également absent des requirements, est importé par les tests
existants d'analyse de figures ; sa version est celle de `.venv`. Pour reproduire la pile testée
après installation des requirements :

```powershell
.\tmp\p\Scripts\python.exe -m pip install starlette==1.3.1 httpx2==2.7.0 anyio==4.14.2 pillow==12.3.0
.\tmp\p\Scripts\python.exe -m pip check
.\tmp\p\Scripts\python.exe -m pytest -q -W error
```

Le contrôle supplémentaire `.venv` Python 3.14 avec `-W error` a produit 17 échecs et 1 251 succès :
ses échecs signalent des connexions SQLite non fermées au ramasse-miettes dans les parcours existants.
Ils ne constituent pas une validation du runtime 3.14, hors version supportée ; la commande standard
du dépôt et le contrôle strict sous Python 3.12 sont consignés séparément dans le tableau ci-dessus.

Ces résultats sont des contrôles techniques. Aucun score scientifique CiderQA nouveau, aucune
baseline de réponses Argo, aucun accord expert et aucun gain mesuré ne sont revendiqués.

L'essai manuel du dry-run a révélé une incohérence préexistante de `config.example.yaml` :
`ingestion.max_tokens=750` dépasse `embeddings.max_sequence_length=512`. Les nouvelles options
de mémoire sont valides, mais le chargement de ce fichier complet est refusé par le validateur
existant. Les exemples d'essai utilisent donc `installer/config.runtime.yaml`, vérifié avec succès.
Le réglage d'ingestion n'a pas été modifié dans cette tâche de mémoire experte.

## Migration réelle à préparer

Aucune base réelle n'a été ouverte pour migration/import dans cette tâche. La migration 35 est
cependant enregistrée dans le schéma commun : le prochain démarrage de l'API ou du worker avec ce
code l'appliquera, même si la mémoire est `off`. `Database.initialize` n'effectue pas de sauvegarde.

Avant ce démarrage :

1. Résoudre les chemins de la base applicative et du corpus commun depuis la configuration réelle,
   et relever la version des processus API/worker installés.
2. Arrêter les processus écrivains et vérifier l'absence de job en cours d'écriture.
3. Produire une sauvegarde SQLite cohérente et vérifier son intégrité pour chaque cible distincte ;
   si les chemins coïncident, une seule sauvegarde suffit. Ne pas copier seulement le fichier `.db`
   en ignorant un éventuel WAL actif.
4. Appliquer la migration par l'initialisation normale, vérifier schéma 35, intégrité/FK et comptes
   des articles, preuves et conversations, puis redémarrer API et workers avec la même version.
5. Effectuer l'import du paquet candidat seulement après ces contrôles. Vérifier que le pointeur
   actif reste vide et que `activated=false` est retourné.

Lorsque les bases sont distinctes, le schéma partagé ajoute aussi les cinq tables expertes vides à
la base corpus ; le script d'import cible uniquement la base applicative. Une ancienne application
limitée au schéma 34 refusera ensuite une base 35 : le retour arrière du logiciel exige une procédure
coordonnée avec la sauvegarde, pas une suppression manuelle des tables. L'intégration réelle reste
une étape distincte de ces tests sur bases temporaires.

## Prochaine tâche exécutable par un agent

Poursuivre le lot 05 avant de modifier les prompts. Relire les contrats C2–C3 et le lot 05 complet,
puis travailler dans cet ordre :

1. Créer `app/knowledge/trace.py` avec modèles fermés du manifeste : version/mode épinglés,
   identités et hashes des candidats par étape, décisions A–D, contextes réellement présentés,
   liens affirmation–preuve, budgets et statut de tentative. Ne pas copier le PDF.
2. Ajouter une nouvelle migration après 35 pour le manifeste et les références de job. Ne pas
   modifier la migration 35 après déploiement. Préserver les anciens jobs en les considérant `off`.
3. Implémenter l'épinglage immuable dans le repository et persister manifeste/résultat/succès dans
   la transaction existante sous contrôle du lease ; tracer aussi échec, annulation et reprise.
4. Brancher seulement la collecte de traces sur `_answer_chatbot` et ses frontières déjà existantes.
   Garder la vague groupée unique, le filtrage global et les validateurs actuels.
5. Ajouter les tests de version modifiée pendant un job, ancienne tentative, annulation, crash et
   réduction du prompt. Vérifier que les IDs tracés correspondent aux preuves effectivement envoyées.
6. Lancer les tests proches, puis les quatre commandes obligatoires de `AGENTS.md`. Consigner les
   résultats ici. Ensuite seulement entreprendre le lot 06 et son contrôle d'autorité.

Pour toute tâche suivante, les commandes copiables et critères d'acceptation de
[la roadmap](EXPERT_MEMORY_ROADMAP.md), des [contrats](EXPERT_MEMORY_CONTRACTS.md) et de
[la validation](EXPERT_MEMORY_VALIDATION.md) restent le cadre ; ce document décrit ce qui existe
réellement et les écarts connus, sans transformer une étape partielle en fonctionnalité terminée.
