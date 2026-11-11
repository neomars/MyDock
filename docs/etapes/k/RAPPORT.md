# Étape k — Le Divertissement : Freetv et Kiosque

Livrée le 11 novembre 2026, branche `claude/jolly-dirac-5np79y`, **versions 1.6.0** (agent, dock, protocole inchangé, et les deux paquets `.deb`).
Les captures de ce dossier sont en **mode démo** (chaînes et partage fictifs : aucun réseau) en 1024×600 tactile, texte doublé ; les états que la démo ne produit pas (assistant absent, lecture qui échoue, partage introuvable…) viennent d'un **dock simulé**.

> **À lire d'abord — ce que je n'ai pas pu vérifier.**
>
> 1. **Ton Pi, ta Freebox.** Je n'ai ici ni l'un ni l'autre. La liste des chaînes est lue **d'après le format que la Freebox publie** (lignes `#EXTINF` avec « numéro - nom », une adresse `rtsp://…/fbxtv_pub/stream?…&flavour=…` par qualité) et essayée contre une liste d'essai de 27 chaînes ; **la vraie liste peut avoir des détails que je n'ai pas prévus**. L'essai « Tester Freetv et Kiosque » (Réglages) dit en trois lignes ce qui répond chez toi.
> 2. **La lecture réelle, sur un banc d'essai, pas sur un Pi.** J'ai fait tourner la chaîne complète dans le conteneur de développement avec de **vrais programmes** : labwc (un compositeur Wayland sans écran), VLC 3, XWayland, ffmpeg, `swayidle`, `wlrctl`, un vrai Samba (`smbd`) avec un partage « Disque dur » et un serveur RTSP que VLC fournit lui-même pour jouer les « chaînes ». Sur ce banc, tout marche de bout en bout (voir plus bas). **Je n'ai pas essayé** : le décodage de tes vraies chaînes (MPEG-2 / H.264 de la Freebox) par VLC sur un Raspberry Pi 4 — **la fluidité est à constater chez toi** — ni le son (HDMI ou prise : aucune carte son ici), ni ton Samba de Freebox (accès libre ou compte ? Je ne sais pas ce que ta box exige : l'écran affiche « Accès refusé » avec la marche à suivre le cas échéant).
> 3. **Ce qui dépend de la version de Raspberry Pi OS.** `wlrctl` (changer la fenêtre active sous labwc) n'existe peut-être pas dans ta distribution : le dock se rabat alors sur `xdotool` (VLC passe par XWayland) — essayé sur le banc, mais pas sur le Pi. Le module RTSP de VLC (`live555`) est absent de plusieurs distributions : dans ce cas **`ffmpeg` recopie le flux vers VLC sans le décoder** (c'est ce chemin que le banc a exercé). Si VLC s'affiche mal sur ton Pi, `/etc/mydock-kiosk/vlc.flags` permet d'ajouter des options (voir le dépannage du README).
> 4. **Mise à jour depuis 1.5.x : redémarre le Pi une fois.** L'assistant de lecture est lancé par la session de l'écran, qui ne le connaît pas encore : l'écran du Divertissement le dit (« L'assistant de lecture de l'écran ne répond pas ») jusqu'au redémarrage.

## Ce que tu demandais, et ce que j'ai fait

> « Nous allons désormais ajouter des fonctions d'entertainment. Je veux que tu supprimes le bouton Alimentation. Et que tu l'ajoutes en petite taille à droite de l'heure. Le bouton "alimentation" est remplacé par le bouton "Divertissement". La première fonctionnalité est "Freetv", c'est un lecteur VLC qui permet la lecture des chaînes de la Freebox (http://mafreebox.freebox.fr/freeboxtv/playlist.m3u). La deuxième fonction est "Kiosque" qui permet de parcourir les partages Samba de la Freebox et ainsi de pouvoir lire des vidéos via VLC. »

Question que j'ai posée : **où la vidéo doit-elle s'afficher ?** Ta réponse : « Sur l'écran du dock ». VLC joue donc **sur le Pi, en plein écran sur le 7"**, avec le son du Pi.

1. **Le bouton Alimentation quitte l'accueil et devient un petit bouton ⏻ à droite de l'heure** (56 px, toutes pages comprises) ; la page Alimentation, ses confirmations et ses garde-fous sont inchangés.
2. **Le bouton « Divertissement » prend sa place** à l'accueil (icône de clap, violet) : il ouvre un menu à deux boutons, **Freetv** et **Kiosque**. Ceux qui avaient réglé l'ordre des pages dans Réglages le **gardent** : Divertissement prend la place qu'occupait Alimentation (migration au premier lancement).
3. **Freetv** : la liste des chaînes de la Freebox (27 dans la liste d'essai), **recherche** au clavier tactile (nom sans accents ni majuscules, ou numéro), bouton **HD**, un toucher lance la chaîne.
4. **Kiosque** : les partages Samba de la Freebox, les dossiers, les vidéos ; un toucher lance le fichier.
5. **La télécommande** : pendant la lecture, **un toucher sur l'écran ramène le dock** avec pause / lecture, sauts de 30 s et 5 min (fichiers), volume, « Revenir à la vidéo » et « Arrêter ». La vidéo continue derrière.
6. **Réglages → Divertissement** : adresse de la liste, serveur de fichiers, identifiant, mot de passe (jamais affiché), essai.

## Ce que voit l'écran

| | |
|---|---|
| **Accueil** ([01](01-accueil-divertissement.png), [02](02-alimentation-par-la-barre.png)) | Le bouton **Divertissement**, qui dit ce qui joue quand quelque chose joue ; le petit **⏻** à droite de l'heure ouvre la page Alimentation, de n'importe où. |
| **Menu** ([03](03-divertissement-menu.png)) | Deux gros boutons, **Freetv** et **Kiosque** ; un bandeau « En lecture » avec le titre ramène à la télécommande ; l'échec d'une lecture est écrit dans le menu et s'efface avec « Compris ». |
| **Freetv** ([04](04-freetv-liste.png)) | Liste de chaînes (numéro, nom, pastille HD), champ de recherche, interrupteur HD, compteur (« 3 chaînes pour « serie » »). |
| **Télécommande, direct** ([05](05-telecommande-direct.png)) | Titre et état (« En direct »), pause, volume, « Revenir à la vidéo », « Arrêter ». Les sauts sont grisés : on n'avance pas dans un direct. |
| **Kiosque** ([06](06-kiosque-partages.png), [07](07-kiosque-dossier.png)) | Les partages, puis les dossiers et les vidéos (taille en Ko, Mo, Go), « Dossier parent » pour remonter, le chemin en haut (« Disque dur › Films »). |
| **Télécommande, fichier** ([08](08-telecommande-fichier.png)) | Position et durée (« 0:03 / 0:30 »), barre d'avancement, ±30 s et ±5 min. |
| **Erreurs** ([09](09-kiosque-erreur.png)) | Un dossier ou un partage introuvable (un accès refusé ou un serveur muet se disent de même) : une phrase en français et un bouton pour réessayer. |
| **Réglages** ([10](10-reglages-divertissement.png)) | Les quatre réglages, le mot de passe en « •••••••• » (le dock ne l'envoie jamais à l'écran, il dit seulement qu'il existe), et l'essai en trois lignes. |

## Comment ça marche

```
 écran (Chromium)           service du dock (mydock, isolé)             session de l'écran (labwc)
  /ws  ───────────────────►  actions dock.tv.* / dock.media.* /          assistant « player-session »
  télécommande               dock.player.*  ─── /ws/player (127.0.0.1) ──►   │  lance VLC (oldrc, socket Unix)
                             PlayerHub : état, minuteur de démarrage         │  ffmpeg → VLC si pas de module RTSP
                             liste Freetv, smbclient                         │  swayidle : un toucher → dock devant
```

- **Pourquoi un assistant.** Le service du dock est isolé du système (il tient le jeton du PC) et n'a aucun accès à l'écran. VLC doit donc être lancé **depuis la session graphique**, par le kiosque, comme le navigateur : `deploy/pi/player-session.sh`, relancé s'il s'arrête, lancé par l'`autostart` de labwc. Il se connecte au dock en **local seulement** (`ws://127.0.0.1:8080/ws/player`).
- **L'état** (`idle`, `starting`, `playing`, `paused`, `error`) vit dans le dock : il survit à un rechargement de la page, un minuteur de 40 s met fin à un démarrage qui n'aboutit pas, et les messages d'une lecture déjà remplacée sont ignorés.
- **Freetv.** Les chaînes sont groupées par adresse (SD, HD, LD sont des variantes d'une même chaîne), l'identifiant d'une chaîne est une empreinte de son adresse, et **l'écran ne reçoit jamais d'adresse de flux**. La liste n'est lue que depuis une adresse du réseau local, sans suivre de redirection, 1 Mo au plus, et gardée dix minutes (une liste périmée vaut mieux qu'aucune si la box ne répond plus).
- **Kiosque.** `smbclient` (liste d'arguments, jamais un shell) : partages (`-L`), contenu d'un dossier (`ls` avec `-D` à part), seulement les vidéos, tri naturel, dossiers d'abord. Les chemins venus de l'écran sont refusés s'ils contiennent `..`, `\`, `/` d'un nom, ou un caractère de contrôle ; l'adresse `smb://` est reconstruite par le dock.
- **Le mot de passe du partage** n'est jamais dans une ligne de commande : il va dans l'environnement de `smbclient` et, pour VLC, dans un petit fichier de configuration (droits 600, dossier d'exécution de la session) effacé à la fin de la lecture.
- **Pause, position, volume.** VLC est piloté par son interface `oldrc` sur un socket Unix propre à l'assistant. J'ai appris sur le banc que `is_playing` vaut 1 même en pause (VLC 3) : la pause se reconnaît à la réponse de `status`, et « en lecture » veut dire « le temps avance ».
- **Le toucher.** `swayidle` voit le premier toucher (comme pour la veille de l'écran) et le dock passe devant ; VLC est mis derrière par `wlrctl` (ou `xdotool`). Le toucher qui a lancé la vidéo ne compte pas (1,5 s de grâce). Pendant la lecture, la veille de l'écran du dock et l'économiseur sont tenus éveillés.

## Sécurité

- Le **navigateur n'envoie que des identifiants** (une chaîne que le dock connaît, ou un chemin relatif à un partage) : jamais une adresse. Aucune commande, aucune adresse de flux ne vient de l'écran.
- **Rien n'est exécuté avec un shell.** `smbclient`, `ffmpeg`, `vlc`, `wlrctl` : des listes d'arguments construites par le dock ou l'assistant.
- **`/ws/player` n'accepte que 127.0.0.1** et refuse toute requête portant un en-tête `Origin` : une page web ne peut pas parler à l'assistant. L'assistant ne reçoit que « joue cette adresse », « pause », « position », « volume », « arrêter » ; il tourne sous l'utilisateur du navigateur et **ne lit jamais le jeton du PC**.
- **Le mot de passe du partage** vit dans `/etc/mydock/dock.toml` (droits 600) et n'est jamais envoyé à l'écran, journalisé, ni affiché par le diagnostic ; l'interface ne sait que « défini ou non ».
- Les adresses de liste sont limitées au **réseau local** ; aucun accès à Internet n'est ajouté (le navigateur du kiosque ne contacte toujours que `127.0.0.1`).
- Limite assumée : le trafic Samba et RTSP entre le Pi et la Freebox est celui de la box (non chiffré par MyDock) ; il reste sur le réseau local.

## Paquet, kiosque, diagnostic

- **Paquet du Pi** : `Recommends` `vlc-bin`, `vlc-plugin-base`, `vlc-plugin-video-output`, `vlc-plugin-samba`, `smbclient`, `xwayland`, `wlrctl | xdotool` ; `ffmpeg` et `swayidle` étaient déjà exigés. Ce sont des recommandations : sans elles, le dock marche et l'écran du Divertissement dit lequel manque, avec la commande `apt`.
- **Kiosque** : `player-session.sh` est lancé par l'`autostart` de labwc ; en mode systemd l'utilisateur du kiosque rejoint le groupe `audio` ; `/etc/mydock-kiosk/vlc.flags` ajoute des options à VLC. Une mise à jour relance l'assistant d'une session déjà ouverte ; la première installation demande un redémarrage.
- **`mydock-dock doctor`** : une section « Divertissement » (VLC, module RTSP ou pont ffmpeg, changement de fenêtre, swayidle, smbclient, XWayland, assistant en marche, réglages sans le mot de passe, liste des chaînes et partages de la Freebox).
- **`mydock-dock init --playlist-url … --smb-host …`** règle la liste et le serveur en ligne de commande.

## Ce qui a été essayé, et comment

**Sur le banc d'essai (vrais programmes, utilisateur ordinaire, compositeur Wayland sans écran)** : accueil → Divertissement → Freetv → TF1 → la chaîne joue (VLC + pont ffmpeg) → la vidéo est devant (capture de l'écran réel) → un vrai clic de souris ramène le dock → « Revenir à la vidéo » → pause puis reprise → arrêt (il ne reste que le navigateur) → Kiosque → fichier du partage `smb://` → il joue (« 0:03 / 0:30 ») → +30 s (« 0:27 / 0:30 ») → arrêt. Le banc a aussi servi à trouver des défauts que les faux programmes ne montraient pas (`is_playing` qui vaut 1 en pause, socket de VLC qui se ferme brutalement à sa fin, `DISPLAY` d'XWayland qui change d'un démarrage à l'autre, identité de la fenêtre de Chromium qui est « chromium » ou « chromium-browser » selon l'installation) et à écrire le repli sur `xdotool`.

**Tests automatiques** : formats de la liste, sécurité des chemins, `smbclient` contre un vrai `smbd`, état de la lecture et minuteurs, assistant contre de faux `vlc`, `ffmpeg`, `wlrctl`, `xdotool` et — hors root — contre le vrai VLC, interface (Vitest), parcours complets (Playwright 1024×600 tactile, vraie pile dock + assistant de démonstration), paquet et installateur.

| Contrôle | Résultat |
|---|---|
| `ruff check`, `ruff format --check`, `shellcheck`, `svelte-check` | propres (0 erreur, 0 avertissement) |
| Python (`pytest`, tout le dépôt) | **2 444 réussis**, 5 ignorés (les 3 essais du vrai VLC, rejoués à part ci-dessous, et 2 qui supposent un utilisateur ordinaire : ici tout tourne en root) |
| Python avec les versions exactes des paquets d'Ubuntu 26.04 (`make test-ubuntu`) | **2 374 réussis**, 4 ignorés |
| Les 3 essais avec le **vrai VLC** (ignorés en root), lancés sous un utilisateur ordinaire | **3 réussis** |
| Interface (Vitest) | **367 réussis** (20 fichiers) |
| Parcours complets (Playwright, 1024×600 tactile, vraie pile dock + assistant de démonstration) | **278 réussis** : une première exécution complète en a montré 2 en échec — deux attentes anciennes de l'ordre des pages (« Alimentation » y figurait encore) que j'avais oubliées ; corrigées, rejouées et réussies |
| `check-web-dist.sh` | l'interface compilée correspond aux sources |

## Pour aller plus loin

Je n'ai pas inventé de fonction que tu n'as pas demandée. À toi de dire, après essai chez toi, si tu veux : un favori de chaînes, la reprise d'un film là où il s'est arrêté, les sous-titres, ou le choix de la piste audio.

## Addendum 1 — version 1.6.1 : ce que tu as constaté sur ton Pi

> « Les vidéos ne se lisent pas. On a bien accès aux répertoires partagés par la Freebox (login/pass). Le message d'erreur de lecture d'une vidéo : "VLC n'a pas pu lire cette vidéo (format, ou accès au partage)". Sur Freetv : "Les chaînes ne sont pas disponibles", alors que mon PC sur le même réseau accède au fichier http://mafreebox.freebox.fr/freeboxtv/playlist.m3u sans login/pass. »

**1. Freetv — une cause trouvée et corrigée.** Le dock ne lisait la liste que depuis une adresse du réseau local. Or le nom que Free donne à la box, `mafreebox.freebox.fr`, se résout vers une adresse **publique** de Free (212.27.38.253), que la Freebox sert chez toi : le dock la prenait pour « hors du réseau local » et refusait de la lire. Mon banc n'avait aucun moyen de le voir (aucun accès à Internet, une liste d'essai sur 127.0.0.1). **Ce nom-là, et lui seul, est maintenant lu** ; une autre adresse doit toujours être privée, aucune redirection n'est suivie (7 tests). *Je n'ai pas pu vérifier que c'était bien le message que tu as eu* — tu n'as cité que le titre ; le texte sous le titre, ou le bouton « Tester Freetv et Kiosque » de Réglages, le dit précisément. Si l'écran affiche encore une erreur, dis-moi laquelle.

**2. Kiosque — la cause n'est pas établie, mais l'écran va la dire.** Ce que j'ai vérifié sur le banc avec un **vrai Samba protégé par identifiant** et le vrai VLC : l'identifiant et le mot de passe arrivent bien à VLC (fichier de configuration en droits 600), un fichier au nom plein d'accents, de crochets, de parenthèses et de `#` se lit, et un identifiant erroné **faisait attendre VLC indéfiniment** (il attendait une saisie que personne ne peut faire) — corrigé. Le message que tu as reçu est celui de « VLC s'est arrêté sans avoir joué et n'a rien dit de reconnaissable » : ce n'est ni un accès refusé, ni un fichier introuvable (VLC les dit, et le dock les reconnaît). Je ne peux pas deviner la suite sans ton Pi : **ce que VLC a écrit était jeté**. Maintenant :

- la phrase de l'écran nomme la cause quand VLC la donne : **accès refusé**, **fichier introuvable**, **module Samba absent** (`vlc-plugin-samba`), **codec qui manque ou trop lourd pour le Pi**, **affichage impossible** (`xwayland`), **plantage de VLC** avec le signal (souvent le décodage matériel : `--avcodec-hw=none` dans `/etc/mydock-kiosk/vlc.flags`) ;
- **le dernier message de VLC s'affiche dessous, en petit** (pour une chaîne, celui de ffmpeg, qui était aussi jeté) ;
- la dernière lecture ratée est gardée en entier dans `$XDG_RUNTIME_DIR/mydock-player.log` (droits 600, sans mot de passe) ;
- `mydock-dock doctor` vérifie les modules de VLC (Samba, décodeurs, affichage) et dit quel paquet installer.

**Ce que tu peux faire :** installer cette version, relancer une vidéo, et me copier **la ligne en petit sous la phrase** de l'écran. C'est elle qui dira si c'est le codec, le lecteur, l'affichage ou le partage.

| Contrôle (1.6.1) | Résultat |
|---|---|
| Tests ajoutés | liste lue sous le nom officiel de la box et refusée sous un autre ; 11 explications d'échec ; détail de VLC sans adresse mémoire ni bruit ; journal en droits 600 sans mot de passe ; message de ffmpeg ; plantage par signal (vrai signal envoyé à un faux VLC) ; modules de VLC au diagnostic ; découpage phrase / détail à l'écran (Vitest) ; mise en page à 1024×600 (Playwright) |
| Essais sur le banc (vrai VLC 3.0.20, vrai Samba) | identifiant juste : lit ; identifiant faux ou absent : échoue en moins d'une seconde (« Permission denied ») ; nom de fichier avec accents, crochets, `#` : lit |
| Suites complètes | `ruff`, `shellcheck`, `svelte-check` propres ; Python **2 471 réussis** (5 ignorés : l'utilisateur ordinaire manque en root) ; avec les versions exactes des paquets d'Ubuntu 26.04 **2 401 réussis** ; essais du vrai VLC sous un utilisateur ordinaire : réussis ; Vitest **371 réussis** ; Playwright **279 réussis** |
| Ce qui reste non vérifié | tout ce qui touche ton matériel : décodage, affichage et son sur le Pi, et ce que ton Samba de Freebox accepte |
