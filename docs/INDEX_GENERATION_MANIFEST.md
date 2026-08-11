# Manifeste de génération d’index

Le RAG de texte intégral peut désormais associer une génération Qdrant à un contrat
reproductible, sans y copier le texte scientifique. Le mécanisme concerne uniquement
la collection de chunks du corpus scientifique ; l’index bibliographique d’abstracts
reste un contrat distinct.

## Emplacement et contenu

Lorsqu’il existe, le sidecar est écrit atomiquement ici :

```text
<qdrant>/index-generation/<collection>.json
```

Il contient notamment :

- un identifiant de génération et l’état `building` ou `ready` ;
- le modèle, le hash de son manifeste local, la dimension, la normalisation, les
  préfixes, la longueur maximale et le périphérique ;
- le contrat de chunking, les réglages Qdrant et leurs valeurs effectivement
  enregistrées par Qdrant ;
- des empreintes SHA-256 des articles et chunks **déjà indexés**, les compteurs et la
  version de schéma SQLite ;
- une signature canonique du contrat sémantique.

Le fichier ne contient ni passage, ni texte de PDF, ni vecteur.

## Compatibilité et lecture

Une collection historique sans sidecar reste consultable afin de ne pas bloquer le
corpus existant. Elle est journalisée une fois comme `legacy_unverified` et n’est
jamais adoptée automatiquement.

Dès qu’un sidecar est présent, une recherche échoue avant l’encodage ou la lecture si
la génération est incomplète, modifiée ou incompatible : modèle, manifeste modèle,
dimension, préfixes, longueur, chunker, paramètres Qdrant, métadonnées Qdrant et
signature sont contrôlés. Une génération `building` ne sert jamais de résultats.

Une génération gérée exige aussi que les fichiers du modèle local correspondent à son
manifeste de fichiers. Leur contenu est vérifié avant une reconstruction, un scellement
`ready` ou un encodage de requête. Une révision de métadonnées de fichiers borne ce
contrôle coûteux : il n’est pas répété tant que les poids sont inchangés.

Le fingerprint complet du corpus n’est volontairement pas recalculé à chaque requête :
cette opération parcourrait potentiellement des centaines de milliers de chunks sur un
portable. Le chemin de recherche vérifie uniquement le contrat constant ; le contrôle
exhaustif est une opération explicite.

## Cycle opérationnel

Pour créer la première génération contrôlée ou repartir après un changement de
contrat :

```powershell
.\.venv\Scripts\python.exe -m scripts.rebuild_index --config config.yaml --recreate
```

`--recreate` écrit d’abord `building`, puis remet les chunks en attente et reconstruit
la collection. Le manifeste devient `ready` seulement après cohérence SQLite/Qdrant.
Un arrêt ou une erreur laisse donc l’index bloqué en `building` au lieu de servir un
mélange silencieux.

Une indexation incrémentale, une suppression, une réindexation d’article et la fusion
historique de vecteurs suivent le même cycle `building → ready`. Une reprise avec le
même contrat est autorisée ; une dérive de modèle ou de chunking est refusée et exige
un `--recreate` explicite.

Pour vérifier une génération prête sans charger le modèle d’embedding :

```powershell
.\.venv\Scripts\python.exe -m scripts.rebuild_index --config config.yaml --verify-generation
```

Cette commande recalcule l’empreinte des chunks indexés et compare chaque identifiant
Qdrant, ainsi que son payload non textuel (`chunk_id`, article, section, pages, modèle),
à SQLite. Elle peut être coûteuse sur un gros corpus, mais ne s’exécute jamais dans le
chemin chaud de recherche.

Ne pas supprimer manuellement le sidecar pour contourner une erreur : cela transformerait
un index géré en index legacy non vérifié. Pour une génération incompatible ou
irrécupérable, reconstruire explicitement ; pour une interruption avec contrat inchangé,
relancer l’opération d’indexation concernée.

## Distribution de corpus

Le sidecar est inclus automatiquement avec les fichiers sous `common/qdrant`. Lors de
l’installation d’un package, son absence reste compatible avec les packages historiques.
S’il est présent, l’installateur vérifie son état, sa signature, ses compteurs SQLite et
Qdrant, les paramètres physiques de la collection et chaque ID/payload de routage
Qdrant contre SQLite avant l’activation du package.
