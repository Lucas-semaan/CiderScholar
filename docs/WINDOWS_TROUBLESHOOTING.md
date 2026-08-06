# Dépannage Windows sans commandes obligatoires

## La clé ARGO est refusée

Ouvrir **Paramètres > Clé personnelle ARGO**, remplacer la clé puis utiliser **Tester la connexion**.
La clé reste chiffrée sur le compte Windows courant. Si ARGO est indisponible, conserver la clé et
réessayer plus tard.

## Le dossier SharePoint n’est pas reconnu

Vérifier dans l’Explorateur que OneDrive affiche le dossier `CiderScholar`, puis que
`corpus\latest.json` est disponible localement. Rouvrir l’assistant et sélectionner ce dossier, pas
son sous-dossier `corpus`. Une icône de nuage seule signifie que la synchronisation locale doit finir.

## Le corpus ou E5 est signalé comme corrompu

Ne pas lancer de chat. Réinstaller CiderScholar depuis l’exécutable et le hash publiés ensemble. Les
conversations et secrets sont conservés. Si le modèle reste invalide, désinstaller,
choisir de conserver les données, puis réinstaller ; contacter l’administrateur avec la version et le
message affiché, sans joindre de PDF ni de contenu de chat.

## Le worker ne répond plus

Ouvrir **Paramètres > Arrêt de l’application**, attendre la fermeture, puis relancer depuis le menu
Démarrer. La file et les étapes sont persistées ; le travail reprend sans resoumettre la question. Si
le problème persiste, transmettre uniquement la version, le type de travail, l’étape et l’heure.

## Une mise à jour applicative est reportée

Une mise à jour n’est jamais appliquée pendant un travail actif. Attendre la fin visible dans le chat,
arrêter CiderScholar, puis lancer le nouvel installateur SharePoint. Le répertoire `UserData` séparé
préserve corpus, conversations, file, configuration et secrets.
