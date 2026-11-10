"""L'autorité racine d'Apple, pour les films des vues aériennes (et eux seuls).

Le serveur des films (`sylvan.apple.com`) présente une chaîne de l'autorité **privée** d'Apple :

    sylvan.apple.com ← Apple Server Authentication CA ← Apple Root CA

L'intermédiaire est envoyé par le serveur, mais « Apple Root CA » ne figure pas dans la liste des autorités de Debian
(ni de Mozilla) : le Pi répond « unable to get local issuer certificate », alors que les appareils Apple, eux, la
connaissent. La vérification ne doit jamais être désactivée : on lui **donne** la racine qui manque.

Cette racine est publique ; Apple la publie sur son site. Le dock la télécharge donc **par une connexion vérifiée**
(`www.apple.com`, dont l'autorité, elle, est reconnue), contrôle que c'est bien un certificat racine auto-signé nommé
« Apple Root CA », la garde dans son dossier d'état, et ne s'en sert que pour les serveurs d'Apple de la liste
(`aerial.APPLE_HOSTS`), en plus des autorités du Pi. Le nom du serveur, la chaîne et les dates restent vérifiés.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from mydock_dock import x509
from mydock_dock.net import build_opener

#: Où Apple publie sa racine (une connexion vérifiée avec les autorités du Pi ; on essaie dans l'ordre).
ROOT_URLS = (
    "https://www.apple.com/appleca/AppleIncRootCertificate.cer",
    "https://www.apple.com/certificateauthority/AppleIncRootCertificate.cer",
)
#: Le nom attendu du certificat, tel que `x509.names` le donne (celui que la chaîne du serveur nomme comme autorité).
EXPECTED_NAME = "Apple Root CA (Apple Inc.)"
#: L'empreinte SHA-256 que j'ai en mémoire pour ce certificat : elle sert à dire « connue » à l'écran. Elle n'est PAS
#: imposée, car je n'ai pas pu la vérifier à la source (aucun accès à Apple depuis l'environnement de développement).
REMEMBERED_SHA256 = "B0B1730ECBC7FF4505142C49F1295E6EDA6BCAED7E2C68C5BE91B5A11001F024"
MAX_BYTES = 16 * 1024
TIMEOUT = 15.0
CACHE_NAME = "apple-root-ca.pem"


class AppleRootError(Exception):
    """La racine d'Apple n'a pas pu être obtenue ou n'est pas celle qu'on attend ; le message est affichable."""


class _SameHostHttps(urllib.request.HTTPRedirectHandler):
    """Une redirection n'est suivie que vers le même serveur et en https : jamais un certificat reçu par un autre chemin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        old, new = urllib.parse.urlsplit(req.full_url), urllib.parse.urlsplit(newurl)
        if new.scheme != "https" or new.hostname != old.hostname:
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fingerprint(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest().upper()


def short_fingerprint(der: bytes) -> str:
    """« B0B1 730E … F024 » : de quoi comparer à l'œil avec la page d'Apple."""
    digest = fingerprint(der)
    return f"{digest[:8]}…{digest[-8:]}"


def parse_root(data: bytes) -> bytes:
    """Le certificat DER d'une racine d'Apple, lu depuis du PEM ou du DER ; lève AppleRootError si ce n'en est pas une."""
    try:
        der = (
            ssl.PEM_cert_to_DER_cert(data.decode("ascii"))
            if data.lstrip().startswith(b"-----BEGIN")
            else data
        )
    except (UnicodeDecodeError, ValueError):
        raise AppleRootError("Le fichier reçu n'est pas un certificat.") from None
    try:
        subject, issuer = x509.names(der)
    except ValueError:
        raise AppleRootError("Le fichier reçu n'est pas un certificat.") from None
    if subject != issuer:
        raise AppleRootError(
            f"Le certificat reçu (« {subject} ») n'est pas une autorité racine : il est signé par « {issuer} »."
        )
    if subject != EXPECTED_NAME:
        raise AppleRootError(
            f"Le certificat reçu s'appelle « {subject} », pas « {EXPECTED_NAME} » : il n'est pas utilisé."
        )
    return der


def to_pem(der: bytes) -> str:
    return ssl.DER_cert_to_PEM_cert(der)


def load_cached(directory: Path) -> bytes | None:
    """La racine déjà obtenue (et toujours valable), ou None."""
    try:
        return parse_root((directory / CACHE_NAME).read_bytes())
    except (OSError, AppleRootError):
        return None


def save_cached(directory: Path, der: bytes) -> None:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        part = directory / f".{CACHE_NAME}.part"
        part.write_text(to_pem(der))
        part.chmod(0o644)
        os.replace(part, directory / CACHE_NAME)
    except OSError:
        pass  # sans cache, on la redemandera au prochain besoin


def fetch_root(urls: tuple[str, ...] | None = None, timeout: float = TIMEOUT) -> bytes:
    """Télécharge la racine d'Apple (connexion vérifiée) ; lève AppleRootError avec ce qui a été essayé."""
    problems: list[str] = []
    for url in urls or ROOT_URLS:
        host = url.split("/")[2]
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "mydock-dock"})
            with contextlib.closing(
                build_opener(_SameHostHttps()).open(request, timeout=timeout)
            ) as response:
                body = response.read(MAX_BYTES + 1)
        except urllib.error.HTTPError as exc:
            problems.append(f"{host} répond « {exc.code} {exc.reason} »")
            continue
        except (OSError, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            problems.append(f"{host} : {reason}")
            continue
        if len(body) > MAX_BYTES:
            problems.append(f"{host} : fichier trop gros")
            continue
        try:
            return parse_root(body)
        except AppleRootError as exc:
            problems.append(str(exc))
    raise AppleRootError(
        "Impossible d'obtenir l'autorité racine d'Apple (" + " ; ".join(problems) + ")."
    )


def obtain_root(directory: Path | None, urls: tuple[str, ...] | None = None) -> tuple[bytes, str]:
    """(racine DER, d'où elle vient : « cache » ou « apple.com »). Le cache est relu et recontrôlé à chaque fois."""
    if directory is not None and (cached := load_cached(directory)) is not None:
        return cached, "cache"
    der = fetch_root(urls)
    if directory is not None:
        save_cached(directory, der)
    return der, "apple.com"
