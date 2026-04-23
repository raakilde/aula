"""
Aula Auth — Token-based authentication for the Aula school platform.
"""

from .errors import (
    AulaAuthError,
    CredentialError,
    IdentityProviderError,
    TokenError,
    TransportError,
    FlowError,
)
__all__ = [
    "AulaAuthError",
    "CredentialError",
    "IdentityProviderError",
    "TokenError",
    "TransportError",
    "FlowError",
]
