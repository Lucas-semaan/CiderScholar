# Collecte Aureli locale

La commande `scripts.harvest_aureli_cider` collecte les notices de la recherche plein texte
`cider` dans Aureli, limitée au type documentaire `Article`. Elle interroge l'API publique Primo
d'Aureli sans compte utilisateur, sans export par courriel et sans ARGO.

La collecte est découpée par année, séquentielle et temporisée. Chaque page de 50 notices est écrite
dans une transaction SQLite atomique, puis met à jour un point de reprise dans le dossier de
campagne. À la reprise d'une session identifiée, le client rejoue sans écriture les pages précédentes
de l'année courante avant d'atteindre l'offset sauvegardé. Aureli limite un visiteur non identifié aux 250 premiers
résultats d'une recherche ; le découpage annuel reste dans cette borne sans contourner
l'authentification. Une session identifiée expose au plus les 2 000 premiers résultats d'une tranche
annuelle (offsets 0 à 1 950 par pages de 50). Le rapport conserve le total annoncé par Aureli afin de
quantifier explicitement toute queue non accessible. Le plafond local est de 40 000 candidats bruts.

Une recherche Aureli déjà limitée à plusieurs titres de revue peut être reproduite avec un
`--journal` par titre. Les titres sont parcourus séquentiellement et dédupliqués lors de leur
consolidation dans la même campagne. `--all-document-types` conserve comme candidats les articles,
articles de magazine et autres types présents dans la recherche ; chaque notice repasse néanmoins
par la décision scientifique `accepted/review/rejected` avant toute acquisition de texte intégral.
Pour une vague explicitement dédiée au texte intégral,
`--retain-accepted-without-abstract` conserve temporairement les notices admises par leur titre et
dotées d'un DOI afin que l'acquisition puisse être tentée. Elles restent au niveau `Metadata only`
et ne constituent aucune preuve tant qu'un texte intégral vérifié n'a pas été persisté.

```powershell
.\.venv\Scripts\python.exe -m scripts.harvest_aureli_cider `
  --limit 2587 --start-year 2026 --end-year 1801 --all-document-types `
  --retain-accepted-without-abstract `
  --journal "Scientific American" `
  --journal "Beverage Industry" `
  --journal "Journal of Food Protection" `
  --journal "Journal of Food Science" `
  --run-dir data\exports\aureli-cider-journals-20260827
```

Lorsqu'une session identifiée est explicitement autorisée, le client lit son jeton uniquement depuis
la variable d'environnement éphémère `CIDERSCHOLAR_AURELI_SESSION_TOKEN`. La valeur ne doit apparaître
ni dans la configuration, ni dans le point de reprise, ni dans les journaux ou rapports. Supprimer la
variable dès la fin de la campagne.

Avant toute écriture, la commande crée une copie cohérente de SQLite, vérifie son intégrité avec
`PRAGMA quick_check` et écrit son empreinte SHA-256. La déduplication utilise d'abord le DOI
normalisé, puis le titre et l'année. Le filtre de pertinence choisit le meilleur des huit thèmes
cidricoles existants. Une notice pertinente sans DOI reste en révision ; une notice sans abstract
est archivée puis retirée de la base active. Seuls les abstracts acceptés avec DOI sont indexés
localement dans Qdrant avec E5.

Échantillon sans mutation :

```powershell
.\.venv\Scripts\python.exe -m scripts.harvest_aureli_cider --dry-run --limit 200 --start-year 2026 --end-year 2026 --run-dir data\exports\aureli-cider-dry-run-sample
```

Collecte de 10 000 candidats, reprenable avec exactement la même commande :

```powershell
.\.venv\Scripts\python.exe -m scripts.harvest_aureli_cider --limit 10000 --run-dir data\exports\aureli-cider-apply
```

Le dossier contient `checkpoint.json`, le journal paginé `pages.jsonl`, l'audit notice par notice
`records-audit.jsonl` et `report.json`. Une campagne interrompue ne marque pas son point de reprise
comme terminé et peut être relancée avec les mêmes bornes.

## Acquisition du texte intégral

Après la curation des notices, lancer d'abord l'audit des fournisseurs sans téléchargement. Les
liens « Obtenir PDF » d'Aureli peuvent mener au DOI, à un PDF direct ou à un résolveur ; le pipeline
local privilégie ensuite la source légale résolue pour le DOI et n'ingère qu'un contenu dont le type
PDF réel, la taille et SHA-256 ont été vérifiés.

```powershell
.\.venv\Scripts\python.exe -m scripts.harvest_full_text --audit-only --fast
.\.venv\Scripts\python.exe -m scripts.harvest_full_text --fast --max-downloads 500 --no-index
```

Si le cache de disponibilité couvre déjà la date de la campagne, un rafraîchissement explicite peut
être limité au run Aureli, sans réinterroger tout le corpus :

```powershell
.\.venv\Scripts\python.exe -m scripts.harvest_full_text --audit-only --refresh-cache --harvest-run-id <UUID_DU_RUN> --sources europe_pmc hal semantic_scholar openalex unpaywall doaj crossref
```

`--sources` est une surcharge limitée au processus courant : elle ne modifie pas la configuration
installée ni les préférences des campagnes ultérieures.

Une notice sans PDF validé reste `Abstract only`. Une notice avec PDF contrôlé et ingéré devient
`Full article`. Les fichiers binaires, cookies et jetons de session ne figurent jamais dans les
rapports de campagne ou dans Git.
