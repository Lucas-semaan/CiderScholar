# Test d’acquisition authentifiée auprès des publishers

Cette fonction est réservée aux collectes pour lesquelles l’utilisateur dispose d’une autorisation
explicite de l’établissement et des éditeurs concernés. Elle est inactive dans la configuration par
défaut.

## Capacités couvertes

- conservation du mot de passe LDAP sous forme chiffrée avec Windows DPAPI ;
- remplissage automatique d’un formulaire d’authentification avec Playwright et Microsoft Edge ;
- navigation en série sur un maximum de 1 000 notices bibliographiques par exécution ;
- extraction des cookies du contexte navigateur puis réutilisation dans un client HTTP borné ;
- téléchargement d’un PDF ou impression PDF du texte intégral HTML ;
- ingestion par le pipeline scientifique existant et liaison durable entre la notice, le PDF,
  l’article local et l’exécution d’acquisition.

Le mot de passe en clair ne figure jamais dans SQLite, `config.yaml`, une réponse HTTP ou un journal.
Le registre utilisateur Windows ne contient qu’un blob DPAPI déchiffrable par le même compte
Windows. La suppression depuis l’interface retire l’identifiant et le blob du profil utilisateur.

## Dépendance navigateur

Installer les dépendances Python :

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Le profil par défaut utilise le Microsoft Edge déjà installé sur Windows. Pour utiliser le Chromium
fourni par Playwright, régler `browser_channel: chromium` et installer le navigateur :

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
```

## Configuration d’un profil autorisé

Activer explicitement le réseau et ajouter un profil dans `config.yaml`. Les URL, domaines et
sélecteurs ci-dessous sont des exemples fictifs à remplacer par ceux communiqués ou validés par
l’éditeur et INRAE :

```yaml
app:
  host: 127.0.0.1
  api_port: 8000
  offline_mode: false
  allow_bibliographic_apis: true
  allow_publisher_automation: true
  log_level: INFO

publisher_access:
  enabled: true
  username_env: CIDERSCHOLAR_LDAP_USERNAME
  password_env: CIDERSCHOLAR_LDAP_PASSWORD_DPAPI
  browser_channel: msedge
  headless: true
  navigation_timeout_seconds: 60
  request_delay_seconds: 1.0
  max_records_per_run: 500
  max_download_bytes: 104857600
  profiles:
    - id: publisher_authorise
      label: Publisher autorisé
      login_url: https://auth.publisher.example/login
      allowed_domains:
        - publisher.example
      username_selector: "#username"
      password_selector: "#password"
      submit_selector: "button[type='submit']"
      success_selector: "[data-authenticated='true']"
      article_ready_selector: "main article"
      pdf_link_selectors:
        - "a[data-action='download-pdf']"
        - "a[href*='.pdf']"
      full_text_selector: "main article"
```

`allowed_domains` doit inclure tous les domaines de premier niveau indispensables au formulaire et
aux pages finales. Les sous-domaines sont acceptés automatiquement. Les téléchargements et toutes
leurs redirections restent obligatoirement en HTTPS et dans cette liste. Un cookie n’est transféré au
client HTTP que si son domaine appartient également à cette liste.

Le moteur automatise actuellement un formulaire à une seule étape. Une authentification avec MFA,
CAPTCHA, iframe inter-domaines ou succession de plusieurs formulaires nécessite un adaptateur de
profil spécifique.

## Lancer le test

1. Redémarrer CiderScholar après avoir modifié `config.yaml`.
2. Ouvrir **Paramètres > Test d’accès publishers autorisé**.
3. Enregistrer l’identifiant et le mot de passe LDAP.
4. Coller les DOI ou identifiants internes des notices, un par ligne.
5. Indiquer la référence traçable de l’autorisation puis lancer la collecte.

Les DOI doivent déjà exister dans la base documentaire. L’exécution et ses éléments sont conservés
dans `publisher_access_runs` et `publisher_access_run_items`. Les fichiers acquis sont rangés sous
`data/pdf/publisher/<record-id>/`, puis référencés dans `publisher_full_text_assets`.

## API locale

- `GET /api/publisher-access/status`
- `PUT /api/publisher-access/credentials`
- `DELETE /api/publisher-access/credentials`
- `POST /api/publisher-access/runs`
- `GET /api/publisher-access/runs/{run_id}`

Les modèles de requête refusent les champs inconnus. Les actions d’enregistrement et de collecte
exigent `authorization_confirmed: true`, et chaque collecte exige une `authorization_reference`.
