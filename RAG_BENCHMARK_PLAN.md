# Plan de benchmark du RAG scientifique CiderScholar

Version initiale : 7 août 2026\
But : décider de promotions scientifiques et opérationnelles, pas produire un score décoratif.\
Règle : aucune configuration n'est dite « meilleure » sans benchmark sur corpus représentatif, annotations expertes, ressources mesurées et revue des régressions.

## 1. Questions auxquelles le benchmark doit répondre

1. Le parser conserve-t-il structure, ordre, nombres et pages ?
2. Le chunking retrouve-t-il la bonne preuve sans couper l'unité scientifique ?
3. Le retrieval retrouve-t-il articles puis fragments pertinents aux rangs utiles ?
4. Le reranker améliore-t-il l'ordre sans perdre le rappel ni dépasser les ressources ?
5. Le pack de contexte couvre-t-il chaque axe avec peu de redondance ?
6. Chaque claim généré est-il exact, complet, supporté et correctement cité ?
7. Les nombres, unités, conditions et contradictions sont-ils fidèles ?
8. Le système s'abstient-il quand il doit, sans faux refus excessifs ?
9. Les profils rapide/équilibré/approfondi offrent-ils un compromis mesurable ?
10. L'indexation incrémentale est-elle complète, reprenable et sans orphelins ?

## 2. Gouvernance des données

### B01 — Splits scellés — ADD

Créer trois ensembles par familles de questions, articles et thèmes sans chevauchement substantiel :

- **train** : création des règles, prompts et analyse d'erreurs ;
- **dev** : choix des paramètres, pools, seuils et calibration ;
- **test** : une ouverture par décision de promotion, rapport signé.

Les hash de fichiers, IDs de questions et versions de corpus sont enregistrés dans le manifeste CiderQA existant.

Coût : temps expert élevé ; CPU/stockage faibles ; API nul ; complexité moyenne.\
Risque : fuite thématique ou articles quasi-dupliqués entre splits.\
Contrôle : déduplication DOI/hash/titre/auteurs, revue des familles, journal des consultations du test.

### B02 — Corpus représentatif — ADD

Échantillonner explicitement :

- anglais, français et questions cross-lingues ;
- PDF texte simple, multi-colonnes, scanné, OCR faible, tables, figures, équations, bibliographies longues ;
- articles avec/sans DOI, abstract, pages ou métadonnées riches ;
- accès full text et abstract-only ;
- ancien/nouveau, revues et thèmes divers ;
- questions avec preuve directe, indirecte et aucune preuve.

Minimum recommandé pour une première décision : 150–250 questions expertes, dont au moins 30 non répondables et 30 à contenu numérique. Une décision majeure de modèle/parser devrait viser davantage et publier l'incertitude.

Coût : annotation expert dominante ; stockage faible ; complexité élevée.\
Risque : échantillon cidricole trop étroit ou cas faciles surreprésentés.\
Contrôle : tableau de stratification publié dans chaque rapport.

### B03 — Unité d'annotation — ADD

Pour chaque question :

- `answerable` et motif ;
- sous-questions/axes obligatoires ;
- articles attendus et acceptables ;
- fragments exacts et pages ;
- type de preuve abstract/body/table/figure ;
- claims atomiques attendus ;
- paires claim-preuve ;
- valeurs, unités, intervalles, comparateurs et conditions ;
- contradictions/convergences attendues ;
- réponse de référence ou critères de réponse.

Coût : élevé ; complexité élevée.\
Risque : une seule « bonne » réponse trop restrictive.\
Contrôle : plusieurs preuves acceptables et désaccord annotateur conservé.

### B04 — Double annotation et arbitrage — ADD

Annoter en double au moins le dev/test, calculer l'accord adapté (catégoriel et spans), puis arbitrer les désaccords scientifiques. Les évaluateurs de réponses sont aveugles à la configuration.

Coût : environ ×2 temps expert ; complexité moyenne.\
Risque : coût et fatigue.\
Contrôle : lots courts, ordre randomisé et guide d'annotation versionné.

## 3. Baseline gelée

### B05 — Baseline opérationnelle — KEEP

Geler avant expérience :

- parser PyMuPDF + OCR actuel ;
- chunker cible 500, max configuré 750, overlap 80, avec version exacte du code ;
- E5-base 768 normalisé, préfixes query/passage ;
- FTS5 + dense + RRF k=60 ;
- reranker off, puis baseline secondaire current-reranker on ;
- budgets des trois intensités ;
- GPT-OSS 120B, prompts/hash, température et limites ;
- fingerprint SQLite et manifeste Qdrant.

La correction d'un invariant de chunk crée une baseline `current-fixed` séparée ; elle ne réécrit pas rétrospectivement les résultats historiques.

Coût : stockage d'artefacts faible/moyen ; complexité faible.\
Risque : modèle local sans hash complet.\
Contrôle : empreinte de tous les fichiers et dépendances.

## 4. Métriques par étage

## 4.1 Parsing

### B06 — Qualité structurelle — ADD

Mesurer sur PDF annotés :

- exactitude ordre de lecture par paires de blocs ;
- F1 sections/sous-sections ;
- exactitude titre/auteurs/DOI/année ;
- rappel/précision des tables, figures, légendes et références ;
- exactitude page/bbox ;
- taux de caractères/nombres/unités altérés ;
- CER/WER OCR par langue ;
- échecs, temps, pic RAM et taille des sorties.

Un parser ne peut être promu s'il gagne en structure mais perd significativement en page ou en nombres sur un sous-groupe critique.

## 4.2 Chunking

### B07 — Invariants et utilité — ADD

Publier :

- distribution p1/p5/p50/p95/p99/max des tokens ;
- pourcentage < minimum souhaité et > maximum dur ;
- taux d'overlap et quasi-doublons ;
- proportion de chunks multi-sections ;
- conservation des paragraphes et valeur-unité ;
- nombre de chunks/index/disque ;
- Recall@10/@20/@50 fragment après retrieval inchangé ;
- précision de page des preuves.

Le contrôle « max = 0 dépassement » est un invariant, pas une métrique moyenne.

## 4.3 Retrieval

### B08 — Métriques de classement — MODIFY

À chaque niveau notice/article/fragment et global/par strate :

- Recall@10, Recall@20, Recall@50 ;
- MRR ;
- nDCG@10, nDCG@20, nDCG@50 ;
- précision@k en diagnostic ;
- taux de questions avec au moins une preuve directe dans top-k ;
- couverture des axes obligatoires ;
- recall oracle après sélection document, union RRF, reranking et pack final.

Publier moyenne, intervalle bootstrap 95 %, nombre de cas et distribution des différences appariées.

## 4.4 Reranking

### B09 — Gain conditionnel — ADD

Mesurer sur le **même pool d'entrée** :

- MRR/nDCG avant et après ;
- Recall@k pour détecter les pertes liées à la troncature du pool ;
- taux de promotion de preuves directes ;
- régressions par langue/type de preuve ;
- p50/p95/p99, pic RAM, taille modèle et débit.

Un reranker ne reçoit pas le mérite du passage d'un pool 40 à 120 ; ce facteur est une expérience distincte.

## 4.5 Contexte

### B10 — Support par budget — ADD

Mesurer :

- rappel des preuves attendues dans le pack ;
- articles et axes uniques ;
- taux de redondance/quasi-doublons ;
- tokens/caractères ;
- preuves directes par 1 000 tokens ;
- négations/conditions/unités perdues par compression ;
- coût/latence de génération à contexte constant et variable.

## 4.6 Génération, claims et citations

### B11 — Exactitude et complétude — KEEP

Conserver les métriques CiderQA d'exactness/completeness au claim atomique. Ajouter une revue aveugle de : supporté, partiellement supporté, non supporté, contredit, non vérifiable.

### B12 — Claim support — ADD

Pour chaque paire claim-preuve :

- entailment ;
- négation ;
- population/matrice ;
- intervention/exposition ;
- comparateur ;
- condition/dose/durée ;
- temporalité ;
- modalité/causalité.

Métriques : précision des claims supportés, rappel des claims attendus et taux de claims non supportés par réponse.

### B13 — Citations — KEEP

Conserver : précision citation, rappel citation, entailment et exactitude de page. Ajouter :

- couverture bibliographique de chaque citation ;
- exactitude DOI/article ;
- précision du rôle support/contraste/méthode ;
- densité de claims composites sous une seule citation.

### B14 — Fidélité numérique — ADD

Au niveau claim numérique, évaluer :

- valeur exacte/canonique ;
- signe/comparateur ;
- unité et échelle ;
- intervalle/incertitude ;
- conversion correcte ;
- condition associée ;
- preuve citée contenant la même quantité dans le même contexte.

Score principal recommandé : proportion de claims numériques entièrement fidèles. Publier aussi les composantes pour diagnostiquer les faux refus du vérificateur.

### B15 — Contradictions — ADD

Sur les questions dédiées : précision/rappel des contradictions, et distinction : vraie contradiction, conditions différentes, non comparable, convergence. Une simple présence de mots « contraire » ne compte pas.

## 4.7 Abstention

### B16 — Calibration et utilité — KEEP

Conserver : sensibilité sur non-répondables, spécificité sur répondables, faux refus, score de Brier. Ajouter courbe selon seuil, motif d'abstention et résultats par famille.

Une hausse d'exactitude accompagnée d'une hausse majeure des faux refus n'est pas une promotion automatique.

## 4.8 Ressources

### B17 — Budget portable — ADD

Mesurer séparément ingestion, indexation et requête :

- CPU wall/user si disponible ;
- pic RSS et mémoire GPU éventuelle ;
- disque modèles, SQLite, FTS et Qdrant ;
- temps d'indexation/doc et total ;
- latence p50/p95/p99 par étage ;
- nombre d'appels ARGO, tokens/longueurs d'entrée-sortie et erreurs/quota ;
- cache chaud/froid.

Les résultats CiderQA doivent conserver par requête les traces non textuelles des variantes, pools
lexicaux/denses/RRF, pré/post-reranking, contexte final et motifs de retrait. Les mesures RAM
avant/après déjà disponibles servent au diagnostic ; une campagne qui publie un **pic** doit utiliser
un échantillonneur explicite. La machine, ses threads, sa RAM libre et les versions doivent figurer
dans le rapport signé.

## 5. Campagnes et ablations

Chaque campagne utilise des IDs stables, un seul changement principal et une baseline réexécutée sur le même état de machine lorsque possible.

### C01 — Correction du maximum de chunk — MODIFY

Comparer :

1. code historique ;
2. coupure stricte, mêmes 500/750/80 ;
3. cible 550, max 900, overlap 10–15 % ;
4. variante avec minimum par fusion.

Contrôles : zéro perte de texte, zéro dépassement, valeurs-unités, Recall multi-k, index size.\
Coût : CPU d'indexation et double stockage ; risque : frontières/IDs.\
Promotion : invariant strict obligatoire ; les nouveaux paramètres 400–700/900 restent au meilleur point dev scientifiquement acceptable.

### C02 — Parsing — TEST

Comparer sur mêmes PDF :

1. PyMuPDF actuel ;
2. PyMuPDF enrichi ;
3. GROBID ;
4. voie sélective ;
5. TEI/JATS natif quand disponible.

Ne pas faire de génération dans la première analyse : isoler d'abord structure, texte, page, nombres et coût.\
Promotion : Pareto structure/provenance/ressources ; aucune baisse critique de page ou nombre.

### C03 — Embeddings — TEST

Chunks et retrieval lexical constants :

1. E5-base ;
2. BGE-M3 dense ;
3. Jina v3 dense ;
4. éventuellement tâches/longueurs propres au modèle, déclarées comme sous-expérience.

Pour chaque modèle : recherche exacte ou paramètres ANN comparables, dimension/normalisation documentées.\
Promotion : gain apparié utile Recall/nDCG sur dev et absence de régression critique ; respect du budget RAM/p95.

### C04 — Hybride — TEST

1. lexical seul ;
2. dense seul ;
3. FTS5 + dense + RRF ;
4. sparse candidat + dense + RRF ;
5. triple fusion seulement si 4 justifie sa complexité.

Varier les poids/k sur train/dev, jamais test. Publier les requêtes exactes, numériques et cross-lingues séparément.

### C05 — Cascade documentaire — TEST

1. chunk global actuel ;
2. document → chunks filtrés ;
3. document → chunks filtrés + canal global de garde ;
4. variante 3 + parent.

Pools document 50/100/200 et garde globale 10/20/50.\
Mesure clé : recall oracle perdu à l'étape document.\
Promotion : aucune baisse critique de Recall@50 ; amélioration du rang/latence ou du support final.

### C06 — Rerankers et pools — TEST

Factoriel borné :

- reranker : off, mMARCO courant, BGE reranker v2 m3 ;
- pool : 40, 80, 120 ;
- entrée : enfant seul, enfant+titre+section, enfant+parent borné.

Réduire d'abord par successive halving sur train/dev si nécessaire, puis confirmer les finalistes.\
Promotion : gain nDCG/MRR/support net du coût et sans baisse de rappel liée au pool.

### C07 — Variantes et entités — TEST

1. originale seule ;
2. originale + fallback bilingue ;
3. originale + plan GPT-OSS ;
4. variante 3 + entités déterministes protégées.

Mesurer fidélité des entités, exact queries, cross-lingue, bruit et appels API. La question originale garde toujours son canal.

### C08 — Pack de contexte — TEST

1. pack actuel ;
2. quotas par axes ;
3. compression extractive ;
4. expansion parent ;
5. combinaison finale.

Tracer support/token et qualité de génération pour 12/20/30 items et 24/36/42/56 k caractères, sans supposer que plus est mieux.

### C09 — Génération — TEST

1. mono-appel actuel ;
2. claims atomiques multi-preuves ;
3. map-reduce par article/axe ;
4. variante 2/3 + vérification déterministe ;
5. variante 4 + juge sémantique.

Température 0/0,1 ; 0,35 seulement comme ablation historique.\
Mesurer exactitude, complétude, support, citations, nombres, contradictions, abstention, API et p95.

### C10 — Profils — TEST

Après fixation des composants, mesurer rapide/équilibré/approfondi sur le même test :

- qualité absolue et marginale ;
- ressources/appels ;
- taux d'échec/quota ;
- saturation du nombre d'items ;
- préférence utilisateur séparée de la correction scientifique.

Un profil plus coûteux doit montrer un gain de couverture/completude ou être simplifié.

## 6. Procédure d'exécution reproductible

1. Vérifier les quatre validations du dépôt.
2. Enregistrer commit/worktree diff hash, config sans secret et versions.
3. Geler corpus SQLite en lecture seule et calculer `corpus_fingerprint`.
4. Construire ou sélectionner la génération Qdrant signée.
5. Chauffer explicitement ou déclarer cache froid ; ne pas mélanger.
6. Exécuter les cas dans un ordre randomisé déterministe.
7. Enregistrer sorties structurées, décisions et ressources sans texte privé dans les logs globaux.
8. Calculer cas individuels puis agrégats et intervalles bootstrap.
9. Produire analyse par strate et liste complète des régressions.
10. Faire noter les réponses finalistes à l'aveugle par experts.
11. Décider sur dev ; exécuter une fois le test scellé.
12. Signer le rapport avec hash du dataset, corpus, index, prompts et configuration.

## 7. Séparation stricte des rapports

### Rapport R — Retrieval

Parser/chunker/index, listes classées à chaque étage, Recall@10/20/50, MRR, nDCG multi-k, ressources. Aucun appel de génération nécessaire.

### Rapport RR — Reranking

Pool d'entrée figé, rang avant/après, métriques et coût du cross-encoder.

### Rapport G — Génération

Pack de contexte figé ; exactitude, complétude, support, nombres, contradictions, abstention et API. Le retrieval ne varie pas.

### Rapport C — Citations

Claims et références : précision/rappel, entailment, page, DOI/article, rôle et locator.

### Rapport E2E — Système

Configuration gagnante candidate de chaque étage, profils et ressources complètes. Un bon E2E ne remplace pas les rapports causaux précédents.

## 8. Critères de promotion

Les seuils numériques finaux doivent être validés avec les experts sur le dev. Les règles suivantes sont obligatoires :

### B18 — Non-régression de sûreté — KEEP

- zéro citation ou DOI fabriqué dans les cas inspectés ;
- zéro référence à un chunk/page inexistant ;
- aucun secret/PDF complet dans sortie ou logs ;
- maximum de chunk respecté pour la génération concernée ;
- synchronisation sans orphelin ; rollback démontré.

### B19 — Fidélité prioritaire — ADD

Une promotion ne peut pas compenser une baisse significative de claim support, citation precision, page accuracy ou numeric faithfulness par un simple gain de rappel. Toute régression dans un sous-groupe critique est revue manuellement.

### B20 — Rappel utile — ADD

Après sûreté, préférer le candidat qui améliore Recall@10/@20/@50 des preuves directes et la couverture des axes. Les intervalles et différences appariées doivent être publiés.

### B21 — Ressources — ADD

Fixer avant le test une enveloppe du portable : pic RAM, disque, p95 et nombre d'appels. Un candidat hors enveloppe peut rester disponible uniquement dans un profil explicite ou être rejeté.

### B22 — Complexité — KEEP

À qualité et ressources équivalentes, conserver la configuration la plus simple et la baseline éprouvée.

## 9. Décision et terminologie autorisée

Un résultat peut être :

- **promu** : passe qualité, sûreté, ressources et revue experte ;
- **promu pour un profil** : utile seulement sous un budget explicite ;
- **inconclusif** : intervalle/dataset insuffisant ;
- **rejeté** : régression ou coût injustifié ;
- **à répliquer** : gain dev non confirmé sur test.

Les formulations autorisées sont factuelles : « améliore Recall@20 de X à Y sur le split Z, IC… ». Les formulations générales « meilleur embedding scientifique » ou « RAG optimisé » sont interdites sans préciser corpus, version, métrique, ressources et incertitude.

## 10. Artefacts attendus par campagne

- manifeste de dataset et guide d'annotation ;
- fingerprint corpus ;
- manifeste parser/chunker/index/modèles ;
- configuration sans secret ;
- résultats cas par cas ;
- rapports R, RR, G, C et E2E applicables ;
- intervalles et analyses par strate ;
- coûts CPU/RAM/disque/API ;
- liste de régressions et arbitrages experts ;
- rapport signé de promotion ou rejet ;
- procédure de rollback.

## 11. Première campagne recommandée

La première campagne ne doit pas comparer cinq modèles à la fois. Elle doit :

1. étendre CiderQA avec Recall@10/@20/@50 et fidélité numérique ;
2. geler la baseline actuelle ;
3. corriger uniquement l'invariant de coupure longue dans une génération pilote ;
4. vérifier zéro perte de texte, zéro dépassement et la stabilité retrieval ;
5. seulement ensuite ouvrir C03 embeddings et C06 rerankers.

Cette campagne rend les optimisations suivantes interprétables et réversibles.
