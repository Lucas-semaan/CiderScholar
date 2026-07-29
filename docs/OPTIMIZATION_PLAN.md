# Plan global d’optimisation de CiderScholar

## Principes non négociables

- Préserver la traçabilité scientifique, les citations, les contrats HTTP et les données locales.
- Mesurer avant d’optimiser ; ne pas introduire de cache ou de parallélisme sans test de cohérence.
- Garder les ressources lourdes paresseuses et explicitement fermées.
- Utiliser Tailwind et les primitives partagées pour toute présentation statique.
- Accompagner chaque refactorisation d’un test ciblé, puis exécuter la validation complète.

## État de référence

Au début de la passe du 29 juillet 2026 :

- 622 tests Python passent ;
- 53 tests frontend passent ;
- Ruff, ESLint, Prettier, TypeScript et le build Vite passent ;
- le bundle de production principal pèse environ 268 kB, soit 85 kB gzip ;
- les pages Paramètres, Corpus et Bibliothèque dépassent ou approchent la limite de taille du projet ;
- aucune suite e2e navigateur durable n’est configurée.

## P0 — Passe actuelle

### Frontend

- Découper les pages monolithiques par responsabilité et conserver les appels API dans la façade commune.
- Mutualiser les contrôles et variantes récurrents au lieu de recopier leurs classes.
- Remplacer les dépendances visuelles distantes par des tokens Tailwind et des polices système.
- Vérifier les états chargement, vide, erreur et succès, ainsi que les parcours clavier.
- Valider chaque route en desktop et mobile dans un navigateur réel.

### Backend

- Déporter dans SQLite les filtres et limites actuellement appliqués après matérialisation Python.
- Mutualiser les opérations de hash identiques et lire les gros fichiers par blocs.
- Mutualiser les présentateurs API seulement lorsque les contrats de sortie restent strictement identiques.
- Conserver les variantes dont la sémantique scientifique ou canonique diffère, même si leur nom se ressemble.

### Qualité

- Conserver la baseline complète verte.
- Relever les erreurs console, réponses HTTP en échec et débordements visuels pendant le smoke e2e.
- Documenter les écarts impossibles à tester sans déclencher une opération lourde, distante ou destructive.

## P1 — Prochain cycle recommandé

### E2E reproductible

- Ajouter une suite Playwright sur une base SQLite temporaire et des fournisseurs externes simulés.
- Couvrir les routes, la navigation mobile, les dialogues, les filtres, les exports et les opérations CRUD.
- Séparer les scénarios rapides de smoke des scénarios scientifiques longs.
- Publier captures et traces seulement en cas d’échec.

### Découpage frontend

- Scinder progressivement `types/api.ts` et `lib/api.ts` par domaine tout en conservant une façade publique stable.
- Centraliser annulation, délai maximal et normalisation des erreurs réseau.
- Ajouter un cache uniquement aux lectures idempotentes dont l’invalidation est explicite.
- Fixer un budget de bundle par route et surveiller toute régression significative.

### Découpage backend

- Extraire `app/services/workflows.py` par cas d’usage, sans déplacer la logique scientifique en bloc.
- Séparer progressivement les dépôts SQLite par agrégat, derrière la façade `Database` existante.
- Examiner les requêtes fréquentes avec `EXPLAIN QUERY PLAN` avant d’ajouter des index.
- Regrouper les écritures compatibles dans des transactions courtes sans réduire la reprise après erreur.

### Dette et observabilité

- Ajouter une détection de code mort Python/TypeScript avec une liste d’exclusions explicite pour les points d’entrée.
- Remplacer les journaux libres des workflows longs par des événements structurés sans données sensibles.
- Mesurer temps de démarrage, latence des lectures courantes, mémoire maximale et durée des jobs.
- Revoir périodiquement les scripts historiques et supprimer uniquement ceux dont l’absence d’appel est prouvée.

## Critères d’acceptation

- Aucun changement de contrat non documenté.
- Aucun accès réseau implicite ajouté.
- Aucun chargement anticipé d’E5, Qdrant ou du LLM.
- Zéro avertissement de build ou de lint.
- Toutes les routes ont un état utile en chargement, vide et erreur.
- Tous les dialogues ont un titre accessible, un focus contenu et une fermeture avec Échap.
- Toute amélioration de performance conserve les résultats fonctionnels et scientifiques dans les tests.
