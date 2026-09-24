# Cible VM Linux 32 Go et audit mémoire

Date de référence : 24 septembre 2026.

## Décision produit

L'édition interne de CiderScholar est un service partagé par une équipe restreinte. Elle est déployée
sur une seule VM Linux disposant de 32 Go de RAM, d'un CPU adapté, d'environ 200 Go de stockage et
d'aucun GPU dédié. La génération utilise exclusivement l'API INRAE ARGO avec le modèle
`chat-gpt-oss-120b`. E5 reste local et s'exécute sur CPU. Il n'existe aucun repli vers un autre LLM,
payant ou local.

Une future édition allégée destinée aux producteurs de cidre pourra utiliser un sous-corpus et un
fournisseur de génération différent. Elle n'est pas dans le périmètre actuel. La séparation entre
contrats scientifiques, fournisseur de génération et stockage doit seulement éviter de la rendre
impossible.

## Verdict

Le cœur RAG peut tenir sur une VM de 32 Go à condition de conserver l'ingestion et le retrieval lourd
bornés et séquentiels. Le dépôt n'est toutefois pas déployable tel quel sur cette cible : plusieurs
chemins d'exploitation restent Windows-only, un second modèle IA local est activé par défaut, et la
concurrence du worker est dimensionnée sur un quota ARGO plutôt que sur la mémoire disponible.

Les garde-fous déjà présents sont utiles : E5 est chargé paresseusement, les embeddings sont produits
par petits lots, les vecteurs et payloads Qdrant sont configurés sur disque, le retrieval local du
chat est sérialisé, les modèles lourds sont libérés avant les validations ARGO, les contextes finaux
sont bornés et SQLite reste l'autorité du texte. Ils réduisent le risque mais ne constituent pas une
validation sur Linux 32 Go.

## Chemins réellement exécutés

```text
PDF
  -> PyMuPDF, page courante extraite
  -> document complet conservé en listes Python (pages, tables, figures)
  -> métadonnées et chunking du document complet
  -> transaction SQLite
  -> E5 CPU par lots
  -> Qdrant embarqué, vecteurs/payloads sur disque

Question
  -> planification ARGO
  -> SQLite FTS5 + E5 CPU + Qdrant embarqué, sous verrou local
  -> fusion/reranking bornés
  -> relecture des preuves depuis SQLite
  -> libération E5/reranker
  -> filtre, vérification et synthèse ARGO
  -> validation locale des citations et nombres
```

L'API FastAPI et le worker sont des processus distincts dans l'exploitation actuelle. Le worker peut
créer plusieurs exécutions chat dans un même processus. Chaque réponse possède ses ressources de
retrieval, mais le verrou `_LOCAL_CHAT_RETRIEVAL_LOCK` empêche deux recherches locales lourdes de
s'exécuter simultanément dans ce processus.

## Risques constatés dans le code

| Priorité | Zone | Comportement actuel et raison | Recommandation |
|---|---|---|---|
| P0 | Secrets Linux | `app/secrets.py` et `app/llm/argo_key.py` utilisent Windows DPAPI pour la saisie persistée. Sur Linux, seule la variable d'environnement ARGO fonctionne sans changement. | Pour le pilote, injecter la clé par secret systemd avec permissions strictes. Ajouter ensuite un coffre Linux ou un adaptateur de secret explicite avant d'autoriser la saisie UI. |
| P0 | OCR Linux | `app/ingestion/windows_ocr.py` et les scripts d'import reposent sur Windows Runtime et PowerShell. Les PDF image-only ne peuvent pas suivre le chemin OCR sur Linux. | Créer un adaptateur OCR Linux isolé, page par page, avec limites de pixels, pages, durée et mémoire ; conserver le même contrat de trace et de revue. |
| P0 | IA exclusive | Avant correction, `FigureAnalysisConfig` activait Ollama avec `qwen3-vl:8b-instruct`, et l'interface exposait un fournisseur LLM personnalisé. Ces chemins contredisaient ARGO exclusif et l'absence de GPU. | Corrigé dans le profil interne : analyse visuelle désactivée et politique serveur `argo_only`. L'abstraction personnalisable reste inaccessible sauf politique d'une autre édition explicitement validée. |
| P0 | Concurrence | Avant correction, `AppConfig.chat_worker_concurrency` valait 20 et `run_job_worker.py` créait autant de workflows complets. Le verrou sérialise E5/Qdrant, mais les contextes, clients HTTP et phases ARGO peuvent coexister. | Corrigé à 2 workflows par défaut. Mesurer le pic agrégé et n'augmenter qu'avec un test de charge. Les travaux d'ingestion/maintenance restent uniques. |
| P0 | Limites mémoire | Avant correction, le profil avertissait à 13 Go, limitait un processus à 14 Go et n'exigeait que 512 Mo disponibles ; aucun profil 32 Go n'existait. Le seuil disponible était trop tardif pour éviter la pression mémoire globale. | Corrigé avec un profil 32 Go réservant 6 Go au système/page cache, un avertissement à 24 Go utilisés et une limite RSS de 20 Go pour le processus worker. |
| P1 | PDF volumineux | `PyMuPdfExtractor` traite les objets page par page, mais conserve tout le texte, les cellules de tableaux et les figures du document dans `ExtractedDocument`. Le chunker matérialise ensuite toutes les unités et tous les chunks. | Ajouter des bornes pour le parser builtin : taille, pages, caractères, cellules et éléments. À terme, persister par segments/pages plutôt que conserver trois représentations complètes. |
| P1 | Déduplication texte | `IngestionPipeline._article_with_same_normalized_text` peut extraire un PDF candidat pendant que le document courant est encore en mémoire. | Comparer d'abord des empreintes persistées et ne réextraire qu'un candidat strictement borné ; éviter la double matérialisation. |
| P1 | Qdrant embarqué | `QdrantLocalIndex` ouvre Qdrant en mode local dans le processus Python. Le code masque l'avertissement de taille du client. Les audits/reconciliations construisent des listes ou dictionnaires de tous les points. | Mesurer le corpus réel. Avant une forte croissance, déplacer Qdrant dans un service sur la même VM et faire paginer les audits sans accumulation globale. SQLite reste l'autorité. |
| P1 | Isolation des rôles | API, worker chat, ingestion et maintenance partagent la même configuration et le même espace de données ; la file principale peut prendre tout type de job. | Déployer des unités séparées et des files logiques : API, chat (concurrence bornée), ingestion/maintenance (concurrence 1). Ne jamais réindexer pendant un pic chat sans fenêtre explicite. |
| P1 | Accès partagé | FastAPI écoute uniquement sur loopback et ne fournit pas d'authentification d'équipe. C'est sûr localement mais incomplet pour une VM partagée. | Garder FastAPI sur loopback, placer un reverse proxy TLS authentifié devant lui et définir sauvegarde, journaux, rétention et contrôle d'accès. |
| P1 | Validation de capacité | Les rapports existants concernent surtout Windows 8/16 Go. Il n'existe pas de mesure Linux 32 Go sur corpus représentatif. | Ajouter un protocole reproductible avec ingestion d'un gros PDF, indexation, requêtes concurrentes et maintenance, sous cgroup. |
| P2 | Caches | Le cache de vecteurs de requête est borné à 128. Les caches de validation sont petits et liés à une réponse. Les caches disque lisent cependant un fichier JSON complet par entrée et n'ont pas de quota global. | Conserver les bornes mémoire actuelles ; ajouter quotas disque, âge et métriques hit/miss/taille. |
| P2 | Stockage | Les PDF, extractions JSON, SQLite, Qdrant, modèles, caches, exports et sauvegardes partagent les 200 Go. Aucun budget par classe n'est imposé. | Définir quotas et alertes à 70/80/90 %, rétention des caches et au moins une sauvegarde hors VM testée en restauration. |

## Risques plausibles à mesurer

- Le pic de PyMuPDF dépend fortement des images, tableaux et polices du PDF ; le nombre de pages seul
  ne suffit pas à prédire la mémoire.
- Le mapping mémoire et le page cache de Qdrant peuvent faire monter la mémoire système sans apparaître
  intégralement comme RSS Python.
- PyTorch et les bibliothèques BLAS peuvent créer plusieurs threads CPU par workflow. Le verrou de
  retrieval limite la simultanéité, mais le nombre de threads et la latence doivent être mesurés.
- Deux réponses simultanées peuvent retenir des preuves et sorties ARGO différentes après la phase
  locale. Ces objets sont bornés, mais aucune mesure agrégée n'existe encore.
- Une reconstruction ou une vérification exhaustive de l'index peut être nettement plus coûteuse que
  la recherche normale, car certains chemins accumulent tous les identifiants/payloads.

## Éléments non critiques pour la RAM de la VM

- Le modèle `chat-gpt-oss-120b` ne réside pas sur la VM : sa mémoire est portée par ARGO.
- Le cache global de vecteurs de requêtes est borné et ne conserve que 128 petits vecteurs.
- Les passages finaux et l'historique envoyés à ARGO sont bornés par les contrats de réponse.
- Qdrant ne stocke pas le texte intégral ; les preuves sont réhydratées depuis SQLite.
- Le reranker est désactivé par défaut. S'il est activé plus tard, son modèle et E5 ne doivent pas être
  multipliés par le nombre de workers.

## Budget de capacité initial

Ce budget est une enveloppe d'exploitation à valider, pas une mesure observée :

| Poste | Enveloppe initiale |
|---|---:|
| OS, services, reverse proxy et marge page cache | 6 Go minimum disponibles |
| API FastAPI | 1 Go cible |
| Worker chat avec E5/Qdrant | 12 Go cible, 20 Go RSS maximum |
| Ingestion ou maintenance | exécution exclusive dans l'enveloppe du worker |
| Marge d'incertitude | incluse dans la réserve système et vérifiée par cgroup |

La compatibilité est acquise seulement si le pic cgroup reste inférieur à 26 Go, sans OOM, swap
soutenue ni repli lexical silencieux, pendant le scénario de charge représentatif.

## Garde-fous appliqués avec cet audit

- profil mémoire `32gb` : avertissement à 24 Go utilisés, limite worker à 20 Go RSS et réserve de
  6 Go disponibles ;
- concurrence chat par défaut ramenée de 20 à 2 ;
- Ollama/Qwen et l'analyse visuelle désactivés par défaut ;
- politique d'édition `argo_only` imposée côté serveur, y compris si un ancien choix personnalisé est
  encore présent sur disque ;
- configuration exemple corrigée pour respecter la fenêtre E5 de 512 tokens ;
- documentation produit, accès, roadmap et règle méthodologique durable alignées sur la VM Linux.

## Plan ordonné par priorité et impact

### P0 — rendre la cible vraie

1. Figer le profil interne : Linux 32 Go, CPU, ARGO `chat-gpt-oss-120b`, E5 local, aucune autre IA.
2. Déployer la clé ARGO comme secret Linux et neutraliser les parcours DPAPI/custom provider.
3. Désactiver Ollama/Qwen et l'analyse visuelle tant qu'un contrat ARGO compatible n'est pas décidé.
4. Limiter le worker chat à 2, ingestion/maintenance à 1, et ajouter le profil mémoire 32 Go.
5. Remplacer ou désactiver explicitement l'OCR Windows dans l'exploitation Linux.
6. Exécuter le gate de capacité Linux 32 Go avant pilote.

### P1 — fiabiliser les gros corpus et la charge équipe

1. Borner le parser builtin et rendre l'ingestion réellement segmentée pour les gros PDF.
2. Séparer les unités API, chat et maintenance avec limites cgroup et politiques de redémarrage.
3. Ajouter reverse proxy TLS, authentification, sauvegarde et restauration testée.
4. Benchmarker Qdrant embarqué ; migrer vers un service Qdrant sur la même VM si la taille ou les
   pauses GIL/page cache franchissent les seuils adoptés.
5. Paginer les audits d'index et supprimer les accumulations exhaustives en mémoire.

### P2 — exploiter et préparer l'édition producteurs

1. Mettre quotas/rétention sur caches, exports et sauvegardes, avec alertes de stockage.
2. Ajouter tableaux de bord de durée, mémoire, files, erreurs ARGO et dégradations retrieval.
3. Conserver l'interface de fournisseur dans le domaine, mais la verrouiller par politique d'édition ;
   la décision fournisseur de l'édition producteurs reste ouverte et non implémentée.
4. Définir ultérieurement le sous-corpus producteurs, ses mises à jour et son modèle de coût.

## Instrumentation et gate de validation

Chaque scénario doit enregistrer sans contenu scientifique : RSS/USS du processus, mémoire cgroup,
mémoire système disponible, swap, durée et pic par étape, taille des lots, pages/caractères/éléments du
PDF, nombre de chunks, points Qdrant, hits/misses cache, attente du verrou local, nombre de jobs actifs,
appels/tokens/erreurs ARGO et espace disque par répertoire.

Scénario minimal : démarrage à froid, ingestion du plus gros PDF représentatif, OCR Linux si activé,
indexation E5 complète, deux chats `deep` simultanés, une reprise après interruption, puis une
vérification d'index. Le gate échoue sur OOM, swap durable, moins de 6 Go disponibles, RSS worker au-delà
de 20 Go, corruption/rejeu, perte de preuve, dégradation vectorielle silencieuse ou disque au-delà de
80 %.
