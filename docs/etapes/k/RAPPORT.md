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

## Addendum 2 — version 1.6.2 : « les chaînes sont listées mais la vidéo ne se lance pas, et dans le Kiosque non plus »

> [Photo de l'écran du Pi : « La lecture a échoué — VLC n'a pas pu lire cette vidéo (format, ou accès au partage). main input error: Votre média d'entrée ne peut être ouvert ; main input error: VLC ne peut pas ouvrir « smb://mafreebox.freebox.fr/Freebox/Download/Daredevil%20%20Born%20Again%20-%20S02E02… »] — « Corrige ces erreurs. Et crée un fichier de log qui permettra le débogage. »

**Ce que la photo apprend (et que 1.6.1 ne pouvait pas voir).** VLC parle **français** sur ton Pi. Tout ce que l'assistant de lecture reconnaissait dans ses messages était écrit en **anglais** : « Permission denied », « No such file », « unable to open the MRL 'smb://… » — et, sans que personne le voie, **« to continue »**, le mot par lequel il reconnaît une lecture en pause. Aucune cause n'était donc jamais reconnue, d'où la phrase générique. Mon banc, en anglais, ne pouvait pas le montrer. **VLC, ffmpeg et smbclient tournent maintenant en C.UTF-8**, quelle que soit la langue de la session (`LC_ALL`, `LANG`, `LANGUAGE`).

**Ce que je n'ai toujours pas pu établir** : *pourquoi* VLC n'ouvre pas ton fichier `smb://mafreebox.freebox.fr/Freebox/Download/…` alors que `smbclient` parcourt le même partage avec le même identifiant. La photo montre « ne peut pas ouvrir » sans la ligne qui dirait pourquoi (elle était coupée : on ne montrait que les dernières lignes, les génériques). Les causes possibles sont toutes propres au module Samba de VLC (absent, ou refusant ce serveur) — et pour toutes, la même parade :

1. **Repli par smbclient** (Kiosque). Quand VLC n'ouvre pas le fichier, `smbclient` le lit et le donne à VLC par un tube (`fd://0`). **Essayé sur le banc avec le vrai VLC, le vrai smbclient et un vrai Samba protégé** : le chemin direct échoue exprès (fichier absent côté VLC), le repli lit le vrai fichier, la lecture démarre (position, durée), le journal raconte les deux tentatives. Limites assumées : **pas d'avance rapide** (VLC ne revient pas en arrière dans un tube ; la télécommande grise les sauts et écrit « sans avance rapide »), et un nom de fichier contenant `;` ou `"` ne passe que par VLC (sinon `smbclient` exécuterait un second ordre — vérifié sur un vrai Samba, puis refusé).
2. **Une vérification avant le tube.** `smbclient` écrit ses erreurs sur **la même sortie que la vidéo** (vérifié : `NT_STATUS_ACCESS_DENIED` arrive dans le flux) : sans cela, un mauvais mot de passe aurait donné une vidéo illisible sans explication. Un test `allinfo` dit d'abord si le fichier est accessible, et pourquoi sinon.

**Freetv — la cause n'est pas établie non plus.** Les chaînes sont listées (le correctif de 1.6.1 a marché), puis la vidéo ne se lance pas, sans que je sache à quelle étape (RTSP, ffmpeg, VLC, affichage). Deux changements plausibles : ffmpeg ne recopie plus que **l'image et le son** (`-map 0:v? -map 0:a?` : un flux de télévision porte aussi du télétexte et des données qu'un copie brut ne sait pas écrire, ce qui fait échouer ffmpeg avant la première image — une hypothèse, pas une certitude), et si VLC a le module RTSP, le pont ffmpeg sert de **second essai**. Le reste, le journal le dira.

**Le fichier de log demandé.**

| Fichier | Qui l'écrit | Contenu |
|---|---|---|
| `/var/log/mydock/player.log` | l'assistant de lecture (sous l'utilisateur de la session de l'écran) | en tête, ce que l'assistant trouve sur le Pi (versions de VLC, ffmpeg, smbclient, modules de VLC, session, langue, `wlrctl`/`xdotool`/`swayidle`) ; pour chaque vidéo : la demande du dock, **chaque tentative**, les commandes exactes (VLC, ffmpeg, smbclient), **tout ce que VLC (lancé en `-vv`), ffmpeg et smbclient disent**, le moment où la lecture démarre, la fin ou la ligne `ÉCHEC lecture N : …` |
| `/var/log/mydock/dock.log` | le serveur du dock (utilisateur `mydock`) | liste des chaînes lue ou refusée, partages et dossiers parcourus, lectures demandées ou refusées, réglages changés (le mot de passe y est « (défini) ») |

Ils tournent (1,5 Mo, trois anciens gardés), sont créés par l'installation (dossier à bit collant `1777`), **n'écrivent jamais le mot de passe** (testé : une occurrence éventuelle devient `***`, l'identifiant n'est même pas écrit), et `mydock-dock doctor` les nomme et montre les dernières lectures ratées. **L'écran d'une lecture ratée écrit le chemin du journal.** Lecture en direct : `sudo tail -f /var/log/mydock/player.log` pendant qu'on touche une chaîne.

**Ce que tu peux faire :** installer cette version (l'assistant se relance tout seul dans ta session), lancer **une chaîne et une vidéo du Kiosque**, puis m'envoyer `sudo cat /var/log/mydock/player.log` (sans le ranger : les noms de fichiers y figurent). Il dira laquelle des étapes coince.

| Contrôle (1.6.2) | Résultat |
|---|---|
| Tests ajoutés | les tentatives et leur ordre ; le repli (VLC échoue sur `smb://`, `smbclient` lit : arguments, environnement, mot de passe absent des arguments et du journal) ; la vérification préalable et ses messages ; refus des noms dangereux (sur un vrai Samba) ; VLC et ffmpeg reçoivent l'anglais ; pont ffmpeg image + son ; journal (emplacements, rotation, secret effacé, échecs retrouvés par le diagnostic) ; journal du dock ; `seekable` du dock à l'écran (Vitest, Playwright) ; installation (dossier `1777`, droit d'écriture du service, purge) |
| Banc d'essai (vrai VLC 3.0.20, vrai smbclient, vrai Samba protégé) | repli : lecture démarrée (1 s / 30 s, sans avance rapide), journal complet ; un nom avec `;` jamais transmis à smbclient |
| Suites complètes | `ruff`, `shellcheck`, `svelte-check` propres ; Python **2 515 réussis** (5 ignorés : l'utilisateur ordinaire manque en root) ; avec les versions exactes des paquets d'Ubuntu 26.04 **2 444 réussis** ; essais du vrai VLC sous un utilisateur ordinaire : réussis ; Vitest **371 réussis** ; Playwright **280 réussis** |
| Ce qui reste non vérifié | tout ce qui touche ton Pi : pourquoi VLC n'ouvre pas ton fichier, pourquoi la chaîne ne démarre pas, et la fluidité du décodage |

## Addendum 3 — version 1.6.3 : ce que dit le journal de ton Pi

> `lecture 3 : la tentative « direct » a échoué (smb stream error: read failed (Invalid argument)) ; on essaie « smbclient » … smbclient n'atteint pas le fichier : Le serveur de fichiers a répondu autrement que prévu. … ÉCHEC lecture 3 : … (VLC et smbclient ont tous deux essayé).` — fichier `smb://mafreebox.freebox.fr/Freebox/Download/Coup de Tête/Coup.de.Tête…BDRip.1080p.x264…mkv`

**Ce que ces lignes apprennent.**

1. **VLC ouvre le fichier** (le chemin, les accents, l'identifiant sont bons : ce que le Kiosque listait était juste) **mais échoue à le lire** : `smb stream error: read failed (Invalid argument)`. C'est le module Samba de VLC et la Freebox qui ne s'entendent pas sur la lecture ; ce n'est ni un codec, ni l'écran. Je ne sais toujours pas *pourquoi* (la ligne `machine :` du journal, désormais écrite en tête, dira si ton Pi est en 32 ou 64 bits : une piste classique pour de gros fichiers).
2. **Mon repli de 1.6.2 a échoué à cause de mon contrôle préalable, pas de ton partage.** Je vérifiais l'accès avec la commande `allinfo` de smbclient et je concluais « inaccessible » si sa réponse ne ressemblait pas à celle de Samba. La Freebox y répond autrement. Le contrôle est **supprimé** : c'était une hypothèse sur le format d'une réponse que je n'avais jamais vue.

**Ce qui change.** smbclient lit le fichier (`get "nom" -`) et l'assistant **lit le premier bloc avant de lancer VLC** : un fichier vidéo ne commence jamais par `NT_STATUS_…` ou `tree connect failed` (smbclient écrit ses erreurs sur la même sortie que le fichier : vérifié), donc un texte d'erreur est reconnu et expliqué, et sinon le bloc et la suite sont relayés à VLC. Une erreur inconnue est **citée** (« Le serveur de fichiers refuse la lecture : « … » ») au lieu de « répondu autrement que prévu », et la phrase finale ajoute ce que VLC avait dit d'abord.

**Un défaut que seul le vrai VLC a montré.** Mon premier relais donnait à VLC l'entrée standard d'asyncio : une **socketpair**, pas un tube. VLC ne lisait alors rien (`prefetch stream error: cannot seek`), alors que les faux programmes du test disaient « ça marche ». Le relais crée maintenant un vrai tube ; le test vérifie que l'entrée de VLC en est un.

**Essayé sur le banc (vrai VLC 3.0.20, vrai smbclient, vrai Samba protégé), trois cas :**

| Cas | Résultat |
|---|---|
| VLC échoue, smbclient lit le fichier | **la lecture démarre** (sans avance rapide) |
| Mauvais mot de passe | « Accès refusé : ce partage demande un identifiant » (VLC et smbclient ont tous deux essayé), avec ce que chacun a dit |
| Fichier absent | « Ce fichier n'existe plus sur le partage », avec le détail |

**Ce qui reste non vérifié** : que `smbclient get` lise tes gros fichiers de la Freebox jusqu'au bout (il se peut qu'elle le refuse aussi : le journal le dira, et la phrase le citera), et Freetv, dont tu n'as pas envoyé de lignes.

| Contrôle (1.6.3) | Résultat |
|---|---|
| Suites | `ruff`, `shellcheck` propres ; Python **2 534 réussis** (5 ignorés : l'utilisateur ordinaire manque en root) ; avec les versions exactes des paquets d'Ubuntu 26.04 **2 463 réussis** ; essais du vrai VLC sous un utilisateur ordinaire : **15 réussis** ; l'interface n'a pas changé depuis 1.6.2 (Vitest 371, Playwright 280 alors) et ses parcours liés au Divertissement (stage k, 9, et les 5 du dock simulé) ont été rejoués : réussis |

## Addendum 4 — version 1.6.4 : « les vidéos du Kiosque se lisent, pas le flux des chaînes »

> « Les vidéos via Kiosque se lisent correctement. Mais pas le stream de chaîne TV. » — [extrait de `player.log` : VLC ouvre la chaîne, `h264_v4l2m2m` décode, « Received first picture », « Detected interlaced video », `drm_av_deinterlace` → `deinterlace_v4l2m2m` sur `/dev/video18`, puis `[destroyed object]: error 7: importing the supplied dmabufs failed` et « Video discarded due to Wayland congestion » en boucle]

**Le Kiosque marche.** La lecture de secours par `smbclient` (1.6.3) lit les fichiers de la Freebox : la cause de l'échec du module Samba de VLC reste inconnue, mais elle est contournée.

**La chaîne n'est pas une panne de flux, de ffmpeg ni de réseau : tout le chemin fonctionne jusqu'à l'écran.** Le journal le montre étape par étape : le pont ffmpeg donne le flux, VLC le lit (« Buffering 99 % »), le décodage matériel démarre (`h264_v4l2m2m`), la première image arrive, le son se synchronise (`pulse audio output … started`). **Puis** VLC voit que la chaîne est **entrelacée** (720×576, comme toute chaîne en SD) et lui applique le **désentrelaceur matériel de Raspberry Pi OS** (`drm_av_deinterlace`, qui passe par `v4l2m2m` sur `/dev/video18`). Les images qu'il produit sont **refusées par labwc** (`importing the supplied dmabufs failed`), et toutes les suivantes sont écartées (`Video discarded due to Wayland congestion`). Résultat : la position avance et le son sort, l'écran reste noir — c'est pourquoi l'écran du dock croit que ça « joue ». Les films du Kiosque, progressifs, n'activent pas ce filtre : ils s'affichent.

**Correction.** VLC est lancé avec **`--deinterlace=0`** : sans désentrelaceur, les images décodées par le matériel passent comme celles des films. Le prix : **un peu de « peigne » sur les mouvements rapides** d'une chaîne entrelacée ; `/etc/mydock-kiosk/vlc.flags` permet de rétablir un désentrelacement par le processeur (`--avcodec-hw=none` puis `--deinterlace=1`, raisonnable en SD, trop lourd en HD — une option par ligne, VLC garde la dernière). Je n'ai pas choisi ce désentrelacement logiciel par défaut : **je ne peux pas essayer l'écran du Pi**, et la seule chose que ton journal prouve est que le désentrelaceur matériel casse l'affichage — pas que le chemin logiciel marche mieux.

**Ce que je n'ai pas pu vérifier** : que l'écran s'affiche une fois le désentrelacement coupé. C'est ce que le journal laisse prévoir (les films, décodés par le même matériel et affichés par le même `wl_dmabuf`, passent), mais c'est une déduction, pas une constatation. J'ai vérifié que VLC **accepte** l'option et que le journal **le signale** désormais : au bout de 20 lignes « image écartée », l'assistant écrit une fois `affichage en difficulté` avec la dernière ligne de VLC — jusqu'ici rien ne distinguait une lecture qui avance sur un écran noir d'une lecture qui marche.

| Contrôle (1.6.4) | Résultat |
|---|---|
| Tests ajoutés | `--deinterlace=0` présent et avant les options de `vlc.flags` (qui peuvent le rétablir) ; une lecture dont VLC écarte les images écrit « affichage en difficulté » une seule fois sans être une panne |
| Suites | `ruff`, `shellcheck` propres ; Python **2 536 réussis** (5 ignorés : l'utilisateur ordinaire manque en root) ; avec les versions exactes des paquets d'Ubuntu 26.04 **2 465 réussis** ; essais du vrai VLC sous un utilisateur ordinaire : **15 réussis** ; banc du repli smbclient (3 cas) inchangé ; l'interface n'a pas changé (Vitest 371) et ses parcours liés au Divertissement (stage k, 9, et les 5 du dock simulé) sont réussis |

