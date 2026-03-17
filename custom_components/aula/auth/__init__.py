"""
Authentication management for Aula integration.
Session management and API setup using MitID OAuth tokens.
"""

from .session import SessionMixin


class AuthMixin(SessionMixin):
    """Mixin providing authentication-related methods for the Aula Client."""

    pass
