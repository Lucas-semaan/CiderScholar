# Architecture de l’application web

## Décision

L’interface Streamlit a été remplacée par une SPA React 19 en TypeScript, construite par Vite et stylée exclusivement avec Tailwind CSS 4. FastAPI sert les routes JSON et, après compilation, les fichiers statiques de la SPA sur le même port local.

Ce découpage garde les calculs scientifiques dans Python tout en donnant une interface composable, testable et adaptée à une croissance fonctionnelle.

## Frontend

- `components/ui` contient les primitives visuelles partagées : bouton, carte, badge, formulaire, dialogue et états de retour.
- `features` isole les six domaines de navigation.
- `lib/api.ts` est l’unique passerelle HTTP ; ses contrats correspondent aux schémas FastAPI.
- `styles/index.css` définit les tokens Tailwind et les styles de base. Aucun CSS de page ni thème concurrent n’est autorisé.
- Les opérations coûteuses ne se lancent qu’après une action explicite et montrent leurs états d’attente ou d’erreur.

## Backend

- `app/api` possède le contrat HTTP et la validation stricte.
- `app/services` orchestre les workflows anciennement liés à l’interface.
- Les modules de domaine restent utilisables depuis les scripts et les tests sans serveur web.
- Les réglages modifiés dans l’interface vivent dans l’état du processus FastAPI et ne réécrivent pas la configuration.

La décision manuelle des notices « À réviser » passe par
`POST /api/library/records/{record_id}/decision`. Une admission est persistée comme priorité
éditoriale et remet l’abstract en attente d’indexation. Un rejet supprime directement son
éventuel vecteur puis efface la notice SQLite ; les sources, occurrences de collecte et entrées
FTS associées sont supprimées en cascade. L’interface sélectionne ensuite la prochaine notice à
réviser disponible sur la page courante.

Tout rejet possédant un DOI est également inscrit dans
`data/common/excluded_bibliographic_dois.json`. Ce registre JSON horodaté est consulté avant
chaque insertion issue d’une collecte et reprend au premier usage les DOI déjà présents dans
l’archive SQLite des rejets. Une réautorisation conserve l’historique mais désactive explicitement
l’exclusion :

```powershell
.\.venv\Scripts\python.exe -m scripts.manage_doi_exclusions list
.\.venv\Scripts\python.exe -m scripts.manage_doi_exclusions reinstate 10.1234/exemple
```

Le paramètre `query` de `GET /api/library/records` recherche chaque terme dans le titre, les
auteurs, le DOI, le journal, l’année, le nombre de citations, le thème, l’URL, la source et
l’identifiant fournisseur. Les variantes usuelles d’un nom d’auteur (`Prénom Nom`, `Nom, Prénom`
ou `Nom Initiale`) sont rapprochées. La recherche hybride d’abstracts ajoute ces correspondances
de métadonnées à ses candidats plein texte et vectoriels.

## Exécution

En production locale, Vite génère `frontend/dist`; FastAPI le sert sur `127.0.0.1:8000`. En développement, Vite utilise `127.0.0.1:5173` et relaie les routes API vers le port 8000.

## Sécurité

- aucune route ne renvoie la valeur d’une clé API ; elle indique seulement si la clé attendue est configurée ;
- la SPA ne lit jamais les variables d’environnement serveur ;
- les chemins persistants restent confinés sous `data` ;
- le service documenté écoute uniquement sur la boucle locale ;
- les pages, DOI et citations sont reconstruits depuis SQLite et non acceptés sur la parole du LLM.

## Validation

La CI frontend enchaîne formatage, lint, typage, tests et build. La suite Python contrôle le domaine et les contrats HTTP avec des bases temporaires.
