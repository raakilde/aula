"""Structured error hierarchy for Aula authentication."""


class AulaAuthError(Exception):
    """Base for all authentication-related errors."""


class CredentialError(AulaAuthError):
    """Raised when credentials are missing or invalid."""


class IdentityProviderError(AulaAuthError):
    """Raised when communication with MitID / NemLogin fails."""


class TokenError(AulaAuthError):
    """Raised when access or refresh tokens cannot be obtained or renewed."""


class TransportError(AulaAuthError):
    """Raised on HTTP-level failures (timeouts, DNS, TLS, etc.)."""


class FlowError(AulaAuthError):
    """Raised when a SAML / OAuth redirect chain breaks unexpectedly."""
