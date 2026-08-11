# Modèle d’accès et de distribution

Statut : accepté pour le pilote.

## Installation

- chaque membre de l’équipe installe CiderScholar sur son poste Windows 11 personnel ;
- l’application écoute uniquement sur `127.0.0.1` ;
- aucun compte ou écran de connexion CiderScholar n’est requis ;
- le compte Windows et le profil local constituent la frontière de confidentialité ;
- les conversations, travaux et clé ARGO restent sur le poste utilisateur.

## Clé ARGO

- chaque utilisateur fournit sa propre clé ;
- la clé est saisie dans l’application et chiffrée avec Windows DPAPI ;
- elle n’est jamais écrite dans `.env`, YAML, SharePoint, SQLite en clair ou les logs ;
- elle reste disponible au worker local après fermeture du navigateur ;
- les quotas sont comptabilisés localement pour éviter les refus ARGO.

## Corpus commun

- la machine administrateur conserve la copie principale modifiable ;
- une sauvegarde est conservée sur un drive protégé ;
- les versions publiées du RAG commun sont déposées dans un espace SharePoint protégé ;
- chaque poste installe la même version validée du corpus commun ;
- une mise à jour est téléchargée, vérifiée puis activée atomiquement au redémarrage ;
- le corpus commun est traité comme non modifiable sur les postes utilisateurs.

## Suggestions

- un utilisateur peut proposer un DOI, une URL, un PDF, une référence manuelle et un commentaire ;
- la pertinence cidricole est évaluée immédiatement avec sa propre clé ARGO après les contrôles locaux ;
- une suggestion retenue est déposée dans l’espace SharePoint d’entrée ;
- la machine administrateur importe les suggestions admissibles lors de la maintenance hebdomadaire ;
- les PDF proposés peuvent être transmis à l’administrateur et intégrés si leurs droits le permettent ;
- tout PDF effectivement intégré est automatiquement indexé dans la même opération ciblée.

## Administration

- le rôle administrateur est activé uniquement par la configuration locale de la machine principale ;
- les clés bibliographiques dédiées à l’outil restent uniquement sur cette machine ;
- la collecte et la publication du corpus commun ne s’exécutent pas sur les postes utilisateurs ;
- au premier lancement administrateur après l’échéance hebdomadaire, l’application propose la collecte ;
- le lancement peut être accepté ou reporté ; aucune machine nocturne permanente n’est requise.
