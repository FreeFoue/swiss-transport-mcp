# Ridolo — Déploiement du serveur MCP et intégration Sandra

État documenté au 16 octobre 2026. Cette documentation décrit les opérations réalisées et les résultats observés ; elle ne constitue pas un audit complet de sécurité ou de conformité.

## Architecture

```text
Utilisateur WhatsApp
        ↓
Sandra / agent Lina (hébergé chez Infomaniak selon l'exploitant)
        ├── Infomaniak AI Services : génération des réponses
        └── https://mcp.ridolo.com/mcp
                   ↓ HTTPS + jeton Bearer
                 Nginx
                   ↓ HTTP sur 127.0.0.1:8000
                 Serveur MCP Docker
                   ↓
                 API opentransportdata.swiss / OJP
```

GitHub conserve le code et son historique. Le service s'exécute sur le VPS : GitHub n'intervient pas dans les requêtes. Aucun déploiement automatique depuis GitHub n'a été configuré dans cette procédure.

## Infrastructure déployée

- VPS Infomaniak sous Ubuntu 24.04.
- Code dans `~/swiss-transport-mcp` ; dépôt `FreeFoue/swiss-transport-mcp`.
- Service Docker Compose : `swiss-transport-mcp`.
- Transport MCP : Streamable HTTP, chemin `/mcp`.
- Publication Docker : `127.0.0.1:8000:8000` ; écoute interne sur `0.0.0.0:8000`.
- Nginx termine HTTPS et transmet les requêtes MCP au conteneur.
- Certificat Let's Encrypt installé par Certbot ; tâche de renouvellement automatique annoncée par Certbot. Le renouvellement doit également être vérifié périodiquement.
- DNS : enregistrement A pour `mcp.ridolo.com` vers l'IPv4 du VPS.
- Pare-feu Infomaniak : TCP 80 et 443 ouverts aux connexions publiques ; règle SSH existante conservée. UFW était inactif lors du diagnostic.

Le conteneur utilise un utilisateur non privilégié, un système de fichiers en lecture seule, `no-new-privileges`, une limite mémoire de 256 Mo, 0,5 CPU et 128 processus. Politique de redémarrage : `unless-stopped`.

## Authentification et secrets

Deux usages distincts :

1. Le jeton Bearer de Sandra autorise l'accès à l'endpoint MCP via Nginx.
2. `TRANSPORT_API_KEY` autorise les appels du MCP à l'API de transport.

Les clés applicatives sont configurées dans `.env` sur le VPS, avec des permissions `600`. Ce fichier n'est pas suivi par Git. Certaines fonctions complémentaires nécessitent leurs propres clés : `SIRI_SX_API_KEY`, `OCCUPANCY_API_KEY`, `FORMATION_API_KEY`, `OJP_FARE_API_KEY`. Leur fonctionnement n'a pas été validé dans les tests décrits ici.

Le jeton de Sandra est conservé séparément sur le VPS dans `~/.ridolo-sandra-token` et configuré dans Nginx ainsi que dans Sandra. Ne pas publier ce fichier ni une copie de la configuration Nginx contenant le jeton. Ne pas confondre ce jeton avec celui d'Infomaniak AI Services ou de GitHub.

Un appel HTTPS à `/mcp` sans jeton a retourné `401`. La découverte des outils depuis Sandra avec authentification a fonctionné.

Le serveur émet encore un avertissement concernant l'absence de `MCP_ALLOWED_HOSTS`. Le port Docker reste limité à l'interface locale ; la configuration exacte de validation Host/Origin doit être revue avant toute modification de l'exposition réseau. CORS ne remplace pas l'authentification.

## Intégration Sandra

- URL : `https://mcp.ridolo.com/mcp`.
- Transport : Streamable HTTP.
- Authentification : Bearer ; renseigner le jeton dans le champ prévu.
- L'intégration Infomaniak a été sélectionnée pour l'agent ; l'ancienne intégration Render a été désélectionnée et conservée comme solution de repli.
- Le fournisseur Infomaniak AI a été configuré dans un agent dupliqué. Le modèle observé dans les essais est `mistralai/Mistral-Small-4-119B-2603`, température `0.3`, maximum de sortie `4096` tokens.

La découverte des outils, un test de départs dans le Playground et un échange WhatsApp ont fonctionné. Le test final du correctif des fuseaux a été observé dans le Playground ; il reste à refaire le même scénario dans WhatsApp avec la configuration finale de l'agent.

## Correctif des fuseaux horaires

### Problème constaté

Les réponses OJP contiennent des horodatages UTC, avec le suffixe `Z`. Le modèle affichait certaines heures UTC comme s'il s'agissait d'heures suisses, produisant un décalage de deux heures dans les essais d'octobre.

### Solution

Commit : [`7a0eef7`](https://github.com/FreeFoue/swiss-transport-mcp/commit/7a0eef7), publié sur `main`.

Fichiers concernés :

- `src/swiss_transport_mcp/local_times.py` : résolution géographique des fuseaux et enrichissement des horaires.
- `src/swiss_transport_mcp/ojp_client.py` : intégration aux résultats de départs et d'itinéraires.
- `pyproject.toml` : ajout de `tzfpy==2.0.0` et `tzdata==2026.5`.

Le fuseau est déterminé hors ligne à partir des coordonnées des lieux renvoyées par OJP. Le code utilise les fuseaux IANA, par exemple `Europe/Zurich`, et `zoneinfo` pour appliquer les règles d'heure d'été et d'hiver. Il ne déduit pas le fuseau de l'utilisateur ou d'un identifiant de gare.

Les champs d'origine sont conservés. Pour un champ tel que `departure`, le serveur ajoute :

| Champ | Rôle |
| --- | --- |
| `departure_timezone` | Fuseau du lieu de départ |
| `departure_local` | Horodatage local ISO avec décalage UTC |
| `departure_display` | Date et heure lisibles, avec le nom du fuseau |
| `departure_local_status` | Résultat de la résolution |

Exemple observé : `2026-10-16T18:38:00Z` devient `2026-10-16T20:38:00+02:00` pour Genève.

Les horaires prévus et estimés sont enrichis séparément. Départ et arrivée utilisent chacun le fuseau de leur lieu. Sans coordonnées exploitables ou correspondance certaine, le code indique un statut non résolu et ne fabrique pas d'heure locale. Les extrémités à pied ou de transfert peuvent rester non résolues.

Le champ `*_display` affiche les minutes sans les secondes : une estimation à `21:24:42` s'affiche `21:24`. Le prompt doit distinguer les horaires prévus des estimations et éviter de présenter cette précision réduite comme une heure garantie.

### Utilisateurs situés dans d'autres pays

L'heure du trajet reste celle du lieu concerné : un utilisateur à Londres qui consulte un départ de Lausanne voit l'heure de Lausanne. Une éventuelle heure supplémentaire dans le fuseau personnel de l'utilisateur constitue une fonction distincte, à implémenter et étiqueter explicitement.

Cette gestion des fuseaux ne garantit pas la disponibilité de données de transport pour Londres, Lisbonne ou Bucarest : la couverture dépend des API de transport utilisées.

## Vérifications réalisées et limites

- Construction de l'image Docker réussie.
- Conversion suisse testée en été (`+02:00`) et en hiver (`+01:00`).
- Réponse réelle Genève–Lausanne enrichie avec des champs locaux résolus.
- Conteneur redémarré ; démarrage applicatif réussi.
- Réponse Sandra Lausanne–Genève affichant des heures locales cohérentes après correction.
- Envoi du commit sur GitHub confirmé : `7aa94ec..7a0eef7 main -> main`.

Points restant à corriger ou confirmer :

- Limiter la réponse au nombre demandé : quatre trains ont été affichés pour une demande de trois.
- Restituer les retards disponibles et distinguer heure prévue et heure estimée.
- Indiquer explicitement où descendre quand le terminus du train diffère de la destination demandée.
- Examiner un appel d'outil contenant un identifiant d'intégration incorrect dans une trace Sandra.
- Ajouter les tests automatisés de fuseaux au dépôt : le commit publié contient uniquement les trois fichiers applicatifs ci-dessus.
- Vérifier le renouvellement du certificat et planifier le redémarrage système demandé par Ubuntu.

## Exploitation courante

Depuis `~/swiss-transport-mcp` sur le VPS :

```bash
sudo docker compose ps
sudo docker compose logs --tail=50
sudo nginx -t
```

Vérification de l'authentification :

```bash
curl -sS -o /dev/null -w 'HTTP %{http_code}\n' --max-time 15 https://mcp.ridolo.com/mcp
```

Résultat attendu sans jeton : `HTTP 401`. Ce test ne valide pas un appel MCP authentifié ; compléter avec « Test connection » dans Sandra et une question utilisant réellement un outil.

Pour tester le renouvellement du certificat :

```bash
sudo certbot renew --dry-run
```

### Mise à jour du code

Avant toute mise à jour, contrôler `git status`, sauvegarder les modifications locales et lire les changements entrants. Une mise à jour GitHub n'est pas automatiquement appliquée au VPS.

Lorsque la copie locale est prête à recevoir la mise à jour :

```bash
git pull --ff-only
sudo docker compose build
```

Ne poursuivre qu'après une construction réussie et les vérifications adaptées :

```bash
sudo docker compose up -d --no-build
sudo docker compose ps
```

Tester ensuite la connexion et un itinéraire depuis Sandra.

### Sauvegarde et retour arrière

Avant le correctif, l'image a été conservée sous `swiss-transport-mcp:before-timezones`. Une sauvegarde des sources existe sur le VPS dans `timezone-backup-20261016-183408/` ; ce dossier n'a pas été ajouté au commit.

Ces copies locales ne remplacent pas une sauvegarde externe. Avant un retour arrière, vérifier que l'image et les fichiers existent encore. Restaurer uniquement les sources ne change pas le conteneur actif : il faut également choisir ou reconstruire l'image puis recréer le service.

## Périmètre de confidentialité

Le VPS MCP et le fournisseur d'inférence choisi sont chez Infomaniak. L'hébergement de Sandra en Suisse est indiqué par l'exploitant. Le canal WhatsApp implique cependant Meta : ne pas présenter l'ensemble du parcours comme exclusivement suisse sans examen de tous les flux, sous-traitants, journaux et sauvegardes.

L'hébergement en Suisse ne démontre pas à lui seul une conformité RGPD ou LPD. Les futurs lieux personnels tels que domicile, travail ou médecin nécessitent une conception dédiée : minimisation, contrôle d'accès, durée de conservation et suppression. Leur stockage n'a pas été implémenté dans les opérations documentées ici.
