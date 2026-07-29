# Isolation des corpus

## Guide utilisateur : partagé ou privé ?

| Élément | Corpus commun | Documents privés |
|---|---|---|
| Qui peut le lire ? | Tous les postes qui installent la même version du corpus | Uniquement l'utilisateur de ce poste |
| Qui peut le modifier ? | Le profil administrateur local pendant une mise à jour validée | L'utilisateur, depuis l'onglet `Documents privés` |
| Où sont les PDF et index ? | `data/common` | `data/private` |
| Une mise à jour commune le remplace ? | Oui, après vérification et avec archivage de la version précédente | Non, ses hashes doivent rester identiques |
| Sauvegarde et restauration | Distribuées avec le paquet de corpus administrateur | Commandes `backup_private_corpus.py` et `restore_private_corpus.py` |
| Peut-il devenir une suggestion partagée ? | Déjà partagé | Seulement après une action explicite de l'utilisateur |
| Étiquette dans les résultats et citations | `Corpus commun` | `Document privé` |

La recherche PDF interroge par défaut les deux espaces, mais le filtre permet de limiter la portée.
Un DOI présent dans les deux espaces ne produit qu'un résultat commun. Sans DOI, les deux documents
restent distincts. Une source privée n'est jamais envoyée, suggérée ou présentée comme commune sans
action explicite.

## Décision SQLite

Le corpus commun et l’espace privé utilisent deux fichiers SQLite indépendants :
`data/common/database/science_rag.sqlite3` et
`data/private/database/science_rag.sqlite3`. Ils ne sont jamais attachés à la même connexion.
Les lectures multi-corpus ouvrent, interrogent puis ferment chaque base séquentiellement et
fusionnent des résultats qui conservent leur `scope`. Une identité technique est donc le couple
`(scope, id)` ; un identifiant seul n’est pas global.

Cette séparation permet de remplacer atomiquement le corpus commun et de sauvegarder ou restaurer
le privé sans recopier l’autre espace. La base historique `data/database/science_rag.sqlite3` peut
être copiée une fois avec `python scripts/migrate_legacy_corpus.py` depuis un profil administrateur ;
la commande conserve la source pour permettre le contrôle du nombre d'articles et des DOI.
La commande ciblée `python scripts/migrate_legacy_abstracts.py` réimporte dans le corpus commun les
abstracts bibliographiques historiques acceptés qui ne possèdent pas encore d'article complet au
même DOI, puis reconstruit uniquement leur collection vectorielle locale. Elle ne recopie ni PDF ni
index de chunks. Les notices rejetées ne sont jamais copiées ; les notices sans DOI restent distinctes
faute de preuve d'identité.

## Décision Qdrant

Chaque portée possède son propre stockage Qdrant local sous `data/common/qdrant` ou
`data/private/qdrant`. Le nom de collection peut rester identique car les stockages physiques sont
distincts. Les index sont ouverts et fermés séquentiellement afin de respecter les postes 8 Go et
un point Qdrant n’est hydraté qu’avec la base SQLite de la même portée.

Il est interdit de fusionner physiquement les points communs et privés ou de recopier le texte des
fragments dans Qdrant.

## Identité et déduplication

Le DOI normalisé est la seule preuve inter-corpus disponible dans une projection de recherche ; le
commun est prioritaire à DOI égal. Sans DOI, aucun rapprochement de titre, auteurs ou année ne
supprime un résultat : ces champs peuvent être identiques pour des documents réellement distincts.
Un futur hash de fichier vérifié pourra constituer une preuve explicite, mais le repli actuel reste
le couple `(scope, article_id)`.
