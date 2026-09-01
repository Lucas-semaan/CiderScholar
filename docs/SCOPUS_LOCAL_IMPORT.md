# Exports texte Scopus locaux

Le parseur local de référence vit dans `app/updates/scopus_text.py`. Il sépare le titre de source des
suffixes volume, numéro, pages, numéro d'article et citations en lisant la ligne depuis la droite.
Il ne déduit aucun éditeur : `Source title` alimente la provenance par EID et, de façon conservatrice,
le champ d'affichage `journal`; `publisher` reste une métadonnée distincte.

## Audit sans mutation

```powershell
.\.venv\Scripts\python.exe -m scripts.reconcile_scopus_source_titles `
  "C:\chemin\export-1.txt" "C:\chemin\export-2.txt"
```

La commande conserve une copie des exports, leurs SHA-256, le plan JSONL et une distribution CSV/JSON
par titre de source. Le rapport indique explicitement que le dénominateur est le nombre d'EID Scopus
uniques.

## Application

Relire le rapport de l'audit, puis relancer avec `--apply` :

```powershell
.\.venv\Scripts\python.exe -m scripts.reconcile_scopus_source_titles `
  "C:\chemin\export-1.txt" "C:\chemin\export-2.txt" --apply
```

Avant toute évolution de schéma ou de métadonnées, la commande crée une sauvegarde SQLite cohérente et
vérifie son hash et `PRAGMA quick_check`. Elle refuse un harvest bibliographique actif. Chaque EID
reçoit son propre `source_title`, dans la provenance active ou dans la provenance de l'archive ; le
champ `journal` n'est remplacé que lorsqu'il est vide ou reproduit exactement l'ancienne coupure à la
première virgule. Les vecteurs déjà valides restent
indexés, car leur texte d'encodage est composé du titre et de l'abstract, tandis que le FTS est mis à
jour automatiquement par les déclencheurs SQLite.

Une seconde exécution sur les mêmes fichiers doit être idempotente : zéro titre de source et zéro
journal restant à modifier.

## Classification dans la base documentaire

Le type affiché dépend seulement du contenu persisté : `Full article` lorsqu'un texte intégral est
disponible, sinon `Abstract only` dès qu'un abstract non vide est présent. Le DOI, le statut de
pertinence et l'état de l'index vectoriel ne modifient pas cette classification. Une référence sans
abstract et sans texte intégral reste une notice à acquérir et n'est pas comptée artificiellement
comme un abstract.
