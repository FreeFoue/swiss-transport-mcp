# SIRI-SX — correctif Ridolo

## Comportement

- Téléchargement XML en flux, libération des éléments après extraction.
- Limites : 64 Mio décompressés, 20 000 alertes, environ 2 Mio par élément non libéré.
- Cache des alertes extraites pendant 35 minutes, commun aux langues et recherches.
- Compteurs persistants : maximum 10 tentatives par heure glissante et 48 par 24 heures glissantes. Les échecs sont comptés, sans nouvelle tentative automatique.
- Verrou entre processus ; téléchargement dans un thread pour ne pas bloquer les autres outils.
- Cache et compteurs dans le volume Docker `siri-state`, sans clé API ni requêtes voyageurs. Ne pas supprimer ce volume pour réinitialiser les quotas. Les quotas ne couvrent pas les autres consommateurs éventuels de la même clé.
- Erreurs explicites : authentification, quota, données invalides, indisponibilité. Aucun échec n'est présenté comme une absence de perturbations.
- Recherche textuelle sans accents, correspondant à au moins un terme. Une recherche Genève–Lausanne peut donc retourner des alertes sur l'une des deux villes ; ce n'est pas une analyse exhaustive de l'itinéraire.
- Les alertes closes/expirées sont exclues. Les alertes futures et celles dont la période n'est pas déterminable restent étiquetées séparément.
- L'heure de récupération et l'âge du cache sont fournis. Ils ne garantissent pas la fraîcheur de publication de la source. Ce service n'est pas une surveillance à la minute.

## Déploiement

Installer les fichiers avec `installer.py` depuis le paquet, qui vérifie leur version précédente et sauvegarde les fichiers modifiés. Il ne modifie ni `.env`, ni Nginx, ni les horaires locaux.

Sur le VPS, dans `~/swiss-transport-mcp` :

```bash
sudo docker image tag swiss-transport-mcp:latest swiss-transport-mcp:before-siri-fix
sudo docker compose config --quiet
sudo docker compose build
sudo docker compose up -d --no-build
sudo docker compose ps
sudo docker stats --no-stream
```

La nouvelle image crée le répertoire de cache pour l'utilisateur non privilégié UID 10001. Le volume est indispensable avec `read_only: true`. La limite mémoire est conservée à 1 Go pour laisser une marge en production.

Tester UNE demande dans Sandra, puis inspecter `get_transport_disruptions`. Attendu : `status=ok`, `fetched_at`, `age_seconds`, `alerts`. Une deuxième demande devrait indiquer `cached=true`. Rafraîchir la découverte des outils dans Sandra pour obtenir la nouvelle description, puis réinitialiser le chat de test.

Ne pas annoncer « aucune perturbation sur le trajet » sur la seule base d'une liste vide. Le prompt Mobi doit reprendre le statut, l'âge du cache et les limites de couverture.

## Retour arrière

Avant de remplacer les sources, arrêter le service si le retour arrière est nécessaire. Restaurer les fichiers depuis le dossier de sauvegarde indiqué par l'installateur, puis :

```bash
sudo docker image tag swiss-transport-mcp:before-siri-fix swiss-transport-mcp:latest
sudo docker compose up -d --no-build --force-recreate
```

Ne pas utiliser `docker compose down -v` : il supprimerait notamment les compteurs persistants.

## Validation

Tests hors ligne : cache, concurrence, quotas, erreurs HTTP, XML invalide, périodes, recherche multilingue, gros flux synthétique. Aucun appel réel SIRI-SX effectué pendant la préparation. Le test de gros flux traite plus de 38 Mio de XML avec moins de 8 Mio d'allocations Python mesurées ; ce n'est pas une mesure de la mémoire totale du conteneur.

La construction Docker et la validation du flux réel restent à effectuer sur le VPS. Les bornes de taille peuvent refuser un flux réel plus volumineux ; ce refus reste une indisponibilité explicite.
