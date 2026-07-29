# Installer CiderScholar depuis SharePoint

1. Ouvrir le dossier SharePoint **CiderScholar / installers** synchronisé par OneDrive.
2. Télécharger l’exécutable `CiderScholar-<version>-windows-x64.exe` et son fichier `.sha256`.
3. Vérifier que la version correspond au fichier `latest.json`, puis lancer l’exécutable par double
   clic. L’installation ne demande pas de droits administrateur.
4. Laisser cochée l’ouverture de CiderScholar. Le navigateur s’ouvre uniquement quand l’API locale
   est prête ; aucune fenêtre de terminal n’apparaît.
5. Dans l’assistant, choisir le dossier SharePoint `CiderScholar`, installer le corpus vérifié, saisir
   la clé ARGO personnelle et confirmer le profil mémoire proposé.

Un second double clic réutilise l’instance en cours. Pour arrêter, ouvrir **Paramètres > Arrêt de
l’application**. Une désinstallation conserve les données par défaut ; leur suppression exige une
confirmation distincte et propose d’abord une sauvegarde dans Documents.

