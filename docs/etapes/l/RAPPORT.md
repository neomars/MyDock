# Étape l — Retrogame : les jeux d'arcade de MAME, à la manette Bluetooth

Livrée le 13 novembre 2026, branche `claude/jolly-dirac-5np79y`, **version 1.7.0** (puis **1.7.1** : voir l'[addendum 1](#addendum-1--version-171--envoyer-un-jeu-depuis-la-page-du-pc-pas-depuis-le-pi-et-la-manette-filaire-usb)) (puis **1.7.2** : voir l'[addendum 2](#addendum-2--version-172--les-gros-jeux-et-lécran-gris-au-démarrage)) (puis **1.7.3** : voir l'[addendum 3](#addendum-3--version-173--les-gros-jeux-par-morceaux)) (agent, dock, protocole, et les deux paquets `.deb`).
Les captures de ce dossier sont en **mode démo** (`mydock-dock run --demo-retro` : douze faux jeux avec de vraies images, trois fausses manettes ; l'assistant de l'écran en démonstration) en 1024×600 tactile, texte doublé. Rien n'y touche MAME ni le Bluetooth : ce qu'un conteneur ne peut pas produire est dit plus bas.

> **À lire d'abord — ce que je n'ai pas pu vérifier.**
>
> 1. **Ton Pi, tes manettes, ton bouton.** Je n'ai ici ni Raspberry Pi, ni manette, ni adaptateur Bluetooth, ni GPIO. Tout ce qui touche au matériel est écrit **d'après les interfaces documentées** (BlueZ, libgpiod, evdev) et essayé contre des **faux** : un faux BlueZ sur un vrai bus D-Bus privé, un faux libgpiod, de vrais descripteurs de fichiers (tubes) à la place de `/dev/input/event*`. La liste précise de ce qui reste à essayer sur le Pi est à la fin de ce rapport (« [Ce qu'il reste à vérifier sur ton Pi](#ce-quil-reste-à-vérifier-sur-ton-pi) »).
> 2. **MAME, lui, a été essayé pour de vrai** (MAME 0.264, labwc sans écran, utilisateur ordinaire, `pong`, qui n'a besoin d'aucune ROM) : il ouvre sa fenêtre sous Wayland, **ignore SIGTERM et SIGINT**, quitte proprement sur le script Lua ou sur la fermeture de sa fenêtre, et dit pourquoi il refuse un jeu. Ce que je n'ai pas pu voir : le rendu sur le GPU du Pi, le son, et un jeu avec ses vraies ROMs (donc les meilleurs scores écrits à la sortie).
> 3. **Mise à jour depuis 1.6.x : redémarre le Pi une fois.** L'assistant de l'écran est lancé par la session graphique, qui ne connaît pas encore la nouvelle version : il s'arrête et reprend tout seul (le lanceur le relance), mais le groupe `gpio` du kiosque ne compte qu'à l'ouverture suivante de la session.
> 4. **`python3-libgpiod` n'a pas pu être essayé pour de vrai** : la version installable ici est la 1.6 (interface v1) compilée pour Python 3.12, alors que les tests tournent sous 3.13 et 3.14. Le code comprend **les deux interfaces** (v2 de Debian Trixie, v1 de Bookworm et d'Ubuntu 24.04), vérifiées contre de fausses bibliothèques qui reprennent les signatures documentées.

## Ce que tu demandais, et ce que j'ai fait

> « Contexte : MyDock (ce dépôt). On ajoute l'étape « l » : Retrogame, des jeux d'arcade MAME lancés sur l'écran du dock (Raspberry Pi 4, Raspberry Pi OS 64 bits Trixie, labwc + Chromium kiosque), joués à la manette Bluetooth, avec un bouton physique câblé sur le GPIO pour quitter le jeu et revenir au dock. »

Après le plan que je t'ai soumis, tu as répondu : « **mame existe dans les dépôts.** — **toucher pendant le jeu permettra de sortir de mame. Pas de bouton supplémentaire.** — **Possibilité d'uploader les jeux depuis l'interface web du PC.** » Trois décisions, appliquées telles quelles :

1. **MAME est un paquet recommandé** (`Recommends: mame`), pas une dépendance : sans lui, l'écran dit « MAME est absent du Pi : sudo apt install mame ».
2. **Aucun bouton pour quitter.** La page « En jeu » n'a qu'une grande zone tactile ; un toucher de l'écran quitte le jeu, comme le bouton physique et Select + Start tenus deux secondes. Pas de « Revenir au jeu » non plus (et donc pas de `SIGSTOP` : MAME n'est jamais mis en pause en douce).
3. **Envoi de jeux depuis le navigateur du PC** : une page `/jeux` du portail du Pi, protégée par **un code affiché sur l'écran du dock** (voir « Sécurité »).

Ce qui est livré, point par point de ton cahier des charges :

| | |
|---|---|
| **A. Interface** | Troisième entrée du Divertissement, **Retrogame** (gros bouton orange). La liste : nom entier, année · éditeur, **capture** (si le dossier de captures en a une), recherche au clavier tactile (nom, éditeur, année, nom MAME, sans accents), **Actualiser**. L'en-tête dit en direct « **Manette connectée** — Xbox Wireless Controller · 78 % » ou « **Aucune manette** », avec le bouton **Manettes**. Sans manette, un avertissement **qui ne bloque pas** ; le toucher lance le jeu en plein écran ; quand il s'arrête (bouton, toucher, combinaison, Échap, plantage) on revient **tout seul à la liste de Retrogame**, avec une phrase claire si le jeu n'a pas démarré. Balayage de gauche à droite pour revenir, comme partout. |
| **B. Lancement de MAME** | Le même assistant (`player_session.py`) pilote **VLC ou MAME, jamais les deux** : lancer l'un arrête l'autre. MAME : plein écran, **SDL Wayland natif**, `-skip_gameinfo`, le plugin des meilleurs scores, **ses propres dossiers** (configuration, mémoire, états, captures) sous le dossier de l'utilisateur de l'écran, `/etc/mydock-kiosk/mame.flags` en dernier, `-video accel` par défaut (voir « MAME »). L'écran n'envoie **que le nom d'un jeu** de la liste ; le dossier des ROMs est `/var/lib/mydock/roms` par défaut, lisible par l'utilisateur de l'écran, réglable (dock.toml, Réglages, `--roms-dir`). La liste vient de **MAME** (`-verifyroms`, `-listxml`), gardée en cache, relue quand le dossier change ou à « Actualiser ». |
| **C. Bouton physique** | **GPIO17 = broche 11** (masse : broche 9), résistance de rappel interne, anti-rebond, broche réglable (dock.toml, Réglages, `--button-gpio`), **libgpiod** (pas RPi.GPIO), lu **par l'assistant** (groupe `gpio`) : il marche même si Chromium est bloqué ou si le serveur du dock redémarre. Un appui : **en jeu**, sortie propre de MAME (script Lua → fermeture de la fenêtre → `SIGKILL`) et retour à la liste ; **pendant une vidéo**, le dock revient devant ; **sinon**, l'écran se rallume s'il dormait. |
| **D. Manettes Bluetooth** | Écran **Manettes** : manettes associées (connectée ou non, **batterie**), **Oublier** (avec confirmation), **Associer une manette** (30 secondes de recherche **qui ne montre que des manettes**, avec la manière de les mettre en mode association : PS4/PS5, Xbox, 8BitDo, Switch Pro). BlueZ **en D-Bus** (aucun `bluetoothctl`) : agent « Just Works » (`NoInputNoOutput`), `Trusted = true`, `Connect()`. Le suivi est **en direct** par les signaux de BlueZ et alimente l'en-tête de la liste. Le serveur du dock parle à BlueZ **sans rien retirer à son bac à sable** (`ProtectSystem=strict` reste) : il entre dans le groupe `bluetooth`. Le Bluetooth n'est allumé que quand on cherche ; `rfkill` n'est touché que sur un geste explicite et si `/dev/rfkill` est inscriptible, sinon l'écran donne `sudo rfkill unblock bluetooth`. Select + Start tenus 2 s quittent le jeu (même chemin que le bouton). |
| **E. Qualité et livraison** | Section « Retrogame » de `mydock-dock doctor` ; tests (voir plus bas) ; mode démo ; paquet (dépendances recommandées, groupes, dossier des jeux, `mame.flags`, `apt purge` qui **garde tes jeux**) ; README (étapes, captures, câblage, association des manettes, envoi de jeux, dépannage) ; changelogs ; ce rapport. Freetv et Kiosque **ne changent pas** au-delà de la cohabitation avec MAME. |

## Ce que voit l'écran

| | |
|---|---|
| **Menu du Divertissement** ([01](01-divertissement-trois-entrees.png)) | Trois gros boutons : Freetv, Kiosque, **Retrogame**. |
| **La liste des jeux** ([02](02-retrogame-liste.png)) | L'en-tête « Manette connectée » (nom, batterie) et **Manettes** ; la recherche, **Actualiser** et **Envoyer des jeux** (le petit bouton) ; puis les jeux, capture à gauche. |
| **Recherche** ([03](03-retrogame-recherche.png)) | « pac man » trouve Pac-Man et Ms. Pac-Man ; le compteur dit « 2 jeux pour « pac man » ». |
| **Lancement** ([04](04-jeu-lancement.png)) | « Lancement de MAME… » : aucun bouton, un seul grand toucher. |
| **En jeu** ([05](05-jeu-en-cours.png)) | Le titre, « En jeu », « Touche l'écran pour quitter le jeu. ». Sur le Pi, MAME recouvre cette page ; elle n'est vue qu'un instant ou si MAME est derrière. |
| **Jeu qui n'a pas démarré** ([06](06-jeu-rom-manquante.png)) | On est revenu à la liste ; au-dessus, « Le jeu n'a pas pu se lancer » et la phrase : « Le jeu « … » est incomplet : MAME ne trouve pas toutes ses ROMs (mslug.zip). Renvoie mslug.zip complet. » |
| **Sans manette** ([07](07-retrogame-sans-manette.png)) | « Aucune manette » et un avertissement orange qui n'empêche rien. |
| **Manettes** ([08](08-manettes-liste.png)) | La manette connectée avec sa batterie, celle qui est hors ligne (« appuie sur son bouton central »), **Oublier**. |
| **Association** ([09](09-manettes-recherche.png), [10](10-manettes-associee.png)) | Le décompte de 30 s, les consignes marque par marque, la manette trouvée et son bouton **Associer**, puis « Manette associée et connectée ». |
| **Envoyer des jeux** ([11](11-envoi-code.png)) | L'adresse à ouvrir sur le PC, le **code**, le nombre de jeux reçus et le temps qu'il reste. |
| **Réglages** ([12](12-reglages-retrogame.png)) | Le dossier des jeux, le nombre de jeux, la broche du bouton (− et +), le schéma de câblage en une phrase, et ce que **l'assistant de l'écran** dit du bouton (« GPIO17 (broche 11) : prêt. »). |

## Comment ça marche

```
 écran (Chromium)          serveur du dock (mydock, isolé)              session de l'écran (labwc)
  /ws ─ retro, pads ──►    GameLibrary (mame -verifyroms/-listxml)      assistant « player-session »
  dock.player.play         PadsManager (BlueZ, D-Bus système)  ◄─ /ws/player ─►  Session ── MamePlayback ── mame
  {kind: game, id}         portail :80 /jeux  ← PC (code de l'écran)               │        (SDL Wayland)
                                                                                    ├─ GpioButton (libgpiod)
                                                                                    └─ InputWatcher (evdev : toucher, Select+Start)
```

- **Un seul occupant de l'écran.** `Session._play` arrête ce qui joue (VLC ou MAME) avant de lancer autre chose ; `engine` (`vlc` ou `mame`) accompagne l'état du lecteur jusqu'à l'écran. Le chien de garde de 40 s du dock vaut aussi pour un jeu (« Le jeu n'a pas démarré : MAME n'a pas ouvert sa fenêtre en 40 secondes »), et l'écran du dock n'est jamais éteint pendant qu'un jeu joue.
- **« Affiché » = la fenêtre existe.** L'assistant ne déclare le jeu « en lecture » que quand `wlrctl toplevel list` montre une fenêtre `mame` (repli : au bout de 6 s si les fenêtres sont illisibles).
- **Sortie en trois temps** (`MamePlayback.stop`) : (1) un **fichier-drapeau** que le script Lua de MAME (`-autoboot_script`) regarde toutes les 15 images (≈ 0,25 s) : MAME s'arrête comme avec Échap, **le plugin des meilleurs scores écrit alors ses fichiers** ; (2) au bout de 2,5 s, **`wlrctl toplevel close`** sur sa fenêtre ; (3) au bout de 4 s de plus, **`SIGKILL`** au groupe de processus. MAME ignore SIGTERM et SIGINT : ils ne servent à rien. Le script Lua et le fichier des commandes (`-ctrlr mydock`) sont réécrits à chaque lancement par l'assistant (rien à installer, rien de root).
- **Les trois voies pour quitter**, toutes vers `Session._quit_game` (qui dit ensuite au dock « terminé, sans erreur » : l'écran revient à la liste) : le **toucher** (`InputWatcher` lit l'écran tactile **directement** — `BTN_TOUCH` ou `ABS_MT_TRACKING_ID` — parce que le navigateur est recouvert ; les 2 premières secondes ne comptent pas : c'est le doigt qui a lancé le jeu ; repli sur `swayidle` si l'écran tactile n'est pas lisible), le **bouton GPIO**, et **Select + Start tenus 2 s** sur une manette. Seuls les écrans tactiles et les manettes sont ouverts, **jamais un clavier**.
- **Reconnaissance des périphériques** : `/proc/bus/input/devices` (lisible par tous) puis les bitmaps `KEY`/`ABS` (`BTN_TOUCH` + une position = écran tactile ; `BTN_SOUTH` ou `BTN_SELECT` + `BTN_START` = manette). Une manette Bluetooth qui se connecte pendant un jeu est reprise au passage suivant (toutes les 2 s).
- **Le bouton** : `libgpiod` trouve la puce du connecteur par son **nom** (`pinctrl-bcm2711` sur le Pi 4, `pinctrl-rp1` sur le Pi 5…), pas par un numéro ; front descendant, résistance de rappel, anti-rebond de 30 ms au niveau du noyau (v2) et de 350 ms dans le programme. Le descripteur est surveillé par la boucle de l'assistant, sans fil d'exécution. Il démarre **avec l'assistant**, avant que le dock réponde : le dock ne fait que lui **transmettre la broche** (`configure`) ; l'état du bouton remonte dans le journal et dans Réglages.
- **BlueZ** (`bluetooth.py`) : `ObjectManager` pour la liste et les signaux (`InterfacesAdded`, `PropertiesChanged`) pour le direct ; batterie par `org.bluez.Battery1`, à défaut par le noyau (`power_supply`, pour les manettes Sony) ; association par `Pair()` avec un **agent « Just Works »** enregistré le temps de l'association, puis `Trusted = true` et `Connect()` ; une association ratée **ne laisse pas d'appareil à moitié fait** (`RemoveDevice`) ; seules les manettes sont montrées (d'après la classe d'appareil ou l'apparence que l'appareil déclare ; un appareil qui ne dit rien mais parle HID est montré : mieux vaut une manette de trop à ignorer qu'une manette introuvable).
- **Envoi de jeux** : `retro_upload.py` (session à code, écriture d'un fichier) et trois adresses de plus au portail (`portal.py`). Le corps est lu **par morceaux** et écrit au fur et à mesure (essayé avec 40 Mo ; je n'ai pas mesuré la mémoire, le code ne la garde pas) dans un fichier provisoire du dossier des jeux (`.envoi-….part`, que la liste ignore) avant le renommage final.

## Sécurité

- **Aucune commande ne vient de l'écran.** Il envoie un **nom de jeu** ; le dock le cherche dans sa liste validée par MAME, et l'assistant le revérifie (`[a-z0-9_]`, 24 caractères) ainsi que le dossier (chemin absolu, un seul dossier, ni `;` ni caractère de contrôle) avant de lancer `mame` **sans shell**, en liste d'arguments. Essayé avec des noms hostiles (`../../bin/sh`, `PONG`, `pong -video none`, nombres…) : MAME n'est jamais lancé.
- **L'écriture de fichiers sur le Pi exige d'être devant le dock.** Code de huit caractères affiché **seulement sur l'écran**, valable 15 minutes, **annulé après cinq erreurs**, vingt demandes par minute et par adresse au plus, comparaison à temps constant ; le jeton qu'il donne est **lié à l'adresse du PC** et meurt avec la session ; quitter l'écran d'envoi ferme la session. Ce qui peut être écrit : `nom.zip` ou `nom.7z` (nom `[a-z0-9_]`, 24 caractères), une **vraie archive** (signature, structure ZIP lisible, 20 000 entrées au plus), 400 Mo par fichier, 3 Go et 300 fichiers par session, jamais en laissant moins de 500 Mo libres, deux envois à la fois, écriture **atomique**. Rien n'est extrait ni exécuté. La page porte sa **propre politique de sécurité** (script et style par empreinte, `connect-src 'self'`, rien d'extérieur) ; le portail reste « sept adresses et rien d'autre ».
- **Le bac à sable du dock ne change pas** : `ProtectSystem=strict`, `ProtectHome=yes`, une seule capacité (`CAP_NET_BIND_SERVICE`), aucune famille d'adresses de plus. Le groupe `bluetooth` ne lui donne que le droit de parler à `bluetoothd`. Le dock **ne lit jamais** `/dev/input` ni `/dev/gpiochip*` : c'est le rôle de l'assistant, dans la session de l'écran, sous l'utilisateur du kiosque (sans accès au jeton).
- **`/var/lib/mydock` passe de 750 à 751** : l'utilisateur de l'écran doit pouvoir **traverser** ce dossier pour lire les jeux (`/var/lib/mydock/roms`, en 755), sans pouvoir y **lister** quoi que ce soit. Le fichier d'état du dock y est créé en 600 ; le reste (cache de la bourse, vidéos de l'économiseur, liste des jeux) n'a rien de secret ; la configuration et le jeton restent dans `/etc/mydock` (750), que personne d'autre ne traverse.
- **Rien d'ajouté sur le réseau** : MAME ne parle pas au réseau ici ; le Bluetooth est local ; le portail n'écoute pas de port de plus.

## Paquet, kiosque, diagnostic

- `Recommends:` **mame**, **python3-libgpiod**, **python3-dbus-fast**, **bluez**.
- `setup.sh` : dossier d'état en 751, dossier des jeux en 755, **serveur du dock dans le groupe `bluetooth`** (si le groupe existe), **kiosque dans le groupe `gpio`** (si le groupe existe ; en mode bureau, l'utilisateur de la session est ajouté à `input` et `gpio` s'il ne l'est pas), `/etc/mydock-kiosk/mame.flags` (jamais réécrit s'il existe). Aucun groupe n'est inventé.
- **`apt purge` garde `/var/lib/mydock/roms`** (des gigaoctets envoyés un à un) et le dit ; tout le reste de l'état s'en va, jeton compris.
- `mydock-dock doctor`, section **Retrogame** : MAME (chemin, version), dossier des jeux (archives, jeux validés, droits de lecture **pour l'utilisateur de l'écran**, place libre, captures), libgpiod, puces GPIO, groupes `gpio` et `input` **du kiosque** (le diagnostic tourne sous `mydock`, qui n'a pas à les avoir), ce que l'assistant a écrit du bouton dans `player.log`, écran tactile et manettes **vus par le noyau**, BlueZ, adaptateur, `rfkill`, groupe `bluetooth`, manettes associées, options de `mame.flags`.
- **Journal** : `player.log` reçoit la ligne de commande exacte de MAME, tout ce qu'il écrit (400 lignes au plus, préfixe `mame|`), la façon dont le jeu s'est arrêté (« propre (script) », « fenêtre fermée », « tué ») et le code de sortie ; `dock.log` les envois de jeux reçus ou refusés.

## Ce qui a été essayé, et comment

**Avec le vrai MAME 0.264** (`test_retro_real.py`, 6 essais, sautés si MAME, labwc ou `wlrctl` manquent ou si l'on est root) : un vrai **labwc sans écran**, un utilisateur ordinaire, `pong`. La fenêtre s'ouvre et le jeu passe à « en lecture » ; **SIGTERM est ignoré** (toujours vivant 1,5 s plus tard) ; la **sortie par le fichier-drapeau** rend le code 0 en moins d'une seconde ; avec un script Lua neutralisé, la **fermeture de la fenêtre** (`wlrctl`) fait quitter MAME avec le code 0 ; un MAME **figé** (`SIGSTOP`) est **tué** ; un jeu inconnu et un jeu sans ses ROMs donnent des phrases justes. Ce banc m'a appris trois choses que les faux n'auraient jamais montrées :

1. Sans `/etc/mame` dans `-inipath`, **MAME ne retrouve pas ses plugins** (« Could not load plugin: hiscore », code 3) : `-inipath` ne lit que le **premier** `mame.ini` trouvé, et c'est celui de `/etc/mame` qui dit où sont les plugins et les polices. Le dossier de l'assistant n'en a pas, donc celui de la distribution s'applique.
2. MAME écrit « `pacman.6e NOT FOUND (tried in pacman puckman)` » : les noms qui suivent « tried in » sont **séparés par des espaces** (le jeu, puis son parent) ; la phrase d'erreur nomme donc le parent (« Il faut aussi puckman.zip »).
3. La fenêtre de MAME se reconnaît à `app_id: mame` dans `wlrctl toplevel list` ; c'est ce qui sert à dire « affiché » et à la fermer.

**Avec de faux composants, mais le vrai code** (le reste) :
- `test_retro_session.py` (46) : un **faux MAME** qui ignore SIGTERM comme le vrai et obéit au drapeau, à la fenêtre fermée ou à rien (il faut le tuer), ou s'arrête avec le message et le code du vrai (ROM manquante, BIOS, jeu inconnu, plantage) ; la session : toucher armé, Select + Start, bouton avec ou sans dock, bouton pendant une vidéo ou au repos, noms hostiles refusés, un jeu qui en remplace un autre, mode démo.
- `test_inputs.py` (19) : de vrais descripteurs (des tubes) où l'on écrit des `input_event` bruts ; reconnaissance sur un `/proc/bus/input/devices` réaliste (écran tactile, clavier **ignoré**, souris, manette), toucher armé ou non, doigt qui reste, multitouch, événement coupé en deux, combinaison tenue ou relâchée trop tôt, manette qui arrive ou disparaît, périphérique illisible.
- `test_gpio_button.py` (13) : libgpiod v2 et v1 contre de fausses bibliothèques (puce choisie par son nom, pull-up, front descendant, anti-rebond), erreurs en phrases (groupe `gpio`, broche déjà prise, bibliothèque absente), rebonds, reconfiguration.
- `test_bluetooth.py` (18) : un **vrai bus D-Bus privé** et un faux BlueZ (`org.bluez`, `ObjectManager`, agent, `Pair`/`Connect`/`RemoveDevice`).
- `test_retrogame.py` (20), `test_retro_upload.py` (44, dont le **vrai portail** et 40 Mo envoyés par morceaux), `test_demo_retro.py` (11), `test_doctor_retro.py` (23), tests du hub (`test_player.py`), du déploiement et du paquet (`setup.sh` en vrai sous un faux préfixe, **le vrai `postrm`** lancé sous une racine temporaire : l'état s'efface, les jeux restent).
- **Interface** : 25 essais Vitest (`retro.test.ts`) ; **13 essais de bout en bout** (`stage-l.spec.ts`) en vrai Chromium 1024×600 tactile contre le vrai dock en démo : menu à trois entrées sans débordement, liste, recherche au clavier tactile, lancement, écran « En jeu » **sans autre bouton que sa zone tactile**, sortie par le toucher et par le bouton (deux fichiers dans le dossier d'exécution de l'assistant de démonstration), réveil de l'écran noir par le bouton, jeu sans ROMs, sans manette, manettes (liste, oubli confirmé ou annulé, recherche, association), **la vraie page `/jeux` du PC** (mauvais code refusé, bon code, un jeu reçu et écrit en minuscules, un mauvais nom refusé par la page, un faux zip refusé par le dock, **aucune violation de la politique de sécurité**), jeton exigé, Réglages.

## Manettes : ce qui est attendu, et les pièges

Compatibles **en principe** (le noyau les lit et MAME les reconnaît par SDL) — **aucune n'a été essayée ici** : **DualShock 4 et DualSense** (pilote `hid-playstation`), **Xbox One et Series** (`xpad`, ou `xpadneo` si tu l'installes), **8BitDo** en mode XInput ou Switch, **Switch Pro** (`hid-nintendo`). Dans MAME, **Select = pièce** et **Start = départ** par défaut ; `mydock.cfg` (écrit à chaque lancement) le dit explicitement pour les quatre joueurs.

Pièges connus, à ne pas redécouvrir :

- **Vieilles Xbox One (firmware d'avant 2016)** : elles s'associent mal tant que **l'ERTM du Bluetooth** est actif (`echo 1 | sudo tee /sys/module/bluetooth/parameters/disable_ertm`, à rendre permanent si ça règle le problème).
- **Xbox Series** : un firmware récent est nécessaire pour la reconnexion automatique.
- **Manettes qui s'associent mais restent « hors ligne »** : elles doivent être **de confiance** (c'est fait) ; certaines ne se reconnectent que lorsqu'on appuie sur leur bouton central, d'où le texte « appuie sur son bouton central ».
- **8BitDo** : le **mode** (XInput, Switch, DInput) change les boutons et leur nom ; MAME s'accommode de XInput et de Switch.
- **Batterie** : `org.bluez.Battery1` n'existe que pour certaines manettes ; pour les Sony, la charge vient du noyau (`power_supply`) ; sinon l'écran ne la montre pas plutôt que d'inventer.

## Ce que j'ai décidé sans ta réponse

Tu n'avais pas tranché ces points ; mes choix, que tu peux changer :

- **Sortie en trois temps** : drapeau Lua (2,5 s) → fermeture de la fenêtre (4 s) → `SIGKILL`. Les durées sont des constantes de `MamePlayback`.
- **Pas de règle `udev` pour `rfkill`** : le Bluetooth n'est débloqué que sur un geste explicite et si `/dev/rfkill` est inscriptible ; sinon l'écran donne la commande.
- **`-video accel`** par défaut (SDL accéléré, sans OpenGL « bureau » ni bgfx), **`-nowindow`** (plein écran) : changer dans `mame.flags` si le rendu saccade.
- **`/var/lib/mydock` en 751** plutôt qu'un dossier de jeux ailleurs : une seule place, un seul réglage ; un autre dossier se règle dans Réglages (il faut alors qu'il soit lisible par l'utilisateur de l'écran, et le diagnostic le dit).
- **`apt purge` garde les jeux** (et le dit).
- **Les captures ne sont pas téléchargées** : un fichier `<nom>.png` ou `.jpg` posé dans `/var/lib/mydock/retrogame/snap/` s'affiche ; sans lui, une manette. (Le dock n'accède pas à Internet pour cela.)
- **L'envoi** : 15 minutes, 5 erreurs, 400 Mo par fichier, 3 Go par session, 500 Mo gardés libres : des bornes raisonnables pour une carte SD, à relever si besoin (`retro_upload.py`).

## Ce qu'il reste à vérifier sur ton Pi

Dans l'ordre où je les essaierais :

1. **`apt install ./mydock_1.7.0_all.deb`**, puis `sudo reboot` (le groupe `gpio` ne compte qu'à la prochaine session). `sudo -u mydock mydock-dock doctor` : la section « Retrogame » doit être sans avertissement (MAME, libgpiod, groupes, écran tactile, adaptateur Bluetooth).
2. **Le rendu de MAME sous labwc** : lancer un jeu léger (Pac-Man) et un lourd (un jeu 16 bits), regarder la **fluidité**, le **plein écran** (la fenêtre doit couvrir le navigateur), les **proportions**. Si ça saccade : `-video opengl`, `-video soft` ou `-video bgfx` dans `/etc/mydock-kiosk/mame.flags` ; la ligne de commande exacte est dans `player.log`.
3. **Le son** : SDL doit trouver la sortie du Pi (HDMI ou prise) pour l'utilisateur du kiosque (groupe `audio`) ; s'il n'y en a pas, `-sound none` pour confirmer que le reste marche, puis régler.
4. **Le bouton physique** : câbler GPIO17 et la masse, regarder « GPIO17 (broche 11) : prêt. » dans Réglages → Retrogame, puis les **trois effets** (quitter un jeu, ramener le dock pendant une vidéo, rallumer l'écran). C'est le premier passage du vrai libgpiod : si le message dit « accès refusé », c'est le groupe `gpio` ; sur un Pi 5, la puce est `pinctrl-rp1`.
5. **La sortie propre avec les meilleurs scores** : avec les ROMs de Pac-Man, faire un score, quitter par chacune des trois voies, relancer : le score doit être là (`~/.local/share/mydock/mame/home/hiscore/pacman.hi`). C'est la seule vérification qui demande de vraies ROMs ; `pong` n'a pas de table de scores.
6. **L'association réelle** d'une manette (PS4 ou PS5, puis une Xbox) : la recherche ne doit montrer que des manettes ; l'association, la **batterie**, puis **éteindre la manette et la rallumer** : elle doit se reconnecter **sans rien toucher à l'écran**.
7. **La latence** : sentir un jeu d'action (le délai entre le bouton et l'écran), avec la manette en Bluetooth et en USB si tu peux comparer. Si la manette n'est pas vue par MAME alors que `evtest` voit ses boutons : `-joystickprovider sdlgame` ou `sdljoy` dans `mame.flags`.
8. **Les boutons Select et Start** : vérifier que Select met bien une pièce et Start lance, **et que Select + Start tenus deux secondes quittent** (les codes `BTN_SELECT` et `BTN_START` varient un peu d'un pilote à l'autre).
9. **L'écran tactile** : un toucher pendant un jeu doit le quitter. Si le diagnostic dit « Aucun écran tactile dans /proc/bus/input/devices » ou « illisible », c'est le groupe `input` (le repli par `swayidle` marche mais voit aussi un clavier branché).
10. **La coexistence Bluetooth et Wi-Fi** : le Pi 4 les fait passer par la même puce, sur 2,4 GHz. Une manette qui coupe, un son qui crépite pendant un jeu, ou un envoi de jeux lent peuvent venir de là : un Pi en **Ethernet**, ou un Wi-Fi en **5 GHz**, règle l'affaire.
11. **L'envoi d'un vrai jeu** (de 10 à 100 Mo) depuis ton PC, en Wi-Fi : essayé ici avec 40 Mo en boucle locale, pas sur un vrai réseau.
12. **Une mise à jour** (1.7.0 → suivante) avec un jeu en cours : l'assistant se relance, le jeu s'arrête (« L'assistant de lecture a redémarré ») et l'écran revient à la liste.

## Pour aller plus loin

- Télécharger les **captures** des jeux (un fichier `.png` par jeu) depuis une source de ton choix : la place est prête (`/var/lib/mydock/retrogame/snap/`), pas le téléchargement.
- **Deux joueurs** : MAME les prend en charge ; l'écran montre déjà « 2 manettes connectées » ; rien d'autre n'a été fait pour eux.
- **Favoris et jeux récents** en tête de liste, si la liste devient longue.

## Addendum 1 — version 1.7.1 : « envoyer un jeu depuis la page du PC, pas depuis le Pi » et la manette filaire USB

Deux demandes après la livraison de la 1.7.0.

### Envoyer un jeu depuis le PC, sans toucher au Pi

> « Je veux pouvoir uploader un jeu via la page html du site depuis le pc (pas depuis le raspi). »

Le constat : l'envoi existait (page `/jeux` du portail), mais (1) la page d'accueil du portail n'y renvoyait pas, et (2) il fallait **recopier un code affiché sur l'écran du Pi**, donc aller au Pi. Je t'ai demandé comment trancher entre commodité et prudence ; tu as choisi **« sans code pour le PC associé »**. Ce qui est livré :

- **Un lien « Envoyer des jeux »** sur la page d'accueil du portail (`http://IP_DU_PI/`), avec une capture : [13](13-portail-accueil-lien.png).
- **Le PC associé n'a plus de code à saisir** : la page `/jeux` demande au dock, en s'ouvrant, « ce PC est-il le PC associé ? » (`POST /api/retro/me`, toujours 200, jamais une erreur dans la console) ; si oui, elle montre **directement le choix des fichiers** ([14](14-page-pc-sans-code.png), [15](15-page-pc-envois.png)). Le PC associé est reconnu à l'**adresse IP enregistrée à l'association** (un nom `.local` saisi à la main est résolu), lue **sur la connexion** elle-même.
- **Tout autre appareil garde le code de l'écran** (un portable, un téléphone) : la page montre alors le champ du code, comme avant. Essayé avec un dock sans PC associé.
- **La page vérifie le nom du fichier avant d'envoyer** (minuscules, chiffres, `_`, `.zip` ou `.7z`, 400 Mo) et le dit en une ligne ; le dock revérifie tout. Un refus rapide d'un gros fichier n'est plus un « le dock ne répond pas » (le dock lit le début du corps avant de refuser, pour que la réponse arrive).
- **Réglage** : Réglages → Retrogame → **Envoi : Sans code / Avec code** (`upload_without_code` dans `[retrogame]`, vrai par défaut). « Avec code » : tout le monde, PC associé compris, recopie le code. Le droit du PC associé est **revérifié à chaque envoi** : couper le réglage ou associer un autre PC coupe aussitôt les jetons déjà donnés.
- **Plafonds** : la session du PC associé a les siens (15 minutes, 3 Go, 300 fichiers) ; recharger la page ne les remet pas à zéro. Le sondage compte dans les vingt demandes par minute et par adresse.

**Ce que ça change pour la sécurité, honnêtement.** Écrire dans le dossier des jeux n'exige plus d'être devant le dock **pour le PC associé** : c'est une confiance par **adresse IP**, du même ordre que la liste `allowed_clients` de l'agent. Quelqu'un qui usurperait l'adresse du PC sur ton réseau local pourrait envoyer des `.zip`/`.7z` (rien d'exécuté, mêmes noms, mêmes archives vérifiées, mêmes bornes) ; il pourrait de toute façon lire le jeton qui circule en clair, limite déjà connue. Ça ne marche que si un PC est associé et si le réglage est actif. Tout est dans le README (Sécurité).

### Une manette filaire (USB)

> « Ajoute la possibilité de manette filaire en usb »

Une manette USB marchait déjà côté MAME (SDL) et pour « Select + Start tenus », mais l'écran **ne la voyait pas** : il ne connaissait que BlueZ, donc « Aucune manette » et un avertissement à tort. Maintenant :

- **Le dock suit les manettes USB à chaud** (`wired_pads.py`) : il relit `/proc/bus/input/devices` (lisible malgré le bac à sable du dock) toutes les 2 secondes et ne prévient l'écran que si la liste change. Une manette dont le **bus est l'USB** est « connectée » ; une manette Bluetooth y figure aussi (bus Bluetooth) mais n'est pas comptée deux fois.
- **L'en-tête de la liste** dit « Manette connectée — Nom · USB » (ou « 2 manettes connectées »), **sans avertissement** ; l'écran **Manettes** a une section « Manettes USB » ([16](16-manette-usb.png)) : « Branchée en USB : rien à associer », **sans « Oublier »** ni batterie. Sans Bluetooth, la page le dit et rappelle qu'une manette USB marche quand même.
- **Un détail attrapé au passage** : le **pavé tactile des manettes Sony** (DualShock 4, DualSense) apparaît au noyau comme un écran tactile ; un toucher dessus aurait pu quitter le jeu. Il est maintenant écarté (propriété « pointeur » du noyau, ou « touchpad » dans son nom), pour l'USB comme pour le Bluetooth.
- `mydock-dock doctor` dit, pour chaque manette vue par le noyau, **par où elle est branchée** (USB ou Bluetooth), et la règle d'envoi de jeux.
- Démonstration : un fichier `demo-usb-pads.txt` du dossier d'état « branche » des manettes USB fictives.

Tests ajoutés : `test_wired_pads.py` (14 : reconnaissance sur un `/proc` réaliste avec DualShock 4 en USB, sa manette de mouvement et son pavé tactile, une Xbox en Bluetooth, un écran, un clavier ; branchement et débranchement à chaud ; lecture ratée ; thème « pads » du dock ; association et oubli refusés pour une manette USB), 19 de plus pour l'envoi sans code (sessions, serveur réel, réglage coupé en cours de route, autre PC associé, adresse `::ffff:`), des essais Vitest et 5 de bout en bout (`stage-l.spec.ts` : PC associé avec le lien de l'accueil et la vraie page, autre appareil avec le code, réglage, manette USB branchée puis débranchée).

### Ce qu'il reste à vérifier sur ton Pi (en plus de la liste ci-dessus)

- **Le PC associé est bien reconnu** : ouvre `http://IP_DU_PI/jeux` depuis lui ; la page doit dire « Ce PC est celui qui est associé au dock : aucun code à saisir ». Si elle demande le code, l'adresse du PC vue par le Pi n'est pas celle enregistrée (PC à deux adresses, Wi-Fi et Ethernet, ou adresse qui a changé) : `mydock-dock doctor` l'indique.
- **Une vraie manette USB** : elle doit apparaître en 2 secondes à chaque branchement, et MAME la lire. Je n'ai pu essayer que sur des listes de périphériques écrites à la main, modelées sur de vrais `/proc/bus/input/devices`.

## Addendum 2 — version 1.7.2 : les gros jeux et l'écran gris au démarrage

Deux problèmes vus sur ton Pi après la 1.7.1.

### Les gros jeux ne s'envoyaient pas

> « La restriction d'upload limite la taille des fichiers. » — `tetriskr.zip` (23 Ko), `tetrisse.zip` (21 Ko), `tetriskr_cga.zip` (157 Ko) : « envoyé ✔ » ; `tetris.zip` (3,1 Mo), `tetrisp.zip` (5,7 Mo), `tetrisp2.zip` (13,9 Mo) : « envoi interrompu : le dock n'a pas répondu (fichier refusé ?) ».

**Ce que j'ai établi.** Ce n'est pas une limite de taille : le plafond par fichier est de 400 Mo, et je n'ai pas pu reproduire l'échec avec un vrai Chromium qui envoie 0,2 / 3 / 14 Mo à un dock sain (les trois arrivent). **Ce que j'ai reproduit**, en revanche, c'est le **mécanisme qui cachait la vraie raison** : quand le dock refuse un fichier avant de le lire (nom, taille, place restante), la réponse de refus part alors que le navigateur envoie encore des Mo ; le dock ne vide que 256 Ko avant de fermer, et la connexion est coupée (RST) : le navigateur ne reçoit jamais le message et affiche le texte générique. Avec un petit fichier (moins de 256 Ko), tout le corps est lu, la réponse arrive, le message s'affiche. C'est exactement la frontière que tu as observée (157 Ko passe, 3,1 Mo non). Le dock refuse un fichier de cette façon quand il ne reste pas assez de place : **il garde 500 Mo libres sur la carte**, donc un jeu n'est reçu que s'il reste 500 Mo après lui.

**Ce que je ne peux pas affirmer.** La raison du refus sur ton Pi (le plus probable : la place libre de la carte, à vérifier avec `df -h /var/lib/mydock` ou la ligne « Place libre pour les jeux » de `mydock-dock doctor`, et `Envoi de jeu refusé … : <code>` dans `/var/log/mydock/dock.log`). Je ne l'ai pas vue.

**Ce qui change.**

- **La page demande avant d'envoyer.** Nouvelle adresse du portail, `POST /api/retro/check` (nom, taille, place ; même jeton que l'envoi ; elle n'écrit rien et ne compte pas comme un envoi). Un refus est donc une réponse normale, avec son message, y compris pour 14 Mo. La page ne l'envoie pas si le dock dit non. Le portail passe de huit à **neuf** adresses (README et docstring à jour).
- **Si la connexion lâche quand même en cours d'envoi**, la page redemande au dock pourquoi avant de parler de réseau (« envoi interrompu avant la fin (réseau ou Wi-Fi ?) : renvoie le fichier »).
- **Le refus de place donne les chiffres** : « Pas assez de place sur la carte du Pi : 480,0 Mo libres, il en faut 514,0 Mo (14,0 Mo pour ce jeu et 500,0 Mo que le dock garde libres). » ([17](17-page-pc-refus-place.png), réponse du dock simulée dans le navigateur pour la capture).
- `mydock-dock doctor` dit, si la carte a moins de 1 Go de libre, **le plus gros jeu qu'on peut envoyer** ; la même ligne existait sans cette indication.
- J'ai **gardé les 500 Mo** de réserve : c'est une protection du système (journaux, mises à jour) et je n'ai pas la preuve que c'est elle qui te bloque. Si la carte est petite et que tu veux la réduire, c'est la constante `FREE_RESERVE` de `retro_upload.py`.

Tests ajoutés : 6 sur `check_upload`, 15 sur la route réelle du portail (accord, refus de place d'un fichier de 14 Mo, nom, taille, jeton, session pleine, JSON), 6 du diagnostic (place libre, écran du dock), et 3 e2e dans un vrai navigateur (un jeu de 6 Mo arrive entier ; un refus annoncé n'envoie rien ; une connexion coupée redemande la raison).

### L'écran gris au démarrage

> « L'écran reste gris avec le pointeur de la souris visible. » (Chromium en vie, dock sain ; après l'avoir tué et relancé, tout marche.)

**Ce que je sais.** Un Chromium en vie qui n'a pas de fenêtre à l'écran : le lanceur du kiosque le démarrait une fois et ne vérifiait plus rien. **Ce que je ne sais pas** : pourquoi il reste gris au démarrage (je n'ai pas de Pi ; les pistes : fenêtre ouverte avant que le compositeur ait un écran, première initialisation graphique qui échoue). Plutôt que parier sur une cause, j'ai fait en sorte que **l'état gris se répare tout seul**, et j'ai fermé les pistes plausibles.

- **Il vérifie que la page est affichée** : la fenêtre de Chromium existe (`wlrctl toplevel list`) **et** le dock voit le navigateur connecté (`/api/health` indique maintenant `browsers`, le nombre de navigateurs connectés). Sinon, **au bout d'une minute**, Chromium est arrêté et relancé ; après **trois échecs** de suite, il repart avec `--disable-gpu`. Un dock qui ne répond pas, ou un compositeur sans `wlrctl`, ne déclenchent rien (on ne tue jamais un Chromium sur un doute).
- **Il attend que le compositeur ait un écran** (30 s au plus) avant d'ouvrir la fenêtre.
- **Il n'y en a qu'un** : un verrou (`flock`) empêche un second lanceur. C'est ce qui s'est passé hier : lancer `kiosk-browser.sh` à la main pendant que celui du système tournait a ouvert une deuxième fenêtre sur le même profil (l'écran qui s'allume et s'éteint). Maintenant le second s'arrête de lui-même, en le disant.
- **Avant chaque lancement**, il arrête un Chromium resté sur ce profil et efface le verrou de profil laissé par une coupure de courant (qui peut désigner un numéro de processus réutilisé depuis).
- `mydock-dock doctor` dit « Aucun navigateur n'est connecté au dock : l'écran est gris ou vide » tant que c'est le cas.

Un détail attrapé en testant : un processus fini mais pas encore « lu » par `wait` répond encore à `kill -0` ; le premier jet du lanceur aurait attendu la minute entière avant de voir un Chromium qui plante au lancement. Il lit maintenant l'état du processus.

Tests ajoutés (`test_deploy.py`, avec un faux Chromium, un faux dock, un faux `wlrctl`) : 14 sur le lanceur (relance d'un Chromium gris, cinq combinaisons fenêtre/dock, aucun geste si le dock ne dit rien, un second lanceur qui n'ouvre rien, verrou de profil périmé, Chromium orphelin arrêté, attente de l'écran, repli sans accélération), un sur `/api/health`.

### Ce qu'il reste à vérifier sur ton Pi

- **Redémarre le Pi une fois après la mise à jour** (`sudo reboot`) : le nouveau lanceur ne remplace celui qui tourne qu'à la prochaine ouverture de session. Si l'écran est gris au démarrage, attends une minute : il doit se réparer seul ; `sudo journalctl -u mydock-kiosk -e` (Pi sans bureau) dit « Chromium n'a rien affiché… ». **Si cela se répète à chaque démarrage, envoie-moi ces lignes** : elles diront si c'est l'écran, le processeur graphique ou autre chose.
- **Envoie un gros jeu** : s'il est refusé, la page doit maintenant en dire la raison avec les Mo libres.
- Je n'ai pas pu essayer le lanceur sur un vrai labwc avec le vrai Chromium ; les essais s'appuient sur de faux outils. Le nom de fenêtre cherché (`chrom…`) est celui que le lecteur utilise déjà pour mettre le dock devant.

## Addendum 3 — version 1.7.3 : les gros jeux, par morceaux

> « L'écran est stabilisé. L'upload ne fonctionne toujours pas : tetris.zip · 3,1 Mo — *envoi interrompu avant la fin (réseau ou Wi-Fi ?) : renvoie le fichier* ; tetrisp.zip · 5,7 Mo — *idem* »

**Ce que ce retour établit.** Avec la 1.7.2, la page demande d'abord au dock si le fichier sera reçu, puis envoie ; quand l'envoi échoue, elle redemande. Ton message veut donc dire : le dock a dit « oui » avant, la connexion a lâché pendant l'envoi, et il dit encore « oui » après. **Ce n'est donc pas un refus** (ni la place, ni le nom, ni les plafonds : mon hypothèse de la 1.7.2 était fausse pour ton cas) : la connexion est coupée en route, pour les requêtes de plusieurs Mo, et jamais pour celles de moins de 157 Ko.

**Ce que j'ai essayé, sans réussir à le reproduire** (vrai portail, vrai envoi, 3 / 6 / 14 Mo, tout arrive) :

- la pile HTTP de uvicorn : `h11` (celle de mes essais) et `httptools` (celle que `apt` installe avec `python3-uvicorn`, qui la recommande) ;
- les versions de FastAPI, Starlette et uvicorn de l'époque de Debian Trixie, à la place de celles, bien plus récentes, de mon environnement ;
- une liaison lente : 3 Mo étalés sur 10 s, puis sur 50 s.

**Ce que je n'ai donc pas trouvé** : *pourquoi* ton réseau coupe ces requêtes. Il est propre à ton installation (Wi-Fi du Pi, carte réseau, box, pare-feu, extension du navigateur du PC : je ne peux pas le voir d'ici). Plutôt que de deviner, j'ai changé la manière d'envoyer, d'après la seule chose établie (les petites requêtes passent) :

- **La page envoie le jeu par morceaux de 128 Ko**, un par requête (`POST /api/retro/upload?name=…&total=…&offset=…`), **chacun repris automatiquement** s'il échoue (six essais, pause qui s'allonge). À chaque réponse, le dock dit **où l'envoi en est** (`received`). Si la réponse d'un morceau s'est perdue alors qu'il était écrit, le morceau renvoyé reçoit « 409 : ici » avec le bon point de reprise, et la page repart de là : **ni trou, ni doublon**. Si le dock a oublié l'envoi (redémarré, abandonné), la page recommence le jeu une fois ou deux depuis le début.
- Le jeu est écrit dans un **fichier provisoire** (`.envoi-*.part`, invisible pour MAME) et **ne prend son vrai nom qu'une fois entier et vérifié** (signature, structure ZIP) : comme avant.
- **Mêmes contrôles et mêmes plafonds** qu'avant (nom, 400 Mo par jeu, 3 Go et 300 fichiers par session, 500 Mo gardés libres), **plus** : la place libre est revérifiée **à chaque morceau** (d'autres envois ont pu remplir la carte), **quatre envois en cours au plus**, un morceau de 1 Mo au plus, un envoi sans nouvelle pendant dix minutes est abandonné et son fichier effacé (les fichiers provisoires orphelins, après un redémarrage du dock, aussi). La session compte un jeu, pas vingt-cinq morceaux.
- **La surface du portail ne grandit pas** : c'est la même adresse `/api/retro/upload`, avec deux paramètres de plus. L'envoi d'un seul tenant (pour `curl`) existe toujours, inchangé.
- **Un refus qui arrive** (nom, session…) **arrive vraiment au navigateur** : le corps d'un morceau (128 Ko) est lu en entier avant la réponse, ce qui n'était pas le cas d'un fichier de plusieurs Mo (c'était la cause du faux « le dock n'a pas répondu » de la 1.7.1).
- **Le journal dit maintenant ce qui se passe** : `Envoi de jeu coupé par le navigateur ou le réseau (adresse, jeu, morceau à l'octet N, annoncé M) après T s` dans `/var/log/mydock/dock.log`, et une erreur interne du dock y est tracée au lieu de couper la connexion. Si un morceau échoue encore sur ton réseau, ces lignes diront **où** (à quel octet, au bout de combien de temps) : c'est ce qu'il faudra m'envoyer.
- À l'écran, un envoi abandonné dit **à quel pourcentage** il s'est arrêté.

Tests ajoutés : 19 sur le gestionnaire par morceaux (chaque cas limite : réponse perdue, morceau coupé en route sans demi-morceau écrit, taille changée, trop d'octets, archive abîmée à la fin, place qui disparaît en route, abandon et ménage, plafonds, deux appareils qui envoient le même nom), 13 sur le vrai portail (un jeu de 3 Mo en vingt-cinq morceaux, reprise d'une réponse perdue, paramètres, jeton, plafonds, coupure de la connexion en plein morceau avec la ligne du journal, erreur interne), et dans un vrai navigateur 2 e2e nouveaux (**un morceau coupé deux fois de suite puis repris ; une réponse perdue après l'écriture**, avec des octets aléatoires comparés à l'octet près) et 1 adapté (la connexion qui lâche pour de bon).

### Ce qu'il reste à vérifier sur ton Pi

- **Envoie `tetris.zip` (3,1 Mo)** : la page doit montrer le pourcentage qui avance par petits pas, puis « envoyé ✔ ». S'il y a des « nouvel essai… » en route mais que ça finit, c'est ton réseau qui perd des paquets : le Pi en filaire, ou plus près de la box, le confirmera.
- **S'il échoue encore**, envoie-moi `sudo grep 'Envoi de jeu' /var/log/mydock/dock.log | tail -20` et le pourcentage affiché.
