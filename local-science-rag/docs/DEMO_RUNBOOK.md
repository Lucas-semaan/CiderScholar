# Démonstration CiderScholar — cinq minutes

Les parcours automatisés et les variantes de panne associés sont recensés dans
`DEMO_E2E_MATRIX.md`. Ils doivent être verts avant une répétition réelle.

Préparer l’écran sur la page **Diagnostic**, sans conversation ouverte. Utiliser uniquement les
questions versionnées dans `demo_questions.json`. Ne jamais saisir de donnée interne, personnelle ou
non publiée pendant la démonstration.

## 0:00–0:40 — prouver que le poste est prêt

1. Cliquer **Diagnostic** dans la navigation.
2. Cliquer **Actualiser les contrôles**.
3. Montrer les quatre lignes ARGO, worker, corpus commun et espace disque, puis la profondeur et l’âge
   de la file.

Résultat attendu : chaque contrôle est vert, aucun texte scientifique n’est généré et aucun compteur
de jetons ou de requêtes n’est affiché. Si ARGO est indisponible, appliquer immédiatement la procédure
de repli plus bas et ne pas montrer une réponse enregistrée comme si elle était réelle.

## 0:40–2:05 — réponse directe en prose et APA 7

1. Cliquer **Chat scientifique**, puis **Nouvelle conversation**.
2. Coller la question `direct-mineral-salts` et cliquer **Envoyer** une seule fois.
3. Pendant le travail, montrer l’étape courante sans quitter la page.
4. À la fin, ouvrir les sources sous la réponse.

Résultat attendu : prose sans puces, affirmations reliées au corpus commun, DOI visible et
bibliographie APA 7. La source minérale attendue est le DOI `10.3390/molecules25163640`.

## 2:05–3:35 — comparaison puis suivi contextuel

1. Dans une nouvelle conversation, envoyer `compare-oxygen-concentrate`.
2. Pendant le calcul, sélectionner la première conversation puis revenir à la seconde.
3. Après la réponse comparative, envoyer `follow-up-volatiles` dans le même chat.

Résultat attendu : la réponse comparative reste dans son chat initial, conserve les deux DOI attendus
et n’utilise pas de puces. Le suivi réutilise explicitement le contexte précédent sans resoumettre la
première question.

## 3:35–4:25 — liste uniquement sur demande

1. Reformuler dans le même chat : « Donne maintenant ces limites sous forme de trois puces. »
2. Envoyer, puis ouvrir la bibliographie.

Résultat attendu : les puces apparaissent uniquement après cette demande explicite ; les références
restent structurées depuis SQLite et ne sont pas inventées par le modèle.

## 4:25–5:00 — confidentialité et durabilité

1. Cliquer **Documents privés** et montrer le badge distinct sans ouvrir de contenu.
2. Revenir au chat, puis cliquer **Paramètres > Arrêter CiderScholar**.
3. Relancer depuis le menu Démarrer et rouvrir la conversation comparative.

Résultat attendu : les origines « Corpus commun »/« Document privé » restent explicites, le travail et
la conversation sont retrouvés, et aucune question n’est renvoyée à ARGO au redémarrage.

## Repli ARGO

Si le diagnostic ARGO est rouge, annoncer « ARGO est indisponible ; aucune génération ne sera
simulée ». Montrer seulement la recherche locale et les sources vérifiées par
`scripts.verify_demo_sources`, puis reprendre la démonstration générative après rétablissement. Une
capture ou réponse antérieure peut illustrer l’interface uniquement si elle porte clairement la
mention **exemple enregistré, non généré pendant cette session**.

## Variantes de répétition

Après le scénario de cinq minutes, dérouler les variantes redémarrage, corpus commun/privé, mise à
jour, suggestion PDF et quota décrites dans `DEMO_E2E_MATRIX.md`. Elles utilisent un profil de
démonstration et une inbox simulée, sans donnée sensible. Ne jamais présenter leur ARGO simulé comme
une génération réelle ; seule la répétition bornée `DEM-015` autorise cette conclusion.
