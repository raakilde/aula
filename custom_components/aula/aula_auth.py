"""
Authentication management for Aula integration.
Handles MitID login, session management, cookie persistence, and browser automation.

This module re-exports the AuthMixin from the auth package for backward compatibility.
"""

from .auth import AuthMixin

__all__ = ["AuthMixin"]
