# Acquisition des textes intégraux

CiderScholar résout les DOI déjà présents dans la base documentaire avant de conserver l'abstract
comme dernier recours. L'ordre est déterministe : Europe PMC, ISTEX, CORE, HAL, Semantic Scholar,
OpenAlex, Unpaywall, DOAJ, Crossref TDM, puis Elsevier. Seuls les PDF réellement accessibles,
signés `%PDF-`, sous la limite de taille configurée et issus d'une URL HTTPS publique sont
conservés. Les URL de pages HTML et les résolveurs DOI présentés à tort comme PDF sont ignorés.

Les DOI des notices `accepted` peuvent être téléchargés, découpés et ajoutés au RAG. Les notices
`review` sont auditées, mais leur texte intégral reste hors RAG tant que la règle thématique ne les a
pas acceptées. Le DOI normalisé empêche une seconde ingestion du même article. La provenance, la
licence signalée, l'URL, le hash et les échecs bornés sont persistés dans `full_text_assets`.

Les réponses de disponibilité sont mises en cache pendant la durée configurée. Un `429`, un
`Retry-After`, une date de remise à zéro, un timeout ou trois refus `403` consécutifs créent un délai
persistant dans `full_text_provider_cooldowns`. Une nouvelle exécution, y compris après
redémarrage, ne contacte pas la source ou l'hôte avant cette date. Aucun paywall, CAPTCHA,
contrôle d'accès, `robots.txt` ou mécanisme anti-bot n'est contourné.

## Commandes administrateur

Audit rapide de tous les DOI, sans téléchargement :

```powershell
python -m scripts.harvest_full_text --audit-only --fast
```

Audit complet incluant les replis Unpaywall et Crossref, puis téléchargement, ingestion et
indexation :

```powershell
python -m scripts.harvest_full_text
```

Le rapport JSON détaillé est écrit sous `data/exports/full-text-harvest-*.json`. Une exécution peut
être bornée avec `--max-downloads N`. `--no-index` laisse les chunks disponibles en recherche
lexicale et en attente d'une indexation vectorielle ultérieure.

Un cycle borné de croissance vers un objectif de corpus collecte d'abord de nouvelles notices via
toutes les API bibliographiques configurées, conserve uniquement celles acceptées par les règles
thématiques, puis tente leurs PDF :

```powershell
python -m scripts.grow_full_text_rag --target-pdfs 10000 --max-downloads 500
```

Les jeux de requêtes focused, expanded, specialized et materials tournent entre les cycles. L'état,
la progression et la prochaine date de reprise sont persistés dans `rag_growth_state`; chaque cycle
écrit un rapport `data/exports/full-text-growth-*.json`. Ajouter `--slow-fallbacks` seulement pour
un cycle destiné aux replis DOI par DOI Unpaywall et Crossref.

## Accès ISTEX

La recherche des notices ISTEX est publique. Les PDF sont protégés par la fédération d'identités.
Après génération légitime d'un jeton ISTEX, le placer uniquement dans la variable d'environnement
`ISTEX_API_TOKEN`. Le jeton ne doit jamais être écrit dans YAML, SQLite, un rapport ou un journal.
Sans ce jeton, CiderScholar note `authentication_required` et ne répète pas le téléchargement.

Sur le profil administrateur Windows, le jeton peut être conservé chiffré avec DPAPI :

```powershell
$env:CIDERSCHOLAR_LOCAL_PROFILE = "admin"
python -m scripts.set_admin_bibliographic_key istex
```

La saisie est masquée. Les commandes `harvest_full_text`, `grow_full_text_rag` et le worker
administrateur hydratent le token uniquement dans leur processus. Lorsqu'un token est ajouté,
les résultats ISTEX précédemment mis en cache comme `authentication_required` sont immédiatement
revérifiés, sans attendre l'expiration normale du cache.

## Corps d'article structurés natifs

En plus du PDF destiné au RAG page par page, CiderScholar conserve le meilleur corps d'article
natif disponible pour chaque DOI accepté : JATS XML depuis Europe PMC, puis TEI XML ou texte
`cleaned` depuis ISTEX. Les liens XML ou texte explicitement typés de DOAJ et Crossref sont traités
par la même chaîne générique ; une URL ambiguë ou une page de destination n'est jamais devinée comme
un article. Ces fichiers sont enregistrés avec URL finale, licence, taille et SHA-256
dans `native_full_text_assets`. Ils ne sont pas encore découpés pour le RAG : cela évite de présenter
une ancre XML comme un numéro de page PDF. La commande `harvest_full_text` borne séparément ces
téléchargements avec `--max-native-downloads N`; la limite persistante est
`full_text.max_native_downloads_per_run`.

## Maintenance

La maintenance hebdomadaire exécute automatiquement la résolution rapide après la collecte
bibliographique, ingère les nouveaux PDF thématiquement acceptés et indexe leurs chunks. Les
abstracts restent disponibles uniquement lorsque le texte intégral ne peut pas être obtenu.
