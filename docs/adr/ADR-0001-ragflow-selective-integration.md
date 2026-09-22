# ADR-0001 — Intégration sélective de RAGFlow

- Statut : accepté pour cadrer les travaux FUS ; aucune activation de production
- Date : 14 septembre 2026
- Décideurs : équipe CiderScholar ; approbation de démarrage de la roadmap par l'utilisateur
- Référence examinée : RAGFlow `b19a10fb70c64a2a0e29c4a3d20eaa90b969c3b9`

## Contexte

CiderScholar est une application scientifique locale dont SQLite est l'autorité
pour les notices, assets, texte, pages, métadonnées, chunks, preuves et états
d'admission. Qdrant et FTS5 sont des index reconstruisibles : ils ne sont pas
des sources de texte ni de citation. RAGFlow possède des capacités utiles de
parsing, structure documentaire, tableaux, figures et navigation hiérarchique,
mais sa pile complète, son stockage et son chat ne satisfont pas par eux-mêmes
aux exigences de provenance, validation et traçabilité de CiderScholar.

Cette décision cadre une reprise sélective des capacités. Elle ne valide ni une
dépendance, ni une version distribuée, ni un mode d'exécution : FUS-004 doit
d'abord valider licence, sécurité, SBOM, poids, réseau et digest d'une version
stable. Le commit ci-dessus est une référence d'inspection, jamais une
dépendance implicite à `main`.

## Décision

CiderScholar reste l'orchestrateur, l'autorité scientifique et le seul
constructeur des citations. Toute capacité inspirée ou dérivée de RAGFlow est
appelée uniquement comme parser optionnel isolé, derrière un contrat
CiderScholar strict. Le parser reçoit un asset contrôlé et renvoie un résultat
d'extraction borné ; il ne reçoit ni la base, ni les index, ni les identifiants
de preuve utilisables par le chat de production.

Le mode par défaut demeure le parser PyMuPDF actuel. Docling et DeepDoc/RAGFlow
restent optionnels, explicitement sélectionnés et désactivés par défaut jusqu'à
validation des gates définis par la roadmap et CiderQA.

## Frontières de confiance et flux

```text
asset local contrôlé (PDF / JATS / TEI, SHA-256, provenance)
  -> registre de parsers CiderScholar
  -> [optionnel : adaptateur externe isolé]
  -> contrat d'extraction CiderScholar validé
  -> admission atomique CiderScholar (qualité, provenance, revue)
  -> SQLite, autorité unique
  -> FTS5 / Qdrant / index outline dérivés et reconstruisibles
  -> retrieval CiderScholar, réhydratation SQLite, filtre A-D
  -> synthèse et validateurs CiderScholar
  -> citations construites depuis les preuves SQLite persistées
```

Les frontières sont les suivantes :

1. **Entrée contrôlée.** Les assets passent par les contrôles CiderScholar de
   taille, type, chemin, SHA-256, provenance, droits et admission. Un parser
   externe ne télécharge pas de document, ne résout pas de DOI et ne suit pas
   de lien réseau.
2. **Bac à sable de parsing.** DeepDoc/RAGFlow ou un autre parser reçoit soit
   des octets, soit un fichier temporaire créé par CiderScholar dans un dossier
   contrôlé. Il renvoie exclusivement le contrat normalisé. L'adaptateur
   supprime les temporaires, borne la réponse et refuse toute sortie hors
   contrat.
3. **Frontière d'admission.** Le résultat externe est une proposition. Seul
   CiderScholar valide les identifiants déterministes, pages ou localisateurs,
   cohérence structurelle, qualité, avertissements et provenance avant une
   écriture atomique dans SQLite. Un résultat incomplet, ambigu ou invalide est
   rejeté ou placé en revue ; il n'écrase jamais une source originale.
4. **Frontière d'index.** Les index sont dérivés de SQLite et peuvent être
   détruits puis reconstruits. Qdrant ne conserve pas le texte intégral et ne
   fournit que des identifiants et scores ; le texte de tout candidat est
   relu dans SQLite avant validation ou génération.
5. **Frontière conversationnelle.** Le chat RAGFlow, ses citations, sa mémoire,
   ses connaissances externes et ses sorties générées sont exclus du chemin de
   production. Le chat CiderScholar conserve la vague unique locale, le filtre
   A-D obligatoire, la réhydratation SQLite et les validateurs d'affirmations.

Les flux interdits incluent : accès RAGFlow direct à SQLite, Qdrant, FTS5,
assets persistés non explicitement passés, secrets CiderScholar ou réseau
sortant ; écriture de RAGFlow dans le corpus ou les index ; utilisation directe
d'un score, chunk, page, citation ou réponse RAGFlow comme preuve ; et injection
d'un texte de log, d'une réponse externe ou d'une sortie synthétique dans la
synthèse finale. Aucune pile MySQL, Elasticsearch, MinIO, Redis ou chat RAGFlow
n'est introduite par cette intégration.

## Classification de contenu et règles de citabilité

| Classe | Définition | Persistance et usage autorisé | Citabilité |
| --- | --- | --- | --- |
| `source` | Contenu original admis : PDF, JATS/TEI, abstract validé, légende ou cellule source, avec asset, hash et localisateur. | Persisté et relu depuis SQLite ; peut devenir une preuve après validation CiderScholar. | Oui, si le localisateur et la validation sont présents. |
| `source-derived` | Représentation déterministe dérivée d'une source : extraction, OCR validé, structure, chunk, texte de cellule ou index, avec source, version et hash. | Persistée avec provenance ; indexée ou utilisée pour retrouver/réhydrater la source. | Seulement lorsqu'elle conserve un ancrage vérifiable vers le contenu source ; l'application cite la source et son localisateur. |
| `synthetic` | Résumé parent, réponse hypothétique, enrichissement LLM, description de figure, regroupement généré ou sortie de chat. | Peut être persisté séparément pour audit, cache ou revue avec modèle, prompt/configuration, version et lien d'entrée. | Non : `citable=false` obligatoire. |
| `external-unpersisted` | Réponse de parser/service externe, notice découverte, résultat de recherche ou contenu non encore admis. | Mémoire transitoire ou diagnostic technique borné ; jamais index scientifique ni contexte final. | Non, jusqu'à admission, validation et persistance SQLite. |

La classe est obligatoire, explicite et non interchangeable. La transformation
vers `source-derived` ou l'admission d'un contenu externe conserve le SHA de
l'asset, l'identité/version/configuration du parser, le modèle éventuel, le
contrat, la date et les avertissements. Une extraction ou un enrichissement ne
modifie jamais le texte source. Les résumés parents, titres générés, légendes
générées, réponses hypothétiques et analyses visuelles restent synthétiques,
même lorsqu'ils améliorent le retrieval.

## Contrat des parsers externes

FUS-101 définit le contrat versionné CiderScholar. Il comportera au minimum
`ParserIdentity` (parser, version, contrat, SHA de configuration et modèle),
pages, éléments, outline, avertissements OCR et localisateurs. Les champs
inconnus sont refusés, les identifiants sont déterministes, les cycles et
nœuds orphelins sont rejetés, et la sortie ne mélange jamais `source` et
`synthetic`. Les caches sont adressés par SHA de l'asset, identité complète du
parser, contrat, modèle et configuration ; toute différence invalide une
réutilisation.

L'ordre d'exécution préférentiel est bibliothèque isolée, puis sous-processus
local, puis sidecar limité à `127.0.0.1`. Une option nécessitant la pile RAGFlow
complète est rejetée. Le sidecar éventuel ne reçoit que l'asset contrôlé et ne
retourne que le contrat CiderScholar, sans endpoint de chat ni stockage propre.

## Limites, versionnement et échec externe

Chaque appel externe doit avoir une configuration explicite et versionnée,
incluse dans les empreintes de cache et d'index : parser/adapter, version de
contrat, version et SHA du modèle, SHA de configuration, taille maximale de
l'asset et de la réponse, nombre maximal de pages, mémoire, durée maximale,
nombre de tentatives et backoff. Les valeurs numériques finales seront fixées
par FUS-103/FUS-004 après mesures Windows ; aucune valeur implicite du
fournisseur ne devient une limite de production.

Toute requête dépassant une limite de taille, de pages, de mémoire ou de délai est arrêtée
et produite comme diagnostic technique sans texte scientifique. Une absence de
modèle, erreur réseau, sortie surdimensionnée, fichier corrompu, incompatibilité
de version, erreur de schéma ou résultat partiel est un échec explicite ou un
état `review_required` suivant la politique choisie. Il n'y a ni retry infini,
ni téléchargement implicite, ni fallback silencieux. La politique est l'une de
`fail`, `review_required` ou `explicit_retry_builtin` ; cette dernière exige une
demande ou une trace explicite du parser demandé, du parser réellement employé,
de la cause, des limites et des avertissements. Les journaux et diagnostics
sont bornés et ne contiennent jamais PDF, extrait scientifique, question,
réponse générée, secret ou chemin local exposé.

## Retrieval hiérarchique et enrichissements

Les titres, chemins de section et éventuels résumés parents forment des nœuds
de retrieval hiérarchiques dérivés. Ils ont leur propre `kind` ou index séparé,
une provenance, une version et `citable=false` dès qu'ils sont générés. Un hit
parent ne suffit jamais : CiderScholar l'étend de manière bornée vers les chunks
SQLite citables, puis applique la sélection scientifique et le filtre A-D.
L'outline ne remplace donc ni les chunks d'ancrage ni les fenêtres intra-article
bornées du chemin à vague unique.

Les tableaux source peuvent devenir des preuves déterministes une fois reliés
à leur asset et localisateur ; les nombres sont vérifiés contre les cellules.
Les analyses de figures, descriptions, résumés, corrections proposées et autres
enrichissements générés sont auditables mais non citables. Toute promotion d'une
analyse visuelle requiert un contrat distinct, validation humaine, modèle et
version, hash d'image, observation, limites et localisateur.

## Recherche complémentaire expérimentale

Une seconde vague ne peut exister que dans le harness d'évaluation, après la
première vague et seulement pour des `EvidenceGap` tracés. Elle est limitée à
trois requêtes, SQLite exclusivement, sans acquisition externe ; elle
réhydrate, déduplique, applique A-D et fusionne les résultats. Elle ne rejoint
pas le chat de production, ne réactive pas les axes conversationnels retirés et
reste désactivée jusqu'à ablation CiderQA, décision de promotion, accord
utilisateur et flag initialement désactivé. Toute reprise est checkpointée et
une différence de corpus, configuration, modèle ou contrat invalide le cache.

## Désactivation et rollback

Le parser PyMuPDF actuel est la voie de repli disponible en permanence. Les
parsers externes, index outline, enrichissements et recherche complémentaire
sont activés par flags désactivés par défaut et ont des signatures de cache
distinctes. Désactiver une capacité arrête les nouveaux jobs qui la demandent,
préserve assets, sorties, provenance et diagnostics, puis revient explicitement
au parser builtin ou à la vague unique ; aucune donnée source n'est supprimée.

Rollback immédiat si un localisateur est faux, du synthétique est utilisé comme
preuve, CiderQA régresse, SQLite et index divergent, le réseau est inattendu,
un secret ou texte scientifique atteint les logs, la mémoire est incompatible
ou l'ingestion n'est plus reprenable. Le rollback désactive le flag, bloque les
nouvelles admissions concernées, invalide les dérivés/caches incompatibles et
reconstruit les index depuis SQLite si nécessaire. Toute mutation future du
corpus reste précédée d'une sauvegarde SQLite vérifiée et d'un plan de reprise.

## Conséquences

Cette architecture permet de comparer et d'adopter des améliorations de parsing
sans déplacer l'autorité scientifique ni changer implicitement le comportement
conversationnel. Elle augmente le coût des adaptateurs (contrat, isolation,
provenance, validation et rollback) mais rend les améliorations mesurables,
désactivables et auditables. Aucune activation par défaut ne peut être justifiée
par une capacité RAGFlow seule : les seuils du benchmark d'extraction, CiderQA,
licence, confidentialité et installation Windows restent des gates.

## Décisions ouvertes

1. Après FUS-004, choisir ou rejeter l'exécution DeepDoc : bibliothèque isolée,
   sous-processus local ou sidecar `127.0.0.1`, avec version stable et digest.
2. Fixer, à partir du benchmark FUS-003/FUS-204, les plafonds numériques de
   fichier, pages, mémoire, réponse, timeout, retry et les seuils qualité.
3. Définir dans FUS-101/FUS-103 le schéma exact du contrat, les états
   d'exécution et la politique par défaut `fail`, `review_required` ou
   `explicit_retry_builtin`.
4. Décider dans FUS-205 quels parsers peuvent être choisis manuellement, quels
   documents exigent une revue et si un mode `auto_experimental` est admissible.
5. Déterminer dans FUS-402 si les nœuds hiérarchiques utilisent un index séparé
   ou un `kind` distinct, après mesures de retrieval et reconstruction.
6. Décider après FUS-604/FUS-605 si la recherche complémentaire demeure rejetée,
   expérimentale ou proposée à promotion ; elle n'est pas promotable sans accord
   utilisateur explicite.
