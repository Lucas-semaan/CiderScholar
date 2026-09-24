# Wiki de raisonnement CiderScholar

Ce wiki est le cœur de réflexion cidricole de CiderScholar. Il condense les fiches AsCoCid les plus fréquemment utiles en cadres de décision, compromis et cas frontières. Le chatbot charge toujours [le cœur](coeur.md), puis au plus deux pages thématiques correspondant à la question.

Le cœur Markdown oriente la recherche et l'organisation de la réponse sans constituer seul une preuve scientifique. Les documents déposés dans les sous-dossiers du wiki deviennent en revanche des sources Ascocid après extraction et persistance dans SQLite. Le mode concis les utilise seuls lorsqu'ils couvrent toutes les vérifications de la question ; les autres modes peuvent les compléter avec le corpus scientifique général. Une citation documentaire est rendue sous la forme `Ascocid — <nom du fichier>`.

## Mettre à jour l'index documentaire

```powershell
.\scripts\convert_text_documents.ps1 -SourceDirectory .\wiki -OutputDirectory .\data\common\ascocid-wiki-converted -Recursive
.\.venv\Scripts\python.exe -m scripts.index_ascocid_wiki
```

La première commande extrait localement les documents Office. La seconde crée une sauvegarde SQLite vérifiée, ingère les PDF et conversions, tente l'OCR local des images et PDF sans texte, puis enregistre l'empreinte et le nom du fichier original. Les formats binaires sans extracteur restent listés dans le rapport d'indexation et ne sont jamais présentés comme des connaissances.

## Pages

- [Cœur de raisonnement](coeur.md)
- [Matière première et extraction](matiere-extraction.md)
- [Clarification](clarification.md)
- [Fermentation](fermentation.md)
- [Microbiologie](microbiologie.md)
- [Assemblage](assemblage.md)
- [Finition et sensoriel](finition-sensoriel.md)
- [Conditionnement](conditionnement.md)
- [Concentration](concentration.md)
- [Mesures](mesures.md)
- [Hygiène](hygiene.md)

Le [registre des sources](sources.json) contient les 194 fiches Word inventoriées, leurs empreintes, leurs repères de paragraphes et leur rôle. Les pages du dossier [sources](sources/) permettent d'ouvrir les originaux par famille. Les règles d'évolution figurent dans [GOUVERNANCE](GOUVERNANCE.md) et les anomalies à revoir dans [RESERVES](RESERVES.md).
