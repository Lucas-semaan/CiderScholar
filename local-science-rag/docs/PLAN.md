# Plan technique court

1. **Socle local** — configuration Pydantic/YAML, arborescence `data`, SQLite et migrations
   explicites.
2. **Ingestion** — SHA-256, extraction PyMuPDF page par page, métadonnées déterministes, détection OCR,
   découpage scientifique, cache de reprise et persistance atomique.
3. **Indexation — livrée** — embeddings multilingues par petits lots, Qdrant embarqué,
   reprise par `embedding_status` et reconstruction contrôlée des index.
4. **Recherche** — couche FTS5, Qdrant, fusion RRF pondérée, agrégation et diversité de vingt
   articles distincts **livrées** ; expansion FR/EN locale et reranking optionnel à poursuivre.
5. **Preuves et synthèse — livrées** — client ARGO, passages limités, fiches par article,
   regroupement thématique, synthèse finale, citations contrôlées, bibliographie SQLite et reprise.
6. **Produit initial — interface livrée** — Streamlit local, exports Markdown/JSON/BibTeX et
   administration de session.
7. **Tests et évaluation — livrés** — matrice des tests obligatoires, métriques P@20/R@20/MRR/nDCG,
   traçabilité des preuves, mesure mémoire et rapports Markdown/JSON reproductibles.
8. **Documentation finale — livrée** — procédure Windows, exploitation, sauvegarde, diagnostic et
   audit des treize critères d’acceptation. L’audit déclare séparément les limites du cahier des
   charges élargi : routes FastAPI métier, expansion automatique, reranker, cache de résultats signé
   et mises à jour officielles facultatives.

Chaque traitement lourd reste séquentiel par défaut. Les modèles sont chargés tardivement, puis
libérés lorsque leur étape est terminée.
