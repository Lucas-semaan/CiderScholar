# Configurer sa clé ARGO dans CiderScholar

Statut : parcours cible à intégrer à l’assistant de premier lancement.

La clé ne doit pas être ajoutée manuellement dans un fichier `.env`. CiderScholar doit la vérifier et
la conserver chiffrée avec la protection Windows du compte utilisateur.

## Obtenir la clé

1. Se connecter au réseau INRAE ou au VPN institutionnel si nécessaire.
2. Ouvrir [https://chatbot.argo.inrae.fr/](https://chatbot.argo.inrae.fr/).
3. Se connecter à son compte ARGO.
4. Cliquer sur l’icône du profil.
5. Ouvrir `Réglages`, puis `Compte`, puis `Clé API`.
6. Cliquer sur `Afficher`.
7. Copier la clé sans la transmettre à une autre personne.

## Enregistrer la clé dans CiderScholar

1. Ouvrir CiderScholar.
2. Dans l’assistant de premier lancement, ouvrir l’étape `Clé ARGO`.
3. Coller la clé dans le champ prévu.
4. Cliquer sur `Vérifier et enregistrer`.
5. Attendre la confirmation que la clé est valide et que le modèle configuré est accessible.

L’application appelle uniquement la liste des modèles pour cette vérification. Si ARGO accepte la clé
et expose le modèle attendu, la clé est considérée comme active. Aucune modification de `.env` ni
activation supplémentaire dans CiderScholar n’est requise.

Après validation, la clé est chiffrée avec Windows DPAPI pour le compte Windows courant. Elle n’est
plus affichée par l’application. L’utilisateur peut seulement la remplacer, la supprimer ou refaire le
test de connexion.

## Résoudre un échec

- vérifier que le poste est connecté au réseau INRAE ou au VPN ;
- vérifier que toute la clé a été copiée, sans espace avant ou après ;
- retourner dans ARGO et afficher de nouveau la clé ;
- remplacer la clé enregistrée puis relancer `Tester la connexion` ;
- si la clé est refusée malgré ces vérifications, contacter le support ARGO sans envoyer la clé.

## Règles de sécurité

- ne jamais envoyer la clé par courriel, messagerie ou ticket ;
- ne jamais la déposer sur SharePoint ;
- ne jamais la copier dans `config.yaml`, `.env`, un document ou une capture d’écran ;
- une clé est personnelle et ses quotas ne doivent pas être partagés ;
- supprimer la clé dans CiderScholar avant de céder le poste ou le compte Windows.

## Rotation et cession du poste

Pour renouveler une clé, créer d'abord la nouvelle clé dans ARGO, la remplacer dans les Paramètres de
CiderScholar, puis utiliser `Tester la connexion`. Révoquer l'ancienne clé dans ARGO uniquement après
la réussite de ce test.

Avant de céder le poste ou le compte Windows, ouvrir les Paramètres, supprimer la clé ARGO et vérifier
que son statut est `Absente`. Révoquer ensuite la clé dans ARGO si elle ne doit plus être utilisée.
