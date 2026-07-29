# Données expérimentales et analyses

Les imports acceptent UTF-8 JSON ou CSV pour quatre familles :

| Famille | Champs et unités obligatoires | Contrôles |
|---|---|---|
| Fermentation | échantillon, réplicat, heures, °C, densité g/mL | témoin, lot, méthode |
| Volatils | échantillon, réplicat, composé, concentration en mg/L ou µg/L | blanc, étalon, méthode |
| Polyphénols | échantillon, réplicat, analyte, concentration mg/L | blanc, étalon, méthode |
| Sensoriel | échantillon, évaluateur pseudonymisé, réplicat, attribut, score et bornes | ordre, session, témoin |

En CSV, les contrôles sont des colonnes `control_<nom>` et la colonne `kind` est obligatoire. Le
fichier brut est copié sous son SHA-256 ; le manifeste relie provenance, auteur, transformations
empreintées et date sans journaliser les lignes.

Les workflows 1.0.0 sont déterministes et bornés : vitesse/changement de densité, moyennes de
composés volatils après conversion, moyennes de polyphénols et scores sensoriels normalisés. Tout
code généré reste inexécutable avant revue explicite de son hash, dépendances, entrées, sorties,
réseau coupé et limites CPU/mémoire/temps.

L’exécution passe ensuite par l’interface `AnalysisExecutor`. Aucun backend n’est fourni par défaut :
`execute_reviewed_analysis` refuse donc tout lancement tant qu’un sandbox Windows ou un conteneur
attesté n’est pas explicitement injecté. Le backend doit rendre un reçu empreinté liant exactement
code, environnement et fichiers de sortie approuvés ; un simple sous-processus local ne satisfait
pas ce contrat d’isolation.

Les trajectoires libres ne sont permises que pour une ambiguïté déclarée : deux au maximum sur 8 Go,
quatre sur 16 Go et jamais davantage que le quota restant. Le consensus conserve minima, maxima,
écart-type, paramètres et échecs. Le workflow déterministe reste le repli.
