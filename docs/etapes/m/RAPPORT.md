# Étape m — le son du Divertissement sort sur le PC

Livrée le 15 novembre 2026, branche `claude/jolly-dirac-5np79y`, **version 1.8.0** (agent, dock, protocole, et les deux paquets `.deb`).

> « J'ai besoin que le son de vidéos, TV et jeux sorte sur le PC. Le Raspi n'a pas d'enceintes. »

Les captures de ce dossier sont en **mode démo** (assistant de lecture simulé, agent de démonstration qui compte le son sans le jouer), en 1024×600 tactile, texte doublé. Le **vrai son**, lui, est essayé par les tests Python avec un vrai PipeWire sans carte son (voir plus bas).

## Ce que ça fait

Le son de VLC (Freetv, Kiosque) et de MAME (Retrogame) sort sur la **sortie par défaut du PC**. L'image reste sur l'écran du dock.

- **La télécommande et l'écran de jeu disent où sort le son** : « Son : sur le PC » ([01](01-telecommande-son-sur-le-pc.png)). Si le PC s'éteint pendant une vidéo, la vidéo continue et l'écran dit « PC hors ligne : pas de son » ([02](02-telecommande-pc-hors-ligne.png)) ; quand le PC revient, **le son repart tout seul**.
- **Réglages → Divertissement → Son** ([03](03-reglages-son.png)) : **Sur le PC** (par défaut) ou **Sur le Pi** (HDMI, prise jack, si tu y branches un jour des enceintes), et l'**avance du son des vidéos** (0 à 1 000 ms, de 50 en 50, 150 ms par défaut).
- **Le flux est une application du PC** : il apparaît dans la page **Son** du dock sous le nom « MyDock », avec **son propre volume** (relevé sur un vrai serveur de son, pas supposé).
- Quand ça ne marche pas, **l'écran dit pourquoi** et la commande qui répare : pas de serveur de son sur le Pi, `pacat` absent du PC, agent du PC trop ancien (il faut le mettre à jour), PC hors ligne.

## Comment ça marche

```
VLC / MAME ──► puits virtuel « mydock_pc » ──► parec ──► assistant de l'écran
   (Pi)            (PipeWire, sans matériel)    (capture)        │  /ws/player, en local
                                                                  ▼
PC : sortie par défaut ◄── pacat ◄── agent « /audio » ◄── dock (relais, jeton)
```

- **Un puits virtuel** (« null sink » de PipeWire) reçoit le son de VLC et de MAME. Pour les y faire jouer **sans toucher à leur configuration** : leur environnement porte `PULSE_SINK` et `PIPEWIRE_NODE`, et le puits est aussi la sortie par défaut le temps de la lecture (pour ce qui passe par ALSA) ; la sortie d'avant est rétablie à la fin.
- **L'assistant de l'écran** (dans la session graphique, où tournent déjà VLC et MAME) lit ce puits avec `parec`, en PCM brut (16 bits, 48 kHz, stéréo), et passe le son **au dock, en local** (127.0.0.1). **L'assistant ne connaît toujours aucun secret** : c'est le dock qui détient le jeton et ouvre la connexion vers le PC.
- **Le dock relaie** à l'agent par une seconde connexion (`ws://PC:8765/audio`, même jeton que `/ws`). Il ne garde jamais de retard : si le PC ne suit pas, les trames **les plus anciennes sont jetées** (un craquement) plutôt qu'accumulées (un son décalé de plus en plus) ; sans PC, rien n'est gardé.
- **L'agent joue** le flux avec `pacat`, sur la sortie par défaut du PC.
- **Les vidéos avancent leur son** (`--audio-desync` négatif) : le trajet (capture, réseau, lecteur du PC) le retarde d'environ un dixième de seconde, et l'image ne doit pas arriver avant lui.
- **Une vidéo en pause ne fait aucun son** : le dock envoie alors un petit message de maintien toutes les trois secondes, sinon l'agent fermerait le flux au bout de dix secondes et l'écran dirait à tort « PC hors ligne ».

## Sécurité

Le cahier des charges de départ reste tenu, point par point :

- **Jeton obligatoire** : `/audio` exige le même jeton que `/ws`, avec les mêmes échecs comptés et la même adresse bloquée au bout de trop d'essais. **Réseau local seulement** : même port, **même liste d'adresses autorisées**, refus muet hors liste. **Rien de plus n'est ouvert** (pas de nouveau port, pas de pare-feu à changer).
- **Jamais une commande venue du dock** : `pacat` est lancé avec une ligne de commande **figée**, dont rien ne vient du dock (vérifié par un test). Ce que le dock peut envoyer par `/audio` est du son, et rien d'autre.
- **Un seul format**, annoncé puis vérifié à l'octet près des deux côtés (PCM 16 bits, 48 kHz, stéréo) ; une trame qui n'est pas faite d'échantillons entiers, ou de plus de 64 Ko, met fin au flux ; **un flux à la fois** (un nouveau remplace l'ancien : un dock qui se reconnecte n'est pas laissé dehors) ; **un flux muet est fermé** au bout de dix secondes sans signe de vie.
- **Le jeton, les clés et les mots de passe** : rien de nouveau n'en manipule ; rien n'est écrit dans un journal ou à l'écran.

**La limite assumée, honnêtement.** Un dock compromis pourrait envoyer du son (fort) sur le PC. Il peut déjà faire plus (éteindre le PC, avec confirmation à l'écran) ; et le volume du PC reste le tien : le flux a son curseur dans la page Son, et se coupe dans ses réglages.

## Ce qui a été essayé, et comment

**Avec un vrai serveur de son** (un PipeWire jetable, avec WirePlumber et la couche PulseAudio, **sans carte son** : des puits virtuels) :

- **Le circuit de l'agent** : de vraies trames du dock, par l'agent et `pacat`, jusqu'à un puits du PC : une note de **440 Hz** est retrouvée à **440 Hz** et à la bonne puissance ; fermer le flux arrête `pacat` ; un PC dont le serveur de son est éteint le dit au dock, avec la raison.
- **Le circuit du Pi** : une note jouée dans le puits est capturée et passée au dock par échantillons entiers ; la sortie par défaut d'avant est rétablie ; le puits n'est créé qu'une fois.
- **VLC réel** (essayé sous un utilisateur ordinaire : VLC refuse de tourner en root) : avec l'environnement et l'option que la session lui donne (`PULSE_SINK`, `--audio-desync=-150`), VLC joue dans le puits et on retrouve sa note à 440 Hz. **L'option d'avance est donc bien acceptée par VLC**.
- **Un défaut trouvé par ces essais, et corrigé** : PipeWire fait du puits la sortie par défaut dès qu'on le crée ; la « sortie d'avant » était relevée après coup et jamais rétablie. Elle est maintenant relevée **avant** la création du puits.

**Avec de faux outils**, pour chaque panne : pas de `pactl` ni de `parec` ; serveur de son arrêté ; capture qui s'arrête seule ; réglage coupé (aucun appel au serveur de son) ; chaque combinaison PC hors ligne / agent ancien / refus / connexion coupée ; format refusé (neuf annonces mauvaises) ; trame invalide ; lecteur qui meurt ; deux flux qui se remplacent ; retard borné (trames jetées, plus récente gardée) ; maintien pendant une pause.

**Dans un vrai Chromium** (1024×600 tactile) contre une pile de démonstration : « Son : sur le PC » ; le PC qui s'éteint puis revient pendant une chaîne ; le réglage Sur le PC / Sur le Pi et l'avance, gardés par le dock après un rechargement ; l'écran de jeu qui dit où sort le son.

Tests ajoutés : **33** pour la porte `/audio` de l'agent, **48** pour le relais, le lecteur et les réglages du dock, **29** pour la capture et le routage de VLC et de MAME (dont ceux sur le vrai PipeWire et le vrai VLC), **12** pour le diagnostic, **6** Vitest, **3** e2e nouveaux (et une vérification dans le test e2e d'un jeu). Le paquet recommande le serveur de son (un test).

## Ce que je n'ai pas pu vérifier (à toi de le faire sur ton Pi)

C'est la partie importante : **je n'ai pas de Pi**, et le son dépend de lui.

- **Le serveur de son du Pi, dans la session de l'écran.** PipeWire doit tourner pour l'utilisateur du kiosque. Le paquet recommande `pipewire-pulse`, `wireplumber`, `pulseaudio-utils`, et l'installateur active `loginctl enable-linger mydock-kiosk`, mais **je n'ai pas vu le gestionnaire de session du kiosque démarrer PipeWire sur Raspberry Pi OS**. Si ce n'est pas le cas, l'écran le dit (« Pas de serveur de son (PipeWire) dans la session de l'écran ») et la lecture continue, muette ; `mydock-dock doctor` et `/var/log/mydock/player.log` (lignes `son :`) disent pourquoi.
- **MAME dans le puits.** Je n'ai pu essayer VLC pour de bon, pas MAME : sa sortie audio suit les mêmes variables d'environnement (SDL les respecte), mais **un MAME sans son sur le PC est possible** sur ton Pi. Dans ce cas, envoie-moi `player.log`.
- **L'avance du son : 150 ms est une estimation.** Elle dépend de ton réseau et de ton PC. À régler à l'œil sur une vidéo (Réglages → Divertissement → Son → Avance) : **plus** si le son est en retard sur l'image, **moins** s'il est en avance. Appliqué à la lecture suivante.
- **Le retard des jeux.** Le son d'un jeu a le même trajet (un dixième de seconde environ), sans avance possible : on peut sentir un petit délai entre un bouton et le son. Si c'est gênant, dis-le-moi : on peut resserrer les tampons, au prix de craquements possibles sur Wi-Fi.
- **Le Wi-Fi.** Un Pi en Wi-Fi saturé fera craquer le son (des trames jetées) ; `dock.log` compte les trames jetées (« Son vers le PC : … »).

### À faire pour essayer

1. Installer le paquet **1.8.0** sur le Pi **et** celui du PC (depuis la page du Pi), puis **`sudo reboot`** sur le Pi (le nouveau lanceur de l'écran et le gestionnaire de session PipeWire).
2. Lancer une chaîne : la ligne « **Son : sur le PC** » doit apparaître sous la barre de lecture, et le son sortir sur le PC.
3. Si rien : `sudo -u mydock mydock-dock doctor` (section « Son du Divertissement »), puis `sudo grep "son :" /var/log/mydock/player.log | tail` ; sur le PC `mydock-agent doctor`. Envoie-moi ces lignes.
