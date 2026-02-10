"""
Authentication management for Aula integration.
Session management, cookie persistence, and API setup.
"""

from .session import SessionMixin


class AuthMixin(SessionMixin):
    """Mixin providing authentication-related methods for the Aula Client."""

    pass
