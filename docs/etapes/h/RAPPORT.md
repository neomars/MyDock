# Étape h — Un accueil de gros boutons, le balayage pour revenir, le texte doublé

Livrée le 4 novembre 2026, branche `claude/jolly-dirac-5np79y`, **versions 1.2.0** (agent, dock, protocole inchangé, et les deux paquets `.deb`).
Les captures de ce dossier sont en **mode démo** (fausses données) en 1024×600 tactile, **texte doublé** ; les états que la démo ne produit pas (jeton refusé) viennent d'un **dock simulé**. Le dock neuf (`03`) et le PC éteint (`02`, `14`) sont de vraies pannes de la pile de démonstration.

> **À lire d'abord — ce que je n'ai pas pu vérifier.** Je n'ai pas ici ton vrai écran tactile : le balayage est essayé avec de vrais événements tactiles (protocole DevTools de Chromium), pas avec ton doigt sur ton écran USB. Les distances du geste (140 px, 90 px depuis le bord) sont réglées pour 1024×600 ; si un balayage franc ne marche pas, ou si un défilement le déclenche, elles tiennent en cinq nombres, dans `dock/web/src/lib/swipe.ts`.

## Ce que tu demandais, et ce que j'ai fait

> « Je veux un affichage sous forme de gros bouton, comme sur l'image (les boutons physiques sont remplacés par des boutons virtuels). Pour sortir d'un sous-menu et revenir au menu principal (gros bouton), je veux qu'on puisse le faire par un balayage de gauche à droite de l'écran (si l'écran tactile le permet…). »
>
> « Double la police de caractère. C'est un écran petit. »

1. **L'écran d'accueil est un menu de gros boutons**, comme les touches d'un Stream Deck : un bouton coloré par page, dans l'ordre choisi dans Réglages. La barre d'onglets du bas **disparaît** : tout l'écran sert.
2. **Chaque bouton ouvre un sous-menu** (la page). On en **revient d'un balayage de gauche à droite**, n'importe où sur l'écran ; un bouton **⌂** en haut à gauche fait la même chose pour qui préfère toucher.
3. **Le texte est doublé** : courant 40 px au lieu de 20, légendes 32 px au lieu de 16. Toutes les pages, le clavier, les confirmations et les fenêtres de choix ont été **refaits pour ce texte** : rien ne déborde, rien n'est coupé.

## Ce qui est livré

### L'accueil

| | |
|---|---|
| **Les boutons** | Un par page visible : Musique, Son, Applis, Steam, Office, Bourse, Système, Alimentation, Réglages, dans l'ordre de **Réglages → Pages du dock** (une page cachée n'a pas de bouton ; les Réglages restent toujours là). Chaque page a sa couleur (neuf teintes distinctes, texte blanc d'un contraste mesuré : testé), son icône et son nom en gros. |
| **La grille** | Elle n'est pas écrite en dur : `homeLayout()` (`lib/home.ts`) cherche, parmi les arrangements possibles, celui qui donne le **plus de surface à chaque bouton** sans descendre sous 300 × 110 px. Neuf pages : **3 × 3** (321 × 150 px, 115 px de haut quand un bandeau est affiché). Quatre : 2 × 2. Un seul : un grand bouton centré. Testé sur toutes les tailles. |
| **Le bandeau** | Au-dessus des boutons, seulement quand il y a quelque chose à savoir ou à faire : **PC hors ligne** + *Allumer le PC* ; **PC allumé, agent arrêté** ; **Connexion refusée** + *Associer à nouveau* ; **Aucun PC associé** + *Réglages* ; ou le **code d'association** avec l'adresse à ouvrir sur le PC et le temps qu'il lui reste (avec une croix pour y renoncer quand un PC est déjà associé). Un PC qui va bien n'a **que ses boutons**. |
| **Les pages du PC, PC éteint** | Leurs boutons sont **grisés** (mais restent touchables : la page explique pourquoi, comme avant). La Bourse, les Réglages et l'Alimentation, qui marchent sans le PC, restent francs. |
| **Le bouton Musique** | Montre le **morceau en cours**, sur sa pochette. |
| **Démarrage** | L'écran repart **toujours de l'accueil** : un dock qui redémarre n'ouvre pas une page au hasard. Seule exception : le rechargement automatique qui répare l'écran d'une mise à jour (étape précédente) **rouvre la page que tu venais de toucher** — avant, c'était le Pi qui se souvenait de la dernière page ; l'accueil rend ce souvenir inutile. |

### Le balayage de gauche à droite

Il se décide au **relâchement** : le geste doit être **assez long** (140 px ; 90 px s'il part du bord gauche de l'écran, comme le « retour » d'un téléphone), **assez horizontal** (deux fois plus de course de côté qu'en hauteur : un défilement oblique n'en est pas un) et **assez vif** (moins de 1,1 s : un doigt qui traîne n'est pas un geste). Il part de **n'importe où** sur la page.

| | |
|---|---|
| **Ce qui garde son geste** | Les **curseurs de volume** et la **barre de progression** de la musique (ils ont déjà besoin de glisser de gauche à droite), les champs de saisie, et tout élément déclaré `touch-action: none` ou `data-noswipe` : un balayage qui commence dessus est le leur, jamais celui de la navigation. Testé en vrai sur le curseur de la sortie. |
| **Le défilement vertical** | Reste au navigateur (`touch-action: pan-y` sur la page) : un défilement ne déclenche jamais le retour, même un peu de travers. |
| **Le toucher qui suit** | Après un balayage, un éventuel « clic » est avalé pendant 350 ms : le doigt qui se relève ne tape pas sur le bouton qui apparaît dessous. **Et inversement** : Chromium prend le premier appui qui suit un balayage *rapide* pour l'ordre d'arrêter une inertie, et n'en produit jamais le clic (les événements « pointeur » arrivent, pas le clic ; constaté sur une page de test minimale comme sur l'interface, et seulement pour un geste d'environ 4 000 px/s). Sans parade, le bouton qu'on touche juste après être revenu à l'accueil resterait muet. Pendant 0,9 s après un balayage de retour, l'interface **rattrape donc cet appui** : s'il n'a pas produit de clic au bout de 80 ms, elle le produit (un seul appui, un bouton, un doigt qui n'a pas bougé de plus de 12 px). Sans effet si le navigateur du Pi n'a pas ce défaut. |
| **Sur l'accueil** | Il ne fait rien. |
| **Mouvement réduit** | Si le système demande moins de mouvement, plus aucun glissement entre les écrans. Sinon : 170–190 ms, rien de coûteux (pas de flou ni d'ombre étendue : 60 images/s sur un Pi 4). |

### Le texte doublé

**Un seul nombre** commande tout : `--ts: 2` dans `dock/web/src/app.css`. Les tailles de texte (`--fs-cap` 32 px, `--fs-small` 36 px, `--fs` 40 px, `--fs-large` 48 px, titres 51 px), les cibles tactiles (`--tap` 72 px) et la barre du haut (88 px) en découlent. **Si c'est trop gros ou pas assez, change ce nombre** (1,5 par exemple) et recompile : tout suit.

Ce qu'il a fallu **refaire** pour que tout tienne dans 1024 × 600 :

- **Les pages** : une seule colonne là où il y en avait deux (Système, Alimentation, Son, Réglages, Bourse), grille de quatre jaquettes pour Steam et de trois pour Applis, trois modules en ligne pour Office, curseurs et barres plus épais (poignée de 44 à 52 px, zone de toucher de 72 à 80 px). Quand une page est plus haute que l'écran, **elle défile dans son cadre** ; l'écran, lui, ne défile jamais (testé sur chaque page : aucun élément coupé à droite, aucun défilement de côté).
- **La barre du haut** : le nom du PC, l'état, le mini-lecteur (qui abandonne la ligne d'artiste), l'horloge, et le bouton ⌂ quand on est dans une page.
- **Le clavier tactile** : titre, *Annuler* et *Valider* sur une ligne, **le champ sur toute la largeur** (le jeton de 43 caractères y tient presque entièrement, et le champ montre toujours la fin de ce qu'on tape), cinq rangées de touches de 58 px au texte de 34 px.
- **La confirmation d'une action sensible** : plus large, **ses deux boutons toujours atteignables** (elle défile si le libellé est très long). Mesurée dans le test : elle tient dans l'écran.
- **Les fenêtres de choix** (Applications du lanceur, Valeurs de la Bourse) passent de deux colonnes à **deux volets** : *Applications* / *Lanceur (3/24)* et *Ajouter* / *Liste (2/20)*. Les deux colonnes ne tiennent pas côte à côte à cette taille ; le compte reste visible sur l'onglet. **Pages et ordre** : une colonne qui défile.
- **L'écran « PC hors ligne »** : l'heure n'y est plus doublée (la barre du haut l'affiche), pour que le bouton *Allumer le PC* tienne sans défiler.

## Vérifié

| | |
|---|---|
| **Tests unitaires de l'interface** (Vitest) | **273 tests** (16 fichiers), tous verts : la grille d'accueil (14), le geste de balayage et le rattrapage de l'appui (16), les pages et leurs couleurs (17), le rechargement de réparation (22)… |
| **Tests de bout en bout** (Playwright, vrai Chromium 1024×600 tactile → vrai dock → vrai agent de démonstration, plus un dock simulé) | **214 tests**, tous verts, dont **20 nouveaux** (`stage-h.spec.ts`). Les 194 autres ont été migrés : ils passent par l'accueil comme une personne le fait (`openPage`, `goHome`). |
| **Balayage** | De **vrais événements tactiles** (protocole DevTools : `touchStart`, `touchMove`, `touchEnd`) : depuis chacune des neuf pages vers l'accueil ; depuis le milieu d'une page ; refusé quand il est trop court, de droite à gauche ou vertical ; refusé sur un curseur de volume ; sans effet sur l'accueil. |
| **Texte doublé** | Corps 40 px, libellés des boutons 36 px, barre du haut 88 px (mesurés) ; chaque page, le clavier, la confirmation et les fenêtres tiennent dans l'écran, boutons de sortie compris. |
| **Reste** | `svelte-check` (0 erreur), `ruff`, `shellcheck` ; **1 691 tests Python** (`make test` : agent, dock, déploiement, deux paquets `.deb` construits et installés pour de vrai) et **1 626** avec les versions exactes des paquets d'Ubuntu 26.04 (`make test-ubuntu`) ; `./scripts/check-web-dist.sh` (l'interface compilée, commitée, correspond aux sources). |

## Changements d'organisation (pour qui lit le code)

- `src/components/` : **`HomeScreen`**, **`HomeTile`**, **`HomeBanner`** (nouveaux, ils remplacent `TabBar` et `PairingBar`) ; `App.svelte` ne garde que « accueil ou page » ; `lib/swipe.ts` (reconnaissance du geste + action Svelte) ; `lib/home.ts` (taille de la grille).
- `lib/pages.ts` : une `tone` (deux teintes) par page ; `shownPage` disparaît (le dock ne choisit plus la page de départ ; le champ `last_page` reste dans l'état d'interface par compatibilité, sans effet).
- `lib/build.ts` : `reloadNow`, `takeReopen`, et `reloadIfAllowed({ reopen })` pour rouvrir la page touchée après le rechargement de réparation.
- Tests de bout en bout : `openPage`, `goHome`, `tileOrder` et `swipe` dans `e2e/fixtures.ts` ; `stage-h.spec.ts` ; les anciens tests passent par l'accueil comme une personne le fait.

## Un correctif au passage (agent)

La relecture de la suite complète a fait échouer, une fois sur trois, un test qui parle à un **vrai serveur PipeWire** : après « **sortie par défaut** » demandée depuis le dock, l'écran gardait l'ancienne sortie. Cause : PipeWire annonce parfois le changement **avant** de le refléter ; le relevé qui suit l'annonce voit encore l'ancienne sortie, et aucun autre événement n'arrive. L'agent **refait donc un relevé 0,3 s après** un changement de sortie ou de micro par défaut (`collectors/audio.py`). Un test unitaire reproduit la course (il échoue sans le correctif) ; le fichier de tests PipeWire a ensuite passé huit fois de suite sans échec.

## Décisions

- **Un balayage « de n'importe où »** plutôt que depuis le bord seulement : c'est ce que tu décrivais (« de gauche à droite de l'écran »). Le bord est plus facile (90 px suffisent) mais n'est pas obligatoire.
- **Deux volets plutôt que deux colonnes** pour les fenêtres de choix : à 40 px de texte, une colonne de 480 px ne laisse pas de place au nom d'une application à côté de trois boutons.
- **Texte à ×2 en dur** (un nombre, pas un réglage à l'écran) : c'est ce que tu demandais ; un réglage de plus aurait ajouté une carte aux Réglages pour un besoin que tu as déjà tranché.

## Non testé

- Le balayage **avec ton doigt sur ton écran USB** : voir l'avertissement du début.
- La lisibilité **à bout de bras** du texte doublé : c'est ton œil qui juge. Un seul nombre à changer (`--ts`) si besoin.
