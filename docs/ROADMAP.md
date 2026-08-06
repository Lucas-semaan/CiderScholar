# Roadmap d’exécution CiderScholar

Version consolidée du 2026-07-27, étendue avec les jalons de validation scientifique, de recherche
full-text approfondie et de découverte assistée. Cette roadmap remplace les hypothèses de serveur
central et de connexion LDAP. Elle décrit une application locale distribuée sur les postes
personnels de l’équipe.

## Cible produit confirmée

- environ dix utilisateurs, chacun sur son poste personnel Windows 11 ;
- installation guidée sans terminal, distribuée depuis SharePoint ;
- 8 à 16 Go de RAM et plusieurs dizaines de gigaoctets disponibles ;
- aucun compte CiderScholar et aucun écran de connexion ;
- conversations privées conservées localement jusqu’à suppression ;
- export et sauvegarde des conversations ;
- une clé ARGO personnelle saisie dans l’application et chiffrée avec Windows DPAPI ;
- quotas ARGO personnels : 20 requêtes/minute, 120/heure et 200/180 minutes ;
- un RAG commun identique, préparé sur la machine administrateur et publié sur SharePoint ;
- une copie de sécurité du corpus principal sur un drive protégé ;
- des documents privés locaux séparés du corpus commun ;
- des suggestions acceptant DOI, URL, PDF, référence manuelle et commentaire ;
- une évaluation de pertinence ARGO immédiate, sans écran de modération ni suivi de statut ;
- les clés bibliographiques dédiées à l’outil restent sur la machine administrateur ;
- collecte hebdomadaire proposée au premier lancement administrateur après l’échéance ;
- démonstration en présentiel depuis le seul poste administrateur.

Les évolutions de qualité scientifique et de découverte assistée sont planifiées après le pilote dans
les jalons M10 à M12. Elles ne modifient pas les critères de sortie du pilote M9 tant qu’un seuil de
promotion explicite issu de CiderQA n’a pas été adopté.

Le contrat rédactionnel est dans
[`CHATBOT_RESPONSE_CONTRACT.md`](CHATBOT_RESPONSE_CONTRACT.md). Le modèle d’accès est dans
[`ACCESS_MODEL.md`](ACCESS_MODEL.md). Le parcours de clé ARGO est dans
[`ARGO_KEY_SETUP.md`](ARGO_KEY_SETUP.md).

## État actuel et responsabilités — 2026-08-05

Le dépôt contient 458 tâches : 400 sont terminées, une est en cours, 19 sont bloquées ou partielles
et 38 restent en attente d’actions réelles. La priorité en cours est la stabilisation du chatbot ; les
évolutions de forme du Corpus restent en attente de sa validation. Les autres blocages correspondent
à des observations CiderQA/expert, un exécuteur système isolé, des postes physiques, SharePoint,
ARGO réel ou des décisions conditionnelles d’architecture.

Conformément à la décision du 2026-07-27 de terminer tout le travail autonome, le code en aval est
désormais réalisé derrière le drapeau d’activation inactif, sans attendre les données humaines. Une
tâche de validation ou de promotion reste néanmoins non cochée tant que son critère réel n’est pas
observable.

Validation consolidée du même état :

- Ruff format et lint : 365 fichiers conformes ;
- Pytest : 663 tests réussis ;
- frontend : format, ESLint, TypeScript, 19 fichiers/61 tests Vitest et build Vite réussis ;
- installeur Windows 0.2.3 : compilation, réinstallation locale et smoke test réussis, manifeste de
  13 011 fichiers, SHA-256
  `3f1a3519352d0dc65f8534572c54f1b3597365fb62223b4de60ac3b315bea58a`.

La colonne **Vous / équipe** regroupe ce que le code ne peut pas auto-attester : secrets personnels,
SharePoint réel, profils Windows distincts, matériel physique, experts et données scientifiques
réelles. La colonne **Code** regroupe les changements que Codex peut poursuivre dans le dépôt. Les
tâches présentes dans les deux colonnes sont mixtes : votre entrée ou validation débloque ensuite une
implémentation ou une correction.

### Vue exhaustive du reste à faire

| Phase | Vous / équipe | Code |
|---|---|---|
| Distribution et tests réels | `KEY-030`, `PKG-035`, `SUG-035`, `ADM-030`, `INS-024`, `INS-025`, `INS-029`, `INS-032`, `INS-033`, `INS-035` | Corriger uniquement les défauts que ces essais font apparaître. |
| Démonstration et pilote | `DEM-015`, `ROL-001` à `ROL-005`, `ROL-008` à `ROL-010` | `DEM-017`, `DEM-018`, `ROL-007`, après réception des rapports réels. |
| CiderQA | `EVL-005` à `EVL-010` : constituer et faire valider le jeu réel | Exécuter les outils prêts d’`EVL-016` et `EVL-018`, puis corriger uniquement les défauts observés. |
| Deep Research | Fournir les observations CiderQA pour `DRS-010`, le jeu complet pour `DRS-024`/`DRS-025` et un poste 8 Go pour `DRS-026` | Les gates et le mode inactif sont prêts ; exécuter les commandes puis corriger seulement les échecs réels. |
| Découverte assistée | Adopter la grille, fournir classements aveugles, validations de domaine, vérité terrain et étude pilote : `DSC-005`, `DSC-007`, `DSC-012`, `DSC-019`, `DSC-020`, `DSC-022`, `DSC-023` | Brancher un exécuteur système isolé dans l’interface préparée de `DSC-011` lorsqu’un environnement cible est choisi. |
| Améliorations postérieures | Décider si une infrastructure centrale ou une insuffisance OneDrive déclenchent `NEXT-009`/`NEXT-010` | `NEXT-001` à `NEXT-008` sont terminées ; aucun développement conditionnel n’est lancé sans besoin observé. |

### Ce que vous pouvez faire maintenant

1. **Débloquer la calibration Deep Research**
   (`EVL-005` à `EVL-010`, puis `DRS-010`) :
   constituer le CiderQA réel et faire étiqueter par les experts la pertinence des résumés contextuels
   du split développement. Le code attend un fichier d’observations d’au moins 20 fragments issus
   d’au moins 10 questions, avec les deux classes pertinent/rejeté et le hash du split gelé. Le format
   et la commande sont dans [`CIDERQA_CONTEXTUAL_CALIBRATION.md`](CIDERQA_CONTEXTUAL_CALIBRATION.md).
2. **Préparer la distribution SharePoint**
   (`INS-024`, `INS-025`) :
   publier l’installateur hashé dans le dossier protégé, créer la page courte d’installation et noter
   le chemin SharePoint synchronisé retenu.
3. **Fournir les environnements Windows réels**
   (`PKG-035`, `INS-029`, `INS-032`, `INS-033`, `INS-035`) :
   un profil Windows temporaire distinct, un poste 8 Go, un poste 16 Go et une personne indépendante.
   Pour chaque essai, conserver version, hash, RAM, résultat et défauts, sans donnée personnelle.
4. **Exécuter les trois contrôles externes bornés** :
   saisir localement une nouvelle clé sans jamais la transmettre (`KEY-030`), lancer une seule
   suggestion publique/non sensible (`SUG-035`) et répéter la démonstration avec une seule génération
   ARGO réelle (`DEM-015`).
5. **Observer la maintenance**
   (`ADM-030`) :
   consigner quatre échéances hebdomadaires manuelles successives ; quatre exécutions rapprochées ou
   simulées ne remplacent pas ces quatre cycles.
6. **Nommer et équiper le pilote**
   (`ROL-001` à `ROL-005`) :
   désigner deux personnes, faire installer depuis SharePoint, vérifier leurs clés personnelles, le
   même hash de corpus et l’isolation des chats/documents privés.
7. **Constituer CiderQA réel**
   (`EVL-005` à `EVL-010`) :
   fournir au moins 100 questions issues de documents cidricoles réels, dont au moins 25 full-text,
   15 non-répondables et 20 multi-articles/comparatives/contradictoires, équilibrées français/anglais,
   puis organiser la validation experte en aveugle.

Les résultats attendus de ces actions sont des rapports datés et non sensibles, des hashes et des
décisions expertes. Les clés ARGO, mots de passe, PDF privés et contenus de chat ne doivent jamais être
ajoutés au dépôt ou envoyés dans cette conversation.
La checklist consolidée et ses champs de rapport prêts à remplir sont dans
[`USER_ACTION_CHECKLIST.md`](USER_ACTION_CHECKLIST.md).

### Ce qu’il reste à coder en priorité

1. **Aucun chantier fonctionnel autonome identifié** : les formats, commandes, gates, migrations,
   interfaces et tests de `DRS-010`, `EVL-016`, `EVL-018`, `DRS-024` à `DRS-026`, M12 et
   `NEXT-001` à `NEXT-008` sont présents.
2. **Intégration préparée mais dépendante d’un choix d’environnement** : `DSC-011` attend un backend
   d’exécution réellement isolé ; le manifeste, l’interface d’exécution et le refus par défaut sont
   prêts afin de le brancher sans modifier les contrats scientifiques.
3. **Code ultérieur uniquement après observation** : exécuter les validations réelles puis corriger
   les défauts qu’elles révèlent (`DEM-017`, `DEM-018`, `ROL-007` et éventuelles régressions).
   Deep Research reste désactivé jusqu’aux baselines, ablations, promotion et profils physiques.

### Ordre révisé

Les actions SharePoint/Windows/ARGO et la constitution de CiderQA peuvent avancer en parallèle du
code. L’ordre de réalisation autonome devient :

Le parcours autonome prévu est achevé : garde-fous `DRS-010` → `DRS-011` à `DRS-023` derrière le
mode inactif → outils `EVL-016`/`EVL-018` et `DRS-024` à `DRS-026` → socle M12 →
`NEXT-001` à `NEXT-008`.

L’ordre de promotion reste inchangé et dépend des résultats réels :

observations CiderQA réelles → calibration `DRS-010` → baselines et ablations
→ seuil `DRS-025` → essais physiques `DRS-026` → activation.

L’ordre de validation humaine devient :

distribution SharePoint → profils Windows réels → démonstration ARGO → pilote à deux → déploiement à
dix → CiderQA expert → tests 8/16 Go → pilote de découverte assistée.

## Architecture cible

Chaque poste exécute localement FastAPI, le frontend, SQLite, Qdrant, E5 et un worker. Le navigateur
ne parle qu’à `127.0.0.1`. Fermer ou changer de chat ne coupe pas le worker. Fermer complètement
l’application ou éteindre le poste interrompt l’exécution, mais la file SQLite reprend au prochain
lancement.

Le corpus commun est un paquet versionné et non modifiable sur les postes utilisateurs. Les documents
privés utilisent un stockage séparé. La recherche fusionne les deux espaces en conservant leur origine.

SharePoint sert de canal de distribution pour l’installateur, les versions du corpus et les suggestions.
La première version s’appuie sur un dossier SharePoint synchronisé localement par OneDrive. Elle
n’intègre pas Microsoft Graph ni une nouvelle authentification Microsoft.

La machine administrateur possède un profil local explicite. Elle seule collecte les sources
bibliographiques, consomme les clés bibliographiques de l’outil, importe les suggestions retenues,
construit une nouvelle version du corpus et la publie sur SharePoint.

## Règles pour le travail multi-agent

1. Une tâche correspond à un objectif observable et à un petit ensemble de fichiers.
2. Un agent vérifie toutes les dépendances avant de commencer.
3. Une tâche ne mélange pas migration, domaine, API et interface sauf mention explicite.
4. Chaque changement de contrat possède un test backend et un test TypeScript séparés.
5. Les tâches de fichiers partagés sont sérialisées ; les autres peuvent avancer en parallèle.
6. Aucun test automatique ne contacte ARGO, SharePoint ou une API bibliographique réelle.
7. Les tests réels manuels sont bornés et explicitement nommés.
8. Aucun secret, PDF complet ou contenu de conversation n’apparaît dans les logs.
9. Une mise à jour ou une reprise doit être idempotente après interruption.
10. Une tâche est terminée uniquement lorsque son critère `Fini lorsque` est vrai.
11. Les tâches sont réalisées successivement dans l’ordre où elles apparaissent dans la roadmap. Ne
    pas commencer la tâche suivante avant d’avoir terminé et vérifié la tâche courante. Cette règle
    d’exécution prévaut sur les indications générales de parallélisation décrites plus bas.
12. Le fond de la roadmap est immuable pendant son exécution : ne pas reformuler, déplacer, fusionner,
    supprimer ou ajouter une tâche, une dépendance ou un critère `Fini lorsque`.
13. Lorsqu’une tâche est réellement terminée et vérifiée, remplacer uniquement sa case `[ ]` par
    `[x]`, puis ajouter immédiatement sous la tâche une ligne `Réalisation :` résumant simplement ce
    qui a été fait et comment cela a été vérifié.
14. La ligne `Réalisation :` doit permettre à un mainteneur humain de comprendre le changement sans
    lire l’historique de l’agent. Elle reste factuelle, courte et ne contient ni raisonnement interne,
    ni journal détaillé, ni donnée sensible.
15. Ne jamais cocher une tâche partiellement réalisée. Une tâche commencée peut être marquée `[~]`
    sans modifier son texte ; une tâche bloquée peut être marquée `[!]` avec une courte ligne
    `Blocage :` factuelle.

Convention : `[ ]` à faire, `[~]` en cours, `[x]` terminé, `[!]` bloqué.
Dans une dépendance, `AAA-001` à `AAA-005` signifie que les cinq tâches inclusives doivent être
terminées. Les jalons `M0` à `M12` ne sont jamais utilisés comme dépendances d’une tâche.

Exemple de mise à jour autorisée :

```markdown
Avant : [ ] TASK-ID — texte original inchangé.
Après : [x] TASK-ID — texte original inchangé.
Réalisation : changement effectué dans les fichiers concernés et validé par le test ciblé.
```

## Jalons et parallélisation

| Jalon | Résultat | Dépendances |
|---|---|---|
| M0 | Architecture locale figée | aucune |
| M1 | Réponses scientifiques et format déterministe | M0 |
| M2 | Clé ARGO intégrée et quotas protégés | M0 |
| M3 | Chats durables en arrière-plan | M1, M2 |
| M4 | Corpus commun et documents privés séparés | M0 |
| M5 | Paquets RAG et mises à jour SharePoint | M4 |
| M6 | Suggestions automatiques via SharePoint | M1, M2, M5 |
| M7 | Maintenance hebdomadaire administrateur | M5, M6 |
| M8 | Installation Windows sans terminal | M2, M3, M5 |
| M9 | Démonstration puis pilote équipe | M7, M8 |
| M10 | Validité scientifique mesurée par CiderQA | M3, M4, M9 |
| M11 | Chat full-text « CiderScholar Deep Research » | M10 |
| M12 | Hypothèses et données expérimentales avec humain dans la boucle | M11 |

Les lots `FMT-*`, `KEY-*` et `COR-*` peuvent commencer en parallèle. `JOB-*` peut avancer avec des
clients ARGO simulés. `PKG-*` et `SUG-*` attendent la stabilisation du format de paquet. Les tests de
démonstration peuvent être préparés dès que les contrats API sont figés.
Les tâches `EVL-*` précèdent toute promotion d’un nouveau pipeline scientifique. `DRS-*` réunit les
briques full-text existantes derrière le chat durable. `DSC-*` ne commence qu’après validation du
mode Deep Research sur CiderQA et ne rend jamais l’exécution expérimentale autonome.

---

## M0 — décisions d’architecture

- [x] `ARC-001` Retenir une installation complète sur chaque poste personnel Windows 11.
  Fini lorsque : la décision figure dans `ACCESS_MODEL.md`.
- [x] `ARC-002` Refuser tout accès réseau entrant à FastAPI dans le pilote.
  Fini lorsque : l’écoute reste limitée à `127.0.0.1`.
- [x] `ARC-003` Utiliser le compte Windows comme frontière locale sans login CiderScholar.
  Fini lorsque : aucune tâche LDAP ne subsiste dans la roadmap.
- [x] `ARC-004` Conserver les conversations uniquement sur le poste de leur auteur.
  Fini lorsque : aucune synchronisation de conversation n’est prévue.
- [x] `ARC-005` Conserver les conversations jusqu’à suppression manuelle.
  Fini lorsque : aucune purge automatique n’est activée par défaut.
- [x] `ARC-006` Utiliser une clé ARGO personnelle par poste et par utilisateur Windows.
  Fini lorsque : le tutoriel ne demande ni `.env` ni partage de clé.
- [x] `ARC-007` Utiliser SharePoint comme canal de distribution protégé.
  Fini lorsque : installateur, corpus et suggestions possèdent des emplacements distincts.
- [x] `ARC-008` Faire de la machine administrateur la source d’autorité éditoriale.
  Fini lorsque : seule cette machine peut publier une version du corpus commun.
- [x] `ARC-009` Conserver une sauvegarde du corpus principal sur un drive protégé.
  Fini lorsque : sauvegarde et publication sont deux opérations distinctes.
- [x] `ARC-010` Séparer corpus commun et documents privés.
  Fini lorsque : une mise à jour commune ne peut supprimer un document privé.
- [x] `ARC-011` Réserver les clés bibliographiques dédiées à la machine administrateur.
  Fini lorsque : elles ne sont jamais placées dans l’installateur utilisateur.
- [x] `ARC-012` Remplacer la collecte nocturne par une proposition hebdomadaire au premier lancement
  administrateur éligible. Fini lorsque : aucune machine permanente n’est requise.
- [x] `ARC-013` Limiter la démonstration initiale au poste administrateur en présentiel.
  Fini lorsque : aucun déploiement multi-poste n’est requis pour M9.

## M1 — contrat scientifique et format ARGO

- [x] `FMT-001` Ajouter un test pour « réponds en prose, sans puces ».
  Réalisation : test d'intégration ajouté ; une réponse ARGO simulée en liste est rejetée lorsque la prose sans puces est demandée, validé par Pytest.
  Dépendances : aucune. Fini lorsque : une réponse simulée `bullet_list` échoue.
- [x] `FMT-002` Ajouter un test pour une question sans consigne de forme.
  Réalisation : test unitaire ajouté pour une question sans consigne de forme ; le style obtenu est `prose`, validé par Pytest.
  Dépendances : aucune. Fini lorsque : le style attendu est `prose`.
- [x] `FMT-003` Ajouter un test pour une liste explicitement demandée en français.
  Réalisation : test unitaire ajouté pour une demande française explicite de liste ; le style obtenu est `bullet_list`, validé par Pytest.
  Dépendances : aucune. Fini lorsque : le style attendu est `bullet_list`.
- [x] `FMT-004` Ajouter le même test en anglais.
  Réalisation : test unitaire anglais ajouté ; les demandes explicites françaises et anglaises produisent toutes deux `bullet_list`, validé par Pytest.
  Dépendances : `FMT-003`. Fini lorsque : les deux langues suivent la même règle.
- [x] `FMT-005` Ajouter un test de priorité pour « liste les facteurs, mais sans puces ».
  Réalisation : test de priorité ajouté ; l'interdiction explicite des puces impose `prose` malgré le mot « liste », validé par Pytest.
  Dépendances : `FMT-001`, `FMT-003`. Fini lorsque : l’interdiction explicite l’emporte.
- [x] `FMT-006` Créer le type fermé `ResponseStyle`.
  Réalisation : enum chaîne `ResponseStyle` créée avec les seules valeurs `prose` et `bullet_list` ; les valeurs inconnues sont rejetées par les tests.
  Dépendances : aucune. Fini lorsque : seules `prose` et `bullet_list` sont valides.
- [x] `FMT-007` Créer une fonction pure de détection du style demandé.
  Réalisation : fonction pure `detect_response_style` ajoutée avec normalisation Unicode et règles bilingues ; tous les cas `FMT-001` à `FMT-006` passent.
  Dépendances : `FMT-001` à `FMT-006`. Fini lorsque : tous les cas passent.
- [x] `FMT-008` Faire de la prose le repli obligatoire.
  Réalisation : repli `prose` testé pour une entrée vide et des consignes de forme inconnues, validé par Pytest.
  Dépendances : `FMT-007`. Fini lorsque : toute entrée inconnue retourne `prose`.
- [x] `FMT-009` Retirer à ARGO le choix libre du champ `response_format`.
  Réalisation : le schéma JSON envoyé à ARGO fixe `response_format` avec `const` à la valeur calculée par l'application, vérifié par un test du client simulé.
  Dépendances : `FMT-007`. Fini lorsque : le schéma impose la valeur applicative.
- [x] `FMT-010` Passer le style attendu au validateur métier.
  Réalisation : le validateur compare la valeur ARGO au style calculé et rejette toute discordance ; le cas prose contre liste est validé par Pytest.
  Dépendances : `FMT-009`. Fini lorsque : une discordance est rejetée.
- [x] `FMT-011` Passer le style attendu au renderer Markdown.
  Réalisation : le renderer reçoit désormais le `ResponseStyle` calculé par l'application et ne lit plus le choix de forme retourné par ARGO, validé en prose et en liste.
  Dépendances : `FMT-010`. Fini lorsque : le renderer ne dépend plus d’une préférence ARGO.
- [x] `FMT-012` Garantir qu’une réponse prose ne commence pas par `-`, `*` ou `•`.
  Réalisation : validation de chaque paragraphe de statement et de limite ajoutée ; les marqueurs `-`, `*` et `•` sont rejetés en prose, validé par Pytest.
  Dépendances : `FMT-011`. Fini lorsque : chaque paragraphe est contrôlé.
- [x] `FMT-013` Garantir qu’une réponse liste produit une puce non vide par énoncé.
  Réalisation : test de rendu ajouté avec deux énoncés ; chacun produit exactement une puce Markdown non vide, validé par Pytest.
  Dépendances : `FMT-011`. Fini lorsque : le rendu liste est déterministe.
- [x] `FMT-014` Ajouter au prompt le ton froid, factuel et non promotionnel.
  Réalisation : consigne explicite de ton froid, factuel et non promotionnel ajoutée au prompt système et vérifiée par un test ciblé.
  Dépendances : aucune. Fini lorsque : le prompt suit le contrat rédactionnel.
- [x] `FMT-015` Exiger des phrases simples et un vocabulaire scientifique précis.
  Réalisation : consigne de phrases simples et de vocabulaire scientifique précis ajoutée au prompt et couverte par le test de construction.
  Dépendances : `FMT-014`. Fini lorsque : la consigne est testée dans le prompt construit.
- [x] `FMT-016` Exiger la présentation des résultats positifs et négatifs pertinents.
  Réalisation : équilibre explicite entre résultats positifs et négatifs pertinents ajouté au prompt et vérifié par le test de construction.
  Dépendances : `FMT-014`. Fini lorsque : aucun optimisme par défaut n’est demandé.
- [x] `FMT-017` Exiger la présentation des biais, erreurs et limites documentés.
  Réalisation : le prompt impose de distinguer les faits des biais, erreurs et limites documentés ; la consigne est vérifiée par le test de construction.
  Dépendances : `FMT-014`. Fini lorsque : le prompt distingue faits et limites.
- [x] `FMT-018` Exiger que les améliorations restent des pistes si elles ne sont pas démontrées.
  Réalisation : le prompt maintient toute amélioration non démontrée au rang de piste et interdit de la présenter comme acquise ; test ciblé validé.
  Dépendances : `FMT-014`. Fini lorsque : une amélioration ne peut être rendue comme résultat acquis.
- [x] `FMT-019` Interdire emojis, émoticônes, compliments et superlatifs non étayés.
  Réalisation : interdiction explicite des emojis, émoticônes, compliments et superlatifs non étayés ajoutée au prompt et vérifiée par test.
  Dépendances : `FMT-014`. Fini lorsque : la liste d’interdictions est explicite.
- [x] `FMT-020` Ajouter un validateur d’absence d’emoji.
  Réalisation : détection Unicode des emojis ajoutée sur les énoncés et limites ; une réponse simulée contenant un emoji est rejetée par le test ciblé.
  Dépendances : `FMT-019`. Fini lorsque : une réponse simulée avec emoji est rejetée.
- [x] `FMT-021` Ajouter un test ciblé pour les introductions creuses connues.
  Réalisation : validateur et test ciblé ajoutés pour refuser les introductions « excellente question », « très bonne question » et leur équivalent anglais.
  Dépendances : `FMT-019`. Fini lorsque : « excellente question » est refusé.
- [x] `FMT-022` Construire les citations auteur-date depuis SQLite.
  Réalisation : citations auteur-date produites par le renderer depuis les métadonnées locales associées aux `record_ids`, sans champ auteur ou année fourni par ARGO ; test ciblé validé.
  Dépendances : aucune. Fini lorsque : ARGO ne fournit ni auteur ni année au renderer.
- [x] `FMT-023` Construire la section `Références` au format APA 7 depuis SQLite.
  Réalisation : section finale `Références` générée par l'application depuis les notices locales, avec auteur, année, titre, revue et DOI disponibles ; tests de rendu validés.
  Dépendances : `FMT-022`. Fini lorsque : chaque référence correspond à une notice autorisée.
- [x] `FMT-024` Dédupliquer et ordonner la bibliographie finale.
  Réalisation : notices dédupliquées par `record_id` puis triées par auteur, année et titre ; répétition et ordre alphabétique sont couverts par Pytest.
  Dépendances : `FMT-023`. Fini lorsque : une notice citée plusieurs fois apparaît une fois.
- [x] `FMT-025` Ajouter des fixtures APA pour un, deux et au moins trois auteurs.
  Réalisation : fixtures paramétrées APA ajoutées pour une, deux et trois signatures ; ponctuation, initiales et esperluette sont vérifiées.
  Dépendances : `FMT-023`. Fini lorsque : les variantes suivent le contrat.
- [x] `FMT-026` Ajouter une fixture APA sans auteur ou sans DOI.
  Réalisation : cas sans auteur et sans DOI ajoutés ; le titre devient l'entrée de référence et aucune identité ni URL DOI n'est inventée.
  Dépendances : `FMT-023`. Fini lorsque : aucune donnée manquante n’est inventée.
- [x] `FMT-027` Ajouter une correction ARGO bornée en cas de violation de structure.
  Réalisation : une réponse structurellement invalide déclenche une seule régénération complète puis échoue si elle reste invalide ; deux appels exactement sont vérifiés.
  Dépendances : `FMT-010`, `FMT-020`. Fini lorsque : une seule correction est tentée.
- [x] `FMT-028` Ajouter un test complet de réponse scientifique en prose avec références.
  Réalisation : test intégré ajouté pour prose, citations auteur-date, limites, absence de puces et bibliographie APA dédupliquée ; contrat complet validé avec ARGO simulé.
  Dépendances : `FMT-012`, `FMT-014` à `FMT-027`. Fini lorsque : le contrat complet passe.
- [x] `FMT-029` Vérifier manuellement une réponse ARGO réelle sans conserver son contenu sensible.
  Réalisation : une génération réelle bornée sur notice synthétique non sensible a été validée le 2026-07-22 avec `chat-gpt-oss-20b` ; seules date, modèle et conformité sont consignés.
  Dépendances : `FMT-028`. Fini lorsque : modèle, date et conforme/non conforme sont consignés.
- [x] `FMT-030` Mettre à jour l’aide utilisateur sur prose, listes et références.
  Réalisation : aide ajoutée dans l'interface et le README pour la prose par défaut, les listes explicites et les références APA locales ; suite frontend complète validée.
  Dépendances : `FMT-029`. Fini lorsque : le comportement est compréhensible sans détail technique.

## M2 — clé ARGO personnelle, chiffrement et quotas

- [x] `KEY-001` Créer une interface de stockage de secret local indépendante de FastAPI.
  Réalisation : protocole `LocalSecretStore` ajouté avec statut, enregistrement/remplacement, lecture et suppression, sans dépendance FastAPI ; contrat validé par Pytest.
  Dépendances : aucune. Fini lorsque : enregistrer, lire en mémoire, remplacer et supprimer existent.
- [x] `KEY-002` Étudier le service DPAPI déjà utilisé pour les identifiants éditeur.
  Réalisation : primitives DPAPI réutilisables et couplages propres au stockage éditeur identifiés dans les choix techniques ; la clé ARGO réutilisera le chiffrement sans dupliquer le registre éditeur.
  Dépendances : `KEY-001`. Fini lorsque : le code réutilisable est identifié sans duplication.
- [x] `KEY-003` Créer un stockage DPAPI dédié à la clé ARGO du compte Windows courant.
  Réalisation : stockage fichier DPAPI courant-utilisateur ajouté avec ciphertext versionné, remplacement atomique, lecture et suppression ; contenu chiffré validé par Pytest.
  Dépendances : `KEY-002`. Fini lorsque : le fichier ne contient que du ciphertext versionné.
- [x] `KEY-004` Définir un emplacement de secret hors SharePoint et hors exports.
  Réalisation : chemin local `data/secrets/argo-key.dpapi` défini, refusé s'il tombe sous `exports_dir` et documenté comme exclu des sauvegardes, paquets et dossiers SharePoint ; tests validés.
  Dépendances : `KEY-003`. Fini lorsque : sauvegardes et paquets l’excluent.
- [x] `KEY-005` Refuser une clé vide, trop longue ou contenant des espaces internes inattendus.
  Réalisation : validation ARGO ajoutée avec nettoyage des bords, limite de 4096 caractères et refus de tout espace interne ; cas négatifs couverts par Pytest.
  Dépendances : `KEY-003`. Fini lorsque : les validations négatives passent.
- [x] `KEY-006` Effacer les buffers de clé accessibles après création du client ARGO.
  Réalisation : copie `_api_key` supprimée du client après construction HTTP et remplacée par un booléen ; aucune propriété publique ne contient la valeur, vérifié par Pytest.
  Dépendances : `KEY-003`. Fini lorsque : aucune propriété publique ne conserve la valeur.
- [x] `KEY-007` Ajouter un statut public `configured` sans valeur partielle de clé.
  Réalisation : modèle public strict limité au booléen `configured` ajouté ; son payload ne contient aucun fragment de clé, vérifié par Pytest.
  Dépendances : `KEY-003`. Fini lorsque : même les derniers caractères ne sont pas renvoyés.
- [x] `KEY-008` Ajouter l’endpoint local d’enregistrement ou remplacement.
  Réalisation : routes locales GET/PUT ajoutées avec requête Pydantic stricte ; enregistrement, remplacement, absence d'écho et rejet des champs inconnus sont testés.
  Dépendances : `KEY-005`, `KEY-007`. Fini lorsque : champs inconnus refusés.
- [x] `KEY-009` Ajouter l’endpoint local de suppression.
  Réalisation : endpoint DELETE idempotent ajouté ; le ciphertext est supprimé et le statut repasse à faux. Les clients actuels étant fermés par requête et sans cache partagé, aucune instance persistante ne subsiste ; test HTTP validé.
  Dépendances : `KEY-007`. Fini lorsque : suppression invalide les clients et caches.
- [x] `KEY-010` Ajouter l’endpoint local `Tester la connexion` utilisant `/models`.
  Réalisation : endpoint POST `/api/argo-key/test` ajouté ; il charge la clé DPAPI et appelle uniquement `health()` sur `/models`, sans génération, vérifié par client simulé.
  Dépendances : `KEY-003`. Fini lorsque : aucun texte n’est généré.
- [x] `KEY-011` Différencier clé absente, refusée, réseau indisponible et modèle inaccessible.
  Réalisation : résultat public actionnable ajouté avec états `missing`, `rejected`, `network_unavailable`, `model_unavailable` et `ready` ; les quatre erreurs sont testées.
  Dépendances : `KEY-010`. Fini lorsque : chaque cas a un message actionnable.
- [x] `KEY-012` Mettre en cache la validation du modèle pour une durée bornée.
  Réalisation : cache partagé de validation ajouté pour 300 secondes, indexé par endpoint, modèle et empreinte de clé ; réutilisation puis expiration sont vérifiées par Pytest.
  Dépendances : `KEY-010`. Fini lorsque : chaque chat n’appelle pas systématiquement `/models`.
- [x] `KEY-013` Invalider ce cache lors du remplacement ou de la suppression de clé.
  Réalisation : remplacement et suppression appellent explicitement l'invalidation du cache partagé ; les deux chemins sont vérifiés par tests HTTP.
  Dépendances : `KEY-009`, `KEY-012`. Fini lorsque : aucune ancienne autorisation n’est réutilisée.
- [x] `KEY-014` Ajouter l’écran de clé dans l’assistant de premier lancement.
  Réalisation : dialogue de premier lancement ajouté lorsque la clé manque, avec saisie masquée et bouton `Vérifier et enregistrer` ; contrat frontend et build validés.
  Dépendances : `KEY-008`, `KEY-010`. Fini lorsque : le bouton est `Vérifier et enregistrer`.
- [x] `KEY-015` Ajouter remplacement, suppression et test dans Paramètres.
  Réalisation : carte Paramètres ajoutée avec saisie masquée de remplacement, test de connexion et suppression ; seule la présence de la clé est affichée, build frontend validé.
  Dépendances : `KEY-009` à `KEY-014`. Fini lorsque : la clé enregistrée n’est jamais relue.
- [x] `KEY-016` Intégrer le tutoriel `ARGO_KEY_SETUP.md` dans l’interface.
  Réalisation : tutoriel hors ligne intégré au premier lancement et aux Paramètres avec obtention, copie, vérification et règles de sécurité de la clé ; build validé.
  Dépendances : `KEY-014`. Fini lorsque : le parcours est consultable hors ligne.
- [x] `KEY-017` Ajouter un avertissement réseau INRAE/VPN avant le test.
  Réalisation : avertissement visible ajouté avant les tests du premier lancement et des Paramètres ; il recommande réseau INRAE/VPN sans bloquer l'action, build validé.
  Dépendances : `KEY-014`. Fini lorsque : il ne bloque pas un poste déjà connecté.
- [x] `KEY-018` Créer la politique locale 20/minute, 120/heure et 200/180 minutes.
  Réalisation : politique pure à trois fenêtres glissantes ajoutée ; limites 20/minute, 120/heure et 200/180 minutes ainsi que leurs frontières sont validées par Pytest.
  Dépendances : aucune. Fini lorsque : les trois fenêtres glissantes sont validées.
- [x] `KEY-019` Créer une table locale de consommation ARGO sans contenu ni clé.
  Réalisation : migration SQLite 11 ajoutée avec table `argo_request_events` limitée à utilisateur Windows, endpoint et horodatage ; schéma neuf et métadonnées sont testés.
  Dépendances : `KEY-018`. Fini lorsque : utilisateur Windows, date et endpoint suffisent au calcul.
- [x] `KEY-020` Purger les événements plus anciens que la fenêtre utile.
  Réalisation : purge SQLite ajoutée avec cutoff conscient du fuseau ; seuls les événements strictement antérieurs aux 180 minutes utiles sont supprimés, frontière testée.
  Dépendances : `KEY-019`. Fini lorsque : la table reste bornée.
- [x] `KEY-021` Calculer le prochain instant autorisé pour les trois fenêtres.
  Réalisation : calcul pur du premier instant autorisé ajouté pour toutes les fenêtres, y compris événements excédentaires ; frontières 20/21, 120/121 et 200/201 testées.
  Dépendances : `KEY-018`, `KEY-019`. Fini lorsque : les frontières 20/21, 120/121 et 200/201 passent.
- [x] `KEY-022` Réserver atomiquement une capacité avant chaque requête ARGO.
  Réalisation : service de réservation `BEGIN IMMEDIATE` ajouté et branché juste avant chaque requête HTTP ARGO ; deux workers concurrents ne peuvent réserver ensemble la dernière capacité, test validé.
  Dépendances : `KEY-021`. Fini lorsque : deux travaux locaux ne dépassent pas ensemble une limite.
- [x] `KEY-023` Compter `/models`, retries et erreurs HTTP selon l’hypothèse prudente.
  Réalisation : réservation placée avant l'appel HTTP ; un test vérifie que `/models`, sa nouvelle tentative et les réponses HTTP en erreur produisent chacune un événement local.
  Dépendances : `KEY-022`. Fini lorsque : toute requête réellement envoyée est comptée.
- [x] `KEY-026` Tester l’isolation DPAPI entre deux comptes Windows simulés.
  Réalisation : deux comptes Windows simulés utilisent le même ciphertext ; le second ne peut pas le déchiffrer, conformément à l'isolation DPAPI, test validé.
  Dépendances : `KEY-003`. Fini lorsque : un autre compte ne peut pas déchiffrer.
- [x] `KEY-028` Exclure secrets et caches de tous les exports et paquets.
  Réalisation : test d'inspection ajouté pour chaque archive actuellement produite ; fichiers et sentinelles placés dans `data/secrets` et `data/cache` restent absents des exports.
  Dépendances : `KEY-004`. Fini lorsque : un test inspecte chaque archive produite.
- [x] `KEY-029` Ajouter une procédure de rotation et suppression avant cession du poste.
  Réalisation : le tutoriel intégré et la documentation opérateur décrivent la vérification de la nouvelle clé, la suppression via l'application et le contrôle final avant cession du poste.
  Dépendances : `KEY-015`. Fini lorsque : elle figure dans le tutoriel.

## M3A — modèle SQLite de travaux durables

- [x] `JOB-001` Définir le type initial `chat_answer` et réserver les futurs types.
  Réalisation : `JobType` reste une union fermée et accepte désormais les cinq types livrés :
  `chat_answer`, `weekly_maintenance`, `deep_research`, `long_synthesis` et `private_ingestion`.
  Dépendances : aucune. Fini lorsque : une union fermée est documentée.
- [x] `JOB-002` Définir les états `queued`, `running`, `succeeded`, `failed`, `cancel_requested`,
  Réalisation : les six états et la matrice complète des transitions autorisées, y compris reprise et annulation coopérative, sont définis et testés ; les états terminaux ne permettent aucune sortie.
  `cancelled`. Dépendances : aucune. Fini lorsque : toutes les transitions autorisées sont écrites.
- [x] `JOB-003` Définir les étapes attente, recherche, enrichissement, ARGO, validation et persistance.
  Réalisation : les six étapes persistables sont fermées par `JobStep` et possèdent chacune un libellé français sûr destiné à l'interface.
  Dépendances : aucune. Fini lorsque : chaque étape possède un libellé utilisateur.
- [x] `JOB-004` Définir erreurs réessayables et définitives.
  Réalisation : timeout et quota sont classés réessayables ; authentification et validation sont terminales dans une table exhaustive sur les catégories d'erreur fermées.
  Dépendances : aucune. Fini lorsque : timeout, quota, authentification et validation sont classés.
- [x] `JOB-005` Définir le nombre maximal de tentatives et les délais.
  Réalisation : la politique autorise au plus trois tentatives avec délais fixes de 30 secondes puis 2 minutes et retourne explicitement l'épuisement, sans boucle illimitée.
  Dépendances : `JOB-004`. Fini lorsque : aucune retry loop n’est illimitée.
- [x] `JOB-006` Définir le payload versionné `chat_answer`.
  Réalisation : `ChatAnswerPayload` version 1 borne le message à 2–4000 caractères, exige une conversation UUID, borne l'option externe à un booléen et refuse tout champ inconnu.
  Dépendances : `JOB-001`. Fini lorsque : message, conversation et option externe sont bornés.
- [x] `JOB-007` Ajouter un `client_request_id` UUID obligatoire.
  Réalisation : le payload exige un `client_request_id` UUID et expose la clé stable `(conversation_id, client_request_id)` que la persistance utilisera pour l'idempotence.
  Dépendances : `JOB-006`. Fini lorsque : il sert de clé d’idempotence.
- [x] `JOB-008` Définir le contrat public d’un travail sans payload interne.
  Réalisation : `JobPublic` expose uniquement identifiants, type, état, étape, progression, dates, résultat et erreur bornée ; le payload, l'idempotence et le worker sont explicitement exclus.
  Dépendances : `JOB-001` à `JOB-007`. Fini lorsque : erreurs et étapes sont sûres.
- [x] `JOB-009` Ajouter une nouvelle migration sans modifier les précédentes.
  Réalisation : la migration 12 est ajoutée à la suite du registre 1–11 et la version courante augmente exactement de 11 à 12, avec test sur base initialisée.
  Dépendances : `JOB-001` à `JOB-008`. Fini lorsque : la version augmente de un.
- [x] `JOB-010` Créer la table `jobs` avec contraintes de type et état.
  Réalisation : `jobs` accepte uniquement les cinq types livrés et les six états contractuels ; les
  insertions SQLite avec type ou état inconnu échouent par contrainte après la migration 25.
  Dépendances : `JOB-009`. Fini lorsque : une valeur inconnue est refusée par SQLite.
- [x] `JOB-011` Ajouter priorité, tentative, `available_at` et horodatages.
  Réalisation : priorité bornée, tentative bornée, disponibilité différable et dates de création, mise à jour, début et fin sont persistées et testées.
  Dépendances : `JOB-010`. Fini lorsque : un travail peut être différé.
- [x] `JOB-012` Ajouter bail, worker, expiration et heartbeat.
  Réalisation : propriétaire, expiration de bail et heartbeat sont persistés ; un état `running` sans propriétaire et expiration est refusé, et une requête identifie les baux expirés.
  Dépendances : `JOB-010`. Fini lorsque : un travail abandonné est détectable.
- [x] `JOB-013` Relier travail, conversation et message utilisateur.
  Réalisation : chaque travail référence par clés étrangères une conversation et son message utilisateur persisté ; les références absentes sont refusées et la suppression du message cascade vers le travail.
  Dépendances : `JOB-010`. Fini lorsque : aucun travail de chat n’est orphelin.
- [x] `JOB-014` Ajouter la contrainte d’idempotence par conversation et `client_request_id`.
  Réalisation : une contrainte unique SQLite sur `(conversation_id, client_request_id)` refuse le second insert et garantit un seul travail par double envoi.
  Dépendances : `JOB-007`, `JOB-013`. Fini lorsque : un double envoi crée un travail.
- [x] `JOB-015` Créer `job_events` sans contenu scientifique.
  Réalisation : `job_events` conserve uniquement travail, état, étape, date et message technique borné à 300 caractères ; aucun champ de payload, réponse ou contenu scientifique n'existe.
  Dépendances : `JOB-010`. Fini lorsque : état, étape, date et message technique sont conservés.
- [x] `JOB-016` Ajouter les index de réclamation FIFO et de liste par conversation.
  Réalisation : les index `idx_jobs_claim` et `idx_jobs_conversation` couvrent respectivement l'ordre priorité/disponibilité/FIFO et les listes de chat ; leurs plans SQLite sont testés.
  Dépendances : `JOB-010` à `JOB-015`. Fini lorsque : les requêtes ciblées utilisent un index.
- [x] `JOB-017` Tester migration d’une base existante.
  Réalisation : un test construit une base en version 11 avec conversation et message, applique la migration 12, puis vérifie leur contenu inchangé et la nouvelle version.
  Dépendances : `JOB-009` à `JOB-016`. Fini lorsque : les conversations restent intactes.
- [x] `JOB-018` Tester création d’une base neuve.
  Réalisation : une base neuve est inspectée pour les deux tables, contraintes de type et JSON, unicité d'idempotence, clés étrangères et index requis.
  Dépendances : `JOB-017`. Fini lorsque : toutes les contraintes existent.

## M3B — dépôt de file et worker local

- [x] `JOB-019` Créer un dépôt de file indépendant de FastAPI.
  Réalisation : `JobRepository` encapsule le fichier SQLite et son initialisation sans importer FastAPI ; un test l'exécute sur un chemin temporaire.
  Dépendances : `JOB-018`. Fini lorsque : il est testable avec un fichier SQLite temporaire.
- [x] `JOB-020` Implémenter `enqueue` atomique avec premier événement.
  Réalisation : `enqueue` insère travail et événement `job.enqueued` dans une transaction immédiate unique ; une défaillance simulée de l'événement annule les deux écritures.
  Dépendances : `JOB-019`. Fini lorsque : travail et événement ne divergent pas.
- [x] `JOB-021` Implémenter le retour idempotent d’un travail existant.
  Réalisation : `enqueue` recherche sous verrou la clé conversation/requête et retourne le travail existant ; un retry conserve le même ID et un seul événement.
  Dépendances : `JOB-020`. Fini lorsque : une reprise HTTP retrouve le même ID.
- [x] `JOB-022` Implémenter la lecture par ID.
  Réalisation : `get` reconstruit le contrat interne complet depuis SQLite et retourne explicitement `None` pour tout UUID absent.
  Dépendances : `JOB-019`. Fini lorsque : un ID absent retourne `None`.
- [x] `JOB-023` Implémenter la liste des travaux actifs d’un chat.
  Réalisation : `list_active` filtre exclusivement `queued`, `running` et `cancel_requested` pour une conversation ; les états terminaux sont couverts par test.
  Dépendances : `JOB-019`. Fini lorsque : seuls les états non terminaux sont rendus.
- [x] `JOB-024` Implémenter la réclamation atomique du prochain travail disponible.
  Réalisation : `claim_next` sélectionne sous `BEGIN IMMEDIATE`, incrémente la tentative, pose propriétaire et bail, puis écrit l'événement ; deux dépôts partagés obtiennent des IDs distincts.
  Dépendances : `JOB-020`. Fini lorsque : deux connexions ne réclament pas le même ID.
- [x] `JOB-025` Implémenter heartbeat et renouvellement de bail.
  Réalisation : `heartbeat` prolonge uniquement un bail actif appartenant au worker appelant ; propriétaire erroné et bail expiré ne modifient rien.
  Dépendances : `JOB-024`. Fini lorsque : seul le propriétaire renouvelle.
- [x] `JOB-026` Implémenter les transitions d’étape atomiques.
  Réalisation : `transition_step` n'autorise qu'une progression avant du worker propriétaire avec bail actif et écrit la nouvelle étape et son événement dans la même transaction, rollback testé.
  Dépendances : `JOB-015`, `JOB-024`. Fini lorsque : chaque étape crée un événement.
- [x] `JOB-027` Implémenter réussite avec référence au message assistant.
  Réalisation : `succeed` vérifie que le résultat est un message assistant de la même conversation avant de finaliser travail, référence et événement atomiquement.
  Dépendances : `JOB-024`. Fini lorsque : succès implique un résultat persisté.
- [x] `JOB-028` Implémenter échec sûr et prochaine tentative.
  Réalisation : `fail_attempt` borne le message à 300 caractères, applique la classification retry/terminal et les délais contractuels, libère le bail et écrit un événement sans erreur brute.
  Dépendances : `JOB-005`, `JOB-024`. Fini lorsque : le message d’erreur est borné.
- [x] `JOB-029` Implémenter annulation immédiate d’un travail en attente.
  Réalisation : `cancel_queued` passe immédiatement à `cancelled` avec date et événement dans une transaction ; le travail ne peut ensuite plus être réclamé.
  Dépendances : `JOB-022`. Fini lorsque : aucun worker ne peut ensuite le réclamer.
- [x] `JOB-030` Implémenter demande d’annulation d’un travail en cours.
  Réalisation : la demande passe `running` à `cancel_requested` sans arracher le bail ; le worker propriétaire l'acquitte atomiquement à la frontière suivante, les autres transitions étant bloquées.
  Dépendances : `JOB-025`. Fini lorsque : elle est honorée à la prochaine frontière sûre.
- [x] `JOB-031` Implémenter récupération des baux expirés au lancement.
  Réalisation : `recover_expired_leases` traite sous transaction tous les baux expirés dans un ordre stable : reprise avec backoff, échec à la limite, ou annulation si demandée.
  Dépendances : `JOB-025`, `JOB-028`. Fini lorsque : reprise ou échec définitif est déterministe.
- [x] `JOB-032` Tester FIFO, priorité et `available_at` futur.
  Réalisation : un test déterministe confirme priorité numérique, FIFO à priorité égale et invisibilité stricte d'un travail avant son `available_at`.
  Dépendances : `JOB-024`. Fini lorsque : l’ordre est exact.
- [x] `JOB-033` Tester deux réclamations concurrentes.
  Réalisation : deux threads synchronisés ouvrent deux connexions vers le même fichier ; exactement l'un réclame l'unique travail et l'autre reçoit une file vide.
  Dépendances : `JOB-024`. Fini lorsque : chaque travail n’est réclamé qu’une fois.
- [x] `JOB-034` Tester crash après appel ARGO mais avant persistance.
  Réalisation : le test simule une réponse ARGO non persistée, l'expiration puis la reprise du bail ; message assistant et succès sont ensuite commis ensemble et une répétition ne crée aucun doublon.
  Dépendances : `JOB-027`, `JOB-031`. Fini lorsque : la reprise ne crée pas deux réponses.
- [x] `WRK-001` Créer l’interface de handler et le contexte de progression.
  Réalisation : `JobHandler`, `JobHandlerResult` et `JobProgressContext` isolent exécution, progression et heartbeat ; un faux handler progresse sans importer ni appeler ARGO.
  Dépendances : `JOB-008`, `JOB-019`. Fini lorsque : un faux handler fonctionne sans ARGO.
- [x] `WRK-002` Créer un registre fermé de handlers.
  Réalisation : `JobHandlerRegistry` normalise l'union `JobType`, refuse tout type inconnu et signale un handler absent avant tout appel métier.
  Dépendances : `WRK-001`. Fini lorsque : un type inconnu échoue avant exécution.
- [x] `WRK-003` Créer `run_once` réclamant au plus un travail.
  Réalisation : `DurableJobWorker.run_once` réclame zéro ou un travail, résout son handler, exécute puis persiste résultat et succès ; une file vide retourne `None` sans effet.
  Dépendances : `JOB-024`, `WRK-002`. Fini lorsque : file vide retourne proprement.
- [x] `WRK-004` Créer la boucle continue avec arrêt propre.
  Réalisation : `run_forever` attend de façon interruptible, s'arrête via événement et ferme en `finally` chaque ressource de handler distincte ; le cycle complet est testé.
  Dépendances : `WRK-003`. Fini lorsque : toutes les ressources sont fermées.
- [x] `WRK-005` Ajouter un identifiant de worker stable par processus.
  Réalisation : un UUID interne est généré une fois au chargement du processus et réutilisé par tous les workers par défaut ; `JobPublic` ne contient aucun `worker_id`.
  Dépendances : `WRK-004`. Fini lorsque : il n’est pas exposé publiquement.
- [x] `WRK-006` Récupérer les baux expirés avant de réclamer un nouveau travail.
  Réalisation : chaque cycle `run_once` récupère d'abord les baux expirés avec la même horloge, puis seulement tente une nouvelle réclamation ; un redémarrage simulé est testé.
  Dépendances : `JOB-031`, `WRK-004`. Fini lorsque : une reprise au lancement est testée.
- [x] `WRK-007` Créer le handler chat en réutilisant `answer_chatbot`.
  Réalisation : `ChatAnswerHandler` adapte le payload durable puis délègue une seule fois à `app.services.workflows.answer_chatbot` et convertit son résultat, sans recopier le RAG.
  Dépendances : `WRK-001`, `KEY-003`. Fini lorsque : aucune logique RAG n’est dupliquée.
- [x] `WRK-008` Relire l’historique depuis SQLite plutôt que depuis le navigateur.
  Réalisation : le handler recharge la conversation ordonnée depuis SQLite, exclut le message utilisateur courant et transmet cet historique autoritatif au workflow.
  Dépendances : `WRK-007`. Fini lorsque : SQLite est l’autorité conversationnelle.
- [x] `WRK-009` Publier l’étape recherche avant le RAG.
  Réalisation : le handler publie atomiquement `search` avant d'appeler le workflow RAG ; le faux workflow vérifie l'étape persistée au moment exact de son invocation.
  Dépendances : `JOB-026`, `WRK-007`. Fini lorsque : l’événement est visible.
- [x] `WRK-010` Publier l’enrichissement uniquement s’il est autorisé sur ce profil.
  Réalisation : l'option externe n'est effective et l'étape `enrichment` n'est publiée que si les deux autorisations locales bibliographiques sont actives ; le profil utilisateur par défaut transmet `False` et n'accède à aucune clé admin.
  Dépendances : `WRK-009`. Fini lorsque : le poste utilisateur n’utilise pas les clés admin.
- [x] `WRK-011` Publier l’étape ARGO juste avant l’appel.
  Réalisation : le client ARGO déclenche un hook après réservation SQLite acceptée et avant l'appel HTTP ; ce hook publie une seule fois l'étape `argo`, ordre testé jusque dans le transport.
  Dépendances : `WRK-009`, `KEY-022`. Fini lorsque : le quota est réservé avant cet événement.
- [x] `WRK-012` Publier l’étape validation après la réponse.
  Réalisation : le workflow déclenche un hook immédiatement après toute réponse ARGO et avant sa validation ; le handler publie `validation`, et un rejet simulé reste attribué à cette étape.
  Dépendances : `WRK-011`. Fini lorsque : un rejet est attribué à cette étape.
- [x] `WRK-013` Persister message assistant et réussite de façon cohérente.
  Réalisation : le worker utilise `persist_result_and_succeed`, transaction unique pour message, conversation, état et événement ; une panne simulée annule tout sans succès ni message orphelin.
  Dépendances : `JOB-027`, `WRK-012`. Fini lorsque : aucun succès sans message n’existe.
- [x] `WRK-014` Mapper timeout ARGO vers retry borné.
  Réalisation : `run_once` transforme `ArgoUnavailableError` en timeout sûr, applique 30 s puis 2 min et termine à la troisième tentative ; les trois cycles sont testés.
  Dépendances : `JOB-028`, `WRK-007`. Fini lorsque : le délai est testé.
- [x] `WRK-015` Mapper authentification vers échec sans retry.
  Réalisation : `ArgoAuthenticationError` devient un échec terminal `authentication` dès la première tentative, avec invitation sûre à remplacer la clé dans les paramètres et sans détail fournisseur.
  Dépendances : `WRK-007`. Fini lorsque : l’utilisateur est invité à remplacer sa clé.
- [x] `KEY-024` Différer un travail au lieu de l’échouer lorsque le quota est atteint.
  Réalisation : `defer_for_quota` remet le travail en file jusqu'au `retry_at` persistant, libère le bail et restitue la tentative puisque aucune génération n'a été envoyée ; reprise exacte testée.
  Dépendances : `KEY-021`. Fini lorsque : l’heure estimée de reprise est persistée.
- [x] `WRK-016` Mapper quota local vers report `available_at`.
  Réalisation : `ArgoLocalQuotaError.retry_at` est transmis à `defer_for_quota` ; le worker rend un travail toujours `queued`, tentative non consommée et heure de reprise exacte.
  Dépendances : `KEY-024`, `WRK-007`. Fini lorsque : le travail reste dans la file.
- [x] `WRK-017` Vérifier annulation avant l’appel ARGO.
  Réalisation : le contexte acquitte l'annulation aux frontières sûres, notamment dans le hook post-réservation/pré-HTTP ; une annulation simulée produit `cancelled` et zéro requête ARGO.
  Dépendances : `JOB-030`, `WRK-011`. Fini lorsque : aucune requête n’est envoyée.
- [x] `WRK-018` Vérifier annulation après l’appel non interruptible.
  Réalisation : le hook post-réponse vérifie l'annulation avant validation ; une réponse ARGO déjà reçue est jetée, l'état final reste `cancelled` et aucun message assistant n'est persisté.
  Dépendances : `JOB-030`, `WRK-012`. Fini lorsque : l’état final est honnête.
- [x] `WRK-019` Ajouter des logs structurés sans contenu.
  Réalisation : chaque issue de travail journalise uniquement `job_id`, `job_step` et `duration_milliseconds` via champs structurés ; des sentinelles de question et réponse restent absentes des logs.
  Dépendances : `WRK-004`. Fini lorsque : seuls IDs, étapes et durées sont présents.
- [x] `WRK-020` Créer la commande worker `--once` et continue.
  Réalisation : `scripts.run_job_worker` construit le handler partagé, accepte `--once` ou la boucle continue interruptible, ferme les ressources et produit un résumé JSON ; les deux modes sont testés.
  Dépendances : `WRK-003`, `WRK-004`. Fini lorsque : les deux modes sont testés.

## M3C — API et interface de chats persistants

- [x] `API-001` Définir la soumission avec message et `client_request_id`.
  Réalisation : `ChatJobSubmitRequest` exige message borné et normalisé, UUID d'idempotence et booléen externe, tout champ inconnu étant refusé.
  Dépendances : `JOB-006` à `JOB-008`. Fini lorsque : champs inconnus refusés.
- [x] `API-002` Définir la réponse `202` avec travail et message utilisateur.
  Réalisation : `ChatJobSubmitResponse` contient uniquement la projection publique du travail et le message utilisateur persisté ; aucun champ de réponse ARGO n'est admis.
  Dépendances : `API-001`. Fini lorsque : aucun résultat ARGO n’est attendu dans cette réponse.
- [x] `API-003` Définir `GET /api/jobs/{id}`.
  Réalisation : `GET /api/jobs/{id}` retourne `JobPublic.to_public()` sans payload ni worker et répond 404 avec un détail sûr pour tout UUID absent.
  Dépendances : `JOB-008`. Fini lorsque : succès et 404 sont spécifiés.
- [x] `API-004` Ajouter les travaux actifs au détail d’une conversation.
  Réalisation : le détail de conversation inclut `active_jobs` reconstruit depuis SQLite avec projections publiques non terminales, ce qui restaure l'attente après rechargement.
  Dépendances : `JOB-023`. Fini lorsque : un rechargement reconstruit l’attente.
- [x] `API-005` Ajouter le nombre de travaux actifs aux résumés de chats.
  Réalisation : `list_chat_conversations` calcule `active_job_count` par sous-requête corrélée dans l'unique lecture de liste ; la barre latérale reçoit tous les compteurs en un appel.
  Dépendances : `API-004`. Fini lorsque : la barre latérale n’effectue pas une requête par chat.
- [x] `API-006` Définir annulation et relance.
  Réalisation : les routes `cancel` et `retry` ferment les transitions à attente/exécution et échec ; tout état incompatible retourne 409, tandis qu'une relance crée un nouvel ID auditable et idempotent.
  Dépendances : `JOB-029`, `JOB-030`. Fini lorsque : transitions invalides retournent 409.
- [x] `API-007` Implémenter l’enqueue atomique du message utilisateur et du travail.
  Réalisation : `enqueue_chat` écrit message utilisateur, travail et premier événement sous une seule transaction immédiate ; la route renvoie 202 et un échec d'événement annule aussi le message.
  Dépendances : `JOB-020`, `API-001`. Fini lorsque : aucun message sans travail ne subsiste.
- [x] `API-008` Retourner le même travail après retry réseau.
  Réalisation : un second POST portant le même UUID de requête retrouve sous verrou le travail et le message canoniques ; IDs et compteurs SQLite restent uniques.
  Dépendances : `JOB-021`, `API-007`. Fini lorsque : l’idempotence est testée.
- [x] `API-009` Implémenter lecture, annulation et relance.
  Réalisation : les routes couvrent lecture, annulation immédiate ou coopérative, relance idempotente et 404 ; un test HTTP traverse tous ces contrats.
  Dépendances : `API-003`, `API-006`. Fini lorsque : tous les contrats sont testés.
- [x] `API-010` Limiter les travaux actifs par conversation.
  Réalisation : trois travaux actifs au plus sont vérifiés sous la transaction d'enqueue, après l'idempotence ; le quatrième reçoit un 409 structuré et aucun message n'est créé.
  Dépendances : `API-007`. Fini lorsque : le dépassement est actionnable.
- [x] `UI-001` Ajouter les types TypeScript de travaux et étapes.
  Réalisation : les unions fermées TypeScript couvrent type, six états, six étapes, quatre erreurs et la projection `DurableJob`, validées par `tsc`.
  Dépendances : `API-001` à `API-006`. Fini lorsque : les unions sont fermées.
- [x] `UI-002` Ajouter les méthodes API enqueue, poll, cancel et retry.
  Réalisation : `api.jobs` expose les quatre méthodes typées avec encodage des IDs et payloads exacts ; Vitest contrôle URLs, verbes et UUID de relance.
  Dépendances : `UI-001`. Fini lorsque : les tests du client contrôlent les payloads.
- [x] `UI-003` Ajouter `active_jobs` aux conversations.
  Réalisation : `ChatConversation` expose les projections actives, les résumés leur compteur, et création, lecture et renommage backend renvoient systématiquement ces champs cohérents.
  Dépendances : `API-004`, `UI-001`. Fini lorsque : le type reflète le backend.
- [x] `UI-004` Générer et conserver le `client_request_id` jusqu’au `202`.
  Réalisation : `PendingChatSubmission` génère un UUID navigateur et `enqueuePendingChat` le réutilise sur retry ; un échec réseau puis succès conserve exactement le même identifiant.
  Dépendances : `API-001`. Fini lorsque : un retry réutilise le même UUID.
- [x] `UI-005` Remplacer l’attente ARGO par l’attente courte de l’enqueue.
  Réalisation : l’envoi du chatbot attend uniquement la création durable du travail et du message, puis rend immédiatement la navigation et le formulaire disponibles pendant l’exécution du worker.
  Dépendances : `UI-002`, `UI-004`. Fini lorsque : la navigation redevient immédiate.
- [x] `UI-006` Afficher le message utilisateur persisté retourné par l’API.
  Réalisation : l’interface ajoute uniquement le message canonique retourné après persistance et déduplique son identifiant lors d’une réponse idempotente, sans message optimiste local.
  Dépendances : `UI-005`. Fini lorsque : aucun doublon optimiste n’apparaît.
- [x] `UI-007` Créer le composant de travail en attente.
  Réalisation : `JobStatusCard` annonce via `role=status` et `aria-live` l’état, l’étape métier libellée et la durée écoulée actualisée chaque seconde.
  Dépendances : `UI-001`. Fini lorsque : état, étape et durée sont accessibles.
- [x] `UI-008` Créer le polling borné d’un travail actif.
  Réalisation : un poller séquentiel par travail, annulable et plafonné à 720 lectures par session, met à jour la projection puis s’arrête dès `succeeded`, `failed` ou `cancelled`.
  Dépendances : `UI-002`. Fini lorsque : il s’arrête en état terminal.
- [x] `UI-009` Ajouter un backoff de polling.
  Réalisation : les lectures suivent un backoff plafonné de 1 s, 1,5 s, 2,5 s, 4 s puis 5 s ; les trois premiers intervalles et l’arrêt terminal sont validés avec l’horloge simulée Vitest.
  Dépendances : `UI-008`. Fini lorsque : les intervalles sont testés avec horloge simulée.
- [x] `UI-010` Recharger le chat après succès.
  Réalisation : dès qu’un poll retourne `succeeded`, l’interface relit la conversation par l’API, remplace ses messages par la projection SQLite et retire la carte de travail seulement après cette relecture.
  Dépendances : `UI-008`. Fini lorsque : le message vient de SQLite.
- [x] `UI-011` Afficher l’échec persistant et l’action relancer.
  Réalisation : un travail échoué reste dans le suivi transversal lorsqu’on change puis revient au chat, affiche son erreur durable et permet de créer une relance avec un nouvel UUID sans effacer l’ancien audit backend.
  Dépendances : `UI-002`, `UI-007`. Fini lorsque : quitter puis revenir conserve l’erreur.
- [x] `UI-012` Afficher annuler tant que l’action est possible.
  Réalisation : la carte permet l’annulation en file et la demande d’annulation pendant l’exécution, puis explique explicitement que le worker s’arrête à la prochaine étape sûre.
  Dépendances : `UI-002`, `UI-007`. Fini lorsque : le libellé reflète la limite d’annulation.
- [x] `UI-013` Autoriser le changement de chat pendant le travail.
  Réalisation : l’état de navigation dépend seulement de l’enqueue court ou du chargement d’une conversation ; la présence d’un travail actif est explicitement ignorée et couverte par Vitest.
  Dépendances : `UI-005`. Fini lorsque : la barre latérale n’est plus désactivée.
- [x] `UI-014` Autoriser la création d’un nouveau chat pendant le travail.
  Réalisation : créer un nouveau chat ne vide pas le registre transversal des travaux ; le filtrage par `conversation_id` masque l’ancien travail sans le détacher, puis le restitue au retour.
  Dépendances : `UI-013`. Fini lorsque : l’ancien travail reste attaché au bon chat.
- [x] `UI-015` Restaurer les travaux au chargement initial.
  Réalisation : le chargement initial transmet les `active_jobs` de la conversation SQLite au registre de suivi, qui relance le polling sans resoumettre la question.
  Dépendances : `UI-003`, `UI-008`. Fini lorsque : F5 reprend le suivi.
- [x] `UI-016` Restaurer les travaux à la sélection d’un chat.
  Réalisation : chaque sélection relit la conversation et ses `active_jobs` depuis l’API avant de les confier au poller, sans réutiliser une projection React potentiellement obsolète.
  Dépendances : `UI-015`. Fini lorsque : aucun état React obsolète n’est requis.
- [x] `UI-017` Afficher un badge de travail sur la conversation.
  Réalisation : chaque conversation affiche un badge accessible du nombre de travaux non terminaux ; la projection suivie remplace le compteur serveur connu et le badge disparaît automatiquement en état terminal.
  Dépendances : `API-005`, `UI-003`. Fini lorsque : il disparaît en état terminal.
- [x] `UI-018` Notifier dans l’application lorsqu’un autre chat termine.
  Réalisation : une notification `aria-live` interne annonce succès, échec ou fin d’un travail d’une autre conversation et permet de l’ouvrir ou de la fermer, sans solliciter l’API de notification système.
  Dépendances : `UI-017`. Fini lorsque : aucune permission système n’est demandée.
- [x] `UI-019` Gérer une perte réseau sans transformer le travail en échec.
  Réalisation : le poller traite les `TypeError` de transport comme transitoires, conserve le dernier état serveur, poursuit le backoff et signale le rétablissement ; le test enchaîne coupure, reprise et succès sans état `failed`.
  Dépendances : `UI-008`. Fini lorsque : le polling reprend automatiquement.
- [x] `UI-020` Empêcher un double envoi.
  Réalisation : un verrou synchrone précède toute création de conversation ou requête réseau et n’est libéré qu’à la fin de l’enqueue ; un double déclenchement immédiat ne peut donc lancer qu’un travail.
  Dépendances : `UI-004`, `UI-005`. Fini lorsque : un double-clic crée un travail.
- [x] `KEY-025` Afficher le quota atteint et l’heure estimée dans le chat.
  Réalisation : une attente quota affiche dans la carte du chat une reprise automatique et son heure locale issue de `retry_at`, sans compteur de requêtes, jetons ou consommation.
  Dépendances : `KEY-024`. Fini lorsque : aucun compteur sensible n’est nécessaire.
- [x] `KEY-027` Tester qu’une clé sentinelle est absente de SQLite, logs et réponses.
  Réalisation : un test d’intégration enregistre une sentinelle chiffrée, sonde `/models`, puis vérifie son absence binaire dans SQLite et son absence textuelle dans les logs et toutes les réponses HTTP.
  Dépendances : `KEY-003` à `KEY-025`. Fini lorsque : la recherche ne trouve aucune occurrence.
- [ ] `KEY-030` Effectuer un test réel manuel avec une clé nouvellement saisie.
  Ordonnancement : reporté au dernier contrôle manuel car le coffre local est actuellement non configuré ; aucune autre tâche ne dépend de ce probe pour être développée.
  Dépendances : `KEY-014` à `KEY-029`. Fini lorsque : `/models` confirme le modèle sans génération.
- [x] `UI-021` Afficher l’étape quota et l’heure estimée.
  Réalisation : une projection avec erreur `quota` remplace l’étape technique par « Attente du quota ARGO » et affiche l’heure locale de `retry_at`, vérifiées ensemble par Vitest.
  Dépendances : `KEY-025`, `UI-007`. Fini lorsque : l’attente est compréhensible.
- [x] `UI-022` Ajouter un test de navigation pendant génération simulée.
  Réalisation : Vitest simule un poll passant de `running` à `succeeded` pendant que la sélection change ; le résultat conserve le `conversation_id` et le message de la conversation initiale, qui déclenche une notification externe.
  Dépendances : `UI-013` à `UI-017`. Fini lorsque : la réponse arrive dans le chat initial.
- [x] `UI-023` Ajouter un test de rechargement pendant génération simulée.
  Réalisation : le chargement d’une conversation simulée contenant un travail `running` transmet uniquement `active_jobs` au tracker ; le test vérifie qu’aucun appel d’enqueue ou resoumission n’a lieu.
  Dépendances : `UI-015`. Fini lorsque : aucune resoumission n’a lieu.
- [x] `UI-024` Ajouter un test échec puis relance.
  Réalisation : le test HTTP provoque un échec, crée une relance distincte, puis relit les deux identifiants ; l’original reste `failed` et la nouvelle tentative reste `queued`, donc les deux audits sont conservés.
  Dépendances : `UI-011`. Fini lorsque : les deux travaux restent auditables.
- [x] `UI-025` Ajouter un test quota puis reprise.
  Réalisation : le worker simulé rencontre d’abord le quota, conserve le travail `queued` sans tentative consommée, puis le reprend exactement à `retry_at` et le termine avec un message persistant.
  Dépendances : `UI-021`. Fini lorsque : le travail aboutit après disponibilité.
- [x] `UI-026` Tester clavier et `aria-live` pour attente, succès et échec.
  Réalisation : les trois états sont rendus et vérifiés avec `role=status`/`aria-live=polite` ; annuler et relancer restent des boutons natifs au clavier, sans `autofocus` ni retrait du parcours de focus.
  Dépendances : `UI-007`, `UI-011`. Fini lorsque : le focus reste stable.
- [x] `UI-027` Retirer l’ancien état `busy` global.
  Réalisation : l’ancien `busy` a disparu du chatbot ; seul `enqueueing` couvre le bref aller-retour de persistance et aucun état de génération ne participe au verrouillage de navigation.
  Dépendances : `UI-005` à `UI-026`. Fini lorsque : aucun changement de chat n’est bloqué.
- [x] `UI-028` Ajouter la fermeture propre du navigateur sans annulation serveur.
  Réalisation : le cleanup du hook avorte exclusivement les `AbortController` des polls et vide leur registre ; le test confirme que les signaux client sont arrêtés tandis que le travail serveur reste `running`.
  Dépendances : `UI-008`. Fini lorsque : un unmount arrête seulement le polling.
- [x] `UI-029` Reprendre les travaux après redémarrage de l’application.
  Réalisation : un test redémarre un worker après expiration de bail, récupère puis termine le même travail ; SQLite conserve un seul job, une seule question et ajoute uniquement la réponse.
  Dépendances : `JOB-031`, `UI-015`. Fini lorsque : un travail abandonné repart au lancement.
- [x] `UI-030` Valider le parcours complet frontend avec services simulés.
  Réalisation : un test intégré simule enqueue, F5 avec `active_jobs`, navigation vers un autre chat, reprise du poll et relecture de la réponse dans le chat initial ; les 41 tests, le lint, TypeScript et le build Vite passent en CI.
  Dépendances : `UI-001` à `UI-029`. Fini lorsque : navigation et reprise passent en CI.
- [x] `API-011` Retirer le chemin synchrone après migration du frontend.
  Réalisation : `POST /api/chatbot`, son schéma, la méthode `api.chatbot.ask` et les helpers d’historique associés sont supprimés ; seul enqueue/poll subsiste et le backend complet passe 310 tests.
  Dépendances : `UI-030`. Fini lorsque : la requête web ne porte plus la durée ARGO.

## M4 — corpus commun et espace documentaire privé

- [x] `COR-001` Définir `common` et `private` comme portées fermées d’une source.
  Réalisation : l’enum fermé `CorpusScope` refuse toute troisième valeur et les projections lexicales, vectorielles, hybrides et articles portent systématiquement une portée sérialisée.
  Dépendances : aucune. Fini lorsque : chaque résultat porte une origine.
- [x] `COR-002` Définir les chemins séparés du corpus commun et du corpus privé.
  Réalisation : `PathConfig` expose deux arbres frères sous `data`, chacun avec PDF, extraction, SQLite et Qdrant ; leur création et leur non-chevauchement sont testés.
  Dépendances : `COR-001`. Fini lorsque : ils restent sous `data` sans se chevaucher.
- [x] `COR-003` Interdire l’écriture dans le corpus commun sur un profil utilisateur.
  Réalisation : upload, import dossier, indexation, réindexation et suppression du corpus commun vérifient tous le profil local avant mutation et retournent 403 sur le profil utilisateur par défaut.
  Dépendances : `COR-002`. Fini lorsque : import, suppression et réindexation sont refusés.
- [x] `COR-004` Autoriser ces mutations sur le profil administrateur.
  Réalisation : `CIDERSCHOLAR_LOCAL_PROFILE=admin`, lu uniquement depuis l’environnement machine, autorise le garde commun ; le profil est absent du modèle YAML distribué et une route d’import vide passe en 200.
  Dépendances : `COR-003`. Fini lorsque : le profil vient d’une configuration locale non distribuée.
- [x] `COR-005` Choisir l’isolation SQLite entre commun et privé.
  Réalisation : deux fichiers SQLite indépendants, jamais attachés ensemble, sont sélectionnés par `corpus_paths`; chacun est initialisé et ouvert séparément dans le test, et la décision est documentée.
  Dépendances : `COR-002`. Fini lorsque : une décision technique testable est documentée.
- [x] `COR-006` Choisir l’isolation Qdrant entre commun et privé.
  Réalisation : `corpus_paths` fournit deux répertoires Qdrant distincts et les index locaux utilisent ces chemins physiques séparés, tout en pouvant conserver le même nom de collection ; décision et contrainte d’hydratation sont documentées.
  Dépendances : `COR-002`. Fini lorsque : collections ou stockages ne sont pas confondus.
- [x] `COR-007` Créer une abstraction de lecture multi-corpus.
  Réalisation : `MultiCorpusReader` reçoit des factories par portée, ouvre puis ferme chaque lecteur dans l’ordre commun/privé et garantit par test qu’un seul corpus est ouvert à la fois, sans import FastAPI.
  Dépendances : `COR-005`, `COR-006`. Fini lorsque : elle n’importe pas FastAPI.
- [x] `COR-008` Adapter la recherche lexicale au commun puis au privé.
  Réalisation : `MultiCorpusLexicalSearchService` interroge commun puis privé via des SQLite distincts, ferme chaque lecteur avant le suivant et remplace explicitement la portée de chaque hit ; ordre et origines sont testés.
  Dépendances : `COR-007`. Fini lorsque : chaque hit conserve son origine.
- [x] `COR-009` Adapter la recherche vectorielle au commun puis au privé.
  Réalisation : `MultiCorpusVectorSearchService` ouvre backend et Qdrant commun, les ferme, puis fait de même pour le privé ; chaque hit reçoit sa portée et un test interdit deux index ouverts simultanément.
  Dépendances : `COR-007`. Fini lorsque : les index sont ouverts et fermés séquentiellement.
- [x] `COR-010` Fusionner les deux listes avec une règle déterministe.
  Réalisation : les listes hybrides sont triées par score, priorité commune puis rang local et identifiants ; chaque résultat conserve `scope`, `corpus_rank`, score et contributions avant attribution du rang global.
  Dépendances : `COR-008`, `COR-009`. Fini lorsque : scores et provenance restent explicables.
- [x] `COR-011` Dédupliquer par DOI entre commun et privé.
  Réalisation : les articles sont regroupés par DOI normalisé et, à DOI égal, la notice commune est conservée même si le score privé est supérieur ; le test couvre URL DOI, casse et priorité.
  Dépendances : `COR-010`. Fini lorsque : le commun est prioritaire à DOI identique.
- [x] `COR-012` Définir le repli sans DOI entre commun et privé.
  Réalisation : sans DOI, la déduplication conserve chaque couple `(scope, article_id)` et ne rapproche jamais titre, auteurs ou année seuls ; deux titres identiques commun/privé restent distincts dans le test et la règle est documentée.
  Dépendances : `COR-010`. Fini lorsque : aucun document distinct n’est fusionné sans preuve.
- [x] `COR-013` Afficher `Corpus commun` ou `Document privé` sur chaque source.
  Réalisation : chaque source locale transporte désormais sa portée jusqu'au contrat API et le chatbot affiche explicitement `Corpus commun` ou `Document privé`, tandis que les sources externes restent identifiées comme API en direct.
  Dépendances : `COR-010`. Fini lorsque : l’origine est visible dans le chatbot.
- [x] `COR-014` Conserver l’origine dans les citations et exports.
  Réalisation : la portée fait partie de chaque entrée bibliographique, préfixe les citations et références applicatives et est exportée en JSON ainsi qu'en champs BibTeX lisibles ; un test privé interdit toute mention de corpus commun.
  Dépendances : `COR-013`. Fini lorsque : une source privée n’est jamais présentée comme commune.
- [x] `COR-015` Empêcher une suggestion implicite à partir d’un document privé.
  Réalisation : une politique centrale distingue automatisme et action utilisateur explicite ; toute tentative automatique visant une source privée lève une erreur de confidentialité testée, alors qu'une confirmation explicite reste autorisée.
  Dépendances : `COR-002`. Fini lorsque : seule une action explicite peut proposer le document.
- [x] `COR-016` Ajouter l’import privé depuis l’interface.
  Réalisation : l'interface propose un espace `Documents privés` avec dépôt de PDF et analyse de dossier ; l'API dédiée applique les workflows existants à des paramètres dont PDF, cache d'extraction, SQLite et Qdrant pointent exclusivement vers `data/private`.
  Dépendances : `COR-003`, `COR-007`. Fini lorsque : le document n’atteint pas le stockage commun.
- [x] `COR-017` Ajouter suppression et réindexation privées.
  Réalisation : les actions de réindexation, indexation globale et suppression de la vue privée appellent des routes dédiées qui ne reçoivent que les chemins SQLite et Qdrant privés ; un test interdit le passage d'un stockage commun.
  Dépendances : `COR-016`. Fini lorsque : aucune donnée commune n’est touchée.
- [x] `COR-018` Ajouter une vue séparée des documents privés.
  Réalisation : la Base documentaire expose un onglet autonome `Documents privés`, avec titre et texte rappelant le stockage local non partagé ; son routage et son client API restent distincts du `Corpus commun`.
  Dépendances : `COR-016`. Fini lorsque : l’utilisateur comprend qu’ils ne sont pas partagés.
- [x] `COR-019` Ajouter filtres de recherche commun, privé ou les deux.
  Réalisation : la recherche PDF propose `Commun + privé` par défaut ainsi que chaque portée seule ; le backend valide cette liste, ouvre les moteurs lourds séquentiellement, déduplique les DOI et renvoie `scope` jusqu'aux badges de résultat.
  Dépendances : `COR-010`. Fini lorsque : le défaut interroge les deux sans masquer l’origine.
- [x] `COR-020` Migrer les données actuelles vers le corpus commun administrateur.
  Réalisation : une commande administrateur copie les articles, PDF, fragments avec leurs identifiants, états d'ingestion, caches et index vers `common`, sans conversations ni privé ; elle vérifie après transaction les comptes et DOI tout en laissant la source intacte.
  Dépendances : `COR-005`, `COR-006`. Fini lorsque : nombre d’articles et DOI sont conservés.
- [x] `COR-021` Tester qu’une mise à jour commune conserve le privé.
  Réalisation : l'activation atomique d'un répertoire commun préparé archive la version précédente et refuse tout chevauchement avec `private` ; le test compare l'intégralité des hashes privés avant et après permutation.
  Dépendances : `COR-016`. Fini lorsque : une permutation simulée du chemin commun laisse
  les hashes privés identiques.
- [x] `COR-022` Tester une recherche contenant une source commune et une privée.
  Réalisation : le test de recherche multi-portée obtient une source de chaque espace dans l'ordre séquentiel et vérifie leurs citations calculées `[Corpus commun · …]` et `[Document privé · …]`, également visibles dans les cartes UI.
  Dépendances : `COR-010` à `COR-014`. Fini lorsque : les deux sont citées correctement.
- [x] `COR-023` Tester un DOI dupliqué dans les deux espaces.
  Réalisation : le test normalise casse et URL DOI, simule un doublon privé mieux scoré et vérifie qu'un seul article commun est rendu avec sa portée explicite, conformément à la règle de priorité.
  Dépendances : `COR-011`. Fini lorsque : un résultat unique et explicable est rendu.
- [x] `COR-024` Adapter les statistiques du tableau de bord par portée.
  Réalisation : l'overview calcule séparément articles, fragments, indexation et incidents depuis les SQLite commun et privé ; le tableau de bord affiche deux cartes autonomes et aucun total ambigu entre les portées.
  Dépendances : `COR-007`. Fini lorsque : commun et privé ne sont pas additionnés sans détail.
- [x] `COR-025` Adapter sauvegarde et restauration du privé.
  Réalisation : les commandes de sauvegarde/restauration créent un ZIP privé manifesté et hashé, utilisent un snapshot SQLite cohérent, refusent traversées et liens, restaurent par permutation avec rollback et vérifient que les hashes communs restent inchangés.
  Dépendances : `COR-002`. Fini lorsque : l’utilisateur peut restaurer sans corpus commun.
- [x] `COR-026` Ajouter un profil mémoire pour postes 8 Go.
  Réalisation : le profil explicite `8gb` abaisse les seuils à 6/5 Go avec 1 Go libre minimum, limite les embeddings à 2, les candidats hybrides à 80 et les fragments de preuve à 50 sans modifier silencieusement les réglages actifs.
  Dépendances : `COR-009`. Fini lorsque : seuils mémoire ne supposent plus 16 Go.
- [x] `COR-027` Ajouter un profil mémoire pour postes 16 Go.
  Réalisation : le profil `16gb` conserve 1 Go de réserve, borne le processus à 12,5 Go et augmente le lot d'embeddings de 2 à 12 ainsi que les fenêtres de candidats, avec comparaison automatisée au profil 8 Go.
  Dépendances : `COR-026`. Fini lorsque : le lot d’embeddings peut être augmenté sans dépasser les seuils.
- [x] `COR-028` Détecter la mémoire disponible au premier lancement.
  Réalisation : la RAM physique détectée produit une recommandation `8gb` ou `16gb` exposée dans les paramètres avec le profil actif ; le contrat indique explicitement `applied_automatically: false` et les tests garantissent l'absence de mutation silencieuse.
  Dépendances : `COR-026`, `COR-027`. Fini lorsque : un profil est recommandé, pas imposé silencieusement.
- [x] `COR-029` Tester recherche commune et privée sur profil 8 Go simulé.
  Réalisation : le test applique réellement le profil 8 Go, vérifie un lot d'embeddings de 2 sur les deux portées et interdit l'ouverture simultanée des lecteurs commun et privé pendant la recherche fusionnée.
  Dépendances : `COR-026`. Fini lorsque : les composants lourds restent séquentiels.
- [x] `COR-030` Documenter clairement ce qui est partagé et ce qui reste privé.
  Réalisation : le guide d'isolation contient un tableau utilisateur comparant lecture, modification, chemins, mises à jour, sauvegarde, suggestions et étiquettes ; le README renvoie vers ce guide et les commandes privées vérifiées.
  Dépendances : `COR-013` à `COR-025`. Fini lorsque : le guide utilisateur contient un tableau simple.

## M5 — paquet RAG commun et mise à jour SharePoint

- [x] `PKG-001` Définir le format du manifeste de corpus.
  Réalisation : `CorpusManifest` est un contrat strict versionné contenant horodatage UTC, schéma SQLite, version minimale de l'application, comptes, liste d'artefacts typés et hashes SHA-256 de chaque fichier et de l'archive.
  Dépendances : `COR-005`, `COR-006`. Fini lorsque : version, date, schéma, app minimale et hashes existent.
- [x] `PKG-002` Définir un identifiant de version immuable.
  Réalisation : l'identifiant `corpus-v1-<sha256>` est dérivé d'un JSON canonique des comptes, versions de schéma/application et artefacts triés ; l'ordre et la date n'influent pas, mais un seul hash de contenu différent change l'ID.
  Dépendances : `PKG-001`. Fini lorsque : deux contenus différents ne partagent pas un ID.
- [x] `PKG-003` Définir les fichiers inclus et exclus.
  Réalisation : le paquet suit une allowlist `common/database`, `common/pdf`, `common/qdrant` et refuse liens symboliques ; WAL, SHM, verrous, temporaires, caches, secrets, configuration et toute arborescence privée sont exclus et couverts par test.
  Dépendances : `PKG-001`. Fini lorsque : secrets, conversations, privé et caches sont exclus.
- [x] `PKG-004` Fermer SQLite et Qdrant avant création du paquet.
  Réalisation : chaque client Qdrant détient désormais un verrou interprocessus pendant toute sa vie et le constructeur de paquet exige ce verrou libre puis une transaction SQLite exclusive ; une ressource encore ouverte bloque l'entrée avant toute copie.
  Dépendances : `PKG-003`. Fini lorsque : aucune copie à chaud incohérente n’est possible.
- [x] `PKG-005` Effectuer un checkpoint WAL avant copie SQLite.
  Réalisation : le garde exclusif exécute `wal_checkpoint(TRUNCATE)`, refuse un checkpoint occupé et ne copie le fichier principal qu'après succès ; la copie passe `integrity_check`, contient les données et ne dépend d'aucun WAL adjacent.
  Dépendances : `PKG-004`. Fini lorsque : la base copiée s’ouvre sans WAL source.
- [x] `PKG-006` Valider le nombre d’articles, chunks et vecteurs avant publication.
  Réalisation : sous garde exclusive, le validateur compare articles, chunks, chunks indexés et comptage exact Qdrant ; tout fragment non indexé ou écart vectoriel lève une erreur chiffrée avant création d'archive.
  Dépendances : `PKG-004`. Fini lorsque : une incohérence bloque le paquet.
- [x] `PKG-007` Créer l’archive dans un répertoire temporaire.
  Réalisation : snapshot, payload et ZIP sont construits sous `.build-*` via `TemporaryDirectory`; le test vérifie qu'aucun temporaire ne subsiste après publication et qu'aucune version n'est exposée pendant la construction.
  Dépendances : `PKG-005`, `PKG-006`. Fini lorsque : une interruption ne laisse pas une version publiée.
- [x] `PKG-008` Calculer SHA-256 de chaque artefact et de l’archive.
  Réalisation : chaque fichier snapshot reçoit taille, type et SHA-256 dans l'ordre canonique ; le ZIP terminé est ensuite hashé et sa taille enregistrée, avec comparaison directe au fichier publié dans le test.
  Dépendances : `PKG-007`. Fini lorsque : le manifeste permet une vérification complète.
- [x] `PKG-009` Écrire atomiquement l’archive finale et son manifeste.
  Réalisation : archive et `manifest.json` sont fermés dans un répertoire de staging, puis le répertoire complet prend atomiquement le nom immuable de version ; une version existante n'est jamais écrasée et doit se revérifier.
  Dépendances : `PKG-008`. Fini lorsque : manifeste et archive ne peuvent être dépareillés.
- [x] `PKG-010` Créer la commande administrateur de construction de paquet.
  Réalisation : `scripts/build_corpus_package.py` exige le profil administrateur, accepte un dossier de sortie et imprime un unique document JSON strict conforme à `CorpusPackageBuildReport`, sans texte parasite.
  Dépendances : `PKG-001` à `PKG-009`. Fini lorsque : elle produit une sortie JSON stricte.
- [x] `PKG-011` Tester la construction deux fois à corpus identique.
  Réalisation : le même corpus est construit dans deux destinations et à deux dates différentes ; identifiant, liste/hashes d'artefacts et hash du ZIP restent identiques, seul l'horodatage éditorial varie.
  Dépendances : `PKG-010`. Fini lorsque : le contenu logique et les hashes sont déterministes.
- [x] `PKG-012` Définir les dossiers SharePoint `installers`, `corpus`, `suggestions/inbox`, `archive`.
  Réalisation : un layout typé crée uniquement ces quatre emplacements (avec le parent `suggestions`) et la documentation interdit clés, identifiants, conversations, privé, configuration et caches dans l'arborescence synchronisée.
  Dépendances : `ARC-007`. Fini lorsque : aucun secret n’est placé dans ces dossiers.
- [x] `PKG-013` Utiliser un chemin local synchronisé OneDrive/SharePoint dans la configuration.
  Réalisation : la section `distribution` ne contient qu'un chemin local synchronisé résolu depuis le fichier de configuration, un nom attendu et une cadence ; aucun champ Graph, jeton ou identifiant Microsoft n'existe.
  Dépendances : `PKG-012`. Fini lorsque : aucune API Graph n’est requise.
- [x] `PKG-014` Valider que le chemin configuré appartient au dossier attendu.
  Réalisation : la distribution exige un dossier existant portant le nom configuré ; un autre nom demande une confirmation explicite, tandis qu'un chemin sous les données locales (donc notamment privé) reste toujours refusé.
  Dépendances : `PKG-013`. Fini lorsque : un chemin arbitraire est refusé ou confirmé explicitement.
- [x] `PKG-015` Publier d’abord la version puis le pointeur `latest` en dernier.
  Réalisation : la publication copie et revérifie la version dans un staging court, renomme son dossier immuable, puis seulement écrit et remplace `latest.json` ; le test observe l'absence du pointeur à l'événement `version_ready`.
  Dépendances : `PKG-009`, `PKG-013`. Fini lorsque : un poste ne voit jamais une version partielle.
- [x] `PKG-016` Archiver le paquet publié sur le drive protégé administrateur.
  Réalisation : la version publiée est recopiée par staging sur un chemin administrateur distinct des données locales et synchronisées, puis manifeste et archive sont revérifiés ; le test compare exactement le SHA-256 SharePoint et archive protégée.
  Dépendances : `PKG-015`. Fini lorsque : SharePoint et sauvegarde ont le même hash.
- [x] `PKG-017` Créer la lecture locale du manifeste `latest`.
  Réalisation : le lecteur local distingue distribution désactivée, dossier non synchronisé, pointeur absent, métadonnées invalides et version disponible ; il vérifie hash du manifeste et cohérence de version sans aucun accès réseau.
  Dépendances : `PKG-013`, `PKG-015`. Fini lorsque : absence de synchronisation est distinguée.
- [x] `PKG-018` Comparer version installée et version disponible.
  Réalisation : un état installé atomique est comparé au manifeste synchronisé ; versions identiques retournent explicitement `update_available: false` et `download_required: false`, tandis qu'une synchronisation indisponible n'entraîne aucune copie.
  Dépendances : `PKG-017`. Fini lorsque : aucun téléchargement si versions identiques.
- [x] `PKG-019` Vérifier compatibilité avec la version de l’application.
  Réalisation : la version sémantique courante est comparée au minimum du manifeste ; une application trop ancienne obtient un blocage typé qui cite clairement versions minimale et installée, avant toute copie.
  Dépendances : `PKG-001`, `PKG-018`. Fini lorsque : une application trop ancienne explique le blocage.
- [x] `PKG-020` Copier une nouvelle archive dans une zone de staging locale.
  Réalisation : une version différente et compatible est copiée via fichiers `.part` dans `data/cache/corpus-updates/<version>` avec son manifeste exact ; le test compare les hashes du corpus commun actif avant et après staging.
  Dépendances : `PKG-018`. Fini lorsque : le corpus actif reste inchangé pendant la copie.
- [x] `PKG-021` Vérifier tous les hashes avant extraction.
  Réalisation : le staging revérifie le manifeste exact, taille/hash du ZIP, unicité et exhaustivité des entrées, puis taille/hash de chaque artefact ; à la première corruption, seul le staging borné sous le cache est supprimé.
  Dépendances : `PKG-020`. Fini lorsque : toute corruption supprime le staging.
- [x] `PKG-022` Extraire avec protection contre les chemins sortants.
  Réalisation : l'extracteur refuse chemins POSIX/Windows absolus, lecteurs, `..`, antislashs et liens symboliques, résout chaque destination sous staging puis copie les flux lui-même ; les cas d'évasion sont paramétrés par test.
  Dépendances : `PKG-021`. Fini lorsque : `..` et chemins absolus sont rejetés.
- [x] `PKG-023` Valider SQLite et Qdrant du staging.
  Réalisation : le staging exécute `integrity_check`, contrôle version de schéma et comptes SQLite, ouvre Qdrant, compare son compte exact, effectue un `scroll` et vérifie l'identifiant de point dans SQLite ; une fausse base est bloquée par test.
  Dépendances : `PKG-022`. Fini lorsque : la version est recherchable avant activation.
- [x] `PKG-024` Marquer la version prête pour le prochain redémarrage.
  Réalisation : seule une instance `ValidatedCorpusPackage` peut produire `ready.json`, qui fixe version, chemin extrait, manifeste/hash et date ; le corpus actif reste lisible et inchangé jusqu'au prochain lancement.
  Dépendances : `PKG-023`. Fini lorsque : aucune activation à chaud n’est tentée.
- [x] `PKG-025` Activer atomiquement la version au lancement.
  Réalisation : le lifespan traite `ready.json` avant l'initialisation des bases, revérifie manifeste/version, nettoie les verrous de staging, permute atomiquement `common`, écrit l'état installé puis retire le marqueur ; ancien et nouveau chemins figurent dans le rapport testé.
  Dépendances : `PKG-024`. Fini lorsque : ancien et nouveau chemins sont résolus explicitement.
- [x] `PKG-026` Conserver une version précédente pour rollback.
  Réalisation : chaque activation déplace l'ancien `common` vers `data/common-archive` sans le supprimer ; une routine de rollback au lancement refuse les chemins hors archive, permute la version retenue et conserve à son tour la version remplacée.
  Dépendances : `PKG-025`. Fini lorsque : une activation défectueuse peut être annulée.
- [x] `PKG-027` Tester interruption pendant copie, extraction et activation.
  Réalisation : des pannes injectées pendant copie, extraction et renommage d'activation suppriment les partiels ou restaurent l'ancien chemin ; chaque test relit le marqueur du corpus actif et l'activation interrompue conserve `ready.json`.
  Dépendances : `PKG-020` à `PKG-026`. Fini lorsque : un corpus valide reste toujours disponible.
- [x] `PKG-028` Tester qu’une mise à jour ne touche pas le corpus privé.
  Réalisation : le test écrit des PDF privés, mémorise tous leurs hashes, effectue activation puis rollback du commun et exige une égalité exacte des deux snapshots privés.
  Dépendances : `COR-021`, `PKG-025`. Fini lorsque : les hashes privés restent identiques.
- [x] `PKG-029` Afficher version installée, disponible et date de publication.
  Réalisation : le payload runtime expose état de synchronisation, versions installée/disponible, besoin de mise à jour et date ; Paramètres affiche ces valeurs séparément avec un statut `Disponible` ou `À jour`.
  Dépendances : `PKG-018`. Fini lorsque : l’information est visible dans Paramètres.
- [x] `PKG-030` Ajouter actions télécharger, installer au redémarrage et revenir à la version précédente.
  Dépendances : `PKG-024` à `PKG-029`. Fini lorsque : chaque action demande une confirmation adaptée.
  Réalisation : trois routes confirmées et trois commandes UI distinctes séparent téléchargement vérifié,
  installation différée et rollback différé ; les marqueurs ne sont consommés qu'au redémarrage.
- [x] `PKG-031` Vérifier les mises à jour au lancement puis au maximum une fois par jour.
  Dépendances : `PKG-017`. Fini lorsque : SharePoint n’est pas interrogé en boucle.
  Réalisation : le lancement persiste un snapshot horodaté sous le cache local ; les lectures suivantes
  le réutilisent pendant l'intervalle configurable, borné à 24 heures minimum.
- [x] `PKG-032` Ne jamais bloquer le chatbot si SharePoint est indisponible.
  Dépendances : `PKG-031`. Fini lorsque : la version locale continue de fonctionner.
  Réalisation : toute erreur de lecture ou de cache devient un état `sync_unavailable` non bloquant ;
  un test d'intégration maintient health, conversations locales et Paramètres accessibles.
- [x] `PKG-033` Documenter comment synchroniser le dossier SharePoint avec OneDrive.
  Dépendances : `PKG-013`. Fini lorsque : un utilisateur non technique peut choisir le dossier.
  Réalisation : le guide décrit clic par clic la synchronisation OneDrive, la copie du chemin depuis
  l'Explorateur, le bloc YAML à saisir, la vérification dans Paramètres et les erreurs de sélection.
- [x] `PKG-034` Documenter publication et rollback administrateur.
  Dépendances : `PKG-010`, `PKG-015`, `PKG-026`. Fini lorsque : la procédure est reproductible.
  Réalisation : une CLI administrateur vérifie, publie puis archive un paquet ; le guide détaille la
  construction, les contrôles de hashes, l'ordre atomique et le rollback par republication immuable.
- [ ] `PKG-035` Réaliser un test manuel de publication puis installation sur un second profil Windows.
  Dépendances : `PKG-001` à `PKG-034`. Fini lorsque : les versions et hashes correspondent.
  Ordonnancement : différé jusqu'à disponibilité d'un second profil Windows et d'une bibliothèque
  SharePoint réellement synchronisée ; les scénarios locaux automatisés restent validés.

## M6 — suggestions documentaires et évaluation immédiate

- [x] `SUG-001` Définir le schéma d’une suggestion avec UUID et version.
  Dépendances : `PKG-012`. Fini lorsque : champs inconnus refusés.
  Réalisation : `SuggestionDraft` porte UUID, version littérale et date dans un modèle Pydantic
  `extra=forbid` ; le test refuse tout champ distant inconnu.
- [x] `SUG-002` Ajouter les variantes DOI, URL, PDF et référence manuelle.
  Dépendances : `SUG-001`. Fini lorsque : chaque variante a ses champs requis.
  Réalisation : une union discriminée stricte définit quatre sources aux champs requis propres ; DOI
  et URL sont normalisés par leurs validateurs, le PDF ne conserve qu'un nom interne.
- [x] `SUG-003` Ajouter un commentaire scientifique facultatif borné.
  Dépendances : `SUG-001`. Fini lorsque : il ne devient jamais une instruction système.
  Réalisation : le commentaire est nettoyé et borné à 1 500 caractères, puis placé uniquement dans
  l'enveloppe JSON explicitement déclarée non fiable du message utilisateur ARGO.
- [x] `SUG-004` Normaliser et valider le DOI avant toute autre opération.
  Dépendances : `SUG-002`. Fini lorsque : DOI invalide est refusé localement.
  Réalisation : préfixes DOI connus, casse et ponctuation finale sont normalisés, puis une expression
  complète refuse localement toute valeur invalide avant le workflow.
- [x] `SUG-005` Valider URL HTTPS sans identifiants ni hôte local.
  Dépendances : `SUG-002`. Fini lorsque : SSRF et schémas dangereux sont refusés.
  Réalisation : le parseur impose HTTPS/443, interdit identifiants, localhost, `.local` et adresses IP
  non publiques ; les tests couvrent loopback et métadonnées link-local sans appel réseau.
- [x] `SUG-006` Ne pas télécharger automatiquement une URL sur le poste utilisateur.
  Dépendances : `SUG-005`. Fini lorsque : l’URL reste une référence pour le traitement administrateur.
  Réalisation : le workflow URL ne possède aucun client HTTP et transforme seulement titre, abstract
  facultatif et référence HTTPS validée en contexte local.
- [x] `SUG-007` Valider extension, signature PDF, taille et hash.
  Dépendances : `SUG-002`. Fini lorsque : un faux PDF est refusé.
  Réalisation : extension `.pdf`, en-tête `%PDF-`, taille configurable et SHA-256 sont contrôlés avant
  extraction ; faux PDF, dépassement et hash modifié sont refusés.
- [x] `SUG-008` Nettoyer le nom de fichier et générer un nom interne sûr.
  Dépendances : `SUG-007`. Fini lorsque : aucun chemin utilisateur n’est conservé.
  Réalisation : le nom original n'entre dans aucun modèle ni paquet ; un nom interne UUID conforme à
  une expression fermée est généré avant toute écriture.
- [x] `SUG-009` Extraire localement titre, DOI et abstract candidat si disponibles.
  Dépendances : `SUG-004`, `SUG-007`. Fini lorsque : le PDF complet n’est pas envoyé à ARGO.
  Réalisation : PyMuPDF et l'extracteur conservateur existant produisent localement titre, DOI,
  abstract candidat et extrait textuel ; aucune donnée binaire n'est retournée.
- [x] `SUG-010` Définir le contexte maximal envoyé à ARGO pour pertinence.
  Dépendances : `SUG-009`. Fini lorsque : titre, métadonnées et texte borné suffisent.
  Réalisation : le contexte ARGO est limité à 8 000 caractères configurables, avec titre 500 et
  abstract 4 000 caractères, et le paquet transmis supprime même l'extrait de travail.
- [x] `SUG-011` Neutraliser les instructions présentes dans document ou commentaire.
  Dépendances : `SUG-003`, `SUG-010`. Fini lorsque : le prompt les traite comme données non fiables.
  Réalisation : système et données sont séparés ; commentaire et texte sont délimités comme
  `DONNÉES_NON_FIABLES` et le test d'injection exige qu'ils n'apparaissent jamais dans le système.
- [x] `SUG-012` Définir un résultat ARGO structuré : pertinent, motif, thème et incertitude.
  Dépendances : `FMT-014` à `FMT-020`. Fini lorsque : la sortie est Pydantic stricte.
  Réalisation : `SuggestionArgoDecision` refuse les champs inconnus et borne pertinence, motif, thème,
  incertitude et confiance ; ARGO reçoit son JSON Schema strict.
- [x] `SUG-013` Interdire à ARGO d’inventer DOI ou métadonnées.
  Dépendances : `SUG-012`. Fini lorsque : toute valeur vient de l’entrée validée.
  Réalisation : la décision ARGO ne contient aucun DOI ni métadonnée ; un test refuse explicitement
  un champ DOI inventé dans la sortie, les valeurs publiées viennent du contexte local.
- [x] `SUG-014` Ajouter un seuil d’acceptation configurable et conservateur.
  Dépendances : `SUG-012`. Fini lorsque : incertitude forte n’est pas automatiquement acceptée.
  Réalisation : le seuil de confiance 0,80 est configurable ; pertinence fausse, score inférieur ou
  incertitude forte empêchent le paquet, y compris avec une confiance élevée.
- [x] `SUG-015` Appliquer les quotas ARGO personnels à cette évaluation.
  Dépendances : `KEY-018` à `KEY-024`, `SUG-012`. Fini lorsque : suggestion et chat partagent le registre.
  Réalisation : l'évaluation utilise `ArgoClient` sans registre alternatif ; celui-ci réserve donc la
  requête dans la même base SQLite et la même politique de quota que chat et synthèse.
- [x] `SUG-016` Afficher immédiatement acceptée, non retenue ou à réessayer.
  Dépendances : `SUG-012` à `SUG-015`. Fini lorsque : aucun écran de suivi n’est nécessaire.
  Réalisation : l'API renvoie immédiatement `accepted`, `not_retained` ou `retry`, avec message,
  décision publique éventuelle et action Paramètres/réessai, sans statut distant.
- [x] `SUG-017` Définir le paquet de suggestion sans clé ni données locales inutiles.
  Dépendances : `SUG-001` à `SUG-016`. Fini lorsque : JSON, PDF éventuel et hashes sont suffisants.
  Réalisation : `suggestion.json` strict contient source validée, contexte minimal sans extrait,
  décision et artefact PDF éventuel hashé ; aucune clé, chemin original ou donnée de conversation.
- [x] `SUG-018` Écrire le paquet dans un dossier temporaire local.
  Dépendances : `SUG-017`. Fini lorsque : une interruption ne publie rien.
  Réalisation : le paquet est finalisé sous `.tmp-<UUID>`, hashé, puis renommé atomiquement dans
  l'outbox ; un temporaire interrompu n'est jamais transmis.
- [x] `SUG-019` Vérifier que le PDF proposé est transmissible selon la confirmation utilisateur.
  Dépendances : `SUG-007`. Fini lorsque : une case explicite est requise avant copie.
  Réalisation : la route PDF exige littéralement la confirmation vraie avant lecture métier, et le
  service refuse encore toute invocation sans consentement explicite.
- [x] `SUG-020` Déplacer atomiquement le paquet vers `suggestions/inbox` SharePoint.
  Dépendances : `PKG-012`, `SUG-018`, `SUG-019`. Fini lorsque : l’administrateur ne voit pas de paquet partiel.
  Réalisation : la copie complète est hashée dans un répertoire `.s-*`, puis renommée vers l'UUID
  seulement après vérification ; une collision différente est refusée.
- [x] `SUG-021` Conserver une copie locale minimale du reçu sans statut distant.
  Dépendances : `SUG-020`. Fini lorsque : seul ID, date et hash sont conservés.
  Réalisation : après transmission, l'outbox est supprimée et les reçus ne contiennent exactement que
  UUID, date et hash de paquet ; leur nom opaque sert d'index local.
- [x] `SUG-022` Refuser un nouveau dépôt local identique par hash ou DOI.
  Dépendances : `SUG-004`, `SUG-007`, `SUG-021`. Fini lorsque : un double clic ne duplique pas.
  Réalisation : les clés DOI et hash PDF sont hachées localement pour détecter reçus et outbox avant
  ARGO ; un test de double dépôt exige un unique appel évaluateur.
- [x] `SUG-023` Créer le formulaire unique de suggestion dans la Base documentaire.
  Dépendances : `SUG-001` à `SUG-016`. Fini lorsque : les quatre variantes sont accessibles.
  Réalisation : une seule carte dans Base documentaire sélectionne DOI, URL, PDF ou référence
  manuelle, partage commentaire/résultat immédiat et appelle exclusivement le client API typé.
- [x] `SUG-024` Ajouter glisser-déposer PDF avec validation avant ARGO.
  Dépendances : `SUG-007`, `SUG-023`. Fini lorsque : erreur de type est immédiate.
  Réalisation : la zone clavier/cliquable accepte aussi le drop, contrôle extension, taille et cinq
  octets de signature dans le navigateur ; trois tests frontend couvrent refus et acceptation.
- [x] `SUG-025` Ajouter l’explication qu’une suggestion acceptée n’est pas encore dans le RAG commun.
  Dépendances : `SUG-016`. Fini lorsque : l’utilisateur comprend le délai hebdomadaire.
  Réalisation : le formulaire et le message de succès indiquent explicitement l'examen administrateur
  hebdomadaire et l'absence d'intégration immédiate au RAG.
- [x] `SUG-026` Ne pas créer de page de liste ou de suivi des suggestions.
  Dépendances : `SUG-023`. Fini lorsque : seules soumission et confirmation existent.
  Réalisation : seules deux routes POST de soumission existent ; GET `/api/suggestions` retourne 404
  et l'interface ne comporte ni historique, ni poll, ni statut distant.
- [x] `SUG-027` Tester suggestion DOI acceptée avec ARGO simulé.
  Dépendances : `SUG-004`, `SUG-012`, `SUG-020`. Fini lorsque : le paquet est complet.
  Réalisation : un évaluateur simulé accepte le DOI ; le test vérifie paquet JSON atomique, absence de
  clé, outbox vidée et reçu minimal.
- [x] `SUG-028` Tester URL dangereuse rejetée avant ARGO.
  Dépendances : `SUG-005`. Fini lorsque : aucun réseau n’est contacté.
  Réalisation : URL loopback rejetée par le modèle FastAPI avant le service ; un espion exige zéro
  appel évaluateur, donc zéro réseau.
- [x] `SUG-029` Tester faux PDF rejeté avant ARGO.
  Dépendances : `SUG-007`. Fini lorsque : aucun fichier n’atteint SharePoint.
  Réalisation : faux contenu PDF et consentement faux retournent 422 avant extraction/ARGO ; les tests
  de workflow n'observent aucun paquet SharePoint.
- [x] `SUG-030` Tester prompt injection dans commentaire et PDF.
  Dépendances : `SUG-011`. Fini lorsque : elle ne change pas le schéma de décision.
  Réalisation : une injection est confinée au message utilisateur délimité, absente du système, et un
  champ DOI ajouté à la décision ARGO est refusé par Pydantic.
- [x] `SUG-031` Tester SharePoint indisponible après acceptation ARGO.
  Dépendances : `SUG-018`, `SUG-020`. Fini lorsque : le paquet local peut être renvoyé plus tard.
  Réalisation : une acceptation simulée avec dossier absent renvoie `retry` et conserve exactement un
  paquet local complet, ensuite transmissible lorsque le dossier réapparaît.
- [x] `SUG-032` Ajouter reprise des paquets locaux non transmis au prochain lancement.
  Dépendances : `SUG-031`. Fini lorsque : aucun double dépôt n’est créé.
  Réalisation : le lifespan réessaie les outbox complètes ; la transmission idempotente crée un seul
  dossier distant puis le second passage retourne zéro.
- [x] `SUG-033` Tester absence de clé ARGO pendant une suggestion.
  Dépendances : `KEY-007`, `SUG-012`. Fini lorsque : l’utilisateur est dirigé vers Paramètres.
  Réalisation : sans clé DPAPI, l'API renvoie immédiatement `retry`, action `settings` et un message
  contenant Paramètres ; l'UI affiche un lien direct vers cette page.
- [x] `SUG-034` Documenter droits, transmission et absence de suivi.
  Dépendances : `SUG-019`, `SUG-025`, `SUG-026`. Fini lorsque : le consentement est clair.
  Réalisation : le guide utilisateur détaille résultats immédiats, contexte ARGO, paquet SharePoint,
  droits de copie PDF, reçu minimal, absence de suivi et conduite à tenir par type d'indisponibilité.
- [ ] `SUG-035` Effectuer un test réel borné d’une suggestion non sensible.
  Dépendances : `SUG-001` à `SUG-034`. Fini lorsque : ARGO et dépôt SharePoint sont conformes.
  Ordonnancement : différé avec `KEY-030` et `PKG-035` jusqu'à disponibilité simultanée d'une clé ARGO
  réelle autorisée et d'un dossier SharePoint synchronisé ; aucun appel externe n'est simulé comme réel.

## M7 — maintenance hebdomadaire sur la machine administrateur

- [x] `ADM-001` Ajouter un profil local `admin` non distribué aux utilisateurs.
  Dépendances : `COR-004`. Fini lorsque : le rôle n’est pas activable depuis l’interface standard.
  Réalisation : le rôle provient exclusivement de `CIDERSCHOLAR_LOCAL_PROFILE`, absent de YAML et de
  toute route ; les gardes et tests existants refusent les mutations communes au profil utilisateur.
- [x] `ADM-002` Conserver les clés bibliographiques uniquement dans le coffre local administrateur.
  Dépendances : `ADM-001`, `ARC-011`. Fini lorsque : elles sont exclues des paquets.
  Réalisation : trois fichiers DPAPI sous `data/admin-secrets` remplacent l'hydratation registre des
  clés bibliographiques ; profil utilisateur, paquets communs et CLI non-admin ne peuvent les lire.
- [x] `ADM-003` Ajouter un statut de dernière maintenance réussie.
  Dépendances : `ADM-001`. Fini lorsque : date, version et résultat sont persistés.
  Réalisation : un JSON atomique strict sous `data/admin` conserve date UTC, version immuable, résultat
  `published` et UUID du travail ; sa relecture exacte est testée.
- [x] `ADM-004` Calculer l’échéance hebdomadaire au lancement administrateur.
  Dépendances : `ADM-003`. Fini lorsque : sept jours complets sont appliqués.
  Réalisation : le calcul central compare l'instant courant à succès + sept jours complets ; les tests
  couvrent la microseconde avant l'échéance et l'instant exact.
- [x] `ADM-005` Afficher la proposition `Lancer maintenant` ou `Reporter`.
  Dépendances : `ADM-004`. Fini lorsque : aucun lancement implicite n’a lieu.
  Réalisation : la carte des paramètres administrateur expose les deux actions avec confirmation ;
  l'API de statut ne crée jamais de travail et le test d'intégration le vérifie.
- [x] `ADM-006` Mémoriser un report sans repousser indéfiniment l’échéance réelle.
  Dépendances : `ADM-005`. Fini lorsque : la proposition revient au lancement suivant.
  Réalisation : le report atomique est distinct du dernier succès et ne modifie pas l'échéance ; il
  masque la proposition pour le processus courant seulement, donc elle revient au lancement suivant.
- [x] `ADM-007` Créer un type de travail durable `weekly_maintenance`.
  Dépendances : `JOB-001` à `JOB-018`. Fini lorsque : il réutilise la file locale.
  Réalisation : type, charge stricte et étapes dédiées sont persistés par la migration 13 et traités
  par le worker durable existant avec événements et reprise.
- [x] `ADM-008` Empêcher deux maintenances simultanées.
  Dépendances : `ADM-007`. Fini lorsque : un verrou persistant est testé.
  Réalisation : un index SQLite partiel unique interdit deux travaux de maintenance actifs ; le dépôt
  retourne le travail existant et les cas neuf et migré sont testés.
- [x] `ADM-009` Sauvegarder corpus principal avant toute mutation.
  Dépendances : `ARC-009`, `ADM-007`. Fini lorsque : la sauvegarde est vérifiée ouvrable.
  Réalisation : le corpus commun est empaqueté avant mutation, ses hashes sont revérifiés et son
  SQLite est ouvert en lecture seule ; une sauvegarde altérée est refusée par les tests.
- [x] `ADM-010` Copier la sauvegarde validée sur le drive protégé.
  Dépendances : `ADM-009`. Fini lorsque : le hash correspond.
  Réalisation : l'archive validée est copiée vers le drive protégé puis relue ; son SHA-256 doit être
  strictement identique avant que le pipeline poursuive.
- [x] `ADM-011` Scanner les nouveaux paquets SharePoint complets.
  Dépendances : `SUG-020`, `ADM-007`. Fini lorsque : paquets partiels sont ignorés.
  Réalisation : le scanner traite uniquement les dossiers finaux contenant manifeste et charge utile,
  et ignore toute écriture temporaire ou incomplète préfixée par un point.
- [x] `ADM-012` Vérifier manifestes, hashes et schémas des suggestions.
  Dépendances : `ADM-011`. Fini lorsque : une corruption est archivée sans import.
  Réalisation : modèles stricts, identité du paquet et hashes sont revérifiés avant accès au contenu ;
  les paquets invalides vont dans l'archive `corrupt` sans import.
- [x] `ADM-013` Dédupliquer suggestions par DOI, URL normalisée et hash PDF.
  Dépendances : `ADM-012`. Fini lorsque : plusieurs utilisateurs ne créent pas plusieurs notices.
  Réalisation : un index des DOI, URL normalisées et SHA-256 PDF du corpus et de la bibliothèque
  commune classe les doublons avant toute mutation.
- [x] `ADM-014` Revalider localement les décisions ARGO reçues avant import.
  Dépendances : `ADM-012`. Fini lorsque : IDs, métadonnées et décision sont cohérents.
  Réalisation : identité, métadonnées, décision ARGO et seuil de confiance sont recalculés localement ;
  toute incohérence classe la suggestion comme rejetée.
- [x] `ADM-015` Importer automatiquement les suggestions pertinentes.
  Dépendances : `ADM-013`, `ADM-014`. Fini lorsque : aucune interface de modération n’est requise.
  Réalisation : un PDF pertinent emprunte l'ingestion commune et une référence pertinente le magasin
  bibliographique, sans écran de modération ni action interactive.
- [x] `ADM-016` Archiver les suggestions importées ou rejetées hors inbox.
  Dépendances : `ADM-015`. Fini lorsque : l’inbox ne rejoue pas les mêmes paquets.
  Réalisation : chaque paquet final est déplacé atomiquement vers une archive datée `imported`,
  `duplicate`, `rejected` ou `corrupt`, hors de l'inbox.
- [x] `ADM-017` Exécuter la collecte bibliographique configurée.
  Dépendances : `ADM-002`, `ADM-007`. Fini lorsque : quotas et délais actuels sont conservés.
  Réalisation : la maintenance réutilise `CiderPilotHarvester` et sa configuration existante, après
  hydratation éphémère des seuls secrets bibliographiques du coffre administrateur.
- [x] `ADM-018` Réserver l’enrichissement externe des chats au profil administrateur.
  Dépendances : `ADM-002`. Fini lorsque : le toggle est absent sur les postes utilisateurs.
  Réalisation : backend et interface exigent tous deux le profil local administrateur ; le toggle est
  absent du HTML utilisateur et le worker ignore toute demande forgée côté utilisateur.
- [x] `ADM-019` Dédupliquer les résultats collectés avec suggestions et corpus existant.
  Dépendances : `ADM-013`, `ADM-017`. Fini lorsque : DOI est prioritaire.
  Réalisation : le magasin de collecte fusionne en priorité par DOI normalisé, puis par URL et identité
  de secours, y compris les notices issues des suggestions déjà importées.
- [x] `ADM-020` Appliquer filtre de pertinence et archive des rejets.
  Dépendances : `ADM-017`, `ADM-019`. Fini lorsque : les invariants actuels restent vrais.
  Réalisation : le filtre de pertinence existant est conservé et son nettoyage archive les rejets avec
  motif minimal plutôt que de les supprimer silencieusement.
- [x] `ADM-021` Indexer uniquement les nouveaux abstracts acceptés.
  Dépendances : `ADM-020`. Fini lorsque : E5 n’est pas chargé sans travail.
  Réalisation : les sélecteurs ne transmettent que chunks communs et abstracts acceptés sans vecteur ;
  le moteur E5 n'est construit que si cette liste non vide existe.
- [x] `ADM-022` Vérifier cohérence SQLite/Qdrant après maintenance.
  Dépendances : `ADM-015`, `ADM-021`. Fini lorsque : toute incohérence bloque la publication.
  Réalisation : la validation compare les comptes SQLite attendus aux points Qdrant communs et bloque
  la suite avant construction ou publication au moindre écart.
- [x] `ADM-023` Construire un nouveau paquet de corpus.
  Dépendances : `ADM-022`, `PKG-010`. Fini lorsque : la version est immuable.
  Réalisation : le builder déterministe produit une nouvelle version à identité de contenu, vérifiée
  et non modifiable, à partir du corpus validé.
- [x] `ADM-024` Publier la version sur SharePoint.
  Dépendances : `ADM-023`, `PKG-015`. Fini lorsque : `latest` est mis à jour en dernier.
  Réalisation : l'archive protégée reçoit et revérifie d'abord le paquet ; le publisher atomique copie
  ensuite les artefacts et remplace le pointeur `latest` en toute dernière opération.
- [x] `ADM-025` Marquer la maintenance réussie seulement après publication.
  Dépendances : `ADM-024`. Fini lorsque : un échec de publication conserve l’échéance.
  Réalisation : le fichier de dernier succès n'est écrit qu'après retour confirmé du publisher ; les
  tests d'échec garantissent que l'ancienne échéance demeure.
- [x] `ADM-026` Produire un rapport local sans contenu complet de documents.
  Dépendances : `ADM-025`. Fini lorsque : compteurs, erreurs et version sont présents.
  Réalisation : le rapport JSON strict contient état, compteurs, version, étapes et erreurs bornées,
  sans texte documentaire, PDF, question ni réponse de conversation.
- [x] `ADM-027` Ajouter reprise après interruption à chaque étape.
  Dépendances : `ADM-009` à `ADM-026`. Fini lorsque : aucune étape terminée n’est répétée inutilement.
  Réalisation : un checkpoint atomique est enregistré après chaque étape ; au redémarrage, le handler
  saute les étapes terminées et reprend exactement la première incomplète.
- [x] `ADM-028` Ajouter rollback depuis la sauvegarde préalable.
  Dépendances : `ADM-009`, `ADM-022`. Fini lorsque : une maintenance défectueuse est annulable.
  Réalisation : avant publication, toute exception revérifie la sauvegarde puis restaure atomiquement
  le corpus commun ; données privées et corpus actif antérieur restent intacts.
- [x] `ADM-029` Tester une maintenance complète avec fournisseurs et ARGO simulés.
  Dépendances : `ADM-001` à `ADM-028`. Fini lorsque : une version SharePoint simulée est publiée.
  Réalisation : le test bout en bout couvre verrou, étapes, publication simulée, rapport, succès,
  interruption, rollback et reprise jusqu'à une version finale publiée.
- [ ] `ADM-030` Observer quatre cycles manuels avant de considérer le workflow stable.
  Dépendances : `ADM-029`. Fini lorsque : quatre rapports successifs sont conformes.
  Ordonnancement : différé jusqu'à quatre semaines réelles d'exploitation administrateur ; une suite
  accélérée ou simulée ne peut pas satisfaire honnêtement ce critère temporel.

## M8 — installation Windows et lancement sans terminal

- [x] `INS-001` Choisir le format d’installateur Windows compatible avec PyTorch et les modèles.
  Réalisation : Inno Setup par utilisateur est retenu après comparaison avec MSIX, archive portable
  et PyInstaller ; taille, droits, mises à jour, licence du compilateur et matrice de décision sont
  consignés dans `WINDOWS_INSTALLER_DECISION.md`.
  Dépendances : `ARC-001`. Fini lorsque : taille, droits admin et mise à jour sont comparés.
- [x] `INS-002` Geler les versions Python, Node et dépendances du jalon.
  Réalisation : `installer/versions.json` fixe application 0.2.0, CPython 3.12.10 avec URL/hash,
  pip 26.1.2, Node de build 24.14.1 et Inno minimal 6.5 ; `requirements-runtime.txt` épingle chaque
  dépendance embarquée et le wheelhouse cible CPython 3.12 Windows x64.
  Dépendances : `INS-001`. Fini lorsque : la matrice est reproductible.
- [x] `INS-003` Construire un environnement Python embarqué ou isolé.
  Réalisation : le build vérifie puis extrait l’archive embeddable officielle, installe uniquement les
  wheels Windows x64, retire outils/tests/développement et exécute un smoke test lourd avec ce Python.
  Dépendances : `INS-001`, `INS-002`. Fini lorsque : aucun Python système n’est requis à l’usage.
- [x] `INS-004` Inclure le frontend de production précompilé.
  Réalisation : `npm ci` et le build Vite précèdent la copie de `frontend/dist` dans le payload ; le
  runtime installé sert ces fichiers et ne contient ni Node ni npm.
  Dépendances : `INS-002`. Fini lorsque : Node n’est pas requis à l’usage.
- [x] `INS-005` Inclure ou télécharger explicitement E5 pendant l’installation.
  Réalisation : le modèle E5 local complet est vérifié avant build puis copié dans l’installateur
  hors ligne sous `UserData/data/models`; aucun accès réseau n’est nécessaire au premier chat.
  Dépendances : `INS-001`. Fini lorsque : aucun téléchargement implicite au premier chat.
- [x] `INS-006` Vérifier l’intégrité du modèle E5 installé.
  Réalisation : un manifeste strict contenu-adressé couvre la liste exacte, taille et SHA-256 de chaque
  fichier ; le post-install Inno relit tout le modèle et annule l’installation sur divergence.
  Dépendances : `INS-005`. Fini lorsque : un hash incorrect bloque l’installation.
- [x] `INS-007` Créer les répertoires commun, privé, file, exports, sauvegardes et secrets.
  Réalisation : le layout par utilisateur crée `common`, `private`, `queue`, `exports`, `backups`,
  `secrets`, `runtime`, `logs`, `models`, bases et caches sous `%LOCALAPPDATA%/CiderScholar/UserData`.
  Dépendances : `COR-002`, `KEY-004`. Fini lorsque : ACL et chemins sont corrects.
- [x] `INS-008` Installer un raccourci bureau et menu Démarrer.
  Réalisation : Inno crée les deux raccourcis vers `runtime/pythonw.exe -m scripts.launch_windows` ;
  ils ont été constatés après installation réelle sans console.
  Dépendances : `INS-003`, `INS-004`. Fini lorsque : aucun terminal n’apparaît.
- [x] `INS-009` Créer un lanceur supervisant API et worker.
  Réalisation : le lanceur démarre API et worker avec `CREATE_NO_WINDOW`, journalise séparément,
  surveille leur vie commune et les arrête ensemble ; le paquet installé a atteint `/health`.
  Dépendances : `WRK-020`. Fini lorsque : les deux processus démarrent ou échouent ensemble clairement.
- [x] `INS-010` Ouvrir automatiquement le navigateur lorsque la santé locale est prête.
  Réalisation : l’ouverture intervient seulement après polling réussi de `/health`, sans temporisation
  fixe, et une indisponibilité produit une erreur locale lisible.
  Dépendances : `INS-009`. Fini lorsque : aucun délai fixe fragile n’est utilisé.
- [x] `INS-011` Réutiliser une instance déjà lancée.
  Réalisation : un mutex Windows nommé protège le superviseur ; un second lancement installé a quitté
  en code 0 après avoir réutilisé la santé de la première instance, sans second worker ni Qdrant.
  Dépendances : `INS-009`. Fini lorsque : un double clic n’ouvre pas deux Qdrant.
- [x] `INS-012` Ajouter l’arrêt propre depuis une icône ou une commande visible.
  Réalisation : la carte Paramètres appelle `POST /api/system/shutdown`, pose le marqueur coopératif,
  laisse API et worker fermer leurs ressources puis termine le superviseur en code 0.
  Dépendances : `INS-009`. Fini lorsque : SQLite et Qdrant sont fermés.
- [x] `INS-013` Persister les travaux avant arrêt.
  Réalisation : le worker ne reçoit aucune annulation destructive ; la demande d’arrêt n’interrompt
  pas son handler actif et tous les états/événements restent dans SQLite avant sortie.
  Dépendances : `JOB-010`, `INS-012`. Fini lorsque : aucun travail n’est perdu.
- [x] `INS-014` Reprendre automatiquement les travaux au lancement suivant.
  Réalisation : le worker continu est relancé avec le même SQLite et récupère les baux expirés ; les
  tests de redémarrage conservent un seul job et une seule question jusqu’au résultat.
  Dépendances : `JOB-031`, `INS-009`. Fini lorsque : l’état revient sans intervention.
- [x] `INS-015` Ajouter l’assistant de premier lancement.
  Réalisation : `FirstLaunchWizard` guide SharePoint, corpus initial, coffre ARGO DPAPI/test et mémoire,
  et l’AppShell l’affiche tant que l’état dérivé n’est pas complet sans superposer l’ancien dialogue.
  Dépendances : `KEY-014`, `PKG-013`, `COR-028`. Fini lorsque : clé, SharePoint et profil mémoire sont guidés.
- [x] `INS-016` Ajouter une étape de sélection du dossier SharePoint synchronisé.
  Réalisation : l’assistant ouvre le sélecteur de dossier Windows natif, valide le nom/confirmation et
  exige un `corpus/latest.json` local strict avant d’enregistrer l’override non secret.
  Dépendances : `PKG-033`, `INS-015`. Fini lorsque : le manifeste est détecté.
- [x] `INS-017` Ajouter une étape d’installation du premier corpus commun.
  Réalisation : l’étape télécharge depuis le dossier synchronisé, valide archive/manifeste/hashes dans
  un staging puis active atomiquement ; un corpus partiel ne peut jamais devenir courant.
  Dépendances : `PKG-020` à `PKG-025`, `INS-016`. Fini lorsque : l’application ne démarre pas sur corpus partiel.
- [x] `INS-018` Ajouter une étape de configuration ARGO suivant le tutoriel.
  Réalisation : l’assistant saisit la clé sans `.env`, la chiffre avec DPAPI pour le compte courant et
  propose le probe `/models` sans génération ni exposition du secret.
  Dépendances : `KEY-014`, `KEY-016`, `INS-015`. Fini lorsque : l’utilisateur ne voit pas `.env`.
- [x] `INS-019` Ajouter la sélection automatique 8 Go ou 16 Go.
  Réalisation : la RAM physique produit une recommandation 8/16 Go, affichée avant application ;
  l’utilisateur peut sélectionner l’autre profil et l’override YAML atomique est relu immédiatement.
  Dépendances : `COR-026` à `COR-028`, `INS-015`. Fini lorsque : le choix peut être corrigé manuellement.
- [x] `INS-020` Ajouter une vérification espace disque avant installation.
  Réalisation : le contrôle calcule payload applicatif, modèle, corpus attendu et marge fixe de 2 Go,
  puis refuse avant copie si l’espace libre de la destination est insuffisant.
  Dépendances : `INS-005`, `INS-017`. Fini lorsque : l’espace requis est calculé.
- [x] `INS-021` Ajouter une vérification Windows 11 et architecture 64 bits.
  Réalisation : Inno exige x64 et Windows build 22000 ; le préflight Python confirme plateforme,
  architecture et build, validés sur l’installation réelle Windows 11 build 26200.
  Dépendances : `INS-001`. Fini lorsque : un système incompatible échoue avant copie.
- [x] `INS-022` Ajouter une désinstallation conservant les données par défaut.
  Réalisation : programme et `UserData` sont séparés ; la suppression des données exige un choix
  interactif distinct et `SuppressibleMsgBox(..., IDNO)` conserve automatiquement en mode silencieux,
  confirmé par le journal réel et une sortie 0.
  Dépendances : `INS-007`. Fini lorsque : la suppression des données demande une confirmation séparée.
- [x] `INS-023` Ajouter une option de sauvegarde avant désinstallation complète.
  Réalisation : avant effacement confirmé, l’uninstaller propose un ZIP manifesté contenant snapshot
  conversations/travaux et archive privée, jamais secrets ni corpus commun ; un échec annule l’effacement.
  Dépendances : `COR-025`, `INS-022`. Fini lorsque : conversations et privé sont exportables.
- [ ] `INS-024` Placer l’installateur signé ou hashé dans SharePoint.
  Dépendances : `INS-001` à `INS-023`. Fini lorsque : hash et version sont publiés à côté.
  Ordonnancement : l’exécutable final `CiderScholar-0.2.0-windows-x64.exe`, son sidecar et
  `latest.json` sont prêts localement avec SHA-256
  `d0ce39e050feb13c77c9ed9a9c76275aa8cd56c4e7430d4c405ba03a0a145989`; le rapport reproductible
  est dans `WINDOWS_BUILD_0.2.0.md`. Aucun dossier SharePoint réel n’est configuré dans cet
  environnement ; la publication distante reste manuelle.
- [ ] `INS-025` Créer une page SharePoint d’installation courte.
  Dépendances : `INS-024`. Fini lorsque : télécharger, installer et premier lancement sont expliqués.
  Ordonnancement : le contenu prêt à publier est dans `SHAREPOINT_INSTALLATION.md`; la création de la
  page SharePoint dépend de l’accès réel de `INS-024` et n’est pas simulée.
- [x] `INS-026` Ajouter une vérification de mise à jour applicative au lancement.
  Réalisation : un manifeste applicatif strict, distinct du corpus, est lu au lancement depuis le
  dossier synchronisé ; état courant, version disponible, hash et raison d’indisponibilité sont exposés.
  Dépendances : `INS-024`. Fini lorsque : elle est distincte des mises à jour du corpus.
- [x] `INS-027` Ne jamais mettre à jour l’application pendant un travail actif.
  Réalisation : `active_job_count` couvre queued/running/cancel_requested et le plan de mise à jour
  retourne `deferred_active_jobs` sans remplacement tant qu’un seul travail durable est actif.
  Dépendances : `INS-026`, `JOB-023`. Fini lorsque : la mise à jour est reportée.
- [x] `INS-028` Préserver données et secrets pendant une mise à jour applicative.
  Réalisation : Inno remplace uniquement le dossier programme et conserve configuration, SQLite,
  corpus, privé, file et secrets sous `UserData`; le test réel avec quatre sentinelles est conforme.
  Dépendances : `INS-026`. Fini lorsque : une installation de version suivante conserve le profil.
- [ ] `INS-029` Tester installation propre sur profil Windows temporaire.
  Dépendances : `INS-001` à `INS-028`. Fini lorsque : aucun terminal n’est requis.
  Ordonnancement : l’installation propre est conforme sur le profil courant, mais un second profil
  Windows temporaire distinct exige une création/connexion externe et reste un contrôle manuel réel.
- [x] `INS-030` Tester mise à jour applicative avec conversations et travaux existants.
  Réalisation : une conversation et un job queued sentinelles restent présents (`1 1`) après mise à
  jour, avec privé et secret identiques ; E5 et le runtime repassent leurs validations.
  Dépendances : `INS-026` à `INS-028`. Fini lorsque : données et file sont intactes.
- [x] `INS-031` Tester désinstallation puis réinstallation avec données conservées.
  Réalisation : le désinstalleur silencieux final choisit explicitement `IDNO`, sort en 0, puis la
  réinstallation retrouve conversation, job, privé et secret et vérifie à nouveau E5 en code 0.
  Dépendances : `INS-022`, `INS-030`. Fini lorsque : le profil est retrouvé.
- [ ] `INS-032` Tester sur un poste 8 Go réel.
  Dépendances : `INS-029`, `COR-029`. Fini lorsque : recherche et chat simulé restent utilisables.
  Ordonnancement : le profil 8 Go est actif et testé de façon simulée/locale, mais la preuve demandée
  porte sur une machine physique de 8 Go et reste externe.
- [ ] `INS-033` Tester sur un poste 16 Go réel.
  Dépendances : `INS-029`. Fini lorsque : le profil mémoire supérieur reste stable.
  Ordonnancement : aucune machine physique 16 Go distincte n’est disponible dans l’environnement ;
  la validation matérielle ne peut pas être déclarée sur la seule détection simulée.
- [x] `INS-034` Rédiger le guide de dépannage sans commandes obligatoires.
  Réalisation : `WINDOWS_TROUBLESHOOTING.md` couvre clé ARGO, dossier SharePoint, modèle/corpus,
  worker, reprise, mise à jour et escalade sans contenu sensible ni commande obligatoire.
  Dépendances : `INS-015` à `INS-023`, `INS-026` à `INS-031`. Fini lorsque : clé, SharePoint, worker et corpus sont couverts.
- [ ] `INS-035` Faire installer la version par une personne n’ayant pas développé le projet.
  Dépendances : `INS-025`, `INS-034`. Fini lorsque : les difficultés observées sont corrigées.
  Ordonnancement : l’installation autonome et le guide sont prêts, mais aucune personne indépendante
  n’est disponible dans cette session ; ce contrôle humain ne sera pas auto-attesté.

## M9 — démonstration et pilote équipe

- [x] `DEM-001` Écrire un scénario présentiel de cinq minutes.
  Réalisation : `DEMO_RUNBOOK.md` cadence chaque clic de 0:00 à 5:00, précise les résultats attendus,
  les origines à montrer, l’arrêt/redémarrage et le repli honnête sans réponse présentée comme réelle.
  Dépendances : `FMT-030`, `UI-030`, `PKG-035`. Fini lorsque : chaque clic et résultat attendu sont
  décrits.
- [x] `DEM-002` Choisir trois questions non sensibles couvertes par le corpus.
  Réalisation : `demo_questions.json` versionne une question directe sur les sels minéraux, une
  comparaison oxygénation/concentré et un suivi sur les protocoles volatils, sans donnée interne.
  Dépendances : aucune. Fini lorsque : directe, comparative et suivi existent.
- [x] `DEM-003` Vérifier localement les sources des trois questions.
  Réalisation : la commande dédiée vérifie localement DOI, titre, hash PDF et ancres de page pour cinq
  associations question/source ; elle passe sur le corpus commun réel et échoue sur un PDF modifié.
  Dépendances : `DEM-002`. Fini lorsque : une modification de corpus peut être détectée.
- [x] `DEM-004` Ajouter une vérification de préparation ARGO, worker, corpus et disque.
  Réalisation : `/api/diagnostics/readiness` sonde seulement `/models`, un heartbeat worker, les comptes
  SQLite communs et 2 Go libres ; le test prouve un unique probe et aucune génération.
  Dépendances : `KEY-010`, `WRK-020`, `PKG-029`. Fini lorsque : aucun texte n’est généré.
- [x] `DEM-005` Ajouter profondeur de file et âge du plus ancien travail.
  Réalisation : `queue_metrics` agrège états actifs, profondeur et âge UTC du plus ancien depuis SQLite
  sans sélectionner payload, question, conversation ni identifiant client ; l’absence est testée.
  Dépendances : `JOB-023`. Fini lorsque : aucun contenu n’est exposé.
- [x] `DEM-006` Ajouter une page de diagnostic lisible.
  Réalisation : la navigation expose une page Diagnostic avec état global, quatre cartes prêtes/à
  corriger, action recommandée pour chaque panne et cinq métriques de file accessibles ; les 48 tests
  frontend, lint, types et build passent.
  Dépendances : `DEM-004`, `DEM-005`. Fini lorsque : chaque panne a une action recommandée.
- [x] `DEM-007` Créer le parcours E2E prose et APA 7.
  Réalisation : le contrat E2E rend deux paragraphes scientifiques cités, une bibliographie APA avec
  DOI uniques et rejette tout marqueur de liste ; il figure dans la matrice de répétition unique.
  Dépendances : `FMT-028`. Fini lorsque : aucune puce n’est rendue.
- [x] `DEM-008` Créer le parcours E2E liste explicitement demandée.
  Réalisation : deux tests imposent `bullet_list` uniquement après une consigne explicite et vérifient
  exactement une puce non vide par affirmation, avec une référence unique.
  Dépendances : `FMT-013`. Fini lorsque : les puces apparaissent uniquement ici.
- [x] `DEM-009` Créer le parcours E2E changer de chat pendant travail.
  Réalisation : le flux durable sélectionne un autre chat pendant le polling, produit une notification
  hors conversation et relit la réponse persistée uniquement dans le chat initial.
  Dépendances : `UI-022`. Fini lorsque : la réponse arrive dans le chat initial.
- [x] `DEM-010` Créer le parcours E2E redémarrer puis reprendre.
  Réalisation : le même flux recharge le travail actif, reprend son suivi puis termine avec un seul
  appel d’enqueue, ce qui prouve qu’aucune question n’est resoumise.
  Dépendances : `UI-029`, `INS-014`. Fini lorsque : aucune question n’est resoumise.
- [x] `DEM-011` Créer le parcours E2E document privé et source commune.
  Réalisation : la recherche séquentielle retourne un résultat `common` puis `private`, chacun marqué,
  et le contrat de présentation vérifie les libellés distincts visibles dans le chat.
  Dépendances : `COR-022`. Fini lorsque : les origines sont visibles.
- [x] `DEM-012` Créer le parcours E2E mise à jour de corpus.
  Réalisation : l’activation d’un corpus commun préparé conserve bit à bit toutes les empreintes du
  répertoire privé tout en rendant le nouveau contenu commun actif et l’ancien réversible.
  Dépendances : `PKG-027`, `PKG-028`. Fini lorsque : le privé reste intact.
- [x] `DEM-013` Créer le parcours E2E suggestion PDF.
  Réalisation : un PDF explicitement confirmé est validé, renommé sans fuite du nom privé puis livré
  atomiquement comme paquet complet dans l’inbox SharePoint simulée.
  Dépendances : `SUG-027` à `SUG-032`. Fini lorsque : le paquet arrive dans l’inbox simulée.
- [x] `DEM-014` Créer le parcours E2E quota atteint puis reprise.
  Réalisation : le quota local replace le même travail en file jusqu’à l’heure persistée sans consommer
  de tentative ; le worker le reprend ensuite et persiste la réponse sans état d’échec.
  Dépendances : `UI-025`. Fini lorsque : le travail n’échoue pas.
- [ ] `DEM-015` Répéter la démonstration avec ARGO réel et une seule génération bornée.
  Ordonnancement : les huit parcours simulés sont verts, mais aucune clé ARGO réelle n’est disponible
  dans cette session ; la génération réelle ne sera ni inventée ni déclenchée sans ce secret utilisateur.
  Dépendances : `DEM-001` à `DEM-014`. Fini lorsque : un rapport daté est conforme.
- [x] `DEM-016` Tester la procédure de repli si ARGO est indisponible.
  Réalisation : un test provoque l’indisponibilité de la sonde, vérifie le blocage sans réponse ni
  détail fournisseur et verrouille les deux mentions honnêtes du runbook ; trois tests ciblés passent.
  Dépendances : `DEM-001`. Fini lorsque : aucune réponse scientifique préenregistrée n’est présentée comme réelle.
- [ ] `DEM-017` Corriger tous les défauts bloquants de répétition générale.
  Ordonnancement : le repli local est conforme, mais la liste réelle des défauts ne peut être établie
  qu’après la répétition ARGO `DEM-015` ; aucun défaut P0/P1 simulé n’est assimilé à cette répétition.
  Dépendances : `DEM-015`, `DEM-016`. Fini lorsque : aucun P0/P1 ne reste ouvert.
- [ ] `DEM-018` Geler une version démontrable.
  Ordonnancement : le gel attend la répétition ARGO et ses corrections ; l’installateur local M8 reste
  une preuve d’installation et non une version de démonstration artificiellement attestée.
  Dépendances : `DEM-017`. Fini lorsque : installateur, app et corpus ont des versions consignées.
- [ ] `ROL-001` Définir un groupe pilote de deux personnes.
  Dépendances : `DEM-018`. Fini lorsque : deux postes Windows compatibles sont identifiés.
- [ ] `ROL-002` Faire installer depuis SharePoint par ces deux personnes.
  Dépendances : `INS-035`, `ROL-001`. Fini lorsque : aucune assistance terminal n’est nécessaire.
- [ ] `ROL-003` Vérifier clé ARGO personnelle sur les deux postes.
  Dépendances : `KEY-030`, `ROL-002`. Fini lorsque : chaque clé reste locale.
- [ ] `ROL-004` Vérifier même version du RAG sur les deux postes.
  Dépendances : `PKG-035`, `ROL-002`. Fini lorsque : version et hash correspondent.
- [ ] `ROL-005` Vérifier conversations et documents privés isolés.
  Dépendances : `COR-021`, `ROL-002`. Fini lorsque : aucun fichier privé n’apparaît sur l’autre poste.
- [x] `ROL-006` Recueillir les défauts sans stocker le contenu des chats.
  Réalisation : la page Retours pilote et son API locale acceptent strictement type, étape et
  description volontaire ; SQLite ne possède aucun champ chat, question, réponse, travail ou document,
  et tout champ JSON supplémentaire est rejeté. Les 11 tests backend ciblés, lint, types et build passent.
  Dépendances : `ROL-002` à `ROL-005`. Fini lorsque : seuls type, étape et description volontaire existent.
- [ ] `ROL-007` Corriger les défauts bloquants du pilote à deux.
  Ordonnancement : le canal de collecte est prêt avant le pilote, mais aucun défaut réel des deux
  personnes ne peut être inventé ; la correction attend donc `ROL-001` à `ROL-005`.
  Dépendances : `ROL-006`. Fini lorsque : aucun P0/P1 ne reste ouvert.
- [ ] `ROL-008` Publier la version corrigée sur SharePoint.
  Dépendances : `ROL-007`. Fini lorsque : installateur et notes sont à jour.
- [ ] `ROL-009` Étendre progressivement aux dix postes.
  Dépendances : `ROL-008`. Fini lorsque : chaque installation est enregistrée sans donnée personnelle.
- [ ] `ROL-010` Vérifier une mise à jour hebdomadaire complète après déploiement.
  Dépendances : `ADM-030`, `ROL-009`. Fini lorsque : les dix postes voient la même version publiée.

## M10 — validité scientifique et benchmark CiderQA

- [x] `EVL-001` Écrire le protocole CiderQA avant de modifier le pipeline scientifique.
  Ordonnancement : la rédaction et les contrats reproductibles sont indépendants du déploiement à dix
  postes et sont avancés sans prétendre que la cohorte experte ou le jeu final existent déjà.
  Réalisation : `CIDERQA_PROTOCOL.md` version 1.0.0 fige population réelle, cinq tâches, métriques,
  quotas de cas, seuils chiffrés, aveugle expert, exclusions, gel et exigences du rapport reproductible.
  Dépendances : `ROL-010`. Fini lorsque : population, tâches, métriques, seuils et règles d’exclusion
  sont versionnés dans un document dédié.
- [x] `EVL-002` Séparer les jeux de développement, validation et test final.
  Réalisation : un manifeste empreinté impose trois fichiers physiques, interdit qu’une famille traverse
  les jeux, contrôle compte et hash, et refuse d’ouvrir les labels finaux hors exécution `final_test`.
  Dépendances : `EVL-001`. Fini lorsque : le test final est gelé et n’est jamais utilisé pour régler
  les paramètres de recherche ou les prompts.
- [x] `EVL-003` Définir un schéma versionné pour chaque question CiderQA.
  Réalisation : le contrat strict v1 valide identifiant, famille, split, langue, tâche, question,
  répondabilité, réponse, affirmations et preuves article/hash/type/extrait/pages sans champ inconnu.
  Dépendances : `EVL-001`. Fini lorsque : question, langue, réponse attendue, caractère répondable,
  articles, extraits et pages de référence sont validés strictement.
- [x] `EVL-004` Supprimer toute fuite des concepts attendus vers le moteur évalué.
  Réalisation : le runner historique n’envoie plus `expected_concepts` au ranker, le schéma CiderQA
  rejette ce champ inconnu et un test inspecte les options d’inférence ; 14 tests d’évaluation passent.
  Dépendances : `EVL-003`. Fini lorsque : ni `expected_concepts`, ni article attendu, ni extrait de
  référence ne sont transmis à la recherche pendant une exécution de benchmark.
- [ ] `EVL-005` Constituer au moins cent questions à partir de documents cidricoles réels.
  Ordonnancement : le protocole et le validateur sont prêts, mais aucun groupe d’experts ni cent
  annotations réelles n’est fourni dans cette session ; les cas synthétiques de test ne sont pas comptés.
  Dépendances : `EVL-002`, `EVL-003`. Fini lorsque : chaque question possède une provenance vérifiée
  et qu’aucun PDF de démonstration synthétique ne compte dans le seuil.
- [ ] `EVL-006` Inclure des réponses absentes des abstracts mais présentes dans le texte intégral.
  Dépendances : `EVL-005`. Fini lorsque : au moins vingt-cinq questions exigent le corps, un tableau
  ou une figure du document.
- [ ] `EVL-007` Inclure des questions volontairement sans réponse dans le corpus.
  Dépendances : `EVL-005`. Fini lorsque : au moins quinze cas mesurent l’abstention sans transformer
  une absence de preuve en conclusion.
- [ ] `EVL-008` Inclure des questions multi-articles, comparatives et contradictoires.
  Dépendances : `EVL-005`. Fini lorsque : au moins vingt cas exigent plusieurs sources ou la
  distinction explicite de résultats incompatibles.
- [ ] `EVL-009` Équilibrer les questions françaises et anglaises pertinentes pour l’équipe.
  Dépendances : `EVL-005`. Fini lorsque : les deux langues couvrent réponse directe, comparaison,
  absence de réponse et suivi conversationnel.
- [ ] `EVL-010` Faire valider en aveugle les réponses et preuves de référence par des experts.
  Dépendances : `EVL-006` à `EVL-009`. Fini lorsque : les désaccords sont arbitrés sans montrer la
  réponse de CiderScholar aux évaluateurs.
- [x] `EVL-011` Mesurer rappel documentaire, MRR et nDCG sans information attendue à l’inférence.
  Réalisation : le calcul post-inférence publie rappel@20, MRR et nDCG@20 séparément pour notices,
  articles et fragments, avec intervalles bootstrap 95 % déterministes et sans label dans le ranker.
  Dépendances : `EVL-004`, `EVL-010`. Fini lorsque : le rapport distingue notices, articles et
  fragments et publie les intervalles d’incertitude.
- [x] `EVL-012` Mesurer l’exactitude et la complétude des réponses de bout en bout.
  Réalisation : exactitude atomique et couverture des affirmations de référence sont distinctes ; une
  affirmation annotée factuellement fausse vaut zéro même munie d’une citation valide.
  Dépendances : `EVL-010`. Fini lorsque : une réponse bien citée mais factuellement fausse échoue.
- [x] `EVL-013` Mesurer précision, rappel et implication sémantique des citations.
  Réalisation : chaque citation évaluée porte identifiant de preuve, implication et exactitude de page ;
  le rapport calcule précision traçable, rappel par affirmation, implication et pages exactes.
  Dépendances : `EVL-010`. Fini lorsque : chaque affirmation atomique est comparée à l’extrait et à
  la page censés l’étayer.
- [x] `EVL-014` Mesurer la calibration de l’abstention et les faux refus.
  Réalisation : les strates répondable/non-répondable produisent sensibilité, spécificité, faux refus et
  score de Brier du signal d’insuffisance ; le protocole fixe 0,85 pour les deux taux de décision.
  Dépendances : `EVL-007`, `EVL-012`. Fini lorsque : répondables et non-répondables possèdent des
  métriques séparées et un seuil documenté.
- [x] `EVL-015` Ajouter un runner CiderQA reproductible et sans appel externe implicite.
  Réalisation : `scripts.evaluate_ciderqa` lit uniquement manifeste, résultats adjugés et contexte
  locaux, consigne corpus/modèles/prompts/paramètres/graines/durée/mémoire/coût ARGO, borne tout appel
  déclaré et produit un JSON canonique signé SHA-256 ; huit tests ciblés passent.
  Dépendances : `EVL-011` à `EVL-014`. Fini lorsque : corpus, modèle, prompts, paramètres, graines,
  durée, coût ARGO et mémoire figurent dans un rapport signé par empreinte.
- [!] `EVL-016` Établir les baselines abstract-only et full-text actuelles.
  Avancement partiel : le comparateur impose deux rapports signés, le même jeu, corpus, ordre,
  modèles, graines et paramètres, puis publie les écarts de qualité et de ressources.
  Blocage : exécuter les deux parcours sur le CiderQA réel gelé après `EVL-005` à `EVL-010`.
  Ordonnancement : le runner accepte les deux modes, mais le jeu réel gelé et ses annotations expertes
  `EVL-005` à `EVL-010` n’existent pas encore ; aucune baseline synthétique ne sera publiée comme réelle.
  Dépendances : `EVL-015`. Fini lorsque : les deux parcours sont exécutés sur le même jeu gelé et que
  leurs résultats ne sont pas présentés comme ceux de PaperQA2.
- [x] `EVL-017` Définir un seuil de promotion scientifique et un budget de régression.
  Ordonnancement : la politique peut être verrouillée avant les baselines afin d’éviter d’ajuster les
  seuils aux résultats observés ; aucune promotion reste possible tant que `EVL-016` manque.
  Réalisation : la politique 1.0.0 fixe onze seuils absolus et onze budgets non compensables ; le gate
  refuse signature, jeu, split, mode ou budget ARGO incompatibles et cinq tests de rapport/promotion passent.
  Dépendances : `EVL-016`. Fini lorsque : une évolution ne peut devenir le défaut si elle dégrade
  exactitude, citations ou abstention au-delà des tolérances adoptées.
- [!] `EVL-018` Transformer les erreurs représentatives en tests de non-régression.
  Avancement partiel : le préparateur et le replay signés couvrent négation, unité, population, page,
  source et réponse forcée, avec refus des catégories manquantes ou du contexte incompatible.
  Blocage : sélectionner et adjuger les cas représentatifs issus des baselines réelles `EVL-016`.
  Ordonnancement : l’infrastructure distingue déjà factualité, implication, page et réponse forcée,
  mais les erreurs représentatives doivent provenir des baselines réelles `EVL-016`, pas être inventées.
  Dépendances : `EVL-017`. Fini lorsque : erreurs de négation, unité, population, page, source et
  réponse forcée possèdent chacune un cas automatisé.

## M11 — CiderScholar Deep Research full-text

- [x] `DRS-001` Définir les contrats « Réponse rapide » et « Analyse approfondie ».
  Ordonnancement : les limites produit et de preuve sont définissables avant promotion, tandis que
  l’activation du mode approfondi restera interdite jusqu’au gate CiderQA `DRS-025`.
  Réalisation : le contrat 1.0.0 fixe sources, niveaux abstract/full-text, délais, volumes RRF/reranker,
  deux itérations, budgets ARGO, durabilité et conditions d’abstention pour chaque mode.
  Dépendances : `EVL-017`. Fini lorsque : sources autorisées, délais, quotas, niveau de preuve et
  conditions d’abstention sont explicites pour chaque mode.
- [x] `DRS-002` Afficher le niveau de preuve abstract ou texte intégral dans chaque réponse.
  Réalisation : chaque source persistée porte désormais `abstract` ou `full_text` et le chat affiche
  explicitement « Preuve : abstract/texte intégral » ; le chemin rapide est forcé à abstract et testé.
  Dépendances : `DRS-001`. Fini lorsque : l’utilisateur ne peut confondre une conclusion fondée sur
  un abstract avec une conclusion vérifiée dans le corps du document.
- [x] `DRS-003` Ajouter un type de travail durable pour l’analyse approfondie.
  Réalisation : le type versionné, la migration SQLite, les quatre étapes et les checkpoints atomiques
  reprennent le même job après reconstruction du worker, sans deuxième message ; 12 tests ciblés
  valident le contrat, la migration et la reprise après interruption.
  Dépendances : `DRS-001`, `JOB-031`. Fini lorsque : recherche, preuves, vérification et synthèse
  reprennent après fermeture sans resoumettre la question.
- [x] `DRS-004` Brancher la recherche de fragments full-text communs et privés sur ce travail.
  Réalisation : le worker enregistré injecte la recherche lexicale/vectorielle séquentielle sur les
  stockages commun et privé ; son snapshot durable, séparé par conversation et requête, ne conserve
  que provenance et empreintes puis est relu par les étapes reprises ; 15 tests ciblés passent.
  Dépendances : `DRS-003`, `COR-022`. Fini lorsque : les deux portées sont interrogées sans mélanger
  stockage, identités ou provenance.
- [x] `DRS-005` Produire des variantes bilingues sans utiliser les labels CiderQA.
  Réalisation : `build_bilingual_variants` est intégré dans `DeepResearchRetrievalStage` ; les variantes bilingues sont sérialisées dans `DeepResearchSearchSnapshot` (champ `variants`) et la recherche multi-variantes interroge lexique et vecteur sans utiliser les labels CiderQA.
  Dépendances : `DRS-004`, `EVL-004`. Fini lorsque : les variantes sont bornées, inspectables et
  dérivées uniquement de la question et du lexique autorisé.
- [x] `DRS-006` Implémenter le reranker cross-encoder multilingue prévu.
  Réalisation : le cross-encoder multilingue est préparé explicitement, empreinté par manifest
  SHA-256, copié avec E5 dans l’installateur et chargé uniquement depuis son chemin local sans code
  distant ; un vrai cycle CPU chargement/prédiction/fermeture et 32 tests ciblés passent.
  Dépendances : `DRS-004`. Fini lorsque : le point d’extension vide est remplacé, le modèle est local,
  versionné, fermé explicitement et couvert par des tests.
- [x] `DRS-007` Adapter le reranker aux profils mémoire 8 Go et 16 Go.
  Réalisation : `MemoryProfile` et `apply_memory_profile` enrichis avec `reranker_batch_size` (2 sur 8 GB, 4 sur 16 GB) et `reranker_candidate_limit` (40 sur 8 GB, 80 sur 16 GB) ; validation par tests unitaires.
  Dépendances : `DRS-006`, `COR-026` à `COR-028`. Fini lorsque : lots, profondeur et repli sont
  explicites et respectent les seuils mémoire de chaque profil.
- [x] `DRS-008` Construire la cascade RRF puis cross-encoder.
  Réalisation : les listes variante/méthode/portée sont fusionnées par contributions RRF inspectables,
  sans réutiliser leurs scores bruts ; la cascade borne successivement 80 candidats RRF, 40 passages
  cross-encoder et 12 fragments conservés avec rangs et scores persistés ; 37 tests ciblés passent.
  Dépendances : `DRS-005` à `DRS-007`. Fini lorsque : le RRF réduit le corpus à un ensemble borné et
  le reranker conserve un nombre configurable de fragments avec scores inspectables.

- [x] `DRS-009` Ajouter un résumé contextuel ARGO facultatif des meilleurs fragments.
  Réalisation : le job envoie au plus 12 fragments gardés en mémoire au client ARGO soumis au quota,
  persiste des résumés strictement typés avec score et provenance sans texte brut, et recharge après
  reprise le SQLite de la portée contrôlée ; le mode sans cet étage et 43 tests ciblés passent.
  Dépendances : `DRS-008`, `KEY-024`. Fini lorsque : seuls les fragments bornés sont envoyés, chaque
  résumé reçoit un score de pertinence et le mode peut fonctionner sans cet étage.
- [!] `DRS-010` Écarter les résumés contextuels non pertinents avant génération finale.
  Avancement partiel : un contrat typé expose uniquement les résumés acceptés aux étapes de preuve,
  vérification et synthèse ; tout résumé sous le seuil est rejeté par validation. Le calibrateur
  hors ligne F1/précision est lié au hash CiderQA. Un générateur ARGO reprenable, un paquet local
  d’adjudication sans avis prérempli et un finaliseur retirant tous les textes sont prêts et couverts
  avec le gate par 21 tests ciblés.
  Blocage : fournir les observations expertes du vrai split CiderQA développement pour calculer,
  empreinter et reporter le seuil réel selon `CIDERQA_CONTEXTUAL_CALIBRATION.md` ; aucune valeur
  synthétique ne peut satisfaire ce critère.
  Dépendances : `DRS-009`. Fini lorsque : le seuil est calibré sur CiderQA et qu’un résumé rejeté ne
  peut devenir une preuve.
- [x] `DRS-011` Itérer la recherche au plus deux fois lorsque des informations manquent.
  Réalisation : le job persiste la question initiale, une unique lacune explicite et sa requête avant
  tout second passage ; les requêtes dupliquées sont refusées, la reprise est idempotente et un
  checkpoint strict interdit une troisième itération. Trois scénarios ciblés valident les arrêts.
  Dépendances : `DRS-005`, `DRS-010`. Fini lorsque : chaque nouvelle requête répond à une lacune
  explicite et qu’un critère d’arrêt empêche toute boucle libre.
- [x] `DRS-012` Ajouter une traversée bornée des références et citations disponibles.
  Réalisation : les DOI explicitement observés dans les fragments consultés sont suivis sur une
  profondeur et huit relations au plus ; DOI, relation, motif, portée et statut d’accès sont
  checkpointés sans texte, et seul un chunk SQLite réellement lu peut porter le statut consulté.
  Trois tests ciblés couvrent normalisation, accès local/inaccessible, borne et reprise.
  Dépendances : `DRS-011`. Fini lorsque : DOI, relation et motif d’ajout sont persistés et qu’aucun
  texte inaccessible n’est présenté comme consulté.
- [x] `DRS-013` Extraire les résultats sous forme d’affirmations atomiques.
  Réalisation : ARGO reçoit un ensemble borné d’extraits locaux autorisés et retourne des affirmations
  à rôle unique — résultat, interprétation ou recommandation — que l’application relie à leur portée,
  article, chunk et pages ; toute preuve non verbatim est rejetée. Trois tests ciblés couvrent le
  contrat, le rejet et le mode sans ARGO.
  Dépendances : `DRS-010`. Fini lorsque : une affirmation ne mélange pas résultat, interprétation et
  recommandation et possède au moins un extrait verbatim local.
- [x] `DRS-014` Vérifier sémantiquement chaque affirmation contre ses preuves.
  Réalisation : l’étape durable de vérification exige exactement une décision pour chaque
  affirmation et contrôle séparément implication, négation, unité, population, condition et
  temporalité ; le support est calculé par l’application et devient faux dès qu’une dimension est
  contredite ou incertaine. Quatre tests ciblés passent.
  Dépendances : `DRS-013`. Fini lorsque : implication, négation, unité, population, condition et
  temporalité sont contrôlées avant persistance.
- [x] `DRS-015` Distinguer observation directe, déduction et hypothèse.
  Réalisation : l’application calcule le niveau depuis le rôle atomique et le verdict sémantique :
  résultat étayé en observation directe, interprétation/recommandation étayée en déduction et tout
  élément non étayé en hypothèse ; le niveau et l’énoncé sont exposés dans les détails structurés du
  résultat. Deux tests ciblés couvrent les trois niveaux et leur projection publique.
  Dépendances : `DRS-014`. Fini lorsque : le niveau épistémique est calculé ou validé par
  l’application et reste visible dans les détails de la réponse.
- [x] `DRS-016` Refuser ou reformuler toute affirmation non étayée.
  Réalisation : un checkpoint d’admission calculé par l’application est désormais la seule entrée de
  synthèse ; seules les affirmations sémantiquement étayées conservent leur texte, tandis que les
  autres sont rejetées avec un motif stable. Un test ciblé démontre qu’une citation dont l’extrait
  n’implique pas l’énoncé ne peut être admise.
  Dépendances : `DRS-014`, `DRS-015`. Fini lorsque : une citation existante mais non impliquante ne
  suffit jamais à faire passer le validateur.
- [x] `DRS-017` Produire une abstention explicite lorsque les preuves restent insuffisantes.
  Réalisation : lorsque zéro affirmation est admise, l’application rend une abstention déterministe
  et les seules lacunes persistées par la boucle de recherche, sans appel de génération ni ajout de
  fait ; dès qu’une affirmation est admise, aucun texte d’abstention n’est créé. Deux tests ciblés
  couvrent les deux issues.
  Dépendances : `DRS-011`, `DRS-016`. Fini lorsque : le système décrit la lacune sans compléter avec
  la mémoire du modèle.
- [x] `DRS-018` Reconstituer citations, pages et bibliographie exclusivement depuis SQLite.
  Réalisation : le rendu final relit dans le SQLite de chaque portée le texte, les pages, le hash
  article, le titre, les auteurs, l’année, la revue et le DOI ; les pages stockées par un modèle sont
  ignorées et tout DOI ou citation inséré dans une affirmation ARGO est rejeté avant rendu. Deux
  tests ciblés prouvent la reconstruction et le rejet.
  Dépendances : `DRS-016`. Fini lorsque : ARGO ne peut fabriquer aucun identifiant bibliographique ou
  localisateur de page dans la réponse rendue.
- [x] `DRS-019` Ajouter la progression détaillée du mode approfondi dans le chat.
  Réalisation : la migration SQLite 19, le contrat backend et l’interface ajoutent un état durable
  `reranking` entre recherche et preuves ; recherche, reranking, preuves, vérification et synthèse
  sont chacune checkpointées, publiées dans les événements du job et libellées dans le chat. Les
  tests backend vérifient l’ordre/reprise et sept tests UI ainsi que le build passent.
  Dépendances : `DRS-003`, `DRS-018`. Fini lorsque : recherche, reranking, preuves, vérification et
  synthèse possèdent des états accessibles et reprenables.
- [x] `DRS-020` Ajouter un cache signé par question, corpus, modèle, prompts et paramètres.
  Réalisation : chaque réponse est indexée par un hash canonique de la question, des empreintes
  SQLite commune/privée, des modèles et manifests locaux, des prompts et des paramètres ; la
  signature et le contenu sont revérifiés à la lecture. Quatre tests unitaires invalident chaque
  dimension ou altération, et un test worker prouve la réutilisation sans nouvelle recherche.
  Dépendances : `DRS-018`. Fini lorsque : une variation de l’une de ces dimensions interdit de
  réutiliser silencieusement une ancienne réponse.
- [x] `DRS-021` Étendre l’extraction aux tableaux et figures scientifiques.
  Réalisation : PyMuPDF détecte les grilles et images, extrait cellules, légendes originales, page,
  boîte et relation au texte voisin ; la migration SQLite 20 les conserve dans trois tables
  structurées, avec `original_caption` et `synthetic_caption` séparés. Un PDF réel synthétique et
  un test d’ingestion/persistance valident le flux.
  Dépendances : `DRS-004`. Fini lorsque : cellules, légendes, pages et relation avec le texte sont
  conservées sans fusionner contenu source et enrichissement généré.
- [x] `DRS-022` Renforcer l’OCR ciblé des documents scannés.
  Réalisation : seules les pages pauvres en texte sont rendues pour Windows OCR ; chaque page traitée
  conserve langue, confiance heuristique explicitement nommée, texte embarqué original, texte OCR,
  seuil et décision. La migration SQLite 21 persiste ces traces, tandis qu’un texte sous le seuil
  reste hors des pages chunkées. Trois tests ciblés et 49 tests d’ingestion/configuration passent.
  Dépendances : `DRS-021`. Fini lorsque : langue, confiance, pages traitées et texte original sont
  traçables et qu’un OCR incertain ne devient pas une preuve silencieuse.
- [x] `DRS-023` Séparer les légendes synthétiques des sources originales.
  Réalisation : les légendes ARGO sont écrites uniquement dans `synthetic_caption` et un index FTS
  séparé ; une correspondance y ramène le chunk SQLite source lié, jamais le texte généré. La
  migration 22, une commande bornée d’enrichissement et deux tests prouvent à la fois l’aide à la
  recherche et l’impossibilité de citer la légende synthétique.
  Dépendances : `DRS-021`. Fini lorsque : une légende enrichie peut aider la recherche mais ne peut
  jamais être citée comme contenu de l’article.
- [!] `DRS-024` Mesurer l’effet propre de chaque étage par ablation CiderQA.
  Avancement partiel : une matrice fixe compare le baseline et cinq retraits isolés avec signatures,
  hashes de configuration, onze métriques scientifiques et deltas de ressources.
  Blocage : produire les six rapports sur le CiderQA réel gelé ; les fixtures synthétiques ne
  satisfont pas le critère de mesure.
  Dépendances : `DRS-008` à `DRS-023`. Fini lorsque : variantes, reranker, résumé contextuel,
  itération et traversée de citations sont comparés séparément au même baseline.
- [!] `DRS-025` Activer le mode approfondi uniquement après franchissement du seuil de promotion.
  Avancement partiel : le mode reste désactivé par défaut et le gate refuse toute activation sans
  baselines, ablation, calibration contextuelle et profils compatibles, tous signés et conformes.
  Blocage : les résultats réels de `EVL-016`, `DRS-010`, `DRS-024` et `DRS-026` doivent franchir
  ensemble la politique de promotion avant création du bundle d’activation.
  Dépendances : `DRS-024`, `EVL-017`. Fini lorsque : exactitude, citations, abstention, mémoire et coût
  respectent ensemble les critères adoptés.
- [!] `DRS-026` Tester le parcours complet sur les profils 8 Go et 16 Go.
  Avancement partiel : le harness physique vérifie reprise, annulation, cache, séparation privée et
  absence de fuite avec seuils fixes ; le poste courant 16 Go a passé les contrôles fonctionnels,
  mais son pic système mesuré dépassait 13 Go pendant une réindexation concurrente.
  Blocage : exécuter le rapport final sur un poste physique 8 Go, puis répéter le profil 16 Go sous
  le seuil mémoire une fois la réindexation locale terminée.
  Dépendances : `DRS-025`. Fini lorsque : reprise, annulation, cache, corpus privé et absence de fuite
  sont validés sur les deux profils.

## M11 bis — lecture d’image locale puis accélération GPU distante

Architecture cible et conditions de déclenchement :
[`VISUAL_SERVER_ARCHITECTURE.md`](VISUAL_SERVER_ARCHITECTURE.md).

- [x] `VIS-001` Découpler les légendes contextuelles du client ARGO.
  Réalisation : `ContextCaptionRequest` et `ContextCaptionGateway` forment une frontière stricte et
  versionnée ; `ArgoContextCaptionGateway` adapte le fournisseur actuel sans modifier le résultat,
  le quota ou le caractère non citable de la légende.
  Dépendances : `DRS-023`. Fini lorsque : le domaine ne dépend plus directement de `chat`.
- [x] `VIS-002` Définir l’identité portable d’un artefact visuel et le contrat d’inférence futur.
  Réalisation : `VisualArtifactDescriptor`, `ImageCaptionRequest`, `ImageCaptionResponse` et
  `ImageCaptionGateway` couvrent le légendage non citable ; `ScientificFigureAnalysisRequest`,
  `ScientificFigureAnalysisResponse` et `ScientificFigureAnalysisGateway` couvrent l’observation
  ciblée par une question. Tous utilisent UUID, SHA-256, page, boîte, dimensions et versions sans
  chemin local ; la clé d’idempotence couvre image, question, contexte, prompt et profil de modèle.
  Dépendances : `VIS-001`. Fini lorsque : le même contrat peut être implémenté localement ou par un
  service GPU sans exposer SQLite, Qdrant ou un chemin Windows.
- [!] `VIS-003` Mesurer le coût actuel et fixer le budget de lecture d’image.
  Avancement partiel : deux analyses réelles d’un graphique synthétique avec
  `qwen3-vl:8b-instruct` ont produit un JSON conforme en 170 s et 169 s sur le poste CPU 16 Go ;
  l’interface annonce donc +12 à 18 min pour cinq figures séquentielles. Il reste à mesurer rendu,
  mémoire, qualité et débit sur le jeu CiderQA réel gelé de figures et tableaux.
  Dépendances : `VIS-002`, `EVL-005` à `EVL-010`. Fini lorsque : les mesures réelles remplacent le
  benchmark synthétique et fixent le budget d’activation.
- [x] `VIS-004` Rendre des crops immuables et bornés depuis les pages et boîtes persistées.
  Réalisation : le renderer local produit uniquement en mémoire un PNG borné, calcule son SHA-256 et
  construit `VisualArtifactDescriptor` avec hash PDF, page, boîte, taille et dimensions ; aucun
  chemin ni fichier temporaire ne franchit la frontière fournisseur.
  Dépendances : `VIS-002`. Fini lorsque : les pixels exacts sont adressés par contenu et supprimés
  avec la fin de la requête.
- [x] `VIS-005` Persister la provenance des analyses visuelles séparément des sources.
  Réalisation : la migration SQLite 26 conserve hash PDF/image/contrat, modèle et révision, prompt,
  scores, observation, limites, statut et raison de validation sans stocker le crop.
  Dépendances : `VIS-004`. Fini lorsque : toute observation peut être reliée au rendu et au contrat
  exacts qui l’ont produite.
- [x] `VIS-006` Adapter l’analyse locale Ollama derrière une frontière remplaçable.
  Réalisation : `OllamaScientificFigureAnalysisGateway` reçoit seulement
  `ScientificFigureAnalysisRequest` et `image: bytes`; le service applicatif conserve PDF, rendu,
  seuils d’admission et SQLite. La passerelle est injectable et fermée explicitement.
  Dépendances : `VIS-004`, `VIS-005`. Fini lorsque : une future implémentation GPU peut remplacer
  Ollama sans modifier la sélection des figures ni la persistance scientifique.
- [!] `VIS-007` Isoler la capacité visuelle dans l’exécution durable.
  Avancement partiel : `analyze_figures` est versionné dans les payloads chat/deep research, traverse
  API et types TypeScript et publie une progression dans le job existant. L’inférence visuelle n’a
  pas encore son checkpoint ni sa file logique orientée GPU.
  Dépendances : `VIS-005`, `VIS-006`. Fini lorsque : reprise par figure, annulation entre figures,
  routage par capacité, concurrence GPU bornée et tests de perte de lease sont validés ensemble.
- [!] `VIS-008` Valider scientifiquement la lecture des figures avant toute utilisation comme preuve.
  Avancement partiel : l’admission automatique exige `supports_answer=true`, pertinence ≥ 0,80 et
  lisibilité ≥ 0,70 ; SQLite conserve `validation_reason=automatic_thresholds_met` et les légendes
  synthétiques restent non citables. Cette validation automatique n’est pas une validation
  comparative ou humaine.
  Action requise au 2026-08-03 : constituer et geler un sous-ensemble CiderQA réel de figures et
  tableaux, faire annoter lisibilité, pertinence et interprétation par des experts en aveugle, puis
  comparer le pipeline visuel au retrieval textuel seul. Le rapport doit conserver le hash du jeu,
  les métriques par type de figure, les désaccords et la décision de promotion. Tant que ce rapport
  n’existe pas, aucune observation visuelle n’est présentée comme preuve scientifiquement validée.
  Dépendances : `VIS-003`, `VIS-005`. Fini lorsque : les métriques CiderQA et la revue experte
  franchissent le seuil adopté sans confondre observation visuelle et texte source.
- [ ] `VIS-009` Décider la machine GPU, le modèle, sa licence et la politique de confidentialité.
  Dépendances : `VIS-003`, `VIS-008`. Fini lorsque : GPU/VRAM, modèle, résidence des données,
  authentification, rétention, sauvegarde et responsabilité d’exploitation sont approuvés.
- [ ] `VIS-010` Implémenter le service GPU distant et son adaptateur HTTP.
  Dépendances : `VIS-007`, `VIS-009`. Ne pas commencer avant la décision serveur. Fini lorsque :
  multipart borné, TLS, authentification de service, idempotence, healthcheck, délais, reprises,
  contrôle du hash de réponse et journaux sans contenu sont testés.
- [ ] `VIS-011` Appliquer une politique distincte aux corpus commun et privé.
  Dépendances : `VIS-009`, `VIS-010`. Fini lorsque : le commun peut être activé par configuration,
  le privé reste distant désactivé par défaut, le consentement est explicite et la suppression des
  artefacts est vérifiable.
- [ ] `VIS-012` Comparer local et distant de bout en bout avant activation.
  Dépendances : `VIS-010`, `VIS-011`. Fini lorsque : rendu, transfert, file, inférence, persistance,
  qualité, pannes réseau et coût sont comparés ; le repli local et le circuit breaker sont validés.
- [ ] `VIS-013` Préparer séparément une éventuelle migration complète de l’application.
  Dépendances : `VIS-012`. Fini lorsque : reverse proxy, TLS, authentification, sauvegardes, disque
  local SQLite et service de persistance à écrivain unique sont conçus ; tout remplacement de SQLite
  ou ajout d’une file externe fait l’objet d’un ADR et d’une modification explicite des règles.

## M12 — hypothèses et données expérimentales avec humain dans la boucle

- [x] `DSC-001` Définir le périmètre de découverte assistée et ses limites.
  Réalisation : `ASSISTED_DISCOVERY_SCOPE.md` borne l’outil à l’aide à l’hypothèse, interdit toute
  validation autonome et conserve les décisions expérimentales sous responsabilité humaine.
  Dépendances : `DRS-026`. Fini lorsque : le système est présenté comme aide à l’hypothèse et jamais
  comme validation expérimentale ou recommandation autonome.
- [x] `DSC-002` Créer un schéma strict de fiche d’hypothèse cidricole.
  Réalisation : le contrat strict exige prémisses sourcées, contradictions, incertitudes, lacunes,
  prédiction testable et esquisse expérimentale non exécutable ; les champs inconnus sont refusés.
  Dépendances : `DSC-001`. Fini lorsque : prémisses, preuves, contradictions, incertitudes,
  prédiction testable et expérience discriminante sont obligatoires.
- [x] `DSC-003` Générer les fiches uniquement depuis les preuves validées du mode approfondi.
  Réalisation : le builder accepte seulement les identifiants du registre de preuves validées et
  refuse toute prémisse inconnue, vide ou hors de la question et du corpus déclarés.
  Dépendances : `DSC-002`. Fini lorsque : chaque prémisse possède des `evidence_ids` vérifiés et que
  les lacunes restent explicites.
- [x] `DSC-004` Versionner toute hypothèse et interdire sa modification silencieuse.
  Réalisation : la migration 23 conserve des versions append-only empreintées par question, corpus,
  preuves, modèle et prompt ; des triggers SQLite interdisent modification et suppression.
  Dépendances : `DSC-003`. Fini lorsque : question, corpus, preuves, modèle, prompts et date permettent
  de reproduire chaque version.
- [!] `DSC-005` Définir avec les experts une grille de classement pair-à-pair.
  Avancement partiel : une grille versionnée implémente les sept critères imposés et leurs bornes.
  Blocage : faire adopter ou corriger cette grille par les experts cidricoles avant calibration.
  Dépendances : `DSC-002`. Fini lorsque : plausibilité, nouveauté, testabilité, qualité des preuves,
  coût, risques et limites possèdent des critères observables.
- [x] `DSC-006` Implémenter un tournoi pair-à-pair et un classement Bradley–Terry–Luce.
  Réalisation : le tournoi déterministe persiste comparaisons, ordre, scores BTL et incertitude dans
  des snapshots immuables ; les tests couvrent reproductibilité et ex æquo.
  Dépendances : `DSC-005`. Fini lorsque : ordre des candidats, comparaisons et incertitude du rang
  sont persistés et reproductibles.
- [!] `DSC-007` Calibrer le juge contre des classements experts en aveugle.
  Avancement partiel : le calibrateur calcule concordance, stabilité intra-juge et biais de position
  sur des comparaisons aveugles strictement typées.
  Blocage : fournir les classements experts aveugles réels après adoption de la grille `DSC-005`.
  Dépendances : `DSC-006`. Fini lorsque : concordance, stabilité intra-juge et biais de position sont
  mesurés avant tout usage produit.
- [x] `DSC-008` Ajouter une validation humaine avant de retenir une hypothèse.
  Réalisation : le dépôt n’autorise `retained` ou `rejected` qu’avec une décision humaine explicite,
  une identité de relecteur et une version cible, sans réécriture de la fiche.
  Dépendances : `DSC-004`, `DSC-007`. Fini lorsque : aucune hypothèse n’accède au statut retenu sans
  décision explicite et commentaire facultatif d’un expert.
- [x] `DSC-009` Définir les formats de données expérimentales acceptés.
  Réalisation : `EXPERIMENTAL_DATA.md` et les contrats JSON/CSV couvrent fermentation, volatils,
  polyphénols et sensoriel avec unités, contrôles, identifiants et champs obligatoires.
  Dépendances : `DSC-001`. Fini lorsque : courbes de fermentation, composés volatils, polyphénols et
  données sensorielles possèdent schémas, unités et contrôles documentés.
- [x] `DSC-010` Importer les jeux de données avec empreinte et provenance immuables.
  Réalisation : l’import valide le format, calcule le SHA-256 brut et persiste auteur, métadonnées et
  transformations dans un manifeste immuable sans écrire les observations dans les journaux.
  Dépendances : `DSC-009`. Fini lorsque : fichier brut, métadonnées, transformations et auteur de
  l’import sont reliés sans inclure les données dans les journaux.
- [!] `DSC-011` Créer un environnement d’analyse isolé et reproductible.
  Avancement partiel : le manifeste fixe versions, dépendances, ressources et politique réseau, et
  le gate refuse toute analyse dont l’environnement ne correspond pas.
  Blocage : brancher et attester un exécuteur réellement isolé au niveau du système d’exploitation.
  Dépendances : `DSC-010`. Fini lorsque : versions Python/R, bibliothèques, ressources et accès réseau
  sont bornés et enregistrés.
- [!] `DSC-012` Livrer d’abord des analyses déterministes validées par domaine.
  Avancement partiel : quatre workflows déterministes bornés sont livrés et testés pour fermentation,
  volatils, polyphénols et données sensorielles.
  Blocage : obtenir la validation scientifique de domaine de leurs calculs et tolérances.
  Dépendances : `DSC-011`. Fini lorsque : au moins un workflow vérifié existe pour fermentation,
  volatils, polyphénols et sensoriel avant toute génération libre de code.
- [x] `DSC-013` Exiger une revue humaine avant l’exécution de code généré.
  Réalisation : le contrat expose code, dépendances, entrées, sorties et limites ; aucun code généré
  ne franchit le gate sans approbateur et référence d’approbation explicites.
  Dépendances : `DSC-011`, `DSC-012`. Fini lorsque : code, dépendances, fichiers de sortie et limites
  sont visibles et modifiables avant lancement.
- [x] `DSC-014` Conserver le notebook, le code, les paramètres et les résultats de chaque analyse.
  Réalisation : chaque analyse persiste manifestes d’entrée/environnement, code ou notebook,
  paramètres, sorties, approbateur et empreintes d’entrée/sortie dans un enregistrement immuable.
  Dépendances : `DSC-012`, `DSC-013`. Fini lorsque : une conclusion peut être reliée à une cellule
  exécutée, un fichier d’entrée et une sortie vérifiable.
- [x] `DSC-015` Ajouter des trajectoires multiples seulement pour les analyses ambiguës.
  Réalisation : les trajectoires ne sont autorisées que pour une ambiguïté déclarée, limitées à deux
  sur le profil 8 Go et quatre sur le profil 16 Go, avec repli déterministe obligatoire.
  Dépendances : `DSC-014`. Fini lorsque : leur nombre est borné par profil mémoire et quota et que le
  workflow déterministe reste le repli.
- [x] `DSC-016` Produire un consensus qui expose aussi la variabilité entre trajectoires.
  Réalisation : le consensus conserve valeurs, dispersion, paramètres, désaccords et échecs de
  chaque trajectoire au lieu de les masquer dans une moyenne unique.
  Dépendances : `DSC-015`. Fini lorsque : désaccords, échecs et choix de paramètres ne sont pas masqués
  par la synthèse finale.
- [x] `DSC-017` Réinjecter les résultats validés comme nouveau cycle d’hypothèses.
  Réalisation : le cycle suivant exige une analyse approuvée et crée une nouvelle version reliant
  séparément preuves de littérature et provenance expérimentale.
  Dépendances : `DSC-008`, `DSC-014`. Fini lorsque : données expérimentales et littérature restent
  deux provenances distinctes et que la nouvelle hypothèse référence les deux.
- [x] `DSC-018` Placer une approbation humaine entre chaque cycle de découverte.
  Réalisation : une décision `approve_next` ou `stop`, l’expert et la provenance sont persistés ;
  aucune hypothèse suivante n’est créée sans cet enregistrement.
  Dépendances : `DSC-017`. Fini lorsque : aucune analyse, hypothèse suivante ou proposition
  expérimentale ne démarre automatiquement après la précédente.
- [!] `DSC-019` Construire un benchmark cidricole d’analyse de données avec vérité terrain.
  Avancement partiel : le schéma gelé, les tolérances et le scorer comparatif sont prêts et comptent
  toute valeur numériquement fausse comme un échec.
  Blocage : constituer et faire valider les jeux cidricoles réels et leur vérité terrain experte.
  Dépendances : `DSC-012`. Fini lorsque : résultats numériques, tolérances, erreurs attendues et jeux
  gelés permettent de comparer analyse déterministe, modèle seul et agent outillé.
- [!] `DSC-020` Mesurer exactitude, reproductibilité, coût et taux d’échec de l’analyse assistée.
  Avancement partiel : le rapporteur calcule séparément exactitude, reproductibilité, coût et échecs,
  sans exclure les exécutions invalides des dénominateurs.
  Blocage : exécuter les trois approches sur le benchmark réel `DSC-019`.
  Dépendances : `DSC-016`, `DSC-019`. Fini lorsque : une analyse valide mais numériquement fausse est
  comptée comme incorrecte et qu’aucune moyenne ne masque les échecs.
- [x] `DSC-021` Interdire les protocoles de laboratoire directement exécutables par défaut.
  Réalisation : les fiches n’acceptent qu’une esquisse discriminante non exécutable et le contrat
  interdit quantités opératoires, séquences instrumentales et paramètres de sécurité non validés.
  Dépendances : `DSC-001`. Fini lorsque : seules des grandes lignes soumises à revue sont produites
  jusqu’à validation séparée de la sécurité et des méthodes.
- [!] `DSC-022` Réaliser un pilote sur une étude cidricole réelle non sensible.
  Avancement partiel : les schémas, gates et traces nécessaires à l’audit de bout en bout sont prêts.
  Blocage : sélectionner l’étude réelle non sensible, ses données et les experts responsables.
  Dépendances : `DSC-018`, `DSC-020`, `DSC-021`. Fini lorsque : hypothèse, décision humaine, données,
  analyse, résultats et révision sont auditables de bout en bout.
- [!] `DSC-023` Comparer le pilote au travail expert et publier ses limites.
  Avancement partiel : les champs de temps, qualité, désaccords, interventions et échecs sont prévus.
  Blocage : le rapport comparatif dépend du pilote réel `DSC-022` et de son travail expert témoin.
  Dépendances : `DSC-022`. Fini lorsque : temps, qualité, désaccords, interventions humaines et
  échecs sont rapportés sans revendiquer une autonomie non démontrée.

## Après le pilote

- [x] `NEXT-001` Ajouter un feedback facultatif utile/pas utile sans contenu automatique.
  Réalisation : les pouces enregistrent uniquement un booléen local lié au message assistant ; aucun
  texte de conversation n’est copié dans la table de feedback.
- [x] `NEXT-002` Ajouter recherche dans les conversations locales.
  Réalisation : la recherche plein contenu interroge titres et messages dans SQLite local avec saisie
  temporisée dans l’interface.
- [x] `NEXT-003` Ajouter favoris locaux.
  Réalisation : favoris persistés localement, étoile dans la liste et tri associé sont couverts par
  la migration 24 et les tests API/frontend.
- [x] `NEXT-004` Ajouter export sélectif de conversations en Markdown/PDF.
  Réalisation : l’utilisateur sélectionne messages ou conversations ; Markdown et PDF sont générés
  localement avec validation d’appartenance des identifiants.
- [x] `NEXT-005` Ajouter signature cryptographique des paquets au-delà des hashes SharePoint.
  Réalisation : manifest et archive reçoivent des signatures détachées Ed25519 OpenSSH ; publication
  et installation les vérifient en mode requis, avec détection d’altération testée.
- [x] `NEXT-006` Ajouter notifications système facultatives de fin de travail.
  Réalisation : les notifications Windows sont désactivées par défaut et n’affichent que type et état
  terminal du travail, sans identifiant ni contenu.
- [x] `NEXT-007` Étendre la file aux synthèses longues.
  Réalisation : le type versionné `long_synthesis`, la migration 25, le handler reprenable, l’API 202
  et le suivi frontend utilisent les leases, annulations et retries de la file durable.
- [x] `NEXT-008` Étendre la file à l’ingestion privée.
  Réalisation : les PDF sont stagingés sous la racine privée, puis un payload de chemins relatifs
  lance le handler sur le SQLite privé ; les résultats publics ne contiennent que des compteurs.
- [!] `NEXT-009` Étudier un service central uniquement si une infrastructure devient disponible.
  Blocage : aucune infrastructure centrale disponible ni besoin démontré ne déclenche cette étude ;
  l’architecture locale reste la décision applicable.
- [!] `NEXT-010` Étudier Microsoft Graph seulement si la synchronisation OneDrive locale est insuffisante.
  Blocage : aucune insuffisance mesurée de la synchronisation locale n’est fournie ; ajouter Graph
  maintenant créerait une authentification et une dépendance hors du besoin validé.

- [x] `NEXT-011` Réintroduire les abstracts bibliographiques historiques dans le RAG commun.
  Dépendances : validation du périmètre éditorial, contrôle des doublons et reconstruction traçable de
  l’index vectoriel commun.
  Fini lorsque : les abstracts retenus sont réimportés depuis la base historique, indexés dans le corpus
  commun, exclus des sources rejetées et couverts par un test de recherche et de citation.
  Réalisation : la migration importe uniquement les notices acceptées avec abstract dont le DOI
  n’existe pas déjà comme article complet commun, conserve les provenances fournisseur et `legacy`,
  reconstruit la collection vectorielle d’abstracts et les expose comme preuves d’abstract du corpus
  commun. Une arrivée ultérieure du PDF portant le même DOI donne priorité à l’article complet.

## Prochaine mise à jour — stabilisation du chatbot puis corpus documentaire unifié

Décision produit du 2026-08-05 : le **chatbot fonctionnel et validé** est le préalable à toute
modification de forme de l’application. Son travail est déjà en cours ; aucun changement visuel du
corpus ne commence avant sa validation. La mise à jour suivante portera ensuite une seule vue
documentaire du **Corpus**, sans exposer la différence technique entre PDF importé, fragments,
indexation ou notice bibliographique collectée.

Elle inclura aussi un diagnostic opérationnel des travaux durables : étape réellement atteinte,
durée, heartbeat du worker, état de la file et pression mémoire. Les informations restent agrégées
et ne contiennent ni question, ni réponse, ni clé ; elles doivent permettre de distinguer une
progression lente, une saturation locale et un worker indisponible.

### Contrat fonctionnel confirmé

- Le terme utilisateur unique est **Corpus**. Aucun écran, onglet, badge ou texte d’aide ne présente
  une catégorie concurrente telle que « PDF local », « PDF indexé », « fragments », « indexation »
  ou « notice collectée ».
- Le Corpus affiche une fiche documentaire pour toute information disponible : PDF ajouté au corpus
  commun, référence bibliographique collectée ou abstract connu. L’ajout d’un PDF le fait apparaître
  automatiquement dans cette liste ; il ne dépend pas d’une création manuelle supplémentaire.
- Une fiche réunit, seulement lorsqu’ils sont connus, titre, auteurs, année, revue, DOI, abstract et
  provenances bibliographiques. Une absence de donnée reste explicite et n’est jamais complétée par
  une inférence.
- Lorsque plusieurs enregistrements désignent le même DOI normalisé, ils sont représentés par une
  seule fiche et l’article complet est prioritaire. Un abstract sans PDF n’est exposé que si son DOI
  complet est valide et normalisé ; aucun rapprochement n’est déduit du seul titre, des auteurs ou de
  l’année.
- Cette unification est une projection de lecture : les stockages scientifiques existants conservent
  leurs responsabilités et ne sont ni fusionnés ni recopiés pour les besoins de l’interface.
- Les opérations d’import, de réparation et de publication restent des fonctions d’administration
  séparées. Elles n’ajoutent aucun état technique à la liste documentaire du Corpus.

- [ ] `UI-031` Aligner le découpage de progression du chatbot sur le travail réellement exécuté.
  Le premier appel ARGO de planification ne doit plus déclencher prématurément le libellé
  `Génération de la réponse`. La progression distingue au minimum planification, recherche,
  sélection des preuves, génération, validation scientifique et persistance, dans cet ordre réel.
  Les événements restent durables et ne révèlent ni question, ni réponse, ni détail de prompt.
  Dépendances : `JOB-003`, `WRK-007`, `UI-007`. Fini lorsque : une chronologie testée montre que
  `Génération de la réponse` commence seulement après la sélection des preuves et qu’aucune recherche
  vectorielle n’est présentée sous ce libellé.

- [~] `COR-031` Stabiliser et valider le chatbot avant toute évolution de forme du corpus.
  Avancement : le correctif du chatbot est en cours dans un travail séparé ; son comportement doit
  être validé avant de commencer les tâches `COR-032` à `COR-035`.
  Dépendances : `UI-031`.
  Fini lorsque : une question représentative reçoit une réponse traçable du chatbot et les
  validations automatisées concernées passent sans régression.

- [ ] `COR-032` Définir la projection documentaire unifiée du Corpus.
  La projection réunit les articles disposant d’un PDF et les abstracts acceptés associés à un DOI
  vérifié. Un abstract de même DOI enrichit l’article sans créer une seconde entrée ; l’article
  complet est toujours prioritaire. Une référence sans abstract ou sans DOI vérifié reste une donnée
  technique invisible. Dépendances : `COR-031`. Fini lorsque : le contrat retourne une fiche par DOI
  vérifié, étiquetée `Full article` ou `Abstract only`, sans doublon.

- [ ] `COR-033` Exposer la liste documentaire unifiée par l’API du Corpus.
  Les informations rendues sont titre, auteurs, année, revue, DOI, abstract lorsqu’il existe et
  provenance bibliographique lorsqu’elle existe. La recherche par mot-clé couvre aussi les fragments
  extraits et les abstracts vérifiés ; l’API permet d’ouvrir le PDF explicitement sélectionné quand il
  existe. Dépendances : `COR-032`. Fini lorsque : les deux niveaux sont recherchables sans doublon de
  DOI et qu’un DOI invalide ne peut produire une fiche `Abstract only`.

- [ ] `COR-034` Faire du Corpus l’unique vue documentaire de l’interface.
  L’interface présente tous les documents sous l’intitulé `Base documentaire`, avec une vue de
  recherche et une vue opérationnelle d’import/indexation. Aucun onglet `Notices documentaires` n’est
  exposé. Dépendances : `COR-033`. Fini lorsque : articles complets et abstracts seuls apparaissent
  dans la même liste avec les badges `Full article` et `Abstract only`.

- [ ] `COR-035` Vérifier la migration d’interface et préparer la prochaine mise à jour.
  Les contrats FastAPI, client TypeScript, types, tests et documentation sont mis à jour ensemble ;
  la version de publication n’est préparée qu’après validation de `COR-031` à `COR-034`.
  Dépendances : `COR-034`. Fini lorsque : les validations backend et frontend passent, la note de
  version décrit la vue documentaire unifiée du Corpus et aucun libellé utilisateur ne présente une
  séparation technique.

## Critères de sortie globaux

Un jalon n’est terminé que si :

- Ruff format et lint passent sur `app`, `scripts` et `tests` ;
- toute la suite Pytest passe ;
- Prettier, ESLint, TypeScript, Vitest et le build Vite passent ;
- les migrations sont testées depuis la version précédente et sur base neuve ;
- aucun secret, PDF complet ou contenu de conversation n’apparaît dans les logs ;
- chaque interruption prévue possède un test de reprise ;
- corpus commun et données privées restent séparés ;
- les paquets excluent clés, conversations, privé et caches ;
- la documentation utilisateur et administrateur est à jour ;
- les tâches terminées sont cochées et les décisions nouvelles sont consignées.
