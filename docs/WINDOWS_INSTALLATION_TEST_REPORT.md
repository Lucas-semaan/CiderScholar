# Rapport de validation de l’installation Windows 0.1.0

> Rapport historique de la version 0.1.0. La version 0.2.0 doit conserver son propre rapport de
> build et repasser les contrôles externes sur profil distinct avant publication.

Date : 22 juillet 2026  
Hôte : Windows 11 x64, build 26200, profil utilisateur courant  
Artefact final : `CiderScholar-0.1.0-windows-x64.exe`  
Taille : 692 156 593 octets  
SHA-256 : `e2c6945a1e6c6e244897aa5594dd2176a23bc562731f3dad2ab115c90a378c53`

## Résultats conformes

- Le build hors ligne utilise CPython 3.12.10 embarqué, le frontend Vite précompilé et le modèle
  `intfloat/multilingual-e5-base` complet.
- Le smoke test du runtime importe FastAPI, PyTorch, Transformers, SentenceTransformers, Qdrant et
  `app.main` après pruning.
- Le hash indépendant de l’exécutable correspond au sidecar et à `latest.json`.
- L’installation silencieuse propre termine en code 0, sans droits administrateur et sans terminal.
- La vérification post-installation contrôle Windows 11 x64, la configuration, l’arborescence et la
  liste exhaustive des fichiers/hash E5.
- Les raccourcis Bureau et menu Démarrer sont créés avec `pythonw.exe`.
- Le superviseur démarre API et worker, expose `/health`, réutilise son mutex lors d’un second
  lancement et termine en code 0 après `POST /api/system/shutdown`.
- Une mise à jour sur place conserve une conversation, un travail durable, un fichier privé et un
  fichier de secret sentinelle. L’ajout de `comparetimestamp` réduit une mise à jour identique de
  21 minutes à moins de 30 secondes sur l’hôte de test.
- La désinstallation silencieuse utilise `SuppressibleMsgBox(..., IDNO)` : le journal confirme le
  choix automatique « Non », la sortie est 0 et `UserData` est conservé.
- La réinstallation retrouve exactement la conversation, le travail, le privé et le secret, puis
  repasse la vérification E5 en code 0.

## Défauts détectés et corrigés pendant la répétition

1. Le premier payload contenait `torch/include` et a subi un rollback lors de l’écriture d’un en-tête
   surveillé par Windows. Les en-têtes, bibliothèques de développement, tests et caches sont retirés ;
   les licences imbriquées restent disponibles dans `THIRD_PARTY_LICENSES.zip`.
2. Le smoke test régénérait des bytecodes après pruning. Il utilise désormais `python -B` et le
   staging final ne contient aucun `__pycache__`.
3. Un `MsgBox` personnalisé bloquait `/SUPPRESSMSGBOXES`. Il a été remplacé par
   `SuppressibleMsgBox` avec `IDNO` comme valeur sûre par défaut.
4. `WizardSilent` n’est pas appelable depuis l’uninstaller. Cette tentative intermédiaire a été
   rejetée par un test réel puis supprimée au profit de l’API Inno dédiée ci-dessus.

## Contrôles restant externes

- publication réelle de l’exécutable, du SHA-256 et de la page courte dans SharePoint ;
- installation depuis un profil Windows temporaire distinct ;
- essais matériels réels sur postes 8 Go et 16 Go ;
- installation observée par une personne n’ayant pas développé le projet.

Ces contrôles ne sont pas simulés ni déclarés terminés dans la roadmap.
