# Corpus scientifique commun

CiderScholar utilise un unique corpus scientifique pour tous les écrans,
imports, recherches et synthèses. Ses PDF, extractions, base SQLite et index
Qdrant résident sous `data/common`.

La vue « Base documentaire » et la recherche locale sont strictement centrées
sur les PDF présents. Les métadonnées bibliographiques peuvent enrichir un PDF
par DOI ou servir temporairement à son acquisition, mais une référence sans PDF
n’est ni un document visible ni une source du RAG local.

Les résultats et citations portent donc toujours l’étiquette `Corpus commun`.
La recherche ne filtre plus de portée et la liste des articles n’est pas
tronquée à 5 000 éléments.

`GET /api/corpus/{article_id}/pdf` ouvre le fichier source correspondant à un
identifiant d’article explicitement sélectionné dans cette base. La route ne
reçoit jamais de chemin : elle résout l’identifiant dans SQLite et ne sert qu’un
fichier PDF existant. Les chemins historiques explicitement persistés restent
lisibles pendant une migration additive ; tout autre cas retourne une erreur
404 sans révéler le chemin local.

## Sauvegarde

`python -m scripts.backup_corpus` crée une archive vérifiée du corpus commun.
`python -m scripts.restore_corpus archive.zip` remplace atomiquement ce corpus
et conserve la version précédente sous `data/backups/corpus/rollback`.
