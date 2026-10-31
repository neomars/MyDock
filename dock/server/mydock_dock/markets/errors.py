"""Pourquoi un fournisseur n'a pas pu répondre. Chaque message est écrit pour être lu à l'écran."""

from __future__ import annotations


class ProviderError(Exception):
    """Base : un fournisseur n'a pas donné ce qu'on lui demandait."""

    #: vrai si c'est le fournisseur lui-même qui est en panne (réseau, quota, refus) et non la valeur
    provider_down = True

    def __init__(self, message: str, *, retry_after: float | None = None):
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after


class NetworkError(ProviderError):
    """Pas de réponse : réseau coupé, nom introuvable, délai dépassé."""


class RateLimited(ProviderError):
    """Trop de requêtes : le fournisseur demande d'attendre."""


class Blocked(ProviderError):
    """Accès refusé (clé invalide, pays non autorisé, adresse bloquée)."""


class Unavailable(ProviderError):
    """Erreur du côté du fournisseur (5xx)."""


class BadResponse(ProviderError):
    """Réponse reçue mais illisible ou inattendue."""


class NotFound(ProviderError):
    """Le fournisseur ne connaît pas cette valeur."""

    provider_down = False


class Unsupported(ProviderError):
    """Le fournisseur ne sait pas faire cela (historique, type de valeur)."""

    provider_down = False
