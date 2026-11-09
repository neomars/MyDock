# Étape j — La sortie sonore de Spotify, et l'économiseur d'écran

Livrée le 9 novembre 2026, branche `claude/jolly-dirac-5np79y`, **versions 1.4.0** (agent, dock, protocole inchangé, et les deux paquets `.deb`).
Les captures de ce dossier sont en **mode démo** (catalogue et appareils fictifs : aucun réseau, aucun compte) en 1024×600 tactile, texte doublé ; les états que la démo ne produit pas (refus de Spotify, agent trop ancien…) viennent d'un **dock simulé**.

> **À lire d'abord — ce que je n'ai pas pu vérifier.**
>
> 1. **Les vraies enceintes.** Je n'ai ici ni ton compte Spotify, ni ton PC, ni une seule enceinte : la liste des appareils, le passage de la musique sur l'un d'eux et son volume sont écrits d'après la documentation de l'API de Spotify (Spotify Connect) et essayés contre une **API simulée** et la démonstration, jamais contre le vrai service. Ce que je sais de Spotify et que j'ai codé en conséquence : seuls les appareils que Spotify a vus récemment sont listés ; certains appareils se déclarent « restreints » ou refusent le réglage du volume ; **toute commande de lecture demande un compte Premium**. Quand Spotify refuse, l'écran affiche sa raison — c'est elle qui dira vite ce qui bloque chez toi.
> 2. **Vues aériennes d'Apple (ajout final).** Sur demande, le dock les télécharge lui-même (dix films de la liste de xscreensaver-aerial, convertis en H.264 par `ffmpeg`, voir le README) et les joue par défaut ; **ni les adresses d'Apple, ni le téléchargement, ni la lecture sur ton Pi ne sont essayés** (aucun accès à Internet ici : réseau simulé et vraie conversion `ffmpeg` sur un film de test). Les scènes dessinées servent de secours. Les films n'entrent jamais dans le paquet.
> 2b. **Les vidéos d'Apple ne sont pas dans MyDock, et ne peuvent pas y être.** Les vues aériennes de l'Apple TV et du Mac sont la propriété d'Apple : je ne peux ni les redistribuer ni, depuis mon environnement (aucun accès à Internet), en télécharger ou en vérifier une seule adresse. **À la place** : cinq scènes animées **dessinées par le programme** (montagnes au lever du jour, fonds marins, ville de nuit vue du ciel, aurore boréale, voyage parmi les étoiles) — des évocations, pas des prises de vue — **et** un dossier où déposer **tes propres vidéos**, qui passent alors en plein écran (voir plus bas).
> 3. **Le Pi 4 lui-même.** Je n'ai mesuré les scènes et lu les vidéos que dans un Chromium de développement sur un PC : ni le coût réel sur ton Pi, ni le décodage matériel d'une vidéo H.264 par son Chromium ne sont vérifiés. Les scènes sont pensées pour peser peu (512×300 agrandi, 24 images par seconde, aucun filtre ni ombre portée) et le diagnostic prévient pour une vidéo trop lourde, mais **la fluidité sur ton écran est à constater chez toi**.
> 4. **Les boutons de lecture quand la musique sort sur une enceinte.** Précédent / lecture-pause / suivant passent toujours par le lecteur du PC (MPRIS) : l'application Spotify doit donc **rester ouverte sur le PC**, et je ne peux pas savoir si ta version annonce encore par MPRIS l'état d'une lecture qui sort ailleurs. L'écran, lui, sait sur quel appareil la musique joue (c'est l'API qui le dit).

## Ce que tu demandais, et ce que j'ai fait

> « Je veux : 1 - pouvoir choisir la sortie sonore dans mon Spotify (enceintes connectées en Wi-Fi et autres…) 2 - si aucune action n'est faite sur l'écran, passe en économiseur d'écran. Le temps d'attente doit être configurable dans Réglages. Voici ce que je veux en économiseur d'écran : "Sur Apple TV et sur Mac, les économiseurs d'écran utilisent des séquences vidéo dynamiques (comme les fameuses vues aériennes de paysages, fonds marins ou environnements urbains)". »

1. **La page Spotify a un quatrième onglet, « Sortie sonore »** : les appareils Spotify Connect de ton compte (ce PC, enceintes connectées en Wi-Fi, téléphones, télévisions…) et les sorties du PC (haut-parleurs, casque, Bluetooth, HDMI). Un toucher sur un appareil **passe la lecture en cours dessus** ; le lecteur dit sur quel appareil la musique sort et règle **le volume de cet appareil**.
2. **L'écran passe en économiseur d'écran** après un délai **réglable dans Réglages** (jamais, 1, 2, **5** — le choix de départ —, 10, 15 ou 30 minutes) : une vue animée plein écran, avec l'heure, le nom de la vue et le morceau en cours ; **le premier toucher la ferme sans rien déclencher dessous**. Cinq scènes dessinées en direct, **et** les vidéos que tu déposes dans le dossier de l'économiseur du Pi.

## La sortie sonore

### Ce que voit l'écran

| | |
|---|---|
| **Onglet « Sortie sonore »** | **Appareils Spotify** : une ligne par appareil, avec son genre (Ordinateur, Enceinte, Téléphone, Télévision, Voiture…), son volume quand il le dit, et une coche sur celui qui joue. **Ce PC** passe en premier et garde le nom de la machine en sous-titre. Un appareil restreint est grisé (« commandes refusées »). **Sorties du PC** dessous : haut-parleurs, casque, Bluetooth, HDMI ; une note explique qu'elles ne servent que lorsque la musique joue sur ce PC. **Actualiser** relit la liste ; elle se relit aussi en ouvrant l'onglet et toutes les 15 s tant qu'il est ouvert. |
| **Un toucher sur un appareil** | La lecture **continue là où elle en était** (en pause, elle reste en pause) sur l'appareil choisi ; un message dit « Sortie : … ». Un second toucher pendant le changement n'en lance pas un autre. |
| **Le lecteur** | Un bandeau « Sortie : Salon (Sonos Era 100) » mène à la liste ; le **volume** réglé est celui de l'appareil, pas celui du PC (le curseur du PC revient dès que la musique revient sur ce PC). |
| **États expliqués** | Pas de compte relié (même écran que la recherche), liste vide (« Ouvre Spotify sur le PC, ou allume ton enceinte »), refus **Premium**, appareil disparu ou qui n'accepte pas les commandes à distance, liste qui ne se charge pas, **agent du PC plus ancien que le dock** (« mets-le à jour »). |

### Ce que fait l'agent

Trois nouvelles actions nommées, comme toutes les autres (liste blanche, paramètres stricts, aucune n'est « sensible ») : `spotify.devices` (liste), `spotify.transfer` (`{device, play}`) et `spotify.volume` (`{device, percent}`). L'identifiant d'un appareil doit être **un identifiant que l'agent a lui-même listé** : s'il ne le connaît pas, il relit la liste une fois, puis refuse (« Cet appareil n'est plus visible »). Les droits demandés à Spotify à la connexion couvraient déjà ces commandes : **aucune nouvelle connexion à faire**.

Au passage, **lire un morceau tient compte de l'appareil actif** : avant, tout morceau touché ramenait la musique sur le PC ; maintenant elle repart là où elle sort déjà (sauf juste après le lancement de l'application du PC, où c'est ce PC qui est visé).

## L'économiseur d'écran

### Les états de l'écran

```
awake ──(délai de l'économiseur)──▶ saver ──(délai de la veille)──▶ asleep (noir)
  ▲                                   │                                │
  └──── waking ◀─── un toucher ───────┴────────────────────────────────┘
```

Le premier toucher d'un économiseur ou d'un écran noir **ne fait que réveiller** : un voile invisible avale le reste du geste (relâchement et clic synthétique du navigateur), comme pour la veille livrée à l'étape f. Si le délai de la veille est **plus court ou égal** à celui de l'économiseur, la veille passe avant et l'économiseur n'a jamais le temps de paraître : la carte des Réglages le dit en orange.

### Les réglages

**Réglages → Économiseur d'écran** : *Démarrer après* (Jamais, 1, 2, **5**, 10, 15, 30 min), *Montrer* (**Tout**, Scènes, Mes vidéos), une phrase qui dit ce qui va se passer, le nombre de vidéos du Pi (ou comment en ajouter), et **Essayer maintenant**. Le choix est gardé **par le dock** (avec celui de la veille), pas par le navigateur ; le diagnostic du Pi le montre (`mydock-dock doctor`, section « Économiseur d'écran »).

### Les scènes

Dessinées par `dock/web/src/lib/scenes.ts` sur un canevas de **512×300**, agrandi à l'écran, à **24 images par seconde**, avec un générateur de hasard à graine (une scène recommencée est la même ; les tests la rejouent). Elles se succèdent toutes les **75 s** par un fondu au noir, **sans jamais répéter deux fois la même** d'affilée.

| Scène | Capture | Ce que ça montre |
|---|---|---|
| **Montagnes, lever du jour** | `06-scene-montagnes.png` | six plans de crêtes qui défilent à des vitesses différentes dans la brume, des nuages, un ciel qui passe peu à peu de l'aube au crépuscule |
| **Fonds marins** | `07-scene-fonds-marins.png` | rayons de lumière qui bougent, deux bancs de poissons, algues qui ondulent, fines particules qui montent |
| **Ville de nuit, vue aérienne** | `08-scene-ville.png` | quartiers éclairés (certains presque noirs : des parcs), avenues plus vives, phares blancs et rouges, le tout survolé de biais, sur un quadrillage qui boucle sans couture |
| **Aurore boréale** | `09-scene-aurore.png` | trois rideaux (vert, bleu, violet) qui ondulent au-dessus d'une forêt de sapins, sous des étoiles qui scintillent |
| **Voyage parmi les étoiles** | `10-scene-etoiles.png` | des étoiles qui s'approchent lentement, devant une nébuleuse colorée |

Rien n'est téléchargé et rien ne pèse sur la carte du Pi. Tout s'arrête dès qu'on touche : minuteurs, dessin, décodage de la vidéo.

### Tes vidéos

Le dock ne contient aucune vidéo. Celles qu'on dépose dans `/var/lib/mydock/screensaver` passent en plein écran, **sans redémarrage** (l'interface relit la liste à chaque départ de l'économiseur et à chaque ouverture des Réglages) :

```bash
sudo mydock-dock screensaver add https://exemple.org/vue-aerienne.mp4     # télécharge (https seulement)
sudo mydock-dock screensaver add ~/Vidéos/fonds-marins.webm                # copie un fichier
sudo mydock-dock screensaver list
sudo mydock-dock screensaver remove fonds-marins.webm
```

- **MP4 (H.264) ou WebM**, les formats que Chromium lit sans réglage ; la légende est le nom du fichier.
- Une vidéo joue **jusqu'au bout** (150 s au plus), puis la suivante ; avec « Tout », une fois sur deux (quand il y en a).
- **Une vidéo que le navigateur ne sait pas lire est écartée** pour cette fois et une scène prend le relais : jamais d'écran noir, jamais d'économiseur bloqué sur une erreur (un test le montre avec un fichier cassé).
- Le diagnostic signale une vidéo de plus de 300 Mo ; la commande `ffmpeg` du README la ramène à 1024×600 H.264 sans le son, ce qui convient au Pi 4 (à vérifier chez toi).

## Sécurité

- **Aucune nouvelle ouverture réseau.** Le serveur du dock reste sur `127.0.0.1` ; le navigateur du kiosque ne contacte toujours jamais Internet (`default-src 'self'` : les vidéos viennent du Pi).
- **Seules les vidéos listées sont servies** : extensions `.mp4`, `.m4v` et `.webm` ; ni sous-dossier, ni lien symbolique, ni fichier vide ; un nom sans chemin ni caractère de contrôle ; **le serveur ne construit jamais un chemin à partir de l'adresse demandée** : il cherche le nom dans ce qu'il vient lui-même de lister (`../`, noms encodés, noms hors liste : 404). Lecture partielle (`Range`) pour la lecture et le défilement. 55 tests, dont les cas hostiles.
- **`screensaver add`** n'accepte que le **https** (une redirection vers du http est refusée), vérifie que le serveur annonce une vidéo, plafonne à 2 Go, laisse 300 Mo libres sur la carte, et écrit par un fichier provisoire (jamais une vidéo à moitié écrite).
- **Spotify** : trois actions de plus à paramètres stricts (identifiant d'appareil de 8 à 128 caractères parmi `A-Za-z0-9_.:-` **que l'agent a listé**, volume entier de 0 à 100, `play` booléen, champs inconnus refusés), testées avec des paramètres hostiles. Le jeton reste sur le PC, comme avant.

## Vérifié

| | |
|---|---|
| **Tests Python** (`make test`) | **2 062 tests** verts (2 ignorés : ils supposent un utilisateur qui n'est pas root), soit **111 de plus** qu'à l'étape i : côté agent, les appareils Spotify Connect (mise en forme, liste, transfert, volume, appareil disparu ou restreint, compte gratuit), le choix de l'appareil pour jouer et la démonstration, plus des paramètres hostiles pour les trois nouvelles actions ; côté dock, les réglages de l'économiseur et leur reprise au démarrage, le dossier de vidéos, le service des fichiers (lecture partielle, noms hostiles, liens symboliques), l'ajout par https ou par fichier, la ligne de commande et le diagnostic (`test_screensaver.py`, 55 tests). |
| **Avec les paquets exacts d'Ubuntu 26.04** (`make test-ubuntu`) | **1 997 tests** verts (1 ignoré, pour la même raison), soit là aussi 111 de plus ; les avertissements sont ceux, connus, de `websockets` sous Python 3.14. |
| **Tests unitaires de l'interface** (Vitest) | **327 tests** (19 fichiers), dont les nouveaux fichiers `saver.test.ts` (délais, choix de ce qui passe, légendes) et `scenes.test.ts` (chaque scène dessine, remplit l'image dès la première, hasard à graine, crêtes qui bouclent), les cas de `screen.test.ts` (la machine à trois états : l'économiseur, la veille qui passe avant, le toucher qui réveille sans rien atteindre dessous) et ceux de `spotify.test.ts` (appareils, libellés, sortie active). |
| **Tests de bout en bout** (Playwright, Chromium 1024×600 tactile → vrai dock → vrai agent de démonstration, plus un dock simulé) | **259 tests**, dont **21 nouveaux** : 16 sur la vraie pile de démonstration (`stage-j.spec.ts` : l'onglet « Sortie sonore », le transfert sur une enceinte et le volume de l'enceinte, les sorties du PC, puis l'économiseur — réglages, départ, toucher, succession, cinq scènes, vidéo, vidéo cassée) et 5 sur un dock simulé (`states.spec.ts` : enceinte active ailleurs, compte gratuit, appareil restreint et liste vide ou en échec, **agent d'avant la 1.4.0**, appareil disparu en cours de route). **258 verts à la première passe** ; le 259ᵉ, un test de l'étape d (« Applis : toucher une application la lance… »), mesurait le rectangle d'une tuile alors qu'elle était encore « enfoncée » (`scale(0,97)`) : une course dans le test, pas dans l'application. Il mesure maintenant la hauteur de mise en page et a passé 8 fois de suite. |
| **Coût des scènes** (mesuré ici, pas sur un Pi) | Dans le Chromium de développement (un PC, sans GPU), la tâche principale du navigateur occupe, à 24 images par seconde, **de 4 % (les étoiles) à 10 % (la ville) d'un cœur**, soit de 1,6 à 4,2 ms par image ; le JavaScript seul en représente de 0,4 à 2,3 %. La ville est la plus lourde (quelque 1 500 petites lumières et 220 phares dessinés de biais). Un Pi 4 est nettement plus lent : à constater chez toi, voir l'avertissement du début. |
| **Reste** | `ruff` (vérification et format), `svelte-check` (0 erreur, 0 avertissement), `shellcheck`, `./scripts/check-web-dist.sh` (l'interface compilée, commitée, correspond aux sources). |

Ce que les tests de bout en bout de l'économiseur vérifient vraiment : les réglages gardés par le dock ; le départ **après 1 minute** (horloge du navigateur avancée) et pas avant ; **un toucher posé exactement sur un bouton le ferme sans rien changer dessous** ; chaque toucher repousse le départ ; réglé sur « Jamais » il ne vient jamais ; l'écran noir vient ensuite à son propre délai et un toucher le réveille ; les scènes se succèdent sans se répéter ; **chacune des cinq scènes dessine une image non noire qui change d'un instant à l'autre** (lecture des pixels du canevas) ; **une vraie vidéo WebM** déposée dans le dossier de l'économiseur est lue en plein écran, légendée, et un toucher la ferme ; **un fichier cassé** est écarté et une scène la remplace.

## Changements d'organisation (pour qui lit le code)

- `agent/mydock_agent/spotify/` : `catalog.py` (mise en forme d'un appareil, identifiants valides), `service.py` (`devices`, `transfer`, `set_volume`, choix de l'appareil pour jouer, messages d'erreur), `actions.py` (trois actions de plus). Démo : `demo/spotify.py` (appareils fictifs).
- `dock/server/mydock_dock/screensaver.py` (nouveau : liste, service des fichiers, ajout, retrait), `store.py` (réglages `saver_after` et `saver_content`), `app.py` (`/api/screensaver`, `/screensaver/<nom>`), `__main__.py` (`mydock-dock screensaver list|add|remove`), `doctor.py` (section « Économiseur d'écran »), `config.py` (`saver_dir`).
- Interface : `lib/saver.ts` et `lib/scenes.ts` (fonctions pures, testées), `lib/saver.svelte.ts` (vidéos et choix), `lib/screen.svelte.ts` (la machine à états, étendue), `components/SaverStage.svelte` et `ScreenSleep.svelte`, carte de `pages/Settings.svelte` ; `components/SpotifyDevices.svelte`, `OutputRow.svelte`, `DeviceVolume.svelte`, `SpotifyPlayer.svelte` et `pages/Music.svelte` (quatrième onglet).
- Tests de bout en bout : `e2e/stage-j.spec.ts` (sur la vraie pile de démonstration), `e2e/states.spec.ts` (dock simulé), `e2e/assets/clip.webm` (3,5 Ko : une vidéo VP8 de 160×90 pour le test de lecture) ; la pile de test (`scripts/dev-stack.py --state-dir`, `e2e/fixtures.ts`) donne à chaque test son propre dossier d'état.

## Décisions

- **Des scènes dessinées, pas des vidéos livrées.** Les vidéos d'Apple sont à Apple ; ailleurs, des vidéos libres de droits pèseraient des dizaines de Mo chacune dans un paquet installé par `apt` sur une carte SD. Un programme qui dessine tient en quelques Ko, se met à jour avec le paquet et ne dépend d'aucun réseau. Le dossier de vidéos laisse la porte ouverte à de vraies prises de vue, au choix de chacun.
- **L'économiseur est actif par défaut, après 5 minutes** : c'est ce que tu demandais (« si aucune action n'est faite sur l'écran… »). Réglages permet de le retarder ou de le couper en un toucher. La veille de l'écran, elle, reste sur « Jamais » tant que tu ne l'as pas choisie.
- **L'économiseur vient avant l'écran noir**, jamais l'inverse : un dock qui s'éteint avant d'avoir montré son économiseur n'en a pas.
- **Le premier toucher ne fait que fermer**, comme pour la veille (même voile invisible), parce qu'un écran de 7" posé à côté d'un PC se touche souvent sans y penser : réveiller ne doit jamais éteindre le PC ni changer de musique.
- **La liste des appareils vient de Spotify, pas du réseau.** Le dock ne cherche aucune enceinte lui-même (pas de mDNS, pas de Bluetooth) : tout passe par l'API de Spotify depuis le PC, comme le reste de la page. Une enceinte invisible dans l'application officielle l'est aussi ici.
- **Un seul volume affiché à la fois** : celui de l'appareil sur lequel la musique sort (le curseur du PC cède la place à celui de l'enceinte, et inversement), pour ne jamais régler le mauvais.
- **Les scènes bouclent sans couture** (crêtes périodiques, quadrillage de la ville qui se raccorde à lui-même) : l'économiseur peut tourner des heures sans saut visible.

## Non testé

- **Tout ce qui parle à Spotify pour de vrai** (voir l'avertissement du début) : la liste des appareils, le transfert, le volume d'une enceinte, l'effet d'un compte gratuit.
- **L'état MPRIS pendant une lecture sur une autre enceinte** : à voir chez toi avec ta version de Spotify.
- **Le coût réel sur le Pi 4** : fluidité des scènes à 24 images par seconde, lecture d'une vidéo H.264 (décodage matériel ou non selon le Chromium du Pi).
- **L'extinction réelle de l'écran après l'économiseur** : inchangée (`swayidle` lit le délai de la veille, pas celui de l'économiseur) mais je n'ai pas de Pi pour vérifier l'enchaînement des deux.
- **Ton doigt sur ton écran** : le toucher qui ferme l'économiseur est essayé avec de vrais événements tactiles dans un Chromium 1024×600, pas sur ton matériel.

## Addendum — 1.5.0 : version, adresse des vidéos saisie à l'écran, Bourse

Trois retours après l'installation de la 1.4.0, traités dans la 1.5.0.

1. **« Tu n'as pas incrémenté le numéro de version du deb. »** Exact : les ajouts faits après la 1.4.0 portaient encore ce numéro, et `apt install` répond alors « déjà la version la plus récente ». Les paquets passent en **1.5.0** (agent, dock, common, les deux `.deb`), avec leurs changelogs. **À chaque livraison désormais, un nouveau numéro** (c'est écrit dans le README, section « Mises à jour »).
2. **« Pas de vidéo, et aucune possibilité de saisir une adresse. »** La 1.4.0 n'avait ni téléchargement automatique, ni champ d'adresse ; le téléchargement des vues d'Apple ajouté ensuite n'avait pas de nouveau numéro et ne laissait rien choisir. Maintenant : **Réglages → Économiseur d'écran → « Vidéos et adresses de téléchargement… »** (captures `12` à `14`) :
   - les vues aériennes d'Apple (dix films, retirables et remettables) et **tes adresses**, saisies au **clavier tactile** (nouveau mode « adresse » : `/ ? = & % ~ : . - _`, majuscules, 500 caractères, déjà rempli de `https://`) ;
   - une adresse peut être celle **d'une vidéo** (MP4, WebM, MOV, AVI…) **ou d'un fichier texte** (`.txt`, `.m3u`, `.list`) qui en liste une par ligne ;
   - le Pi télécharge, **examine chaque fichier avec `ffprobe`** et **ne convertit que ce qui en a besoin** (un MP4 H.264 ou un WebM déjà léger est gardé tel quel) ; chaque ligne dit « 3 sur 10 prêtes » ou **pourquoi ça n'avance pas** ; un film en échec n'arrête pas les suivants ; retirer une adresse (avec confirmation) efface ses films ; un bouton suspend ou reprend ;
   - tout est testé jusqu'au bout : réseau simulé, **un vrai serveur https local** (certificat auto-signé : redirection vers du http refusée, 404, page web au lieu d'une vidéo, liste, film MP4/WebM/AVI/trop large) et de vraies conversions `ffmpeg` (54 tests dans `test_aerial.py`), plus 5 tests de bout en bout sur l'écran réel (adresse saisie touche par touche, gardée après rechargement, retrait avec confirmation, adresse refusée, retrait et retour des vues d'Apple).
3. **« La page Bourse ne fonctionne pas. »** **Je n'ai pas pu reproduire ni identifier la cause** : le code de la Bourse n'a pas changé depuis la 1.3.0, ses 31 tests et ses tests de bout en bout passent, et mon environnement n'a aucun accès à Internet pour essayer Yahoo Finance et Binance. Ce que j'ai fait à la place, pour trouver et réduire les causes qu'on rencontre vraiment sur un Pi :
   - **un chemin de connexion qui ne se bloque plus** (`net.py`) : sur un réseau dont l'IPv6 est annoncé mais muet, la première adresse faisait attendre tout le délai, puis la suivante, et les cotations (comme les téléchargements) finissaient « délai dépassé » alors que l'IPv4 marchait. Maintenant IPv4 et IPv6 en alternance en commençant par l'IPv4, 3 s par adresse, quatre essais au plus (9 tests) ;
   - **un bouton « Tester la connexion »** sur la page Bourse quand les cotations n'arrivent pas (capture `15`) : heure du Pi, résolution du nom, IPv4 et IPv6 serveur par serveur avec leur temps, réponse de chaque fournisseur, et **une phrase qui dit quoi faire** (11 tests, dont un de bout en bout avec le vrai dock sans réseau). **Si la page reste en panne chez toi, ce bouton dit pourquoi : envoie-moi ce qu'il affiche.**
   - Aussi : `mydock-dock doctor` dit si le téléchargement des vidéos est actif, combien d'adresses sont saisies et si `ffmpeg` est là.

**Vérifié** : voir les chiffres de la livraison dans la pull request. **Non vérifié** : Yahoo Finance et Binance depuis ton Pi, les adresses d'Apple, un téléchargement réel de bout en bout sur ton Pi.
