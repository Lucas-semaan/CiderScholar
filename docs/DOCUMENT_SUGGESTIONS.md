# Proposer un document scientifique

Le formulaire **Base documentaire > Proposer un document scientifique** accepte un DOI, une URL
HTTPS, un PDF ou une référence saisie manuellement. Il donne immédiatement l'un des trois résultats :

- **acceptée et transmise** : ARGO a confirmé une pertinence suffisante et le paquet a rejoint l'espace
  SharePoint protégé ;
- **non retenue** : la pertinence ou la confiance n'atteint pas le seuil conservateur ;
- **à réessayer** : la clé ARGO, le service ou la synchronisation SharePoint n'est pas disponible.

Lorsqu’un PDF retenu est importé dans le corpus commun, son extraction et son indexation ciblée sont
enchaînées automatiquement. Une référence sans texte intégral reste une notice bibliographique et
n’est pas présentée comme un PDF indexé.

## Ce qui est transmis

Pour l'évaluation, ARGO reçoit uniquement le titre, le DOI littéral éventuel, l'abstract disponible,
un extrait textuel local borné et le commentaire scientifique. Le PDF complet n'est jamais envoyé à
ARGO. Texte et commentaire sont délimités comme données non fiables et ne peuvent pas modifier les
instructions ou le schéma de décision.

Après acceptation, SharePoint reçoit un dossier UUID complet contenant `suggestion.json` et, uniquement
pour la variante PDF, une copie renommée et hashée. Le paquet ne contient aucune clé ARGO, identifiant
éditeur, conversation, chemin de fichier original ou document privé sans action explicite.

Le poste conserve ensuite seulement un reçu avec l'UUID, la date et le hash du paquet. Ce reçu sert à
éviter un double dépôt du même DOI ou du même PDF ; il ne prouve pas une acceptation administrative.

## Droits et consentement PDF

Avant de joindre un PDF, vérifier que sa licence, son statut de libre accès ou l'autorisation reçue
permettent sa copie dans l'espace SharePoint de l'équipe. La case de consentement est obligatoire :
elle confirme seulement le droit de transmettre ce fichier précis. Elle n'autorise ni collecte
automatique depuis un site éditeur, ni réutilisation d'autres documents privés.

Une URL reste une référence HTTPS pour l'administrateur : le poste utilisateur ne la télécharge pas.
Les URL locales, avec identifiants ou vers des adresses réseau privées sont refusées avant ARGO.

## En cas d'indisponibilité

- **Clé ARGO absente** : ouvrir **Paramètres**, enregistrer et tester la clé personnelle, puis soumettre
  de nouveau.
- **ARGO ou quota indisponible** : attendre le délai indiqué et réessayer depuis le formulaire.
- **OneDrive / SharePoint indisponible après acceptation** : ne pas recréer la proposition. Le paquet
  complet reste dans l'outbox locale et CiderScholar tente une transmission idempotente au prochain
  lancement.

Pour une question sur les droits de diffusion, ne pas joindre le PDF : proposer plutôt son DOI ou sa
référence, puis demander validation au responsable documentaire.
