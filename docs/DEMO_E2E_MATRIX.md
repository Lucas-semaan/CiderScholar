# Matrice E2E de démonstration

Cette matrice rend les huit parcours `DEM-007` à `DEM-014` répétables sans appel ARGO réel. Elle
complète le scénario humain de cinq minutes de `DEMO_RUNBOOK.md` : les doubles de test simulent les
réponses ou les pannes, tandis que les assertions portent sur les données persistées et les contrats
visibles par l’interface.

## Lancement unique

Depuis la racine du dépôt :

```powershell
.venv\Scripts\python.exe -m pytest `
  tests/test_pilot_rag.py `
  tests/test_multi_corpus.py `
  tests/test_corpus_updates.py `
  tests/test_job_worker.py

Set-Location frontend
npm test -- --run src/features/chatbot/durableChatFlow.test.ts `
  src/features/chatbot/sourcePresentation.test.ts
```

Un échec de commande interrompt la répétition. Aucun résultat ne doit être corrigé à la main dans un
rapport : le test en défaut est la preuve à conserver.

## Contrats couverts

| Tâche | Parcours | Preuve automatisée | Résultat attendu |
| --- | --- | --- | --- |
| `DEM-007` | Réponse scientifique en prose | `test_complete_scientific_prose_response_contract` | Aucun marqueur de liste, citations dans le texte et références APA avec DOI uniques. |
| `DEM-008` | Liste demandée explicitement | `test_pilot_rag_uses_bullets_only_when_the_requested_format_is_explicit` et `test_pilot_rag_renders_one_non_empty_bullet_per_statement` | Une puce non vide par affirmation, uniquement après une demande explicite. |
| `DEM-009` | Changement de chat pendant le travail | `durableChatFlow.test.ts` | La notification vise l’autre chat et le résultat persiste dans la conversation d’origine. |
| `DEM-010` | Redémarrage et reprise | `durableChatFlow.test.ts` | Le travail actif est relu puis suivi ; la requête d’envoi reste unique. |
| `DEM-011` | Sources commune et privée | `test_lexical_search_reads_common_then_private_and_marks_every_hit` et `sourcePresentation.test.ts` | Chaque résultat porte son origine et les libellés les distinguent. |
| `DEM-012` | Mise à jour du corpus commun | `test_common_directory_swap_preserves_every_private_hash` | Tous les hash du corpus privé sont identiques avant et après l’activation. |
| `DEM-014` | Quota puis reprise | `test_local_quota_keeps_job_queued_until_persisted_retry_time` | Le travail reste en file sans tentative consommée, puis réussit à l’heure persistée. |

## Vérification visuelle locale

Pour une répétition avec l’application, exécuter les étapes du runbook puis ces variantes de panne
sur un profil de démonstration sans donnée sensible :

1. Pendant une réponse, ouvrir un autre chat, revenir au chat initial et constater que la réponse y
   apparaît sans nouvel envoi.
2. Pendant un travail en file, fermer proprement CiderScholar, le relancer et constater que la carte
   de travail reprend le même identifiant.
3. Afficher ensemble un résultat commun et un résultat privé ; vérifier leurs badges sans ouvrir le
   contenu privé devant le public.
4. Activer un paquet de corpus de test et comparer les empreintes privées consignées avant/après.
5. Simuler le quota local, constater l’état « en attente », avancer jusqu’à l’heure de reprise et
   vérifier que le même travail termine sans état d’échec.

La répétition locale ne remplace pas `DEM-015`, qui exige une génération ARGO réelle et bornée.
