# Gouvernance du wiki de raisonnement

## Frontière entre wiki et RAG

Une information entre dans le wiki lorsqu'elle sert fréquemment à cadrer une question, distinguer deux concepts, choisir les observations nécessaires, comparer des options ou rendre visible une contrepartie. Une dose, un seuil, un protocole d'appareil, un résultat particulier, une spécification, une réglementation ou une décision historique reste dans le RAG et n'est chargé que lorsque la question le justifie.

Le wiki ne doit jamais servir de citation scientifique. Le chatbot peut s'en servir pour formuler des hypothèses et structurer une réponse, mais chaque affirmation affichée doit être soutenue par les preuves persistées du RAG. En cas de contradiction, la preuve scientifique contextualisée prime dans la réponse et la contradiction déclenche une revue du wiki.

## Autorité

Les fichiers sources proviennent du sous-dossier intitulé « 5 - Fiches validées et importées ». Ce classement est conservé comme provenance ; il ne prouve pas que la présente distillation a reçu une revue experte. Les formulations ajoutées pour relier les fiches sont des interprétations éditoriales à revoir.

Une révision scientifique approuvée doit enregistrer la personne ou le rôle de revue, la date, les pages concernées et l'empreinte exacte de la version. Sans cette trace, le statut reste « distillation éditoriale non revue ».

## Révision

Pour modifier une page :

1. comparer la proposition avec les fiches originales citées et les preuves scientifiques récentes pertinentes ;
2. conserver les distinctions, conditions, incertitudes et contradictions ;
3. ajouter ou corriger les repères de source ;
4. incrémenter la révision de la page et recalculer son empreinte dans `manifest.json` ;
5. exécuter le validateur et les tests ;
6. soumettre la différence à une revue experte avant de déclarer la nouvelle interprétation approuvée.

Les suppressions restent visibles dans l'historique Git. Une source modifiée ne met pas silencieusement à jour la distillation : son empreinte change et appelle une revue ciblée.

## Budgets d'attention

Le cœur est chargé pour toute question scientifique cidricole acceptée. Au plus deux pages thématiques sont ajoutées par correspondance lexicale déterministe. Le texte injecté est borné à 12 000 caractères. Une absence de correspondance conserve le cœur seul et n'empêche jamais le RAG de rechercher la question.

