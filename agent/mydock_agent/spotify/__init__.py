"""Spotify : recherche, parcours de la bibliothèque et lecture, pour la page Spotify du dock.

Le lecteur lui-même (lecture, pause, morceau suivant, volume, pochette en cours) se pilote par MPRIS : voir
`collectors/music.py`. Ce paquet ajoute ce que MPRIS ne sait pas faire (chercher, parcourir, jouer un album précis)
avec l'API web de Spotify :

- `http.py`    le transport HTTP, limité aux serveurs de Spotify ;
- `auth.py`    la connexion de l'utilisateur (OAuth « code d'autorisation » avec PKCE, sans secret) et son jeton ;
- `catalog.py` ce que l'API renvoie, remis en forme pour l'écran (pures fonctions) ;
- `service.py` les requêtes, la lecture (API, sinon MPRIS) et le lancement de l'application ;
- `actions.py` la liste blanche des actions `spotify.*` ;
- `cli.py`     `mydock-agent spotify login | logout | status`.

Rien n'est stocké sur le Pi : le jeton vit sur le PC, dans `~/.config/mydock/spotify.toml` (droits 600).
"""
