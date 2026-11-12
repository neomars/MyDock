# MyDock

Un dock tactile pour piloter un PC Ubuntu : un Raspberry Pi 4 avec un écran 7" (1024×600) posé à côté du PC affiche son état et permet de le commander. Le dock reste utile PC éteint : horloge, bourse, et bouton **Allumer le PC** (Wake-on-LAN).

**Une seule chose à installer : le paquet `mydock` sur le Pi.** Le Pi sert alors, sur le réseau local, une **page web** d'où le PC (Ubuntu ou autre) télécharge son propre paquet et s'associe au dock avec un code affiché sur l'écran : rien à copier, aucun jeton à recopier.

| Accueil | PC éteint | Dock neuf : associer un PC |
|---|---|---|
| ![Accueil : un gros bouton par page](docs/etapes/i/00-accueil.png) | ![Accueil, PC éteint : bandeau et boutons grisés](docs/etapes/i/00b-accueil-pc-eteint.png) | ![Accueil d'un dock neuf : le code à saisir sur le PC](docs/etapes/i/00c-accueil-association.png) |

| Spotify : en lecture | Spotify : recherche | Spotify : un album |
|---|---|---|
| ![Page Spotify : le lecteur](docs/etapes/i/01-lecteur.png) | ![Page Spotify : résultats de recherche](docs/etapes/i/04-recherche.png) | ![Page Spotify : un album](docs/etapes/i/05-album.png) |

| Spotify : sortie sonore | Spotify : sur une enceinte | Réglages : économiseur |
|---|---|---|
| ![Page Spotify : choisir la sortie sonore](docs/etapes/j/01-sortie-sonore.png) | ![Page Spotify : la musique sort sur une enceinte](docs/etapes/j/03-lecteur-sur-une-enceinte.png) | ![Réglages : l'économiseur d'écran](docs/etapes/j/04-reglage-economiseur.png) |

| Économiseur : montagnes | Économiseur : fonds marins | Économiseur : ville de nuit |
|---|---|---|
| ![Économiseur d'écran : montagnes, lever du jour](docs/etapes/j/06-scene-montagnes.png) | ![Économiseur d'écran : fonds marins](docs/etapes/j/07-scene-fonds-marins.png) | ![Économiseur d'écran : ville de nuit, vue aérienne](docs/etapes/j/08-scene-ville.png) |

| Divertissement : l'accueil | Freetv : les chaînes | Kiosque : un dossier de la Freebox |
|---|---|---|
| ![Accueil : le bouton Divertissement, et l'alimentation en petit à droite de l'heure](docs/etapes/k/01-accueil-divertissement.png) | ![Freetv : la liste des chaînes, avec recherche](docs/etapes/k/04-freetv-liste.png) | ![Kiosque : les vidéos d'un dossier du partage Samba](docs/etapes/k/07-kiosque-dossier.png) |

| Spotify : bibliothèque | Son | Système |
|---|---|---|
| ![Page Spotify : la bibliothèque](docs/etapes/i/07-bibliotheque.png) | ![Page Son](docs/etapes/h/05-son.png) | ![Page Système](docs/etapes/h/10-systeme.png) |

| Applis | Steam | Office |
|---|---|---|
| ![Page Applis](docs/etapes/h/06-applis.png) | ![Page Steam](docs/etapes/h/07-steam.png) | ![Page Office](docs/etapes/h/08-office.png) |

| Bourse | Alimentation | Réglages |
|---|---|---|
| ![Page Bourse](docs/etapes/h/09-bourse.png) | ![Page Alimentation](docs/etapes/h/11-alimentation.png) | ![Page Réglages](docs/etapes/h/12-reglages.png) |

| Clavier tactile | Confirmation | Pages et ordre |
|---|---|---|
| ![Clavier tactile](docs/etapes/h/20-clavier.png) | ![Confirmation d'une action sensible](docs/etapes/h/21-confirmation.png) | ![Pages du dock et leur ordre](docs/etapes/h/22-pages-et-ordre.png) |

| Page d'installation, vue du PC | Choix des applications | Choix des valeurs de la Bourse |
|---|---|---|
| ![Page d'installation servie par le Pi](docs/etapes/g/06-portail-sur-le-pc.png) | ![Applications du lanceur](docs/etapes/h/23-applications.png) | ![Valeurs de la Bourse](docs/etapes/h/24-bourse-ajouter.png) |

*Captures prises en mode démo (fausses données), en 1024×600 tactile, texte doublé. PC éteint, une page du PC explique pourquoi : [écran « PC hors ligne »](docs/etapes/h/14-ecran-hors-ligne.png).*

## Où en est le projet

La livraison se fait par étapes, chacune utilisable.

| Étape | Contenu | État |
|---|---|---|
| **a** | Squelette : agent sécurisé, serveur du dock, kiosque, écran « PC hors ligne », Wake-on-LAN, Réglages | **livrée** ([rapport](docs/etapes/a/RAPPORT.md)) |
| **b** | Système (CPU, mémoire, GPU sans réveiller la carte NVIDIA, réseau, disques, historique de 60 s, processus avec arrêt confirmé) et Musique (MPRIS, pochette, progression glissable, mini-lecteur) | **livrée** ([rapport](docs/etapes/b/RAPPORT.md)) |
| **c** | Son (sortie par défaut, volume et muet par sortie et par application, micro et micro utilisé, volume sur la page Musique) | **livrée** ([rapport](docs/etapes/c/RAPPORT.md)) |
| **d** | Lanceur d'applications (choisies dans Réglages, dans l'ordre voulu), Steam (jaquettes, jeu en cours), LibreOffice (nouveau document, documents récents) | **livrée** ([rapport](docs/etapes/d/RAPPORT.md)) |
| **e** | Bourse : cryptos et actions lues par le Pi (Yahoo Finance et Binance, sans clé), liste au choix, graphique sur 1 j à 1 an, utilisable PC éteint | **livrée** ([rapport](docs/etapes/e/RAPPORT.md)) |
| **f** | Alimentation (verrouiller, veille, redémarrer, éteindre, chacun avec ses garde-fous), Réglages complets (pages visibles et ordre, veille de l'écran du dock), finitions | **livrée** ([rapport](docs/etapes/f/RAPPORT.md)) |
| **g** | **Installation simplifiée** : un seul paquet (`mydock`) sur le Pi, qui sert une page web où le PC prend son paquet puis s'associe par un code affiché sur l'écran du dock | **livrée** ([rapport](docs/etapes/g/RAPPORT.md)) |
| **h** | **Accueil en gros boutons** (comme les touches d'un Stream Deck), retour par un **balayage de gauche à droite**, **texte doublé** pour l'écran de 7" | **livrée** ([rapport](docs/etapes/h/RAPPORT.md)) |
| **i** | **Spotify** à la place de « Musique » : lecteur complet, **recherche**, **bibliothèque**, pages d'album, d'artiste et de playlist, aléatoire et répétition, lancement de l'application ; et **les applications Snap, Flatpak et Electron se lancent enfin depuis le dock** | **livrée** ([rapport](docs/etapes/i/RAPPORT.md)) |
| **j** | **Choisir la sortie sonore de Spotify** (enceintes connectées en Wi-Fi, téléphones, télévisions : Spotify Connect) et **économiseur d'écran** : des vues animées plein écran, comme celles de l'Apple TV, après un délai réglable (scènes dessinées en direct, vidéos téléchargées à l'adresse saisie à l'écran) | **livrée** ([rapport](docs/etapes/j/RAPPORT.md)) |
| **k** | **Divertissement** à la place du bouton Alimentation (qui devient un petit bouton à droite de l'heure) : **Freetv** (les chaînes de la Freebox) et **Kiosque** (ses partages Samba), lus par **VLC sur l'écran du dock** | **livrée** ([rapport](docs/etapes/k/RAPPORT.md)) |

Le cahier des charges est entièrement livré. Ce qui reste est de **vérifier sur le vrai matériel** ce qu'un conteneur de développement ne peut pas essayer : la liste est à la fin du [rapport de l'étape g](docs/etapes/g/RAPPORT.md).

## Comment ça marche

```
navigateur du PC ──http──▶  portail du Pi (port 80)   page d'installation, paquet du PC, association par code
                                     │
Chromium (kiosque) ──ws──▶  serveur dock (Pi, 127.0.0.1)  ──ws + jeton──▶  agent (PC, ta session)
 interface Svelte            configuration · Wake-on-LAN · bourse           D-Bus · PipeWire · psutil
```

- **Le Pi** est la seule machine à installer à la main : le paquet `mydock` contient le serveur du dock, l'interface, le kiosque, la veille de l'écran **et le paquet du PC**.
- **Le portail** est une petite page web que le Pi sert à tout le réseau local (port 80). Elle propose le paquet du PC, dit quoi taper, et reçoit le code d'association. Elle ne donne ni jeton ni configuration.
- **L'agent** tourne sur le PC, dans la session graphique (service systemd *utilisateur*). Il n'envoie son état que lorsqu'il change et n'exécute que des actions nommées d'une liste blanche.
- **Le serveur du dock** sert l'interface, garde la configuration, envoie le Wake-on-LAN et se reconnecte tout seul à l'agent (1 à 10 s). Le navigateur du kiosque ne parle qu'à lui, en local : il ne voit jamais le jeton.
- **L'écran** est un Chromium en mode kiosque dans un compositeur Wayland minimal (labwc).

À l'association, le dock enregistre l'**adresse IPv4** du PC telle qu'il la voit (la plus fiable) : pas de nom `.local` à résoudre depuis le Pi, ce qui échoue souvent. Réserve-la dans ta box pour qu'elle ne change pas.

## Installation

Trois gestes : installer le paquet sur le Pi, ouvrir la page du Pi depuis le PC, taper le code affiché sur l'écran.

### Prérequis

- **Pi** : Raspberry Pi 4 sous **Raspberry Pi OS 64 bits « Trixie » (Debian 13)**, avec bureau (labwc) ou Lite, écran tactile USB + HDMI. Le paquet s'appuie sur les paquets Python de la distribution (fastapi 0.115+, uvicorn, websockets 13+, psutil, pydantic 2.7+) : ceux de l'ancienne version « Bookworm » sont trop anciens, `apt` refuserait l'installation (voir le [dépannage](#dépannage)).
- **PC** : Ubuntu 26.04 (GNOME, Wayland ou X11) ou Debian 13, avec une session graphique ouverte. Autre distribution : [voir plus bas](#autre-système-que-ubuntu-ou-debian).
- Le PC et le Pi sur le même réseau local.

### 1. Sur le Pi : un seul paquet

```bash
git clone https://github.com/neomars/MyDock.git
cd MyDock
python3 packaging/build_deb.py --target pi      # → build/mydock_<version>_all.deb  (seul dpkg-deb est requis)
sudo apt install ./build/mydock_*_all.deb
sudo reboot                                      # l'écran affiche le dock en plein écran
```

`apt` installe les dépendances (labwc, Chromium, swayidle, les paquets Python…) et le paquet fait le reste : utilisateur système `mydock`, configuration en droits 600 (`/etc/mydock/dock.toml`), services, **kiosque** (il détecte le type d'image : bureau Raspberry Pi OS → le kiosque démarre avec la session ouverte automatiquement, l'`autostart` d'origine est sauvegardé en `.avant-mydock` ; Lite → labwc et Chromium sur la console 1 par un service systemd), et veille de l'écran. Pour changer de mode ou forcer la résolution : `sudo /usr/lib/mydock/setup --kiosk systemd` ou `--force-mode 1024x600@60`.

Au redémarrage, **l'écran affiche « Associer un PC » : l'adresse à ouvrir depuis le PC et un code**.

> Le paquet se construit aussi sur le PC (`python3 packaging/build_deb.py --target pi`) et se copie sur le Pi (`scp`) : n'importe quelle machine Debian ou Ubuntu convient, le résultat est le même fichier.

### 2. Sur le PC : la page du Pi

1. Dans un navigateur, ouvrir **l'adresse affichée sur l'écran du dock** (par exemple `http://192.168.1.60/`, ou `http://raspberrypi.local/`).
2. **Télécharger** le paquet proposé, puis dans un terminal de **ta session graphique** (pas en SSH, **pas avec `sudo`** pour la seconde commande) : les deux commandes sont écrites sur la page, avec un bouton *Copier* :

   ```bash
   sudo apt install ./mydock-agent_<version>_all.deb
   mydock-agent setup --pi 192.168.1.60 --open-firewall      # 192.168.1.60 = l'adresse du Pi
   ```
3. La commande demande **le code affiché sur l'écran du dock** : le taper. Le dock répond « PC associé » et se connecte tout seul, en quelques secondes. Il n'y a **rien d'autre à saisir**, ni adresse, ni port, ni jeton.

`mydock-agent setup` :

- crée `~/.config/mydock/agent.toml` (droits 600) avec un jeton de 256 bits, et n'autorise que l'adresse du Pi ;
- démarre le service `mydock-agent` (service systemd *utilisateur*, fourni par le paquet et activé pour tous les utilisateurs, sans effet tant que `setup` n'a pas été lancé) ;
- si `ufw` est actif, autorise le Pi sur le port de l'agent (`--open-firewall` : `sudo` demande ton mot de passe) ; sans le drapeau, il affiche la commande à copier ;
- **associe le PC au dock** : il présente le code au portail du Pi, avec le jeton qu'**il a lui-même généré** ; le Pi vérifie qu'il joint bien l'agent puis enregistre l'adresse, le port et le jeton. Si l'association échoue, la commande dit pourquoi et ce qu'il reste à saisir à la main dans **Réglages → PC piloté**.

La commande est idempotente : la relancer ne change pas le jeton (sauf `--rotate-token`). Options utiles : `--code CODE` (sans question), `--no-pair` (pas d'association), `--portal-port N` (si le portail n'est pas sur le port 80). **`mydock-agent pair --pi ADRESSE`** ne refait que l'association (changement de dock, code manqué).

**Un deuxième PC, ou un autre** : sur le dock, **Réglages → Associer un PC…** affiche un nouveau code (valable dix minutes) ; le PC refait la même chose. Le dock pilote **un PC à la fois** : une nouvelle association remplace l'ancienne.

### Autre système que Ubuntu ou Debian

La page du Pi ne propose aujourd'hui que le paquet `.deb`. Sur une autre distribution, l'agent s'installe depuis les sources (environnement Python dédié et service utilisateur), puis s'associe de la même façon :

```bash
git clone https://github.com/neomars/MyDock.git && cd MyDock
./deploy/pc/install-agent.sh --pi 192.168.1.60
~/.local/share/mydock/venv/bin/mydock-agent pair --pi 192.168.1.60     # le code est sur l'écran du dock
```

Désinstallation : `./deploy/pc/uninstall-agent.sh --purge`. Ce script refuse de tourner si le paquet est installé.

### 3. Vérifier

```bash
# sur le PC
mydock-agent doctor
# sur le Pi
sudo -u mydock mydock-dock doctor
```

Les deux affichent un diagnostic lisible. Celui du PC vérifie la configuration, la session, le réseau, le **pare-feu** (avec les commandes exactes si `ufw` est actif), le service systemd, **le portail du dock** (répond-il, avec quelle version), et prépare le test `curl` à lancer depuis le Pi. Celui du Pi teste **le portail** (port, paquet proposé), **chaque adresse du PC** (port ouvert, refusé ou muet, et ping pour distinguer un pare-feu d'un PC éteint) puis le jeton. Dans l'interface, **Réglages → Tester la connexion** donne le verdict en un toucher, et **Mesurer** donne la latence appui → PC.

### La bourse : aucune clé nécessaire

Yahoo Finance (actions, indices, devises, et cryptos en secours) et Binance (cryptos) répondent sans compte ni clé, comme dans `dashboard_crypto`. Si un jour Yahoo cesse de répondre, une clé **Finnhub** gratuite (finnhub.io) ajoute un secours pour les actions américaines :

```bash
sudo nano /etc/mydock/dock.toml     # ajouter, en gardant les droits 600 :
#   [markets]
#   finnhub_key = "ta-clé"
sudo systemctl restart mydock-dock
```

### Spotify : connecter ton compte

La page Spotify pilote déjà le lecteur du PC (lecture, pause, morceau suivant, aléatoire, volume) **sans rien configurer**. Pour **chercher** et **parcourir ta bibliothèque** depuis le dock, relie ton compte Spotify **une seule fois, sur le PC** (il faut pour cela le paquet du PC en 1.3.0 ou plus, proposé par la page du Pi : un agent plus ancien le dit sur la page Spotify) :

1. Sur <https://developer.spotify.com/dashboard>, **Create app** (le nom est libre : « MyDock »), coche **Web API**, et dans **Redirect URIs** ajoute exactement `http://127.0.0.1:8888/callback`, puis **Save**.
2. Ouvre **Settings** et copie le **Client ID** (32 caractères, ce n'est pas un secret).
3. Sur le PC, dans un terminal de ta session graphique :

```bash
mydock-agent spotify login --client-id TON_CLIENT_ID
```

   Le navigateur du PC s'ouvre sur la page de Spotify ; accepte, et la commande dit « Connecté à Spotify en tant que … ». `mydock-agent spotify status --check` vérifie, `mydock-agent spotify logout` retire la connexion (et `spotify.com/account/apps` retire l'accès côté Spotify).

- **Rien n'est partagé avec le Pi ni écrit dans le dépôt** : le jeton reste sur le PC, dans `~/.config/mydock/spotify.toml` (droits 600). C'est le flux OAuth « code d'autorisation avec PKCE » : il n'utilise **aucun « client secret »**. MyDock ne demande que la lecture de ta bibliothèque et de l'état de la lecture, et la commande de la lecture : jamais de quoi modifier tes playlists ni ton profil, ni lire ton adresse e-mail.
- **Limites imposées par Spotify** aux applications en « mode développement » (règles annoncées en février 2026, qui évoluent : vérifie-les sur developer.spotify.com) : le propriétaire de l'application doit avoir un compte **Premium**, et seuls ce compte et ceux que tu ajoutes dans **User Management** (5 au plus) peuvent s'y connecter. Si Spotify refuse, la page le dit avec la raison.
- **Lire un morceau précis** passe par l'API de Spotify et demande un compte **Premium** : le dock choisit l'appareil « ce PC » (Spotify Connect). Sans Premium, ou si l'appareil n'apparaît pas, le dock demande au lecteur du PC d'ouvrir lui-même l'album, la playlist ou le morceau.
- **Choisir la sortie** (onglet « Sortie sonore ») : la liste vient de **Spotify Connect** et passer la musique sur un appareil est une commande de l'API de Spotify : **compte Premium requis**, comme pour toute commande de lecture. Un appareil n'est proposé que si Spotify le voit : allumé, relié à ton compte, sur le même réseau que ton application. Les droits demandés à la connexion couvrent déjà ces commandes : **pas besoin de te reconnecter**, mais le paquet du PC doit être en **1.4.0**.
- **L'application Spotify** peut être installée en paquet `.deb`, en **Snap** ou en **Flatpak** : le dock la retrouve dans les entrées de menu du PC. `mydock-agent doctor` (section « Spotify ») dit comment elle est installée et si le compte est connecté.

### Mises à jour et désinstallation

```bash
git pull
python3 packaging/build_deb.py --target pi && sudo apt install ./build/mydock_*_all.deb    # Pi : le portail propose aussitôt le nouveau paquet du PC
```

**Chaque livraison porte un nouveau numéro de version** (le paquet s'appelle `mydock_1.5.0_all.deb`, par exemple) : sans cela, `apt install` répondrait « déjà la version la plus récente » et rien ne changerait sur le Pi. Vérifier ce qui est installé : `apt policy mydock mydock-agent` ou la version affichée dans Réglages.

L'écran du dock se met à jour tout seul : quand le dock revient après son redémarrage, l'interface compare ce qu'elle est à ce que le dock sert et se recharge si elles diffèrent. **Sauf la toute première fois**, de la 1.1.0 à la 1.1.1 : relance alors son navigateur (`pkill -x chromium`) ou le Pi.

Sur le PC, télécharger le nouveau paquet depuis la page du Pi et `sudo apt install ./mydock-agent_*_all.deb` : les sessions ouvertes redémarrent leur agent toutes seules, **sans nouvelle association** (le jeton est conservé).

**Depuis l'ancienne installation par script** (`install-dock.sh`, environnement dans `/opt/mydock`) : `sudo apt install ./build/mydock_*_all.deb` reprend la configuration et le jeton existants, retire l'ancien service et sa vieille unité systemd ; le PC déjà associé continue de marcher tel quel. Libérer ensuite la place : `sudo rm -r /opt/mydock`.

**Désinstallation** : `sudo apt remove mydock` retire le kiosque (l'`autostart` d'origine est restauré) et les services, en gardant la configuration ; `sudo apt purge mydock` efface aussi la configuration, le jeton, l'état et les utilisateurs système. Sur le PC : `sudo apt remove mydock-agent` (la configuration reste dans `~/.config/mydock` ; `rm -r` pour l'effacer).

## Utilisation

**Un écran d'accueil de gros boutons**, comme les touches d'un Stream Deck : un bouton coloré par page (neuf, dans l'ordre choisi dans Réglages). On touche un bouton pour ouvrir sa page ; on **revient à l'accueil d'un balayage de gauche à droite**, n'importe où sur l'écran (sauf sur un curseur de volume ou une barre de progression, qui gardent leur geste), ou avec le bouton **⌂** en haut à gauche. L'écran repart toujours de l'accueil. En haut, toujours : le nom du PC et son état, le **mini-lecteur** (pochette, titre, précédent, lecture/pause, suivant : présent sur toutes les pages), l'horloge et, à sa droite, le petit bouton **⏻ Alimentation**. **Texte doublé** pour un écran de 7" lu à bout de bras (courant 40 px, légendes 32 px), cibles tactiles d'au moins 48 px (72 px pour la plupart), thème sombre ; la taille se règle d'un seul nombre, `--ts` dans `dock/web/src/app.css`.

- **Accueil** : un bandeau n'apparaît que s'il y a quelque chose à savoir ou à faire (*PC hors ligne* avec le bouton **Allumer le PC**, *PC allumé, agent arrêté*, *Connexion refusée* avec **Associer à nouveau**, le **code d'association** et l'adresse à ouvrir sur le PC). Les boutons des pages qui ont besoin du PC sont **grisés** quand il est éteint (ils restent touchables : la page explique pourquoi) ; la Bourse et les Réglages, eux, ne dépendent pas du PC. Le bouton **Spotify** montre le morceau en cours, sur sa pochette ; le bouton **Divertissement** dit ce qui est en lecture.

- **Divertissement** : le menu a deux boutons, **Freetv** et **Kiosque**. La vidéo s'affiche **en plein écran sur le dock lui-même**, avec le son du Pi. Voir [Divertissement (Freetv et Kiosque)](#divertissement-freetv-et-kiosque).

- **Spotify** : la page la plus proche possible de l'application officielle. À gauche, **quatre onglets** comme son menu :
  - **En lecture** : pochette, titre, artiste · album, **progression que l'on fait glisser** (appliquée au relâchement), précédent / lecture-pause / suivant, **lecture aléatoire** et **répétition** (rien, la liste, le morceau), et le **volume** de la sortie du PC avec son bouton muet (quand la musique sort sur une enceinte, c'est le volume de **l'enceinte**, et un bandeau dit sur quel appareil elle joue). Spotify fermé : un bouton **Lancer Spotify** démarre l'application du PC et la page attend qu'elle s'annonce.
  - **Rechercher** : un champ au clavier tactile ; les résultats sont rangés en **morceaux, artistes, albums et playlists**, chacun avec sa pochette. Un toucher joue le morceau (dans son album) ou ouvre l'album, l'artiste ou la playlist ; le morceau qui joue est surligné.
  - **Ta bibliothèque** : tes **playlists**, tes **titres likés**, tes **albums**, les **artistes** que tu suis et ce que tu as **écouté récemment**.
  - **Sortie sonore** : où sort la musique. La liste montre les **appareils Spotify Connect** (ce PC d'abord, puis les enceintes connectées en Wi-Fi — Sonos, Echo, Google Nest… —, les téléphones, les télévisions…) avec leur genre et leur volume, l'appareil actif étant coché. **Un toucher y passe la lecture en cours** : la musique continue là où elle en était. De retour dans « En lecture », le volume est celui de l'appareil choisi ; un toucher sur « Ce PC » ramène la musique ici. Dessous, les **sorties du PC** (haut-parleurs, casque, Bluetooth, HDMI) : elles servent quand la musique joue sur ce PC. La liste se relit en ouvrant l'onglet, toutes les 15 s tant qu'il est ouvert, et avec le bouton **Actualiser**.
  Une page d'album, d'artiste ou de playlist a son bouton **Lecture** et la liste de ses morceaux (pour un artiste : ses titres populaires et ses albums). Le mini-lecteur de la barre du haut reste visible partout. Tous les lecteurs MPRIS (VLC, Firefox, Brave…) restent pilotables : avec plusieurs ouverts, des pastilles choisissent celui qu'on commande. **Lecture, pause, suivant, aléatoire, répétition et volume marchent sans compte Spotify** ; la **recherche** et la **bibliothèque** demandent de connecter ton compte, une seule fois : voir [Spotify : connecter ton compte](#spotify--connecter-ton-compte).
- **Son** : la **sortie** (volume au doigt, bouton muet, pastilles pour changer de sortie : haut-parleurs, casque, Bluetooth, HDMI…), le **micro** (volume, muet, choix du micro, et « Micro utilisé par Discord » quand une application l'écoute), et la liste des **applications** qui jouent du son, chacune avec son volume et son bouton muet (Spotify toujours en tête). Le volume de la sortie est aussi sur la page **Spotify**. Le dock ne dépasse jamais 100 % ; bouger le curseur d'un son coupé le rétablit.
- **Applis** : les applications que **tu** choisis (Réglages ou bouton « Modifier » de la page, avec recherche au clavier tactile, ajout, retrait et ordre), en grande grille d'icônes ; un toucher lance l'application. Elles viennent des fichiers `.desktop` du PC (système, utilisateur, Flatpak, Snap), avec le nom dans la langue de la session et l'icône du thème.
- **Steam** : tes jeux installés (tous les disques de jeux, sans les outils comme Proton), avec leurs **jaquettes** (celles du cache de Steam, demandées au fil du défilement) ; les jeux joués récemment d'abord ; un toucher lance le jeu, et le jeu **en cours** passe en tête avec son badge.
- **Office** : trois boutons « Nouveau texte / tableur / présentation » (LibreOffice Writer, Calc, Impress) et la liste de tes **documents récents** (celle de LibreOffice) : un toucher rouvre le document.
- **Bourse** : tes cryptos et tes valeurs (actions, indices, ETF, devises) avec prix, variation **toujours écrite avec son signe** (+1,41 %, −0,32 %), mini-graphique et, pour une action, l'état du marché (ouvert, fermé) et **le dernier échange quand il est ancien** (cours différés ou valeur peu échangée). Un toucher ouvre le graphique de la valeur sur **1 jour, 5 jours, 1 mois ou 1 an**. **Modifier** : on **cherche par nom ou par symbole** au clavier tactile (`apple`, `bitcoin`, `MSTR`, `^FCHI`, `CW8.PA`…) ; le dock interroge Yahoo Finance (il connaît d'avance les cryptos courantes, même sans réseau) et un résultat s'ajoute d'un toucher après vérification de son prix ; on ordonne et on retire ; la devise des cryptos (euro ou dollar) se règle au même endroit, et **la fréquence des mises à jour** (15 s, 30 s, 1 min, 5 min) dans Réglages. C'est **le Pi** qui lit les cotations : la page marche PC éteint, et elle ne demande rien à Internet tant qu'elle n'est pas ouverte. Si un fournisseur ne répond plus, un bandeau dit lequel et pourquoi, et les derniers cours connus restent affichés **avec leur âge** ; le bouton **Tester la connexion** du bandeau dit où ça coince (heure du Pi fausse, nom introuvable, IPv4 ou IPv6 muet, fournisseur qui refuse) et ce qu'il faut faire.
- **Système** : processeur (charge, fréquence, température), mémoire, **carte graphique**, réseau ↓↑, graphique des 60 dernières secondes (charge ou réseau), une barre par cœur, disques, batterie, et les 8 processus les plus gourmands avec un bouton ✕ qui demande **confirmation** avant d'arrêter un processus (« Forcer » s'il résiste). Sur un portable hybride, la carte NVIDIA endormie s'affiche « En veille » : le dock ne la réveille jamais.
- **Alimentation** : ouverte par le petit bouton **⏻** à droite de l'horloge (sur toutes les pages). À gauche le **réveil** (Wake-on-LAN), à droite quatre commandes pour **le PC** : **Verrouiller** (un toucher : l'écran de verrouillage de la session s'affiche), **Mettre en veille**, **Redémarrer** et **Éteindre** (chacune demande d'abord **confirmation à l'écran**, avec le nom du PC et ce qui va arriver). Un bouton grisé dit pourquoi : PC hors ligne, indisponible sur ce PC, « Bloqué par « Mises à jour logicielles » » (le programme qui s'oppose est nommé), mot de passe exigé par le système. Voir [Alimentation](#alimentation-verrouiller-veille-redémarrer-éteindre).
- **Réglages** : **Associer un PC** (affiche un code et l'adresse du portail ; voir [Installation](#2-sur-le-pc--la-page-du-pi)), adresse, port et jeton du PC à la main (clavier tactile AZERTY intégré), test de connexion, adresse MAC du réveil, **écran du dock** (extinction après 1, 5, 10 ou 30 minutes sans toucher), **économiseur d'écran** (départ après 1 à 30 minutes, ce qu'il montre, **adresses de téléchargement des vidéos** saisies au clavier tactile, bouton d'essai), **Divertissement** (adresse de la liste des chaînes, serveur de fichiers, identifiant et mot de passe, essai), **pages du dock** (lesquelles ont un bouton sur l'accueil, et dans quel ordre ; les Réglages restent toujours visibles), mesure de latence, lanceur d'applications, valeurs de la bourse, adresse(s) du Pi, adresse de la page d'installation et paquet du PC qu'elle propose.
- **Économiseur d'écran** : sans toucher pendant 5 minutes (réglable, ou jamais), l'écran montre une **vue animée plein écran** — paysages, fonds marins, ville de nuit… — avec l'heure et le morceau qui joue. **Le premier toucher la ferme sans rien déclencher** dessous. Voir [Économiseur d'écran](#économiseur-décran).
- **Écran qui s'éteint** : après le délai choisi (par défaut : jamais), l'écran passe au noir ; **le premier toucher le rallume sans rien déclencher** (le doigt ne tape jamais sur ce qui se trouve dessous). Voir [Veille de l'écran](#veille-de-lécran-du-dock).
- **Dock neuf, ou jeton refusé** : l'accueil porte déjà l'adresse du portail et **le code** ; en ouvrant une page du PC, l'écran **« Associer un PC »** donne l'adresse à ouvrir sur le PC, les trois gestes à faire et le code, avec le temps qu'il lui reste ; « Nouveau code » en demande un autre. Un dock sans PC garde toujours un code affiché ; un dock déjà associé n'en montre un que sur demande (**Réglages → Associer un PC…**, ou **Associer à nouveau** sur l'écran « Connexion refusée »). Quand le PC s'associe, un message le dit et les pages arrivent.
- **PC éteint ou injoignable** : l'accueil le dit dans son bandeau ; en ouvrant une page du PC, l'écran dit pourquoi, **en nommant l'adresse essayée** — *PC hors ligne* (éteint, en veille, pare-feu), *PC allumé, agent arrêté* (session fermée ?), *Connexion refusée* (jeton ou adresse du Pi non autorisée) — compte à rebours de la prochaine tentative, bouton **Allumer le PC**. Bourse et Réglages restent utilisables.
- **`?debug=1`** ajouté à l'adresse (ou `chromium.flags`) affiche les images par seconde et l'état des liaisons.
- **`?scene=mountains`** (ou `ocean`, `city`, `aurora`, `space`) ajouté à l'adresse impose cette scène à l'économiseur d'écran : pour l'essayer ou la photographier.

## Sécurité

- **Jeton** de 256 bits généré à l'installation, dans un fichier en droits 600 de chaque côté (le programme refuse de démarrer s'ils sont plus ouverts). Envoyé dans l'en-tête `Authorization`, jamais dans une URL, jamais journalisé, jamais envoyé au navigateur.
- **Réseau** : l'agent refuse en silence toute adresse hors de `allowed_clients` (idéalement l'IP du Pi) avant même de regarder le jeton, et bloque une minute une adresse qui échoue 8 fois. Il n'écoute qu'en IPv4. Le serveur du dock n'écoute que sur `127.0.0.1` et refuse toute autre origine (contre les sites malveillants et le « DNS rebinding »).
- **Actions** : jamais de commande venue du dock, seulement des actions nommées d'une liste blanche, aux paramètres validés (champs inconnus refusés). Les actions sensibles exigent une confirmation à l'écran **et** un jeton de confirmation à usage unique côté agent, lié à la connexion, à l'action et à ses paramètres (10 s). Lancer une application, un jeu ou un document (`apps.launch`, `steam.launch`, `office.*`) ne demande pas de confirmation non plus, mais le dock n'envoie jamais qu'un identifiant que l'agent a lui-même listé (fichier `.desktop` installé, jeu de la bibliothèque Steam, document de la liste de LibreOffice qui existe encore) : le chemin et la commande sont reconstruits par l'agent, sans shell, et le programme est lancé par `gio launch` dans son propre scope systemd. Régler le volume, couper le son ou changer de sortie (`audio.*`) ne demande pas de confirmation (réversible, sans danger) mais n'agit que sur un périphérique ou une application que l'écran montre, avec un volume entre 0 et 100 %. Les actions sensibles sont **arrêter un processus** (`system.kill`) et **mettre en veille, redémarrer, éteindre** (`power.suspend`, `power.reboot`, `power.poweroff`) : l'écran de confirmation dit ce qui va arriver. Pour `system.kill`, l'agent revérifie lui-même que c'est le même processus (heure de démarrage), qu'il appartient à ton utilisateur et que ce n'est ni lui-même ni un processus de session (gnome-shell, pipewire…). Les ordres d'alimentation, eux, ne contiennent aucun paramètre (rien à détourner) : l'agent les passe à **logind par D-Bus**, jamais par une commande ni un shell, **sans jamais demander de mot de passe** (`interactive = false`) et sans jamais forcer : si le système refuse, l'ordre échoue en le disant. Verrouiller (`power.lock`) ne demande pas de confirmation (on le défait avec son mot de passe). Jamais deux ordres d'alimentation en même temps, et chacun est écrit dans le journal de l'agent avec l'adresse qui l'a demandé. Les pochettes de musique ne sont lues que si le fichier est une image, ou en https vers une machine publique (jamais le réseau local). Les actions `spotify.*` (chercher, ouvrir une page, jouer, lancer, lister les appareils, passer la musique sur l'un d'eux, régler son volume) sont aussi des actions nommées aux paramètres stricts : un identifiant Spotify de 22 caractères alphanumériques, une adresse `spotify:<type>:<identifiant>` exacte, une requête de recherche de 120 caractères sans caractère de contrôle, un identifiant d'appareil (8 à 128 caractères parmi `A-Za-z0-9_.:-`) que l'agent a lui-même listé (sinon il relit la liste une fois, puis refuse), un volume entier de 0 à 100 ; aucune adresse web, aucun chemin, aucune commande ne vient jamais du dock.
- **Portail du Pi** (page d'installation, port 80) : c'est la **seule** écoute du Pi hors de `127.0.0.1`, et elle est limitée à quatre adresses : la page, le téléchargement d'un paquet qui existe vraiment dans le dossier du portail (nom exact vérifié, jamais un chemin), l'état public (version, nom du Pi, paquet proposé : ni jeton, ni code, ni configuration) et l'association. Pas de WebSocket, des en-têtes de sécurité partout (CSP stricte, `nosniff`, pas d'iframe), corps de requête limité à 4 Ko, rien de la requête n'est jamais recopié dans la page, au plus 32 connexions à la fois. Un port 80 pris ou refusé n'arrête pas le dock : l'écran le dit. Le service du dock n'a qu'une capacité Linux, `CAP_NET_BIND_SERVICE`, pour écouter ce port.
- **Association par code** : le code **n'apparaît que sur l'écran du Pi** (jamais sur le portail ni dans son état public) : il prouve qu'on est devant le dock. Huit caractères d'un alphabet sans ambiguïté (≈ 40 bits), **valable dix minutes, à usage unique**, annulé après cinq erreurs, au plus douze demandes par minute et par adresse, comparaison à temps constant. Le PC y joint le jeton **qu'il a lui-même généré** ; avant d'enregistrer quoi que ce soit, le Pi vérifie qu'il joint bien l'agent avec ce jeton, et le jeton n'est jamais renvoyé ni journalisé. Limites assumées : une personne du réseau local peut épuiser les essais et **empêcher** une association (déni de service sans conséquence : un nouveau code suffit) mais pas en forcer une ; et le jeton voyage du PC au Pi en clair sur le réseau local, comme la liaison elle-même (voir la limite ci-dessous).
- **Séparation des privilèges sur le Pi** : le serveur (qui détient le jeton) et le navigateur tournent sous deux utilisateurs système différents ; un navigateur compromis ne lit pas le jeton.
- **Secrets** : jamais dans le dépôt (`.gitignore`, et un test vérifie qu'aucun secret n'y est suivi). La clé d'API facultative de la bourse (Finnhub) va dans la section `[markets]` du fichier de configuration du Pi, en droits 600 : elle n'est jamais envoyée à l'écran, jamais mise dans une adresse (elle part dans un en-tête) et jamais écrite dans un journal ni dans le diagnostic.
- **Spotify** : la connexion se fait **sur le PC** (OAuth avec PKCE, sans « client secret », sur une adresse locale `127.0.0.1` seulement, avec un `state` aléatoire vérifié). Le jeton reste dans `~/.config/mydock/spotify.toml` en droits 600 (refusé s'il est plus ouvert), n'est jamais envoyé au Pi ni au navigateur, ni journalisé, ni affiché par le diagnostic. L'agent ne parle qu'à `accounts.spotify.com` et `api.spotify.com`, en https, sans suivre de redirection, et ne télécharge de pochettes que chez `*.scdn.co` et `*.spotifycdn.com` : le dock ne reçoit que des identifiants de pochette opaques et ne demande jamais une adresse. Les erreurs 429 de Spotify mettent tous les appels en pause le temps demandé.
- **Économiseur d'écran** : les vidéos ne sont lues que depuis le dossier `screensaver` du Pi, et le serveur ne sert **que ce qu'il vient lui-même de lister** : extensions MP4 et WebM seulement, ni sous-dossier ni lien symbolique, noms sans chemin ni caractère de contrôle, jamais de chemin construit à partir de l'adresse demandée (une adresse `../` ou un nom hors liste renvoie « introuvable »). `screensaver add` n'accepte que des adresses **https** (une redirection vers du http est refusée), 2 Go au plus, et laisse 300 Mo libres sur la carte. Le navigateur du kiosque ne contacte jamais Internet : les vidéos viennent du Pi (`default-src 'self'`).
- **Adresses de vidéos saisies à l'écran** : seules des adresses **https** complètes sont acceptées (ni identifiants dans l'adresse, ni espace, ni caractère de contrôle ; 500 caractères et 20 adresses au plus), validées par le dock lui-même ; une redirection vers du http est refusée ; 2 Go au plus par film ; une liste de 64 Ko et 50 films au plus. Les fichiers téléchargés sont examinés par `ffprobe` puis, si besoin, convertis par `ffmpeg` avec `-protocol_whitelist file` (un fichier ne peut pas lui faire ouvrir une autre adresse), dans le service du dock (sans privilèges, système de fichiers en lecture seule hors `/var/lib/mydock`). Limite assumée : l'adresse est ouverte **depuis le Pi**, y compris vers ton réseau local (un NAS en https, par exemple) : seule une personne devant l'écran du dock peut en saisir une. Le navigateur du kiosque, lui, ne contacte toujours jamais Internet.
- **Certificats des serveurs de films** : le dock vérifie toujours le nom du serveur, la chaîne et les dates ; il ne télécharge jamais « sans vérifier ». La seule autorité ajoutée à celles du Pi est la **racine publique d'Apple**, obtenue sur `www.apple.com` par une connexion vérifiée, contrôlée (certificat racine auto-signé nommé « Apple Root CA », 16 Ko au plus), gardée dans `/var/lib/mydock/trust/` et **donnée à la vérification pour `sylvan.apple.com` seulement** (jamais pour une adresse saisie, et une redirection vers un autre serveur est vérifiée par les seules autorités du Pi). Limite assumée : son empreinte SHA-256 est **affichée** dans la fenêtre des vidéos mais **pas imposée** (je n'ai pas pu la vérifier à la source) : si `www.apple.com` était usurpé avec un certificat public valide, une fausse « Apple Root CA » serait acceptée pour ce seul serveur, et l'effet se limiterait à des films choisis par l'attaquant, qui passent ensuite par `ffprobe` et `ffmpeg` comme tous les autres. Compare l'empreinte affichée à celle de la page de l'autorité de certification d'Apple pour t'en assurer.
- **Divertissement** : le navigateur ne reçoit **jamais** l'adresse d'un flux ni celle d'un fichier : il envoie l'identifiant d'une chaîne (que le dock a lui-même dans sa liste) ou le chemin d'un fichier **relatif à un partage** (sans `..`, sans `\` ni caractère de contrôle), et le dock reconstruit l'adresse. La liste des chaînes n'est lue que depuis une adresse du **réseau local** (privée ou le Pi lui-même) — ou sous le nom officiel de la box, `mafreebox.freebox.fr`, que Free résout vers une adresse publique qu'elle sert chez toi (aucun autre nom n'a cette exception) —, sans suivre de redirection, 1 Mo au plus. `smbclient` est lancé **sans shell** (liste d'arguments), le dossier visité est un argument à part (`-D`), et le **mot de passe du partage ne passe jamais par la ligne de commande** : il va dans l'environnement de `smbclient` et, pour VLC, dans un petit fichier de configuration en droits 600 du dossier d'exécution de la session, effacé à la fin de la lecture. Il est gardé dans `/etc/mydock/dock.toml` (droits 600, lisible du seul utilisateur `mydock`), n'est **jamais envoyé à l'écran** (l'interface sait seulement qu'il existe) ni journalisé ni affiché par le diagnostic. L'assistant de lecture tourne sous l'utilisateur du navigateur (il ne peut pas lire le jeton), n'accepte de parler qu'à `127.0.0.1` (le dock refuse tout autre client et toute requête portant un en-tête `Origin`, donc une page web ne peut pas l'atteindre), et ne reçoit jamais de commande à exécuter : seulement « jouer cette adresse », « pause », « position », « volume », « arrêter ». Limite assumée : le trafic Samba et RTSP entre le Pi et la Freebox n'est pas chiffré par MyDock (c'est celui de la box) ; il reste sur ton réseau local.
- **Divertissement, lecture par smbclient et journaux** : la lecture de secours passe par `smbclient` lancé **sans shell**, avec le dossier en argument à part (`-D`) ; le **nom du fichier**, qui doit figurer dans la commande `get "nom" -`, est refusé s'il contient `;` ou `"` (smbclient sépare ses ordres par `;` : un nom comme `a";rm "b.mkv` ferait exécuter un second ordre, essayé et refusé sur un vrai Samba). Le mot de passe va dans l'environnement de `smbclient`, jamais dans ses arguments. Les journaux (`/var/log/mydock/*.log`, droits 644 dans un dossier à bit collant) **effacent le mot de passe du partage** s'il apparaissait, et ne contiennent pas l'identifiant ; ils contiennent en revanche les adresses lues (noms de fichiers, chaînes) : ne les publie pas tels quels.
- **Accès à Internet** : côté Pi, seul le serveur du dock sort, et seulement vers Yahoo Finance, Binance (et Finnhub si une clé est configurée), à des adresses écrites dans le code ; le Divertissement, lui, ne parle qu'à la Freebox, sur le réseau local ; côté PC, l'agent ne sort que vers Spotify, pour la page Spotify, après connexion de ton compte. Les symboles saisis sont validés (lettres, chiffres, `. ^ = - _`), une redirection vers un autre site est refusée, une réponse de plus de 2 Mo est abandonnée, et chaque fournisseur a sa limite de débit et sa pause après un refus. Le navigateur du kiosque ne contacte jamais Internet (`connect-src 'self'`).

Limite connue : le trafic sur le réseau local n'est pas chiffré (le jeton ne l'est pas dans la poignée de main). Il reste protégé par le jeton et par la liste des adresses autorisées, mais une personne qui écouterait le réseau local le verrait passer. Le chiffrement TLS avec certificat épinglé n'est pas fait : à envisager si ton réseau n'est pas de confiance.

## Wake-on-LAN

Allumer un PC **éteint** à distance n'est possible qu'en **Ethernet** : la carte réseau doit rester à l'écoute, et le BIOS doit l'autoriser. En Wi-Fi, au mieux depuis la veille et selon la machine.

1. Brancher le PC en Ethernet (portable : le laisser sur secteur).
2. Lancer sur le PC `mydock-agent enable-wol` (réglage durable via NetworkManager ; `--dry-run` pour voir sans appliquer).
3. Dans le BIOS : activer « Wake on LAN » / « Power on by PCI-E », désactiver « ErP / Deep Sleep ».
4. Le dock mémorise tout seul l'adresse MAC du PC tant qu'il est allumé ; elle se voit et se corrige dans **Réglages**. Le dock préfère l'Ethernet au Wi-Fi et avertit si seule l'adresse du Wi-Fi est connue.

## Alimentation (verrouiller, veille, redémarrer, éteindre)

L'agent demande à **logind** (le service de session de systemd) ce qu'il autorise à ton utilisateur, et ne propose que cela : sur un PC où la veille n'existe pas, le bouton est grisé « Indisponible sur ce PC ». Il ne force jamais rien. Si un programme demande à bloquer l'extinction (une mise à jour qui s'installe, une copie de fichiers), le bouton dit « Bloqué par « … » » et ce programme est nommé sur la page : termine-le d'abord.

- **Verrouiller** envoie à ta session graphique le signal de verrouillage de logind (GNOME, KDE et la plupart des bureaux y répondent) ; l'écran de verrouillage de GNOME sert de repli.
- **Veille** : le PC s'endort, le dock affiche « PC hors ligne » puis se reconnecte tout seul au réveil. Le réveil depuis la veille par le réseau marche si le Wake-on-LAN est réglé (voir plus haut) : bouton **Allumer le PC**.
- **Redémarrer** et **éteindre** ferment les programmes ouverts : la confirmation le dit.
- **Le système demande un mot de passe** (« Mot de passe exigé par le système ») : rare avec Ubuntu 26.04, plus fréquent si un autre utilisateur est aussi connecté. `mydock-agent doctor` (section « Alimentation ») montre les droits **tels que l'agent en marche les voit**, et donne la règle polkit qui autorise veille, redémarrage et extinction sans mot de passe à ton utilisateur :

  ```js
  // /etc/polkit-1/rules.d/50-mydock-power.rules   (à créer avec sudo, remplace « mars » par ton nom d'utilisateur)
  polkit.addRule(function (action, subject) {
    if (subject.user == "mars" && ["org.freedesktop.login1.suspend",
        "org.freedesktop.login1.reboot", "org.freedesktop.login1.power-off"]
        .indexOf(action.id) >= 0) {
      return polkit.Result.YES;
    }
  });
  ```

## Divertissement (Freetv et Kiosque)

Le bouton **Divertissement** de l'accueil ouvre deux lecteurs. **VLC lit la vidéo sur l'écran du dock**, en plein écran, avec le son du Pi (HDMI ou prise) : le PC n'est pas utilisé, et le dock marche aussi PC éteint.

- **Freetv** : les chaînes de la Freebox, d'après sa liste `http://mafreebox.freebox.fr/freeboxtv/playlist.m3u`. Une grille de chaînes avec **recherche** au clavier tactile (par nom, sans accents ni majuscules, ou par numéro : « 6 » trouve la chaîne 6) ; un toucher lance la chaîne. Les chaînes sont lues en **qualité standard** pour ne pas peiner le Pi ; le bouton **HD** choisit la haute définition quand la liste la propose. La liste est relue au plus toutes les dix minutes.
- **Kiosque** : les **partages Samba de la Freebox** (disque dur, enregistrements…). On touche un partage, on descend dans les dossiers (le bouton **Dossier parent** remonte, et **Partages** à la racine), et un toucher sur une **vidéo** la lance ; seules les vidéos sont montrées (mkv, mp4, avi, mov, ts, mpeg…), classées par nom, dossiers d'abord.
- **La télécommande** : pendant la lecture, **un toucher sur l'écran ramène le dock** (la vidéo continue derrière) avec la télécommande : **pause / lecture**, **−5 min, −30 s, +30 s, +5 min** (pour un fichier), **volume**, **Revenir à la vidéo** et **Arrêter**. La tuile **Divertissement** de l'accueil dit ce qui joue. À la fin d'un film, ou à l'arrêt, la page revient d'elle-même au menu du Divertissement.
- **Réglages → Divertissement** : l'adresse de la liste, le **serveur de fichiers** (`mafreebox.freebox.fr` par défaut), l'**identifiant** et le **mot de passe** du partage (laisser vides pour un partage ouvert à tous ; le mot de passe se tape au clavier tactile et ne s'affiche jamais), et **Tester Freetv et Kiosque**, qui dit en trois lignes ce qui répond. En ligne de commande : `sudo -u mydock mydock-dock init --playlist-url http://… --smb-host IP_DE_LA_BOX`.
- **Sur la Freebox** : le partage de fichiers (« Partage de fichiers Windows ») doit être activé dans Freebox OS ; selon son réglage, le Pi y entre sans identifiant ou avec celui d'un compte de la box.

**Ce qui est installé** : `apt` ajoute VLC (`vlc-bin`, `vlc-plugin-base`, `vlc-plugin-video-output`, `vlc-plugin-samba`), `smbclient`, `xwayland` et `wlrctl` (ou `xdotool`), avec `ffmpeg` et `swayidle` que le dock avait déjà (ils sont recommandés, pas obligatoires : s'il en manque un, l'écran du Divertissement dit lequel, avec la commande `apt`). `sudo -u mydock mydock-dock doctor` a une section « Divertissement ». Des options de VLC en plus (une par ligne) vont dans `/etc/mydock-kiosk/vlc.flags` (par exemple `--aout=alsa` pour le son, `--avcodec-hw=none` si l'image est noire ou saccadée).

**Journaux de débogage** : quand une vidéo ne se lance pas, tout est dans `/var/log/mydock/` — **`player.log`** (la lecture : ce que le dock a demandé, les commandes lancées, tout ce que VLC — lancé en mode bavard —, ffmpeg et smbclient ont dit, chaque tentative, une ligne de suivi toutes les 15 secondes — images affichées et perdues, discontinuités du flux — et la fin) et **`dock.log`** (le serveur : liste des chaînes lue ou refusée, partages parcourus, lectures demandées ou refusées). `sudo cat /var/log/mydock/player.log` ; `sudo tail -f /var/log/mydock/player.log` pour suivre en direct pendant qu'on touche une chaîne. Ils tournent (1,5 Mo, trois anciens gardés) et **ne contiennent jamais de mot de passe**. L'écran d'une lecture ratée donne ce chemin, et `sudo -u mydock mydock-dock doctor` montre les dernières lectures ratées.

**Façons d'afficher (et désentrelacement)** : par défaut VLC décode et affiche par le **matériel du Pi** (`wl_dmabuf`), **sans désentrelacement** (`--deinterlace=0`) : le désentrelaceur matériel de Raspberry Pi OS donne à labwc des images qu'il refuse. Mais l'affichage matériel d'une **chaîne** peut échouer quand même : labwc coupe alors la connexion Wayland de VLC (`importing the supplied dmabufs failed`), qui écarte tout (`Video discarded due to Wayland congestion`) — une image, puis plus rien, alors que le son et la position avancent. **L'assistant le détecte et relance seul** la lecture avec une façon plus sûre, dans l'ordre : (1) **matériel** ; (2) **logiciel** (`--avcodec-hw=none --deinterlace=-1` : décodage et désentrelacement par le processeur, sans peigne, raisonnable en SD) ; (3) **logiciel en mémoire partagée** (en plus `--vout=wl_shm`, que tout compositeur sait montrer). Une chaîne qui a obligé à changer en garde la façon pour les chaînes suivantes (**jamais pour les films**, trop lourds à décoder sans le matériel) jusqu'au redémarrage de l'assistant ; on s'arrête à la dernière. Le journal dit chaque changement (« l'image n'arrive pas à l'écran … on relance en … »). Pour **forcer** une façon d'afficher ou garder le peigne, `/etc/mydock-kiosk/vlc.flags` passe avant ces choix (VLC garde la dernière valeur).

**Plusieurs façons de lire** : pour un **fichier d'un partage**, VLC l'ouvre d'abord lui-même (`smb://`) ; s'il n'y arrive pas (son module Samba manque, ou échoue là où `smbclient` parcourt bien le partage), **`smbclient` lit le fichier et le donne à VLC par un tube** : la lecture marche, mais **sans avance ni retour rapides** (la télécommande grise les sauts et le dit). Pour une **chaîne**, VLC lit le RTSP s'il en est capable, sinon `ffmpeg` recopie l'image et le son vers VLC. Le journal dit quelle façon a servi (« tentative 2/2 « smbclient » »).

**Comment ça marche** : le service du dock est isolé (aucun accès à l'écran), alors **un petit assistant, lancé par le kiosque dans la session de l'écran** (`deploy/pi/player-session.sh`), lance VLC. Il se connecte au dock en local (`ws://127.0.0.1:8080/ws/player`, jamais depuis le réseau) et reçoit « joue ceci » au moment de lire. VLC est piloté par son interface `oldrc` sur un socket Unix qui appartient à l'assistant ; quand VLC n'a pas le module RTSP des chaînes (c'est le cas de plusieurs distributions), `ffmpeg` recopie le flux tel quel vers VLC, sans le décoder. Un toucher est vu par `swayidle` (comme la veille de l'écran) : le dock repasse devant, la vidéo continue. Pour la même raison que la veille de l'écran, **un toucher sur la vidéo ne déclenche rien d'autre** que le retour au dock.

## Veille de l'écran du dock

**Réglages → Écran du dock** : *Jamais* (le choix de départ : le dock ne coupe rien sans que tu l'aies demandé), 1, 5, 10 ou 30 minutes. Deux étages :

1. **L'interface** noircit l'écran après le délai (elle ne dépend de rien) et avale le premier toucher : on rallume en touchant, sans rien déclencher dessous. Le bouton **Éteindre maintenant (essai)** montre l'effet tout de suite.
2. **Le Pi** coupe réellement l'écran (rétroéclairage) avec `swayidle` : `deploy/pi/screen-sleep.sh`, lancé par le compositeur à côté du navigateur, lit la durée choisie auprès du dock (`/api/screen`), relance `swayidle` quand elle change, éteint avec `wlopm` (ou `wlr-randr` à défaut) et rallume au premier toucher. Si le script ne trouve pas `swayidle` il le dit dans le journal et l'écran reste seulement noirci par l'interface (`sudo apt install swayidle`).

## Économiseur d'écran

**Réglages → Économiseur d'écran** : *Jamais*, 1, 2, **5 minutes (le choix de départ)**, 10, 15 ou 30 minutes sans toucher l'écran. Passé ce délai, le dock montre une **vue animée plein écran**, comme les vues aériennes de l'Apple TV et du Mac, avec en bas l'heure, le nom de la vue et le morceau qui joue sur le PC. **Le premier toucher la ferme sans rien déclencher dessous** : le doigt ne tape jamais sur le bouton qui se trouvait là. **Essayer maintenant** la montre tout de suite. *Montrer* choisit ce qui passe : **Mes vidéos** (le choix de départ : les vues aériennes ci-dessous, puis les tiennes), **Scènes** ou **Tout** (scènes et vidéos mêlées).

**Les vidéos se téléchargent toutes seules, d'après les adresses que tu saisis à l'écran.** **Réglages → Économiseur d'écran → « Vidéos et adresses de téléchargement… »** ouvre une fenêtre qui montre d'où le dock tire ses films et où ça en est (« 4 sur 10 prêtes », ou la raison de l'échec). Deux sortes de sources :

- **Les vues aériennes d'Apple** (de série) : dix films « Aerial » (cinq de jour, cinq de nuit, ceux de la liste du projet [xscreensaver-aerial](https://github.com/graysky2/xscreensaver-aerial)) à l'adresse publique qu'Apple utilise pour l'Apple TV. Elles se retirent (avec leurs films) et se remettent d'un toucher.
- **Tes adresses** : **Ajouter une adresse…** ouvre le clavier tactile (avec les touches d'une adresse web : `/ ? = & % ~ : . - _`), déjà rempli de `https://`. On y met l'adresse **d'une vidéo** (MP4, WebM, MOV, AVI…) **ou d'un fichier texte** (`.txt`, `.m3u`, `.list`) qui en liste **une par ligne** (les lignes qui ne sont pas une adresse https, et celles qui commencent par `#`, sont ignorées ; 50 films au plus par liste, 20 adresses au plus). Chaque adresse est reprise toutes les deux minutes : un film ajouté à ta liste arrive tout seul.

Le Pi **télécharge, examine chaque fichier avec `ffprobe`, et ne convertit que ce qui en a besoin** : un MP4 H.264 ou un WebM déjà à la taille de l'écran est gardé tel quel, le reste est converti en **MP4 H.264 1024×600 sans le son** avec `ffmpeg`, à faible priorité (plusieurs minutes par film sur un Pi 4). Retirer une adresse, c'est **effacer ses films** (la fenêtre demande confirmation). **Suspendre** arrête les téléchargements sans rien effacer. Une minute après le démarrage, le téléchargement commence de lui-même ; **un film en échec n'arrête pas les suivants** et le dock retente tout seul toutes les 30 minutes, en disant sur la ligne de la source pourquoi (« Le serveur répond 404 », « Cette adresse ne donne pas une vidéo », « Liste injoignable »…). Tant qu'aucun film n'est prêt, les scènes dessinées passent. https seulement (une redirection vers du http est refusée), 2 Go au plus par film, 300 Mo toujours laissés libres sur la carte. `ffmpeg` est une dépendance du paquet du Pi. Les films d'Apple sont téléchargés **chez toi, pour ton usage**, jamais embarqués dans MyDock. **Le serveur des films d'Apple** (`sylvan.apple.com`) présente un certificat d'une **autorité privée d'Apple** (« Apple Server Authentication CA ← Apple Root CA ») que la liste des autorités de Debian ne contient pas : sans rien faire de plus, le Pi le refuserait (« unable to get local issuer certificate »). Le dock **obtient donc lui-même la racine publique d'Apple** sur `www.apple.com` (connexion vérifiée par les autorités du Pi ; une redirection n'est suivie que vers le même serveur, en https), **contrôle que c'est bien un certificat racine auto-signé nommé « Apple Root CA »**, la garde dans `/var/lib/mydock/trust/` et ne la donne à la vérification **que pour le serveur des films d'Apple** : jamais pour les adresses que tu saisis, jamais pour un autre serveur vers lequel il te renverrait, jamais pour un refus d'une autre nature (certificat périmé, mauvais nom de serveur). Le nom du serveur, la chaîne et les dates restent vérifiés : **le dock ne désactive jamais la vérification des certificats**. La fenêtre des vidéos montre alors l'**empreinte SHA-256** de cette racine (début et fin), « celle de l'autorité d'Apple » quand elle est celle que le dock connaît, à comparer sinon avec la page de l'autorité de certification d'Apple (`apple.com/certificateauthority`). Si l'obtention échoue, la ligne de la source dit ce qui a été essayé. **Non vérifié** : je n'ai pas d'accès à Internet dans mon environnement, donc ni l'adresse de la racine sur `www.apple.com`, ni l'acceptation par le Pi de la vraie chaîne d'Apple, ni un vrai téléchargement n'ont été essayés (le code est testé avec un réseau simulé, **de vrais serveurs https locaux dont la chaîne reproduit celle d'Apple** — racine non envoyée — et de vraies conversions `ffmpeg`).

**Les scènes sont dessinées en direct par l'interface** : aucun fichier, rien à télécharger, rien qui pèse sur la carte du Pi. Cinq, qui se succèdent toutes les 75 s par un fondu au noir, sans jamais répéter deux fois la même :

| Scène | Ce que ça montre |
|---|---|
| **Montagnes, lever du jour** | des crêtes à six plans qui défilent lentement dans la brume, sous des nuages et un ciel qui passe peu à peu de l'aube au crépuscule |
| **Fonds marins** | des rayons de lumière dans l'eau bleue, deux bancs de poissons, des algues qui ondulent, de fines particules qui montent |
| **Ville de nuit, vue aérienne** | des quartiers éclairés, des parcs sombres, de grandes avenues, des phares qui roulent, le tout survolé de biais |
| **Aurore boréale** | des rideaux verts, bleus et violets qui ondulent au-dessus d'une forêt de sapins, sous les étoiles |
| **Voyage parmi les étoiles** | des étoiles qui s'approchent lentement, devant une nébuleuse colorée |

Chaque image est calculée en 512×300 puis agrandie, à 24 images par seconde (un peu de travail pour le Pi, pas de décodage vidéo), et **tout s'arrête dès qu'on touche** : minuteurs, dessin, décodage.

> **Les vidéos d'Apple ne sont pas dans le paquet.** Les « Aerial » de l'Apple TV et du Mac sont la propriété d'Apple : MyDock ne les embarque pas ; c'est ton dock qui en télécharge dix chez Apple (voir au-dessus). Les scènes ci-dessus s'en inspirent mais **sont des dessins animés par le programme**, pas des prises de vue. Pour de **vraies vues aériennes**, ajoute **tes propres vidéos** (libres de droits, ou à toi : Pexels, Pixabay, Wikimedia Commons en proposent ; vérifie la licence de chacune).

**Autres façons d'ajouter des vidéos** : elles vivent dans le dossier `/var/lib/mydock/screensaver` du Pi, et **aucun redémarrage** n'est nécessaire (l'interface relit la liste à chaque départ de l'économiseur et à chaque ouverture des Réglages). Sans passer par l'écran, sur le Pi :

```bash
sudo mydock-dock screensaver add https://exemple.org/vue-aerienne.mp4      # télécharge (https seulement)
sudo mydock-dock screensaver add ~/Vidéos/fonds-marins.webm                 # ou copie un fichier du Pi
sudo mydock-dock screensaver list                                           # ce que l'économiseur peut montrer
sudo mydock-dock screensaver remove fonds-marins.webm
```

- **Formats** : par l'écran, tout ce que `ffmpeg` lit (MP4, WebM, MOV, MKV, AVI…) est converti si besoin ; en copiant un fichier à la main (`screensaver add`), **MP4 (H.264)** ou **WebM**, ceux que Chromium lit sans réglage. La légende est le nom du fichier (`Vue aérienne_nuit.mp4` → « Vue aérienne nuit »).
- **Pour que ce soit fluide sur le Pi 4**, prépare-les à la taille de l'écran, sans le son (ni l'économiseur ni le Pi n'en ont l'usage) : 

  ```bash
  ffmpeg -i entree.mov -vf "scale=1024:600:force_original_aspect_ratio=increase,crop=1024:600" \
         -c:v libx264 -crf 23 -preset slow -pix_fmt yuv420p -an -movflags +faststart sortie.mp4
  ```

  `sudo -u mydock mydock-dock doctor` (section « Économiseur d'écran ») signale une vidéo de plus de 300 Mo. **Je n'ai pas pu essayer la lecture sur un vrai Pi 4** : une vidéo trop lourde saccadera, le doctor et ce choix de taille sont là pour l'éviter.
- Une vidéo joue **jusqu'au bout** (150 s au plus), puis la suivante. Une vidéo que le navigateur ne sait pas lire est **écartée pour cette fois** et une scène prend le relais : jamais d'écran noir ni d'économiseur bloqué.

**Avec la veille de l'écran.** L'économiseur passe **en premier** ; si *Écran du dock* est réglé plus long, l'écran passe ensuite au noir (et le Pi coupe le rétroéclairage). Réglé **pareil ou plus court**, la veille passe avant et l'économiseur n'a jamais le temps de paraître : la carte des Réglages le dit en orange.

## Dépannage

| Symptôme | Cause probable | Remède |
|---|---|---|
| « PC hors ligne » alors que le PC est allumé | **Nom `.local` non résolu depuis le Pi** (le plus fréquent) ; pare-feu (`ufw`) ; PC en veille ; Wi-Fi qui isole les appareils | **Saisir l'adresse IP du PC dans Réglages.** L'écran dit l'adresse essayée (`ubuntu.local (192.168.1.3:8765)`) et pourquoi ; sur le Pi : `sudo -u mydock mydock-dock doctor` teste chaque adresse ; sur le PC : `mydock-agent doctor` |
| « PC allumé, agent arrêté » | Session fermée (l'agent vit dans ta session) ; service arrêté | `mydock-agent doctor` (section « Service systemd »), `journalctl --user -u mydock-agent -e` |
| « Connexion refusée » | Jeton différent, ou IP du Pi absente de `allowed_clients` | Bouton **Associer à nouveau** sur l'écran du dock, puis `mydock-agent pair --pi IP_DU_PI` sur le PC ; `mydock-agent doctor` ; `mydock-agent setup --pi IP_DU_PI` si l'adresse du Pi n'est pas autorisée (le message dit quelle adresse le PC voit arriver) |
| Le nom `ubuntu.local` change ou ne répond pas | Deux machines nommées « ubuntu » | `sudo hostnamectl set-hostname mon-pc`, ou utiliser l'IP (idéalement réservée dans la box) |
| Page Spotify : « Spotify n'est pas ouvert » alors que Spotify joue | Le lecteur ne parle pas MPRIS (Spotify en Flatpak/Snap : accès au bus bloqué) ou l'agent n'a pas la session D-Bus | Sur le PC : `mydock-agent doctor` (section « Musique (MPRIS) » : lecteurs vus) ou `playerctl -l` ; `journalctl --user -u mydock-agent -e` |
| Page Spotify : « Musique indisponible » | Bus de session D-Bus injoignable (agent lancé hors session graphique) | Lancer l'agent par le service (`systemctl --user restart mydock-agent`), pas depuis un terminal SSH |
| Système : la carte graphique dit « En veille » | La carte NVIDIA dort pour économiser la batterie : c'est voulu, le dock ne la réveille pas | Lancer un programme sur la carte (`prime-run …`) : sa charge apparaît |
| Page Spotify : « Connecte ton compte Spotify » | Aucun compte n'est relié au PC (la lecture marche, pas la recherche) | `mydock-agent spotify login --client-id ID` sur le PC (voir [Spotify : connecter ton compte](#spotify--connecter-ton-compte)) |
| Page Spotify : « Le compte Spotify est refusé » | Jeton expiré ou révoqué ; compte absent de « User Management » ; propriétaire de l'application sans Premium (février 2026) | Lire le message affiché ; `mydock-agent spotify status --check` ; relancer `mydock-agent spotify login` |
| Page Spotify : un morceau ne démarre pas, ou seulement dans l'application | Compte gratuit (la lecture précise demande Premium), ou l'appareil « ce PC » n'apparaît pas dans Spotify Connect | Le dock demande alors à l'application d'ouvrir le contenu ; ouvrir Spotify une fois sur le PC, puis réessayer |
| Sortie sonore : la liste ne montre que « Ce PC » | Spotify ne voit pas d'autre appareil : enceinte éteinte ou pas utilisée depuis longtemps, autre compte Spotify, autre réseau (Wi-Fi invités, VLAN qui isole les appareils) | Allumer l'enceinte et lancer une lecture dessus **depuis l'application Spotify** (elle réapparaît ensuite), puis **Actualiser** dans l'onglet ; vérifier que l'enceinte et le PC sont sur le même réseau |
| Sortie sonore : « Cet appareil ne répond plus » ou « n'accepte pas les commandes à distance » | L'appareil s'est éteint depuis la dernière lecture de la liste ; certains appareils (télévisions, voitures, enceintes d'autres marques) refusent les commandes de l'API de Spotify | **Actualiser** ; sinon choisir l'appareil depuis l'application Spotify. Les commandes demandent un compte **Premium** |
| Sortie sonore : « Cet appareil n'est plus visible » | La liste a changé entre l'affichage et le toucher | Rien : le dock la relit seul ; retoucher l'appareil |
| Sortie sonore : l'onglet dit « Connecte ton compte Spotify » | Comme pour la recherche, la liste des appareils demande le compte relié | Voir [Spotify : connecter ton compte](#spotify--connecter-ton-compte) |
| Divertissement : « L'assistant de lecture de l'écran ne répond pas » | Il démarre avec la session de l'écran : pas encore relancée après l'installation ou une mise à jour | `sudo reboot` (une fois) ; `pgrep -af player-session` ; `sudo -u mydock mydock-dock doctor` (section « Divertissement ») |
| Divertissement : « VLC est absent du Pi », « swayidle, ou wlrctl (ou xdotool), manque » | Paquets recommandés non installés (`apt install --no-install-recommends`) | `sudo apt install vlc-bin vlc-plugin-base vlc-plugin-video-output vlc-plugin-samba smbclient xwayland swayidle wlrctl` |
| Freetv : « Les chaînes ne sont pas disponibles » | Le message sous le titre dit pourquoi : « n'est pas sur le réseau local » (adresse saisie dans Réglages qui n'est pas privée : remettre `http://mafreebox.freebox.fr/freeboxtv/playlist.m3u`), « est introuvable » (le Pi ne résout pas le nom : DNS, ou pas sur le réseau de la box), « répond 404 » (mauvaise adresse) | **Réglages → Divertissement → Tester Freetv et Kiosque** ; `sudo -u mydock mydock-dock doctor` (section « Divertissement ») ; depuis le Pi : `curl -sI http://mafreebox.freebox.fr/freeboxtv/playlist.m3u` |
| Divertissement : une vidéo ne se lance pas (Kiosque ou Freetv), « VLC n'a pas pu lire cette vidéo… » et autres échecs de lecture | La phrase dit la cause quand VLC la donne (accès refusé, fichier introuvable, module Samba ou décodeur manquant, affichage impossible, plantage) ; **le dernier message de VLC (ou de ffmpeg) est écrit dessous, en petit** | Lire **`sudo cat /var/log/mydock/player.log`** (tout ce qui s'est passé, du démarrage de l'assistant à la dernière lecture) et `sudo cat /var/log/mydock/dock.log` ;  `sudo -u mydock mydock-dock doctor` (modules de VLC, dernières lectures ratées) ; `sudo apt install vlc-plugin-samba vlc-plugin-base vlc-plugin-video-output xwayland` ; options dans `/etc/mydock-kiosk/vlc.flags` |
| Freetv : « La chaîne ne répond pas » | La Freebox est éteinte, ou le Pi n'est pas sur son réseau ; chaîne non incluse dans ton abonnement | Régler **Réglages → Divertissement → Tester Freetv et Kiosque** ; ouvrir `http://mafreebox.freebox.fr/freeboxtv/playlist.m3u` depuis un PC du même réseau |
| Freetv : le son sort (ou la position avance) mais l'écran est noir, ou une image puis plus rien | Le journal dit `affichage en difficulté` : VLC écarte les images (`importing the supplied dmabufs failed`, `Video discarded due to Wayland congestion`) | Depuis la 1.6.5 l'assistant relance seul en décodage logiciel (journal : « l'image n'arrive pas à l'écran … on relance en … ») ; sinon `sudo grep -E "affichage|dmabuf|congestion" /var/log/mydock/player.log` puis `--avcodec-hw=none` dans `vlc.flags` |
| Freetv : la chaîne s'arrête d'elle-même au bout de 40 secondes (message « La lecture n'a pas démarré ») | Corrigé en 1.6.6 : l'assistant ne disait pas au dock que ça jouait (le temps de VLC reste à 0 sur ces flux) ; `dock.log` dit « toujours en démarrage … arrêtée par le dock », `player.log` « rien n'avait encore été montré » | Mettre à jour vers la 1.6.6 ; si cela persiste, envoyer `player.log` (lignes `suivi`, `la lecture a démarré`) |
| Freetv : image saccadée, son qui saute, lignes `ts demux warning: discontinuity` | Paquets perdus (le flux arrive en UDP) : le journal de suivi compte les `discontinuités` | Essayer `--rtsp-tcp` dans `/etc/mydock-kiosk/vlc.flags` (le flux passe alors en TCP, sans perte) ; Pi en Ethernet plutôt qu'en Wi-Fi |
| Freetv : image saccadée | Décodage trop lourd (HD), ou désentrelacement par le processeur | Qualité standard (HD désactivé) ; retirer `--avcodec-hw=none` et `--deinterlace=1` de `vlc.flags` |
| Divertissement : l'image passe, pas de son | Sortie audio du Pi (HDMI ou prise) ; utilisateur du kiosque hors du groupe `audio` (mode systemd) | `sudo /usr/lib/mydock/setup --kiosk systemd` ajoute le groupe ; `--aout=alsa` dans `vlc.flags` ; vérifier le volume du dock |
| Divertissement : un toucher pendant la vidéo ne ramène pas le dock | `swayidle` ou `wlrctl`/`xdotool` absent, ou session qui n'est pas labwc | `sudo -u mydock mydock-dock doctor` dit lequel manque |
| Kiosque : « Accès refusé » ou « Identifiant ou mot de passe refusé » | Le partage de la Freebox demande un compte | Renseigner l'identifiant et le mot de passe dans **Réglages → Divertissement** |
| Kiosque : « Le serveur de fichiers ne répond pas », ou aucun partage | Partage de fichiers désactivé dans Freebox OS, mauvaise adresse, pare-feu | Vérifier l'adresse (`mafreebox.freebox.fr` ou l'IP de la box) ; `smbclient -L //mafreebox.freebox.fr -N` depuis le Pi |
| Page Spotify : « L'agent installé sur le PC est plus ancien que le dock » | Le Pi est à jour mais pas le paquet du PC | Télécharger le paquet à jour depuis la page du Pi (`http://IP_DU_PI/`) et `sudo apt install ./mydock-agent_*_all.deb` |
| Spotify (Snap, Flatpak) ne se lance pas depuis Applis ni depuis la page Spotify | Les programmes lancés par l'agent héritaient de ses restrictions (`NoNewPrivileges`), qui empêchent un Snap de démarrer : corrigé en 1.3.0, l'application est maintenant lancée par le gestionnaire systemd de l'utilisateur | Mettre à jour le paquet du PC ; `mydock-agent doctor` (sections « Lancement des programmes » et « Spotify ») |
| Toucher une application ne l'ouvre pas | `gio` absent, ou `systemd-run --user` inutilisable (agent lancé hors session) | `mydock-agent doctor` (sections « Lancement des programmes » et « Applications (lanceur) ») ; `sudo apt install libglib2.0-bin` |
| Steam : « n'est pas installé » ou jeux manquants | Dossier Steam inhabituel, jeu sur un disque non monté | `mydock-agent doctor` (section « Steam ») liste les dossiers lus et le nombre de jeux et de jaquettes |
| Office : aucun document récent | LibreOffice jamais ouvert, ou profil dans un dossier inhabituel | `mydock-agent doctor` (section « LibreOffice ») |
| Bourse : « Cotations indisponibles » | Le Pi n'a pas d'accès à Internet ou de DNS, ou les fournisseurs refusent | `sudo -u mydock mydock-dock doctor` (section « Bourse ») teste chaque fournisseur ; `curl -sI https://query2.finance.yahoo.com` depuis le Pi |
| Bourse : bandeau orange et cours « il y a 3 h » | Un fournisseur ne répond plus : ce sont les derniers cours connus | Le dock réessaie seul (pause croissante, 15 min au plus) ; **Actualiser** force un essai |
| Bourse : « Binance n'est pas accessible depuis ce pays » | Binance coupe l'accès (HTTP 451) dans certains pays | Rien à faire : son miroir de données puis Yahoo Finance prennent le relais |
| Alimentation : boutons grisés « Mot de passe exigé par le système » | Le système n'autorise pas ces actions au service de l'agent sans mot de passe | `mydock-agent doctor` (section « Alimentation ») : règle polkit à copier, voir la section Alimentation |
| Alimentation : « Bloqué par « … » » | Un programme de la session empêche l'extinction ou la veille (mise à jour, copie en cours) | Le terminer, ou attendre ; `systemd-inhibit --list` sur le PC |
| Alimentation : « Le service de session (logind) ne répond pas » | `systemd-logind` arrêté, PC sans logind (conteneur) | `systemctl status systemd-logind` ; le verrouillage peut rester possible |
| Alimentation : « Cette version de l'agent ne sait pas faire cela » | Le Pi est à jour mais pas le paquet du PC | Télécharger le paquet à jour depuis la page du Pi (`http://IP_DU_PI/`) et `sudo apt install ./mydock-agent_*_all.deb` |
| L'écran du dock ne s'éteint pas (il devient noir mais reste allumé) | `swayidle` absent, ou le compositeur ne gère pas l'extinction | `sudo apt install swayidle` ; `pgrep -a swayidle` ; `journalctl -t labwc -e` (ou la sortie du kiosque) ; essayer `wlopm --off '*'` à la main |
| L'écran du dock se rallume mais l'interface reste noire jusqu'au deuxième toucher | Le premier toucher a servi à réveiller l'écran physique | Normal sur certains écrans : un second toucher suffit |
| L'économiseur ne démarre jamais | Réglé sur « Jamais », ou l'écran s'éteint **avant** (Écran du dock ≤ délai de l'économiseur) : Réglages le dit en orange | Réglages → Économiseur d'écran : choisir un délai plus court que celui de l'écran du dock ; **Essayer maintenant** montre l'effet |
| L'économiseur ne montre que ses scènes, jamais mes vidéos | Dossier vide ou fichier non reconnu (`.mov`, `.mkv`…) ; « Montrer » réglé sur « Scènes » ; vidéo que Chromium ne sait pas lire (écartée) | `sudo mydock-dock screensaver list` ; Réglages → Économiseur d'écran → *Tout* ou *Mes vidéos* ; convertir en MP4 H.264 (commande `ffmpeg` de la section Économiseur d'écran) ; `sudo -u mydock mydock-dock doctor` |
| Une vidéo de l'économiseur saccade | Trop lourde pour le Pi (résolution ou débit trop grands) | La convertir en 1024×600 H.264 sans le son (commande `ffmpeg` ci-dessus) ; le diagnostic signale celles de plus de 300 Mo |
| Économiseur : « 0 sur 10 prêtes » qui n'avance pas | `ffmpeg` absent, pas d'accès à Internet depuis le Pi, ou carte presque pleine | Le message est sur la ligne de la source (Réglages → Économiseur d'écran → Vidéos et adresses) ; `sudo -u mydock mydock-dock doctor` (section « Économiseur d'écran ») ; `sudo apt install ffmpeg` ; `journalctl -u mydock-dock -e` |
| Économiseur : « Certificat de … refusé par le Pi (unable to get local issuer certificate) » | Le Pi ne reconnaît pas l'**autorité de certification** du serveur. Pour les vues aériennes d'Apple (`sylvan.apple.com`), le dock ajoute lui-même la racine d'Apple (voir « Économiseur d'écran ») ; si le message reste là, **la racine n'a pas pu être obtenue** (la ligne dit ce qui a été essayé : `www.apple.com` injoignable, fichier reçu qui n'est pas la racine…) ou le Pi refuse encore la chaîne (le message nomme alors ce que le serveur présente). Pour une adresse que tu as saisie, le Pi n'ajoute **jamais** d'autorité de lui-même. Le dock **ne désactive jamais** la vérification des certificats : tant qu'elle échoue, rien n'est téléchargé | Vérifier l'heure du Pi et son accès à `www.apple.com` ; une autre adresse de vidéo (au certificat reconnu) se saisit dans Réglages → Économiseur d'écran → Vidéos et adresses ; pour une autorité de ton choix, l'installer sur le Pi (`sudo cp autorite.crt /usr/local/share/ca-certificates/ && sudo update-ca-certificates`) ; `sudo apt install --reinstall ca-certificates` ; un routeur qui filtre le https remplace parfois les certificats. La page Bourse → **Tester la connexion** montre la même vérification pour Yahoo et Binance |
| Économiseur : « Cette adresse ne donne pas une vidéo » / « Le serveur répond 404 » | L'adresse mène à une page web (lien de partage, page de lecture) ou n'existe plus | Il faut l'adresse **directe du fichier** (qui se termine par `.mp4`, `.webm`…) ; essayer l'adresse dans le navigateur d'un PC |
| Bourse : « Cotations indisponibles » alors que le Pi a Internet | IPv6 annoncé mais muet (la box), heure du Pi fausse, DNS de la box, ou un fournisseur qui refuse | Bouton **Tester la connexion** sur la page Bourse : il dit laquelle de ces causes (le dock passe déjà par l'IPv4 en premier) |
| « Allumer le PC » n'a aucun effet | Pas d'Ethernet, BIOS, câble débranché à l'extinction | Voir la section Wake-on-LAN ; Réglages → vérifier l'adresse MAC |
| `apt install ./mydock_*.deb` refuse : « python3-fastapi (>= 0.115) » introuvable ou trop ancien | Le Pi est sous Raspberry Pi OS **Bookworm** (Debian 12), dont les paquets Python sont trop anciens | Réinstaller le Pi avec Raspberry Pi OS 64 bits **Trixie** (Debian 13) avec Raspberry Pi Imager |
| La page `http://IP_DU_PI/` ne s'ouvre pas depuis le PC | Portail arrêté ou port 80 pris ; mauvaise adresse ; PC et Pi sur deux réseaux (Wi-Fi invités, VLAN) | L'écran du dock dit si le portail tourne (Réglages → À propos) ; sur le Pi : `curl http://127.0.0.1/api/portal`, `sudo -u mydock mydock-dock doctor` (section « Portail »), `journalctl -u mydock-dock -e` ; essayer l'adresse IP plutôt que le nom `.local` |
| Association : « Code incorrect » / « Trop d'essais » / « Aucune association n'est en cours » | Faute de frappe, code expiré (dix minutes) ou annulé | Sur le dock : **Réglages → Associer un PC…** (un dock sans PC affiche déjà un nouveau code), puis relancer `mydock-agent pair --pi IP_DU_PI` |
| Association : « Le dock ne joint pas ce PC » | Le pare-feu du PC bloque le port de l'agent (8765), ou le PC est sur un autre réseau que le Pi | `mydock-agent setup --pi IP_DU_PI --open-firewall` ; la commande affiche l'adresse que le dock utilise et la règle `ufw` exacte |
| Écran noir au démarrage du Pi | Compositeur ou Chromium ne démarre pas | `journalctl -u mydock-kiosk -e` (Lite) ; vérifier `chromium` et `labwc` installés ; essayer `--force-mode 1024x600@60` |
| Écran à la mauvaise résolution | Mode non détecté | `sudo /usr/lib/mydock/setup --force-mode 1024x600@60` ; ou `video=HDMI-A-1:1024x600@60` dans `/boot/firmware/cmdline.txt` |
| Les boutons du dock ouvrent une page vide juste après une mise à jour | L'écran était ouvert pendant la mise à jour : il garde l'ancienne interface en mémoire et demande des pages qui n'existent plus. **Depuis la 1.1.1 il se recharge tout seul** (et, depuis la 1.2.0, la page touchée se rouvre) ; la première fois (de la 1.1.0 à la 1.1.1) il faut relancer son navigateur | Sur le Pi : `pkill -x chromium` (le kiosque repart en 2 s) ou `sudo reboot` ; puis `sudo -u mydock mydock-dock doctor` (section « Interface web » : tous les fichiers sont-ils là, le dock les sert-il ?) |
| Interface saccadée | Accélération GPU absente | `?debug=1` pour voir les i/s ; ajouter des options dans `/etc/mydock-kiosk/chromium.flags` (ex. `--use-gl=angle`) |
| Le jeton a fuité | — | Sur le PC : `mydock-agent setup --pi IP_DU_PI --rotate-token` ; le dock doit afficher un code (**Réglages → Associer un PC…**) : le nouveau jeton lui est transmis avec |

Outils : `./deploy/pc/diagnose.sh [--anonyme]` décrit le PC (session, GPU, audio, Steam, LibreOffice, réseau) sans afficher de secret.

## Développement

```bash
make venv          # environnement Python (3 paquets en mode éditable)
make test-fast     # tests Python rapides
make test          # tous les tests Python, déploiement et paquet compris
make test-ubuntu   # tests de l'agent avec les versions EXACTES des paquets Python d'Ubuntu 26.04
make deb           # construit build/mydock_<version>_all.deb (Pi, avec le paquet du PC dedans) et build/mydock-agent_<version>_all.deb
make lint          # ruff, svelte-check, shellcheck
cd dock/web && npm ci && npm test      # tests unitaires de l'interface
make demo          # agent en mode démo + dock sur http://127.0.0.1:8080
make e2e           # tests Playwright 1024×600 tactiles ; make shots régénère les captures de l'étape en cours
make web           # recompile l'interface (dock/web/dist est commité : pas de Node sur le Pi)
```

```
common/   protocole WebSocket, jeton, configuration 600, registre d'actions   (partagé)
agent/    service du PC : serveur, hub d'état, collecteurs (système, GPU, processus, musique, son, applications, Steam, LibreOffice, alimentation), Spotify (connexion OAuth/PKCE, recherche, bibliothèque, lecture, appareils Spotify Connect : `spotify/`), lanceur de programmes, mode démo, doctor
dock/
  server/ serveur du Pi : liaison avec l'agent, portail web et association par code, Wake-on-LAN, configuration, bourse (fournisseurs, cache, limites de débit), réglages de la veille de l'écran et de l'économiseur, vidéos de l'économiseur (`screensaver.py`), Divertissement (`freebox.py` : liste des chaînes ; `samba.py` : partages ; `player.py` : état de la lecture ; `player_session.py` : l'assistant qui pilote VLC dans la session de l'écran)
  web/    interface Svelte 5 + Vite + TypeScript (dist/ compilé et commité)
packaging/ construction des deux paquets .deb (build_deb.py) : deb-pi/ (le Pi, « mydock ») et deb/ (le PC, « mydock-agent »)
deploy/   pc/ : installation sans paquet ; pi/ : mise en place système lancée par le paquet (setup.sh), unité du kiosque, kiosque, veille de l'écran
docs/     rapports d'étape et captures 1024×600
scripts/  pile de démonstration, contrôle de l'interface compilée, tests de déploiement
```

Le protocole est décrit dans [`common/mydock_common/protocol.py`](common/mydock_common/protocol.py). Les tests automatiques tournent sous Python 3.13 et 3.14, ainsi qu'avec les versions exactes des paquets d'Ubuntu 26.04 (`make test-ubuntu`) ; le code vise Python 3.11 et plus.
