"""Le son du dock vers le PC : le format du flux, partagé par le dock (qui l'envoie) et l'agent (qui le joue).

Le Raspberry Pi n'a pas d'enceintes : son lecteur (VLC, MAME) joue dans un puits virtuel, dont le dock lit le son et l'envoie à
l'agent du PC par `/audio` (un WebSocket sur le port de l'agent, avec le même jeton que `/ws`).

    dock -> agent   {"t":"audio","v":1,"fmt":"s16le","rate":48000,"ch":2}     texte, une fois, en premier
    agent -> dock   {"t":"ready","latency_ms":80}                              texte, quand le PC est prêt à jouer
                    {"t":"err","code":"unavailable","msg":"..."}                texte, puis la connexion est fermée
    dock -> agent   <son brut>                                                 binaire, des trames de 4 octets multiples

Un seul format, vérifié à l'octet près des deux côtés : PCM 16 bits petit-boutiste, 48 kHz, stéréo. Rien d'autre ne passe.
"""

from __future__ import annotations

FORMAT = "s16le"
RATE = 48_000
CHANNELS = 2
SAMPLE_BYTES = CHANNELS * 2  # un échantillon stéréo de 16 bits
MAX_CHUNK_BYTES = 64 * 1024

#: La première trame texte du dock, exactement.
HELLO = {"t": "audio", "v": 1, "fmt": FORMAT, "rate": RATE, "ch": CHANNELS}
