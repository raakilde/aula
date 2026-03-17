"""
Aula Auth — Token-based authentication for the Aula school platform.

Provides a high-level `AulaAuthenticator` that drives the full
OAuth 2.0 PKCE → SAML → MitID → token-exchange pipeline.
"""

from .authenticator import AulaAuthenticator
from .errors import (
    AulaAuthError,
    CredentialError,
    IdentityProviderError,
    TokenError,
    TransportError,
    FlowError,
)

__all__ = [
    "AulaAuthenticator",
    "AulaAuthError",
    "CredentialError",
    "IdentityProviderError",
    "TokenError",
    "TransportError",
    "FlowError",
]
