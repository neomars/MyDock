"""La commande `mydock-agent spotify` : connecter, déconnecter, vérifier le compte Spotify de ce PC."""

from __future__ import annotations

import sys
from collections.abc import Callable

from mydock_agent.spotify.auth import (
    DEFAULT_PORT,
    AuthError,
    TokenStore,
    login_instructions,
    refresh_token,
    run_login,
    whoami,
)
from mydock_agent.spotify.http import Transport, urllib_transport
from mydock_common.config import ConfigError


def cmd_login(
    client_id: str | None,
    *,
    port: int,
    open_browser: bool,
    ask: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
    **kwargs: object,
) -> int:
    if not client_id:
        if not sys.stdin.isatty():
            out(
                "Donne le Client ID de ton application Spotify :  mydock-agent spotify login --client-id ID"
            )
            out("\n" + login_instructions(port))
            return 2
        out(login_instructions(port) + "\n")
        client_id = ask("Client ID : ")
    return run_login(client_id, port=port, open_browser=open_browser, out=out, **kwargs)  # type: ignore[arg-type]


def cmd_logout(*, store: TokenStore | None = None, out: Callable[[str], None] = print) -> int:
    store = store or TokenStore()
    if store.delete():
        out(
            f"Connexion à Spotify retirée ({store.path} supprimé). Pour retirer aussi l'accès côté Spotify : spotify.com/account/apps."
        )
    else:
        out("Ce PC n'est connecté à aucun compte Spotify.")
    return 0


def cmd_status(
    *,
    check: bool = False,
    store: TokenStore | None = None,
    transport: Transport = urllib_transport,
    out: Callable[[str], None] = print,
) -> int:
    store = store or TokenStore()
    try:
        creds = store.load()
    except ConfigError as exc:
        out(f"Fichier du jeton inutilisable : {exc}")
        return 1
    if creds is None:
        out(
            "Ce PC n'est connecté à aucun compte Spotify : « mydock-agent spotify login --client-id ID »."
        )
        return 1
    out(
        f"Connecté à Spotify{' en tant que ' + creds.user if creds.user else ''} (jeton dans {store.path})."
    )
    if not check:
        return 0
    from time import time

    try:
        if creds.expired(time()):
            refresh_token(transport, creds, time())
            store.save(creds)
        name = whoami(transport, creds)
    except AuthError as exc:
        out(f"Mais Spotify refuse le jeton : {exc}")
        return 1
    out(f"Spotify répond : compte « {name} ».")
    return 0


__all__ = ["DEFAULT_PORT", "cmd_login", "cmd_logout", "cmd_status"]
