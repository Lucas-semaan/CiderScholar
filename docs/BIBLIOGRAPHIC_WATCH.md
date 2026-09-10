# Veille bibliographique locale

La carte **Réglages → Veille bibliographique**, réservée au profil administrateur, permet
d'activer la veille et de modifier les tags et leurs requêtes. La configuration est enregistrée
dans la base applicative locale, sans clé API. Les huit thèmes existants sont proposés au départ.
L'activation vaut autorisation de collecte via les API configurées et déclenche le premier job.
La désactivation annule le job en attente ou demande l'arrêt coopératif du job actif.

Le planificateur vérifie l'échéance toutes les 60 secondes pendant que l'API fonctionne. Une seule
veille peut être en attente ou active. Une échéance manquée entraîne un seul rattrapage, même après
plusieurs semaines d'arrêt ; la prochaine échéance est fixée sept jours après la mise en file.
Le mode hors ligne suspend le déclenchement. Le worker administrateur habituel exécute le travail ;
aucun service Windows supplémentaire n'est installé et aucun modèle n'est chargé au démarrage.

## Collecte et bornes

- Un cycle cible 1 000 notices et 100 articles faisant l'objet d'une tentative d'acquisition
  intégrale au maximum. Les lots sont de 25 notices. Une tentative peut explorer plusieurs
  fournisseurs légaux pour le même article.
- Trois créneaux de recherche sur quatre sont affectés aux nouveautés et un au fonds ancien.
  Une voie épuisée cède ses créneaux à l'autre. L'ordre des thèmes et fournisseurs tourne entre
  les exécutions ; les curseurs dépendent aussi de l'empreinte de la requête.
- Crossref utilise la date d'indexation, OpenAlex la date de publication. La première fenêtre
  couvre 30 jours, puis les fenêtres se recouvrent sur sept jours. La fenêtre supérieure reste
  figée pendant sa pagination. Les autres adaptateurs conservent leur pagination habituelle,
  et cette limite incrémentale est visible dans le bilan. Les limites de pagination imposées
  par les fournisseurs restent des erreurs identifiées, jamais une saturation scientifique.
- Les appels OpenAlex restent exclusivement dans le budget gratuit disponible et dans le plafond
  configuré. Les quotas, les clés manquantes et les erreurs ne ferment pas un thème comme improductif.
  Deux rotations complètes sans gain déclenchent la fermeture opérationnelle existante de sept jours.
- La collecte occupe au plus 30 minutes de l'enveloppe d'une heure afin de laisser du temps à
  l'acquisition et à l'indexation. Les limites sont vérifiées entre opérations reprenables ; une
  opération atomique déjà commencée peut les dépasser légèrement. Le temps consommé est persisté.

Les requêtes respectent les paramètres documentés par [Crossref](https://github.com/CrossRef/rest-api-doc)
et [OpenAlex](https://help.openalex.org/api/filtering/). Les accès existants sont réutilisés ; les clés
restent dans leur coffre et sont hydratées dans l'environnement du worker. Aucune clé supplémentaire
n'est nécessaire pour activer les fournisseurs publics déjà pris en charge.

## Persistance et reprise

La migration 36 ajoute le type de job `bibliographic_watch`, ses états opérationnels, ses checkpoints
et le registre des contenus suivis. Elle conserve les jobs et événements existants. Les publications,
curseurs et rapports restent dans la base scientifique commune ; activation et échéance sont locales
à la base applicative et ne sont pas transférées par les paquets de corpus.

Avant la collecte, une sauvegarde SQLite cohérente est créée et contrôlée sous
`data/common/database/watch-backups/`. Les ajouts utilisent les services bibliographiques, le filtre
éditorial et les exclusions DOI existants. Une page et son avancement sont validés ensemble dans une
transaction. Les cas incertains restent en revue ; les décisions manuelles sont préservées.

Le texte intégral accessible légalement est acquis en priorité. À défaut, un abstract admissible est
indexé. Les nouveaux textes et les fragments en échec sont indexés de façon ciblée. Le registre distingue
les contenus déjà recherchables des véritables ajouts et empêche un double comptage après interruption.
Une indexation échouée laisse le contenu à reprendre. Les acquisitions différées tournent entre les scans.
La veille n'effectue ni purge, ni reconstruction globale d'index, ni publication de paquet.

La file empêche une veille de s'exécuter en même temps qu'un autre job. Les verrous Qdrant existants
restent applicables. Les campagnes lancées directement par scripts restent soumises à la règle du
writer unique du guide méthodologique ; elles ne doivent pas être lancées pendant une veille active.

## Contrats HTTP

Toutes les routes suivantes exigent le profil administrateur :

| Route | Contrat |
| --- | --- |
| `GET /api/admin/bibliographic-watch` | Configuration, prochaine échéance, suspension éventuelle, job actif et vingt derniers bilans |
| `PUT /api/admin/bibliographic-watch/configuration` | `{ "enabled": boolean, "themes": [{ "key": string, "query": string }] }` ; champs inconnus refusés |
| `GET /api/admin/bibliographic-watch/history` | Vingt derniers bilans |
| `POST /api/admin/bibliographic-watch/launch` | Objet vide ; lancement manuel, y compris lorsque la périodicité est désactivée ; réponse 202 avec le job |

Le suivi, l'annulation et la relance emploient les routes existantes `/api/jobs/{id}`,
`/api/jobs/{id}/cancel` et `/api/jobs/{id}/retry`. L'interface présente ajouts effectivement indexés,
textes intégraux, abstracts seuls, doublons, décisions éditoriales, éléments différés et erreurs.
Aucune notification Windows n'est émise pour ces jobs.
