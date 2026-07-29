# Signature cryptographique des paquets communs

Les SHA-256 détectent une corruption mais ne prouvent pas l’identité de l’émetteur. CiderScholar
peut donc exiger deux signatures détachées OpenSSH Ed25519 : `manifest.json.sig` et
`corpus.zip.sig`, sous le namespace `ciderscholar-corpus-v1`.

L’administrateur crée hors du dossier synchronisé une clé Ed25519 avec `ssh-keygen`, conserve la clé
privée dans son profil protégé et distribue uniquement un fichier `allowed_signers`, par exemple :

```text
ciderscholar-admin ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA...
```

Après construction et avant publication :

```powershell
python -m scripts.sign_corpus_package `
  artifacts/corpus-packages/corpus-v1-... `
  --private-key C:\chemin-protege\ciderscholar-signing `
  --identity ciderscholar-admin
```

Activer ensuite `distribution.signature_required` et renseigner
`distribution.allowed_signers_path`. Publication, copie synchronisée et installation échouent
fermées si un fichier, une signature, l’identité, le namespace ou la clé autorisée diffère. La clé
privée n’entre jamais dans le paquet, l’archive, les exports ou SharePoint.
