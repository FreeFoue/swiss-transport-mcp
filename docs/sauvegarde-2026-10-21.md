# Sauvegarde du serveur — 21 octobre 2026

Les dix fichiers modifiés ou ajoutés ont été récupérés sur le VPS et sont conservés tels quels, sur la base GitHub 5bb0a51.

Inclus : correctifs SIRI-SX (lecture en flux, cache persistant et quotas), conversion des heures de requête OJP, configuration Docker et tests.

Exclus : .env, clés, journaux, caches, sauvegardes locales du VPS. Le prompt et les réglages de Mobi dans Sandra ne sont pas exportés ici. Cette sauvegarde du code ne constitue pas une image complète du serveur.

Validation de la copie : 312 tests hors ligne réussis, 3 ignorés, 7 exclus ; git diff --check réussi. Aucun déploiement effectué pendant la sauvegarde.

La documentation historique SIRI-SX mentionne 64 Mio : le code sauvegardé fixe désormais MAX_BYTES à 128 Mio. La limite Docker est de 1 Gio. La documentation du 16 octobre décrit un état antérieur ; l'agent s'appelle désormais Mobi.

Les résultats SIRI-SX constituent une recherche textuelle partielle. Une liste vide ne prouve pas l'absence de perturbation sur un itinéraire. Les alertes illisibles ou incomplètes limitent la couverture.
