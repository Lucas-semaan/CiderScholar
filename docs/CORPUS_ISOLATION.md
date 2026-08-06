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
identifiant d’article explicitement sélectionné dans cette base.

## Migration des installations existantes

Exécuter une fois :

```powershell
python -m scripts.merge_legacy_split_corpus
python -m scripts.transfer_legacy_vectors
```

La première commande consolide les articles, fragments, éléments documentaires,
traces OCR et PDF gérés des anciens emplacements dans `data/common`. Elle crée
une sauvegarde SQLite préalable sous `data/backups` et ne supprime pas les
sources historiques. La seconde commande remappe directement les vecteurs
existants vers les nouveaux identifiants de fragments, sans recalculer les
embeddings déjà indexés.

## Sauvegarde

`python -m scripts.backup_corpus` crée une archive vérifiée du corpus commun.
`python -m scripts.restore_corpus archive.zip` remplace atomiquement ce corpus
et conserve la version précédente sous `data/backups/corpus/rollback`.
