# Distribution locale OneDrive / SharePoint

CiderScholar lit un dossier SharePoint déjà synchronisé sur le PC par OneDrive. L'application ne
demande aucun compte Microsoft et n'appelle pas Microsoft Graph.

## Choisir le dossier sur un poste utilisateur

1. Ouvrir le site SharePoint de l'équipe dans le navigateur.
2. Ouvrir la bibliothèque qui contient le dossier **CiderScholar**.
3. Cliquer sur **Synchroniser** et accepter l'ouverture de Microsoft OneDrive.
4. Attendre que l'Explorateur de fichiers affiche le dossier et une coche verte sur `latest.json`.
5. Dans l'Explorateur, ouvrir le dossier **CiderScholar**, cliquer dans la barre d'adresse et copier
   le chemin complet, par exemple `C:\Users\Alice\INRAE\Equipe - Documents\CiderScholar`.
6. Fermer CiderScholar, ouvrir `config.yaml` avec le Bloc-notes et renseigner :

```yaml
distribution:
  enabled: true
  synchronized_root: "C:\\Users\\Alice\\INRAE\\Equipe - Documents\\CiderScholar"
  administrator_archive_root: null
  expected_folder_name: CiderScholar
  check_interval_hours: 24
```

7. Enregistrer puis relancer CiderScholar. Dans **Paramètres > Version du corpus commun**, vérifier
   que la version disponible et sa date apparaissent.

Le dossier choisi doit s'appeler `CiderScholar`. Il ne faut sélectionner ni `data`, ni un sous-dossier
tel que `corpus`. Une croix rouge OneDrive ou le message « dossier indisponible » n'empêche pas le
chatbot d'utiliser la dernière version locale : corriger la synchronisation, puis relancer après le
prochain contrôle quotidien.

## Contenu attendu

```text
CiderScholar/
|-- installers/
|-- corpus/
|   |-- latest.json
|   `-- corpus-v1-<sha256>/
|       |-- manifest.json
|       `-- corpus.zip
`-- archive/
```

- `installers` distribue les installateurs signés ;
- `corpus` distribue les versions immuables et le pointeur `latest` ;
- `archive` conserve les versions retirées de la vue courante.

Ce dossier ne contient jamais de clé ARGO, identifiant éditeur, secret DPAPI, conversation,
configuration locale, document privé ou cache.

## Publication et rollback administrateur

Ces opérations se font uniquement sur la machine administrateur, avec l'application arrêtée et
OneDrive synchronisé. Le drive d'archive doit être protégé, distinct du dossier SharePoint et de
`data`.

1. Renseigner `synchronized_root` et `administrator_archive_root` dans `config.yaml`.
2. Ouvrir PowerShell dans le dossier du projet et activer explicitement le profil administrateur :

```powershell
$env:CIDERSCHOLAR_LOCAL_PROFILE = "admin"
```

3. Construire une version depuis le corpus commun local :

```powershell
.\.venv\Scripts\python.exe -m scripts.build_corpus_package --output .\dist\corpus
```

4. Copier le chemin `version_directory` du rapport JSON, puis publier et archiver la même version :

```powershell
.\.venv\Scripts\python.exe -m scripts.publish_corpus_package `
  .\dist\corpus\corpus-v1-<sha256>
```

5. Vérifier que les champs `publication.pointer.corpus_version` et
   `protected_archive.version_directory` désignent la même version, puis attendre la coche verte
   OneDrive sur le dossier de version et sur `corpus\latest.json`.

La publication copie d'abord `manifest.json` et `corpus.zip`, vérifie leurs tailles et SHA-256, rend
le dossier de version visible, puis remplace `latest.json` atomiquement en dernier. Une interruption
avant cette dernière étape laisse donc l'ancienne version annoncée.

Pour retirer une publication défectueuse, ne modifier aucun ZIP et ne supprimer aucun dossier. Relancer
la commande de publication avec le répertoire immuable de la version saine conservée sur le drive
protégé. Le pointeur `latest.json` revient alors vers cette version après vérification complète. Les
postes utilisateurs pourront la télécharger, ou utiliser **Revenir à la version précédente** si elle
est encore conservée localement. Consigner versions, hashes et heure de l'opération dans le journal
d'exploitation.
