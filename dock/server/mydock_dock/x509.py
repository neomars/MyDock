"""Lecture minimale d'un certificat X.509 (DER) : de quoi dire « émis par … » dans un message d'erreur.

Ce n'est pas une vérification (OpenSSL s'en charge) : seulement l'affichage des noms du sujet et de l'émetteur, pour
qu'un refus de certificat dise *quelle* autorité n'est pas reconnue au lieu d'un code technique.
"""

from __future__ import annotations

from collections.abc import Iterator

_COMMON_NAME = b"\x55\x04\x03"
_ORGANIZATION = b"\x55\x04\x0a"
_UTF16 = 0x1E


def _tlv(data: bytes, pos: int) -> tuple[int, int, int]:
    """(étiquette, début du contenu, fin du contenu) de l'élément DER qui commence à `pos`."""
    if pos + 2 > len(data):
        raise ValueError("certificat tronqué")
    tag, first = data[pos], data[pos + 1]
    if first < 0x80:
        start, length = pos + 2, first
    else:
        count = first & 0x7F
        if not 1 <= count <= 4 or pos + 2 + count > len(data):
            raise ValueError("longueur DER invalide")
        length = int.from_bytes(data[pos + 2 : pos + 2 + count], "big")
        start = pos + 2 + count
    end = start + length
    if end > len(data):
        raise ValueError("certificat tronqué")
    return tag, start, end


def _children(data: bytes, start: int, end: int) -> Iterator[tuple[int, int, int]]:
    pos = start
    while pos < end:
        tag, s, e = _tlv(data, pos)
        yield tag, s, e
        pos = e


def _text(tag: int, raw: bytes) -> str:
    return raw.decode("utf-16-be", "replace") if tag == _UTF16 else raw.decode("utf-8", "replace")


def _name(data: bytes, start: int, end: int) -> str:
    """Un nom X.501 lisible : son CN, sinon son O, avec l'organisation entre parenthèses quand les deux existent."""
    found: dict[bytes, str] = {}
    for _tag, s, e in _children(data, start, end):  # chaque RDN (SET)
        for _t2, s2, e2 in _children(data, s, e):  # chaque attribut (SEQUENCE)
            parts = list(_children(data, s2, e2))
            if len(parts) == 2 and parts[0][0] == 0x06:
                oid = data[parts[0][1] : parts[0][2]]
                if oid in (_COMMON_NAME, _ORGANIZATION):
                    found.setdefault(oid, _text(parts[1][0], data[parts[1][1] : parts[1][2]]))
    common, organization = found.get(_COMMON_NAME), found.get(_ORGANIZATION)
    if common and organization and organization not in common:
        return f"{common} ({organization})"
    return common or organization or "?"


def names(der: bytes) -> tuple[str, str]:
    """(sujet, émetteur) d'un certificat DER ; lève ValueError si ce n'en est pas un."""
    try:
        _tag, start, end = _tlv(der, 0)
        tbs_tag, ts, te = next(_children(der, start, end))
        if tbs_tag != 0x30:
            raise ValueError("pas un certificat")
        items = list(_children(der, ts, te))
        index = 1 if items[0][0] == 0xA0 else 0  # [0] : la version, absente des certificats v1
        issuer, subject = items[index + 2], items[index + 4]
        return _name(der, subject[1], subject[2]), _name(der, issuer[1], issuer[2])
    except (IndexError, StopIteration):
        raise ValueError("pas un certificat") from None


def chain_text(chain: list[bytes]) -> str:
    """« serveur ← intermédiaire ← autorité » : ce que le serveur présente, du plus bas au plus haut."""
    labels: list[str] = []
    issuer = ""
    for der in chain:
        try:
            subject, issuer = names(der)
        except ValueError:
            continue
        labels.append(subject)
    if labels and issuer and issuer != labels[-1]:
        labels.append(
            issuer
        )  # l'autorité qui a signé le dernier certificat envoyé, souvent absente de la chaîne
    return " ← ".join(labels)
