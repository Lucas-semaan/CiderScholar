# Validation physique du mode approfondi

DRS-026 demande deux exécutions distinctes, sur un poste disposant réellement d’environ 8 Go puis
sur un poste d’environ 16 Go. Modifier artificiellement le profil d’un poste ne satisfait pas ce
critère.

Sur chaque poste, depuis exactement la même révision et avec exactement le même corpus :

```powershell
python -m scripts.validate_deep_research_profile `
  --profile 8gb `
  --config config.yaml `
  --output artifacts/deep-research/profile-8gb.json
```

Remplacer `8gb` par `16gb` sur le second poste. Le runner mesure la RAM physique, la mémoire de
chaque sous-processus et la mémoire système. Il exécute séparément les contrôles figés de reprise,
annulation, cache avec corpus privé et absence de contenu dans les logs. Le rapport ne conserve que
les identifiants de tests, leurs empreintes, leur durée et l’empreinte de sortie.

Lorsque les deux rapports passent :

```powershell
python -m scripts.finalize_deep_research_profiles `
  --eight-gb artifacts/deep-research/profile-8gb.json `
  --sixteen-gb artifacts/deep-research/profile-16gb.json `
  --output artifacts/deep-research/profiles-final.json
```

Le finaliseur refuse un test manquant, une signature modifiée, un profil simulé, un échec ou une
différence de révision/corpus.
