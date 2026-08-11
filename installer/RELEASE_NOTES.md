# CiderScholar 0.2.9

- Réduit la mémoire de construction, de signature et de publication en traitant les archives de
  corpus et les installateurs Windows en flux plutôt qu'en lectures intégrales.
- Centralise les migrations de compatibilité du niveau d'effort de réponse et les libellés de
  progression, avec des tests de non-régression partagés.
- Supprime les composants frontend sans consommateur, extrait le fil de conversation et conserve
  des contrats TypeScript publics stables.
- Intègre l'audit de préparation de la démonstration sans modifier les règles de preuve, les
  données scientifiques ni les données utilisateur persistées.

# CiderScholar 0.2.8

- Ajoute une intensité de réponse adaptée à la demande, avec conservation explicite du choix
  dans les travaux durables et l'interface du chatbot.
- Renforce la vérification des affirmations numériques, la traçabilité de la génération et les
  replis scientifiques lorsque les preuves disponibles sont insuffisantes.
- Lie les index vectoriels à un manifeste de génération afin de détecter les index obsolètes ou
  incompatibles avant la recherche.
- Intègre les corrections locales du retrieval, du classement, du Deep Research, de la sauvegarde
  du corpus et de leur couverture de tests depuis la 0.2.7.

# CiderScholar 0.2.7

- Rend les campagnes d'évaluation P0/P1/P2 séquentielles et auditables : une conversation
  immuable par cellule, un seul job actif et une identité question/profil persistée.
- Garantit une sortie visible pour chaque question : réponse validée, repli extractif,
  diagnostic explicite ou notice terminale en cas d'échec et d'annulation.
- Supprime de la réponse visible du chatbot le bloc redondant `Définition retenue` qui
  reformulait la question avant la synthèse.
- Décompose la progression réelle d'une réponse en planification, recherche, enrichissement,
  classement, sélection des preuves, couverture, figures, génération finale, validation et
  enregistrement. Le premier appel ARGO n'est plus affiché comme une génération finale.
- Intègre les corrections et consolidations applicatives présentes dans le workspace depuis la
  0.2.6, avec migration SQLite 29 rétrocompatible et conservation des données utilisateur.

# CiderScholar 0.2.6

- Ajoute un diagnostic local des travaux durables : état et fraîcheur du worker, étapes et durée des
  travaux actifs, ainsi que la mémoire de l’API, du worker et du système. Il ne révèle ni contenu des
  questions, ni réponses, ni clés, ni identifiants de processus.
- La vue documentaire unifiée `Corpus` reste volontairement différée jusqu’à la validation d’une
  réponse chatbot traçable, conformément à la roadmap.

# CiderScholar 0.2.5

- Une interruption de sécurité après un upsert Qdrant conserve désormais le lot déjà durablement
  écrit comme `indexed`, au lieu de le marquer à tort `failed` dans SQLite.

# CiderScholar 0.2.4

- Le runtime Windows suit les migrations de schéma 27 et 28 du corpus commun, afin qu'une
  installation mise à jour puisse rouvrir la base sans perdre les conversations ni les preuves.
- Les acquisitions de texte intégral natives sont persistées dans le corpus commun avant leur
  éventuelle indexation, avec leur provenance et leur statut de téléchargement.

# CiderScholar 0.2.3

- La sélection des preuves couvre séparément les axes scientifiques, filtre leur pertinence
  sémantique avec ARGO et autorise une unique recherche complémentaire ciblée.
- Le chatbot peut analyser explicitement jusqu'à cinq figures locales après le retrieval textuel,
  avec un modèle Ollama remplaçable et des seuils stricts de pertinence et de lisibilité.
- Les observations visuelles admises conservent leur provenance SQLite sans stocker les crops et
  restent distinctes des citations textuelles.
- L'option de lecture des figures traverse les travaux durables, Deep Research et l'interface, sans
  activer de service GPU distant ni envoyer de pixels à ARGO.

# CiderScholar 0.2.2

- La recherche locale utilise en priorité les passages du texte intégral avec leurs pages.
- Les requêtes scientifiques conservent les taxons comme ancres de recherche.
- En l’absence de matrice exacte, le repli suit une hiérarchie explicite : jus standard ou
  concentré, puis système modèle.
- Les données d’occurrence naturelle restent distinguées des essais par inoculation.

# CiderScholar 0.2.1

- Le chatbot utilise désormais le corpus commun embarqué par défaut.
- Les titres exacts d’articles sont retrouvés dans les abstracts validés.
- Le corpus de base ne déclenche plus la sélection SharePoint à chaque lancement.

# CiderScholar 0.2.0

- installateur Windows 11 x64 par utilisateur, sans demande de droits administrateur ;
- runtime CPython 3.12.10 et frontend de production inclus ;
- modèles E5 et cross-encoder multilingue inclus et vérifiés fichier par fichier ;
- assistant de premier lancement pour SharePoint, corpus, ARGO et mémoire ;
- analyse approfondie livrée désactivée derrière son gate CiderQA de promotion ;
- synthèses longues et ingestion privée ajoutées à la file durable avec heartbeats ;
- recherche, favoris, feedback et exports sélectifs des conversations locales ;
- signatures Ed25519 facultatives des paquets et notifications Windows facultatives ;
- socle de découverte assistée strictement humain dans la boucle ;
- supervision locale de l’API et du worker, reprise de la file et arrêt visible ;
- données, documents privés et secrets conservés hors du répertoire applicatif remplaçable.
