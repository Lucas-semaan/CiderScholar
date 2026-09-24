# Modèle d’accès et de distribution

Statut : cible interne acceptée le 24 septembre 2026. Cette décision remplace le pilote distribué sur
postes Windows.

## Déploiement interne

- une VM Linux unique héberge React, FastAPI, les workers, SQLite, Qdrant, E5, les PDF et les caches ;
- la VM possède 32 Go de RAM, un CPU sans GPU et environ 200 Go de stockage ;
- FastAPI reste sur `127.0.0.1` derrière un reverse proxy TLS authentifié ;
- les membres de l'équipe accèdent au service partagé selon des identités et droits définis au proxy ;
- les conversations, travaux, corpus et journaux restent sur la VM et suivent une politique de
  sauvegarde et de rétention explicite.

## Clé et fournisseur IA

- ARGO avec `chat-gpt-oss-120b` est l'unique fournisseur de génération de l'édition interne ;
- aucune API payante, aucun modèle local et aucun repli automatique ne sont autorisés ;
- la clé est injectée comme secret Linux au processus worker et n'est jamais écrite dans YAML,
  SQLite, les exports ou les logs ;
- les permissions du secret, sa rotation et le redémarrage associé relèvent de l'administrateur VM ;
- les quotas sont comptabilisés localement pour éviter les refus ARGO.

## Corpus commun

- SQLite sur la VM reste l'autorité scientifique ; Qdrant ne contient que vecteurs, identifiants et
  scores nécessaires ;
- les PDF, extractions, modèles locaux et index restent sur la VM ;
- une sauvegarde hors VM, chiffrée et restaurée périodiquement en test, est obligatoire ;
- les mises à jour du corpus restent versionnées, vérifiées et activées atomiquement ;
- les utilisateurs ordinaires n'exécutent ni collecte, ni réindexation, ni suppression destructive.

## Administration

- les rôles API, chat et ingestion/maintenance sont des unités d'exploitation séparées ;
- le chat démarre avec une concurrence de deux, l'ingestion et la maintenance avec une concurrence de
  un ; toute hausse exige un test de charge Linux 32 Go ;
- les clés bibliographiques restent dans le coffre d'exploitation et ne sont accessibles qu'aux jobs
  administratifs autorisés ;
- collecte, OCR, réindexation et maintenance sont planifiés hors pic et sont reprenables ;
- l'audit et le gate de capacité sont définis dans
  [`LINUX_VM_32GB_ARCHITECTURE_AND_MEMORY_AUDIT.md`](LINUX_VM_32GB_ARCHITECTURE_AND_MEMORY_AUDIT.md).

## Édition producteurs ultérieure

Cette édition n'est pas implémentée. Elle utilisera un sous-corpus et son fournisseur de génération
reste à décider entre API payante et modèle hébergé. Les contrats scientifiques et l'interface de
fournisseur restent séparés pour éviter un verrou architectural, sans exposer ce choix dans l'édition
interne ARGO-only.
