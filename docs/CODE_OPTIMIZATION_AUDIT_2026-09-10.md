# Audit et optimisation du code — 10 septembre 2026

La demande a été traitée avec trois critères d'acceptation : préserver les fonctionnalités
accessibles, conserver les règles de validation et de traçabilité scientifiques, et vérifier chaque
optimisation par comparaison avec l'état initial et par les tests du projet.

L'ensemble des sources du dépôt a été inventorié : environ 90 000 lignes applicatives et de scripts,
plus 38 000 lignes de tests au départ. L'analyse globale porte sur les structures Python AST, les
corps de fonctions dupliqués, les références aux symboles, les imports et exports TypeScript, les
accès aux fichiers et les connexions SQLite. La revue manuelle porte sur les candidats détectés et
leurs appelants, les chemins de production et leurs tests. Il s'agit d'un examen structurel de
l'ensemble du dépôt assorti d'une revue ciblée, pas d'une certification formelle de chaque ligne.

Les dépendances installées, fichiers générés, bases, PDF, index et modèles locaux sont exclus du
nettoyage. Aucun corpus utilisateur n'a été modifié. Les changements déjà présents avant cette tâche
ont été conservés ; les chiffres ci-dessous comparent le résultat à cet état de travail initial,
et non simplement au dernier commit Git.

| Zone examinée | Résultat de l'examen et intervention |
| --- | --- |
| API, configuration et modèles de transport | Contrats HTTP, routes, schémas de validation et paramètres scientifiques conservés. |
| Orchestration et chatbot | Retrait du pipeline par axes abandonné, de son adaptation de couverture et des auxiliaires sans appel. |
| Retrieval, génération et recherche approfondie | Mutualisation des contrats de clients ; algorithmes, prompts actifs, budgets et décisions scientifiques conservés. |
| Ingestion et fournisseurs bibliographiques | Centralisation du SHA-256, de la vérification stricte des DOI et du nettoyage des auteurs partageant le même format fournisseur. |
| SQLite, maintenance, sauvegardes et installation | Fermeture explicite des connexions oubliées ; rollback par flux avec vérification des tailles et empreintes avant activation. |
| Scripts de collecte, audit et évaluation | Lecteur de journal partagé ; réutilisation des empreintes de fichiers sans lecture intégrale en mémoire. |
| Frontend | Retrait de composants orphelins, correction des écouteurs d'annulation et réutilisation des formateurs français. |
| Tests, packaging et migrations | Suites complètes exécutées ; migration, distribution et données historiques conservées. |

Les modifications retirent 1 841 lignes de production et en ajoutent 218, soit **1 623 lignes nettes
en moins**. Avec les nouveaux tests, le bilan des sources est de 539 lignes ajoutées et
1 845 retirées. Ces chiffres excluent la documentation et les modifications antérieures à la tâche.

Les changements principaux sont les suivants :

1. `app/services/workflows.py` passe de 4 795 à 3 560 lignes. Le chemin privé
   `_answer_chatbot_axis_legacy`, inutilisé et remplacé depuis la décision méthodologique du
   27 août, est supprimé. Les auxiliaires sans appel sont également retirés. Le contrôle historique
   des axes encore testé est déplacé dans `app/retrieval/coverage_assessment.py`, son module métier.
   Les tests qui interdisent le retour de l'ancienne couverture peuvent toujours injecter un
   détecteur d'appel ; ils ne nécessitent plus de garder le pipeline abandonné en production.
2. Douze déclarations de protocole LLM identiques par groupes partagent trois contrats dans
   `app/llm/contracts.py`. Les différences de paramètres et de types entre ces groupes restent
   explicites. Les noms importables existants sont conservés par alias, sans imposer une nouvelle
   méthode aux fournisseurs.
3. Sept implémentations supplémentaires d'empreinte de fichier rejoignent
   `app/file_integrity.py`. Le contrat utilisé par l'ingestion conserve les chemins texte et la
   taille de bloc personnalisable. Les empreintes de texte normalisé, de modèles avec comptage
   simultané des octets et de données sérialisées restent distinctes lorsque leur sémantique diffère.
4. Trois vérifications strictes de DOI partagent `verified_normalized_doi`. Elles continuent à
   refuser une URL DOI, un préfixe ou un suffixe à corriger : extraire un DOI d'une chaîne et valider
   une identité déjà normalisée restent deux opérations différentes. Les auteurs DOAJ, ISTEX et
   Semantic Scholar partagent un nettoyage qui conserve l'ordre et les noms distincts.
5. Cinq lecteurs JSONL et quatre fonctions d'ajout de ligne partagent
   `app/updates/checkpoints.py`. La lecture s'effectue ligne par ligne ; les lignes JSON invalides
   et les valeurs non objets sont ignorées comme auparavant, et les erreurs d'E/S ou d'encodage
   restent visibles. Les autres écrivains conservent leurs règles particulières de tri des clés,
   de création des répertoires ou de sérialisation.
6. Le rollback de maintenance ne matérialise plus l'archive et ses gros membres en mémoire. Les
   tailles déclarées et réellement écrites, ainsi que chaque SHA-256, sont contrôlés avant
   l'activation. Une sauvegarde invalide ne remplace jamais le corpus courant ; le répertoire
   d'extraction est nettoyé en cas d'échec.
7. Six ouvertures SQLite dans les diagnostics, la vérification des sources de démonstration et
   l'audit des métadonnées sont désormais fermées explicitement. Le contexte transactionnel des
   écritures est conservé. Les tests vérifient la fermeture sur succès, erreur SQL, audit périmé
   et échec au milieu du traitement.
8. Le suivi frontend retire l'écouteur `abort` quand chaque temporisation se termine. Il évite
   ainsi leur accumulation pendant les longues tâches, tout en interrompant immédiatement une
   attente annulée. Le composant `ArgoKeySettingsCard`, remplacé par l'écran des fournisseurs, et
   la copie isolée `local-science-rag/frontend/src/app/App.tsx`, absente de tout build, sont retirés.
   Les écrans et actions accessibles restent présents dans le frontend actif.

La conservation du comportement scientifique a été vérifiée à deux niveaux. La comparaison AST
avec l'état de départ retrouve **70 fonctions conservées identiques** dans `workflows.py` et
**61 fonctions conservées identiques** dans `pilot_rag.py`. Les changements de ces deux fichiers
portent sur les suppressions et la mutualisation des imports. Les tests existants des citations,
quantités, abstentions, sources locales, rechargement SQLite, recherche et synthèse restent verts.
Les budgets d'effort, le filtre A–D obligatoire, les contrôles numériques et de provenance, ainsi que
les possibilités de reprise ne sont pas diminués.

| Mesure locale sur données synthétiques | Avant | Après |
| --- | --- | --- |
| SHA-256 d'un fichier de 64 Mio : pic d'allocations Python | 64,001 Mio | 2,127 Mio |
| Même SHA-256 : durée médiane de cinq exécutions | 72,0 ms | 64,7 ms |
| Formatage français de 5 000 nombres : médiane de cinq exécutions | 139,07 ms | 2,76 ms |

Les empreintes et les chaînes formatées sont identiques entre les deux implémentations mesurées.
Le pic est mesuré avec `tracemalloc` et concerne les allocations Python, pas la mémoire totale du
processus ou le cache du système. Ces mesures isolées ne constituent pas une mesure du temps total
d'une réponse scientifique ; les caches, la charge machine et les appels distants influencent ce temps.

| Validation | État initial | État final |
| --- | --- | --- |
| Ruff : format et lint | Réussite | Réussite, 506 fichiers Python conformes au format |
| Pytest complet | 1 319 tests réussis | **1 348 tests réussis**, aucun avertissement |
| Frontend : format, lint, types, tests et build | 99 tests réussis, build réussi | **101 tests réussis**, build réussi, aucun avertissement |
| Vérification des espaces du diff | — | Réussite |

Les 29 nouveaux cas Python couvrent les lectures bornées, les empreintes, les identités
bibliographiques, les journaux reprenables, le refus d'activer une sauvegarde altérée et la fermeture
SQLite. Deux cas frontend couvrent le retrait des écouteurs et l'annulation pendant l'attente.
Les tests proches des changements ont précédé les suites complètes.

Commandes finales exécutées :

```powershell
.\.venv\Scripts\python.exe -m ruff format --check app scripts tests
.\.venv\Scripts\python.exe -m ruff check app scripts tests
.\.venv\Scripts\python.exe -m pytest -q
npm.cmd --prefix frontend run ci
git -c core.safecrlf=false diff --check
```

Certains modules backend restent volumineux : `workflows.py`, `pilot_rag.py`, `harvest.py` et
`database/sqlite.py`. Leur découpage complet serait une refonte d'architecture distincte et n'apporte
pas, à lui seul, de gain d'exécution. Les répétitions restantes examinées incluent de courtes
interfaces typées, des constructeurs, des parseurs de commandes dont le contexte diffère, et quelques
petites fonctions de sérialisation. Elles ne sont pas fusionnées au prix d'un couplage entre
sous-systèmes ou d'un changement des formats signés. Aucun symbole privé sans référence ne subsiste
dans le relevé AST global ; cette propriété statique n'est pas une preuve générale d'absence de code
mort dans un langage dynamique.

Aucune nouvelle campagne de génération distante ou d'évaluation experte sur le corpus réel n'a été
lancée. La conclusion porte sur la conservation vérifiée du code scientifique actif et l'absence de
régression dans les tests disponibles ; elle ne revendique pas une nouvelle validation scientifique
des réponses produites par un modèle externe. Aucun modèle, index ou paquet de distribution n'a été
promu ou publié.
