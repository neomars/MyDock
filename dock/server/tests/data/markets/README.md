# Réponses d'exemple des fournisseurs de cotations

Ces fichiers ont le **format des réponses** de Yahoo Finance (`v8/finance/chart`), de Binance
(`/api/v3/ticker/24hr`, `/api/v3/klines`) et de Finnhub (`/api/v1/quote`). Ils ont été **construits
d'après la documentation et d'après `dashboard_crypto`**, pas enregistrés : le conteneur de
développement n'a pas accès à ces services. Les tests prouvent donc que le dock lit correctement
ce format, pas que les services répondent encore ainsi aujourd'hui (voir le rapport de l'étape e).

« Maintenant » vaut 1793370000 (30 octobre 2026, 14 h 20 UTC) dans les tests.
