# Étape i — Spotify à la place de « Musique », et les applications Snap se lancent

Livrée le 8 novembre 2026, branche `claude/jolly-dirac-5np79y`, **versions 1.3.0** (agent, dock, protocole inchangé, et les deux paquets `.deb`).
Les captures de ce dossier sont en **mode démo** (catalogue fictif : aucun réseau, aucun compte) en 1024×600 tactile, texte doublé ; les états que la démo ne produit pas (pas de compte relié, compte refusé, Spotify fermé…) viennent d'un **dock simulé**.

> **À lire d'abord — ce que je n'ai pas pu vérifier.**
>
> 1. **Le vrai Spotify.** Je n'ai ici ni ton compte, ni ton PC, ni accès au site des développeurs de Spotify (il n'est pas joignable depuis mon environnement) : tout ce qui parle à `api.spotify.com` est écrit d'après la documentation que je connaissais et essayé contre une **API simulée** (235 tests sur le paquet `spotify`, sa connexion et sa démonstration), jamais contre le vrai service. Spotify a **retiré et renommé des champs** de son API en février 2026 pour les applications en « mode développement » ; le code lit chaque champ avec prudence, accepte les deux noms connus (`tracks` / `items` d'une playlist), a un repli quand « titres populaires d'un artiste » n'existe plus, et **dit pourquoi** quand Spotify refuse, mais **si une rubrique ne s'affiche pas chez toi, c'est probablement là** : le message à l'écran dit ce que Spotify a répondu.
> 2. **Le lancement d'un Snap.** La cause de « Spotify ne se lance pas depuis Applis » est établie (voir plus bas) et la correction est testée avec un faux `systemd-run`, mais **pas sur un vrai bureau Ubuntu** : un conteneur n'a pas de gestionnaire systemd utilisateur. `mydock-agent doctor` (section « Lancement des programmes ») le vérifie chez toi en une commande.
> 3. **Aléatoire et répétition** dépendent de ce que le lecteur annonce par MPRIS, et je ne sais pas ce que ta version de Spotify en annonce : quand il ne les annonce pas, **les boutons ne sont pas proposés** (plutôt qu'un bouton qui ne fait rien).
> 4. **Ce qui n'y est pas** : le cœur « J'aime » du morceau en cours, la file d'attente, les paroles et la page d'accueil de recommandations de Spotify. Les recommandations ont été retirées de l'API pour les nouvelles applications ; pour le cœur et la file d'attente, je n'ai pas pu vérifier ce que l'API de février 2026 permet encore, et je préfère ne pas promettre ce que je ne peux pas faire marcher.

## Ce que tu demandais, et ce que j'ai fait

> « Je peux ajouter des applications depuis Applis. Mais quand je clique sur l'application Spotify, l'application ne se lance pas sur mon PC. Je pense que Spotify n'est pas un .deb. Je veux disposer d'une interface complète de Spotify quand je clique sur l'icône Musique. Remplace Musique par Spotify. Une interface complète signifie : recherche, précédent, suivant, play pause, niveau sonore, jaquette… Bref, le plus proche possible de l'interface officielle. »

1. **Tu avais raison pour le `.deb`** : Spotify est probablement installé en **Snap** (c'est la voie d'Ubuntu), et c'est bien ce qui l'empêchait de démarrer. Corrigé pour **toutes** les applications du lanceur, pas seulement Spotify.
2. **Le bouton « Musique » est devenu « Spotify »** (icône et couleur de Spotify) et sa page est une vraie interface Spotify : lecteur complet, recherche, bibliothèque, pages d'album, d'artiste et de playlist.

## Pourquoi l'application ne se lançait pas

L'agent tourne comme un service systemd de ton utilisateur, avec le durcissement `NoNewPrivileges=yes`. Une application lancée **par** l'agent en est l'enfant, et **hérite** de cette interdiction pour toujours : elle ne peut plus jamais obtenir de privilèges. Or un Snap démarre par `snap-confine`, un programme qui n'a le droit de faire son travail que parce qu'il est « setuid root » : sans privilèges il échoue. Le bac à sable d'Electron et de Chromium, et `bwrap` de Flatpak, y perdent aussi des moyens. Même `systemd-run --scope` ne changeait rien : le *client* `systemd-run` est lui aussi un enfant de l'agent.

**Correction** (`agent/mydock_agent/launcher.py`) : l'agent demande au **gestionnaire systemd de l'utilisateur** de lancer le programme, comme un service à lui (`systemd-run --user`) : ses enfants ne descendent plus de l'agent. Ce service range à son tour l'application dans son propre « scope » (comme le fait GNOME) : le service s'arrête quand `gio launch` a rendu la main, l'application, elle, continue. Le durcissement de l'agent **reste en place** (`NoNewPrivileges=yes`). Sans `systemd-run`, ou si le gestionnaire est injoignable, l'agent lance directement le programme, comme avant.

`mydock-agent doctor` vérifie désormais, dans « Lancement des programmes », ce que voit un programme lancé ainsi (`NoNewPrivs: 0`), et dans « Spotify » comment l'application est installée (paquet, Snap ou Flatpak).

## Ce qui est livré

### La page Spotify

À gauche, **trois onglets** comme le menu de l'application officielle ; à droite leur contenu ; la barre du haut garde son mini-lecteur.

| | |
|---|---|
| **En lecture** | Pochette, titre, artiste · album, **progression glissable**, précédent / lecture-pause / suivant, **lecture aléatoire** et **répétition** (rien → la liste → le morceau), **volume** de la sortie du PC avec son bouton muet. Les autres lecteurs (VLC, un onglet de navigateur) restent pilotables par des pastilles ; un navigateur n'a ni aléatoire ni répétition. Spotify fermé : un grand bouton **Lancer Spotify**. |
| **Rechercher** | Un champ au clavier tactile. Résultats rangés en **morceaux, artistes, albums, playlists**, chacun avec sa pochette (demandée au fil du défilement). Un toucher joue le morceau **dans son album**, ou ouvre l'album, l'artiste ou la playlist. Le morceau qui joue est surligné. |
| **Ta bibliothèque** | **Playlists, titres likés, albums, artistes suivis, écoutés récemment** (un morceau réécouté n'y figure qu'une fois). |
| **Une page d'album / d'artiste / de playlist** | Pochette, titre, bouton **Lecture**, liste des morceaux (numérotés dans un album). Un artiste : ses titres populaires **et** ses albums, qui s'ouvrent à leur tour (le bouton de retour défait les pages une à une). |
| **Le vert de Spotify** | Il ne colore que cette page (progression, volume, boutons, ligne en cours) : un seul réglage de `--accent` dans la page. |

### Connecter ton compte (une seule fois, sur le PC)

`mydock-agent spotify login --client-id TON_ID` : flux OAuth **avec PKCE**, donc **aucun « client secret »**, aucun secret à recopier ni à protéger. La commande explique chaque étape (créer l'application sur developer.spotify.com, déclarer `http://127.0.0.1:8888/callback`, copier le Client ID), ouvre le navigateur du PC, attend la réponse de Spotify sur une adresse **locale** seulement (vérifie un `state` aléatoire), puis range le jeton dans `~/.config/mydock/spotify.toml` (droits 600) et le renouvelle seule. `spotify status [--check]` et `spotify logout` complètent. **Sans compte**, la lecture, la pause, le suivant, l'aléatoire, la répétition et le volume marchent quand même (MPRIS) ; la page dit, à la place de la recherche et de la bibliothèque, quoi taper.

**Ce que Spotify impose** aux applications en « mode développement » (règles annoncées en février 2026, que je n'ai pas pu relire à la source ; elles évoluent, vérifie-les sur developer.spotify.com) : le **propriétaire de l'application doit avoir un compte Premium**, et seuls lui et les comptes ajoutés dans « User Management » (5 au plus) peuvent s'y connecter. Si Spotify refuse, la page affiche sa raison.

### Jouer

`spotify.play` lance Spotify s'il est fermé (et attend qu'il se manifeste), puis demande à **l'API** de jouer sur l'appareil « ce PC » (Spotify Connect, compte Premium) ; sinon (compte gratuit, appareil introuvable, API qui refuse) **le lecteur du PC ouvre lui-même** l'album, la playlist ou le morceau (`OpenUri` de MPRIS). Un morceau touché dans une liste est joué **dans son album ou sa playlist** : « suivant » enchaîne comme dans l'application.

## Sécurité

- **Le jeton reste sur le PC** : `~/.config/mydock/spotify.toml`, droits 600 (refusé s'il est plus ouvert, comme les autres secrets), jamais envoyé au Pi ni au navigateur, jamais journalisé, jamais affiché (le diagnostic ne lit que son contenu non secret et ne le modifie pas).
- **Droits demandés à Spotify, et rien de plus** : lire le profil, l'état de la lecture, la bibliothèque, les playlists, les artistes suivis, l'historique ; commander la lecture. Jamais de quoi modifier une playlist, la bibliothèque ou le profil, ni lire l'adresse e-mail (un test le vérifie).
- **Réseau** : l'agent ne parle qu'à `accounts.spotify.com` et `api.spotify.com`, en https, **sans suivre de redirection** ; les pochettes ne sont téléchargées que chez `*.scdn.co` et `*.spotifycdn.com`. Le dock ne reçoit que des **identifiants de pochette opaques** et ne donne jamais d'adresse. Un 429 de Spotify met tous les appels en pause le temps demandé.
- **Actions `spotify.*`** (liste blanche, aucune n'est « sensible ») : chaque paramètre est validé avant d'arriver au service — identifiant de 22 caractères alphanumériques, adresse `spotify:<type>:<identifiant>` exacte, contexte d'un morceau limité à un album ou une playlist, requête de recherche de 120 caractères sans caractère de contrôle, champs inconnus refusés. 27 cas de paramètres hostiles sont testés (adresses web, chemins, commandes, sauts de ligne…).
- **Le dock** attend jusqu'à 70 s la réponse de l'agent aux actions `spotify.*` (lancer l'application puis jouer prend parfois une quarantaine de secondes), 6 s pour toutes les autres.

## Vérifié

| | |
|---|---|
| **Tests Python** (`make test`) | **1 951 tests** verts (2 ignorés : ils supposent un utilisateur qui n'est pas root), dont **260 nouveaux** : le paquet `spotify` (235 : mise en forme des réponses, PKCE et fichier du jeton, commande `login` essayée avec un vrai serveur local, transport limité aux serveurs de Spotify, service sur une API simulée, actions et paramètres hostiles, commande `spotify`, démonstration), le lancement par le gestionnaire systemd, le diagnostic, le délai du dock. |
| **Un vrai bus D-Bus** | Trois nouveaux tests contre un **vrai `dbus-daemon`** et un faux lecteur qui parle comme Spotify : écrire `Shuffle` et `LoopStatus` (variantes D-Bus) et les relire, un lecteur qui n'a ni l'un ni l'autre, et `OpenUri` arrivant au lecteur avec la bonne adresse depuis `SpotifyService`. |
| **Avec les paquets exacts d'Ubuntu 26.04** (`make test-ubuntu`) | **1 886 tests** verts (1 ignoré, pour la même raison). |
| **Tests unitaires de l'interface** (Vitest) | **287 tests** (17 fichiers), dont 14 nouveaux (`spotify.test.ts` : répétition, textes, morceau en cours, compte, rubriques, contexte de lecture) ; les couleurs des boutons (contraste du libellé et de l'icône) passent avec le vert de Spotify. |
| **Tests de bout en bout** (Playwright, Chromium 1024×600 tactile → vrai dock → vrai agent de démonstration, plus un dock simulé) | **238 tests**, tous verts, dont **24 nouveaux** : 14 sur la vraie pile de démonstration (`stage-i.spec.ts` : l'accueil, le lecteur, l'aléatoire et la répétition, la recherche au clavier tactile avec ses pochettes demandées au fil du défilement, un morceau joué, un album, un artiste et le retour page par page, la bibliothèque et ses cinq rubriques, une playlist, le balayage depuis chaque onglet, le texte doublé sans rien couper) et 10 sur un dock simulé (`states.spec.ts` : pas de compte relié, compte refusé, agent plus ancien que le dock, Spotify fermé ou non installé, recherche qui échoue puis réussit, lecture refusée, pochettes demandées par identifiant seulement, page qui ne se charge pas). Les 214 autres ont passé tels quels, sauf le nom du bouton (« Spotify ») et le texte de l'écran « aucun lecteur ». |
| **Reste** | `ruff`, `svelte-check` (0 erreur, 0 avertissement), `shellcheck` ; `./scripts/check-web-dist.sh` (l'interface compilée, commitée, correspond aux sources). |

## Changements d'organisation (pour qui lit le code)

- `agent/mydock_agent/spotify/` (nouveau) : `auth.py` (PKCE, fichier du jeton, commande `login`), `http.py` (transport limité aux serveurs de Spotify), `catalog.py` (mise en forme des réponses, fonctions pures), `service.py` (le service : requêtes, renouvellement du jeton, lancement, lecture), `actions.py` (la liste blanche), `cli.py` (`mydock-agent spotify …`). Démo : `demo/spotify.py`.
- `collectors/music.py` : aléatoire (`Shuffle`), répétition (`LoopStatus`), `OpenUri` ; actions `music.shuffle` et `music.repeat`.
- `launcher.py` : lancement par le gestionnaire systemd de l'utilisateur.
- Interface : `pages/Music.svelte` (l'identifiant interne reste `music` : l'ordre et les pages cachées déjà choisis dans Réglages sont conservés), `components/Spotify*.svelte`, `lib/spotify.ts` (fonctions pures testées) et `lib/spotify.svelte.ts` (état).

## Décisions

- **L'identifiant interne de la page reste `music`.** Seuls le nom, le dessin et la couleur changent : l'ordre des pages, les pages cachées et les tests déjà écrits sont conservés, sans aucune migration de la configuration du Pi.
- **Trois onglets à gauche, en icônes seules, plutôt qu'une rangée en haut.** Ce qui manque sur cet écran, c'est la hauteur (482 px utiles sous la barre du haut), pas la largeur : un menu vertical rend toute la hauteur aux listes. Le nom de chaque onglet reste lu par les lecteurs d'écran (`aria-label`) et chaque section affiche son titre.
- **Le temps écoulé, la barre et la durée sur une seule ligne**, comme dans l'application officielle : c'est ce qui permet au lecteur, à ses commandes, au volume et aux pastilles de lecteurs de tenir sans défiler.
- **La connexion se fait sur le PC, pas sur le dock.** Taper des identifiants sur un écran de 7" serait pénible et ferait passer un secret par le Pi ; le flux OAuth avec PKCE n'en demande aucun et laisse le jeton là où l'agent s'en sert.
- **Pas de cœur « J'aime », ni de file d'attente.** Les droits d'écriture sur la bibliothèque auraient élargi ce que MyDock peut faire sur ton compte pour une fonction que je ne peux pas vérifier ici (l'API a changé en février 2026) : je n'ai demandé que ce qui sert. Si tu veux le cœur, c'est une petite extension (un droit de plus, une action, un bouton).
- **Jouer par l'API d'abord, par le lecteur du PC ensuite**, plutôt que l'un des deux seulement : l'API est la seule voie qui sache placer un morceau dans son album, mais elle demande Premium et un appareil Connect visible ; `OpenUri` marche sans.
- **Le vert de Spotify ne colore que sa page** (`--accent` redéfini dans la page) : le reste du dock garde son bleu.

## Non testé

- **Tout ce qui parle à Spotify pour de vrai** : voir l'avertissement du début. Si une rubrique reste vide ou si Spotify refuse, le message affiché dit ce que Spotify a répondu : c'est lui qui permettra de corriger vite.
- **Le lancement du Snap sur ton PC** (`mydock-agent doctor` le vérifie) et le comportement de `systemd-run --user` avec ta version de systemd.
- **La lecture aléatoire et la répétition de ton Spotify** : selon sa version, il annonce ou non ces deux réglages (MPRIS) ; sinon les boutons n'apparaissent pas.
- **Les accents et l'apostrophe** : le clavier tactile du dock (celui de toutes les pages) n'en a pas. La recherche de Spotify les tolère en général (« edith piaf » trouve « Édith Piaf »), mais je n'ai pas pu l'essayer sur le vrai service.
- **Ton doigt sur ton écran** : le texte, les cibles et les défilements sont mesurés dans un Chromium 1024×600 tactile, pas à bout de bras.
