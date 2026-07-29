# Checklist des actions externes restantes

Ce document regroupe uniquement les preuves que le dépôt ne peut pas produire seul. Ne jamais y
coller de clé ARGO, mot de passe, contenu de chat, PDF privé ou donnée scientifique sensible.

## 1. Distribution Windows et SharePoint

- [ ] Publier dans `CiderScholar / installers` `CiderScholar-0.2.0-windows-x64.exe`, son `.sha256`
  et `latest.json` (voir [`WINDOWS_BUILD_0.2.0.md`](WINDOWS_BUILD_0.2.0.md)).
- [ ] Copier le contenu prêt de [`SHAREPOINT_INSTALLATION.md`](SHAREPOINT_INSTALLATION.md) dans une
  page SharePoint courte.
- [ ] Sur un profil Windows temporaire distinct, noter : version, SHA-256, heure, installation sans
  terminal, premier lancement et défauts éventuels.
- [ ] Répéter sur un poste physique 8 Go et un poste physique 16 Go.
- [ ] Faire installer par une personne qui n’a pas développé l’application ; ne consigner que les
  difficultés et résultats non sensibles.

Rapport conseillé :

```text
Date :
Poste/profil pseudonymisé :
RAM physique :
Version :
SHA-256 installateur :
SHA-256 corpus :
Installation sans terminal : oui/non
Premier lancement : réussi/échoué
Défauts P0/P1/P2 :
Décision :
```

## 2. Contrôles ARGO et démonstration

- [ ] Saisir localement une nouvelle clé selon [`ARGO_KEY_SETUP.md`](ARGO_KEY_SETUP.md), puis
  exécuter [`ARGO_MANUAL_VALIDATION.md`](ARGO_MANUAL_VALIDATION.md).
- [ ] Envoyer une seule suggestion publique non sensible selon
  [`DOCUMENT_SUGGESTIONS.md`](DOCUMENT_SUGGESTIONS.md).
- [ ] Répéter la démonstration avec une seule génération ARGO réelle selon
  [`DEMO_RUNBOOK.md`](DEMO_RUNBOOK.md).
- [ ] Observer quatre échéances hebdomadaires réelles de maintenance.

Ne conserver que date, type de contrôle, état final, durée, compteurs et défauts. La clé, la question
privée et la réponse ne font jamais partie du rapport.

## 3. CiderQA et Deep Research

- [ ] Constituer et faire adjuger CiderQA selon [`CIDERQA_PROTOCOL.md`](CIDERQA_PROTOCOL.md).
- [ ] Produire `readiness.json` avec `scripts.validate_ciderqa_dataset` avant les baselines.
- [ ] Finaliser les observations contextuelles selon
  [`CIDERQA_CONTEXTUAL_CALIBRATION.md`](CIDERQA_CONTEXTUAL_CALIBRATION.md).
- [ ] Exécuter les deux baselines signées selon [`CIDERQA_BASELINES.md`](CIDERQA_BASELINES.md).
- [ ] Exécuter les ablations selon [`CIDERQA_ABLATIONS.md`](CIDERQA_ABLATIONS.md).
- [ ] Transformer les six catégories d’erreur réelles selon
  [`CIDERQA_REGRESSIONS.md`](CIDERQA_REGRESSIONS.md).
- [ ] Exécuter les profils physiques selon
  [`DEEP_RESEARCH_PROFILE_VALIDATION.md`](DEEP_RESEARCH_PROFILE_VALIDATION.md).

Le mode approfondi reste indisponible tant que le bundle d’activation signé n’est pas accepté par le
gate de promotion. Aucun seuil ni résultat synthétique ne doit être substitué aux observations.

## 4. Découverte assistée

- [ ] Faire adopter la grille à sept critères par les experts.
- [ ] Fournir des classements pair-à-pair aveugles pour la calibration.
- [ ] Valider les quatre workflows déterministes et leurs tolérances.
- [ ] Choisir un sandbox Windows ou conteneur réellement isolé pour implémenter `AnalysisExecutor`.
- [ ] Constituer un benchmark cidricole non sensible avec vérité terrain.
- [ ] Sélectionner une étude pilote, nommer les approbateurs et un travail expert témoin.

Les formats sont décrits dans [`ASSISTED_DISCOVERY_SCOPE.md`](ASSISTED_DISCOVERY_SCOPE.md) et
[`EXPERIMENTAL_DATA.md`](EXPERIMENTAL_DATA.md). Sans backend isolé, approbation ou vérité terrain,
l’application refuse l’exécution au lieu de simuler une validation.

## 5. Pilote et décisions conditionnelles

- [ ] Nommer deux personnes pilotes et vérifier installation, clés personnelles, même version de RAG
  et isolation des espaces privés.
- [ ] Après correction des défauts réels, publier la version corrigée et étendre progressivement aux
  dix postes.
- [ ] N’étudier un service central que si une infrastructure devient réellement disponible.
- [ ] N’étudier Microsoft Graph que si une insuffisance de la synchronisation OneDrive locale est
  mesurée.
