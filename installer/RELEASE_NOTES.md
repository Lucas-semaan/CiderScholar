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
