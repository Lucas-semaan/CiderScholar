# CiderScholar — règles d’ingénierie

Ce fichier adapte au projet CiderScholar les principes de développement de CiderScope. Il s’applique à tout le dépôt.

## Intention produit

CiderScholar est une application scientifique locale. Elle ingère des PDF, collecte des métadonnées et abstracts, recherche dans un RAG traçable et produit des synthèses reliées à leurs preuves. La qualité scientifique, la confidentialité et la reprise après erreur priment sur la vitesse d’ajout de fonctionnalités.

## Architecture de référence

- `frontend/` : React, TypeScript, Vite et Tailwind CSS. Aucune logique d’accès direct à SQLite, Qdrant ou aux fournisseurs externes.
- `app/api/` : contrats HTTP FastAPI, validation des entrées, sérialisation et traduction des erreurs. Les routes restent minces.
- `app/services/` : orchestration des cas d’usage de l’interface et gestion explicite des ressources lourdes.
- `app/ingestion/`, `app/retrieval/`, `app/llm/`, `app/updates/` : logique métier spécialisée, indépendante du framework web.
- `app/database/` : source d’autorité SQLite et migrations. Toute évolution de schéma est versionnée et testée.
- `scripts/` : commandes d’exploitation réutilisant les services métier, sans dupliquer leur logique.

Les dépendances vont de l’interface vers l’API, de l’API vers les services, puis vers le domaine et les adaptateurs. Le domaine ne dépend jamais de React ou de FastAPI.

## Règles frontend

- Tailwind CSS est l’unique architecture visuelle. Ne pas ajouter de bibliothèque de composants, de fichier CSS de fonctionnalité, de CSS module ou de style global hors `frontend/src/styles/index.css`.
- Les couleurs, polices et espacements partagés sont des tokens Tailwind. Une valeur dynamique calculée à l’exécution peut utiliser un style inline ciblé ; les styles statiques restent des classes Tailwind.
- Réutiliser les primitives de `frontend/src/components/ui/` avant de créer une variante locale. Toute nouvelle primitive doit être accessible, typée et indépendante du métier.
- Organiser les écrans par fonctionnalité dans `frontend/src/features/`. Extraire un composant lorsqu’il est réutilisé, possède son propre état ou rend un écran difficile à lire. Limite stricte : 500 lignes par fichier ; cible habituelle : moins de 350.
- Utiliser des composants React fonctionnels, des props explicites et aucun `any`. Pour une mise à jour dépendant de la valeur précédente, toujours utiliser la forme fonctionnelle `setState(previous => ...)`.
- Tous les contrôles doivent fonctionner au clavier, avoir un focus visible, une étiquette et une cible tactile suffisante. Les dialogues ont un titre accessible et se ferment avec Échap.
- Chaque appel réseau passe par `frontend/src/lib/api.ts`. Les types de transport vivent dans `frontend/src/types/api.ts`.
- Afficher les états chargement, vide, erreur et succès. Une opération lourde n’est jamais déclenchée implicitement au chargement d’une page.

## Règles backend et scientifiques

- Les modèles Pydantic des requêtes refusent les champs inconnus. Ne jamais exposer une clé, un jeton ou le contenu complet d’un PDF dans une réponse ou un journal.
- Les secrets viennent uniquement des variables d’environnement documentées. Aucun secret dans le code, YAML, test, fixture ou export.
- Normaliser et vérifier le DOI avant insertion. Le DOI normalisé est la première clé de déduplication bibliographique ; les identifiants fournisseurs et le titre normalisé ne sont que des replis contrôlés.
- SQLite reste l’autorité pour le texte, les pages, les métadonnées et les preuves. Qdrant ne stocke pas le texte intégral.
- Toute affirmation scientifique générée doit pointer vers une preuve persistée et validée. Le LLM ne fabrique jamais DOI, page, référence ou citation.
- Les appels aux API bibliographiques et au LLM sont bornés, séquentiels lorsque requis, temporisés et reprenables. Respecter les quotas de chaque fournisseur et le mode OpenAlex gratuit configuré.
- Fermer explicitement modèles, clients HTTP et index. Ne jamais charger E5, Qdrant ou un LLM au démarrage de l’application.
- Une modification destructive de données exige une cible explicite, une confirmation applicative et un test de non-régression.

## Discipline de modification

- Préserver les changements utilisateur sans rapport avec la tâche. Faire des modifications ciblées et éviter les réécritures mécaniques non nécessaires.
- Ne pas dupliquer un workflow : extraire ou étendre un service partagé.
- Documenter toute nouvelle variable d’environnement, commande d’exploitation, route publique ou migration.
- Un changement de contrat HTTP met à jour ensemble le schéma FastAPI, le client TypeScript, les types et les tests.
- Aucun fichier généré (`dist`, `node_modules`, caches, base locale, index) n’est versionné.

## Validation obligatoire

Avant livraison :

```powershell
.\.venv\Scripts\python.exe -m ruff format --check app scripts tests
.\.venv\Scripts\python.exe -m ruff check app scripts tests
.\.venv\Scripts\python.exe -m pytest -q
npm.cmd --prefix frontend run ci
```

Pour une modification ciblée, lancer d’abord les tests proches, puis la suite complète avant livraison. Une fonctionnalité n’est terminée que si le build de production et les tests passent sans avertissement.
