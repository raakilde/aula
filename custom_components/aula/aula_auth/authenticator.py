"""
AulaAuthenticator — high-level orchestrator for the full
OAuth 2.0 PKCE → UniLogin broker → NemLogin → MitID SRP → token pipeline.

Orchestrates the complete login pipeline for the Aula school platform.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import secrets
import time
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .errors import (
    AulaAuthError,
    CredentialError,
    FlowError,
    IdentityProviderError,
    TokenError,
    TransportError,
)
from .mitid import MitIDSession

_LOG = logging.getLogger(__name__)

# ── fixed endpoints & client config ─────────────────────────────────
_AUTH_HOST = "https://login.aula.dk"
_BROKER_HOST = "https://broker.unilogin.dk"
_APP_REDIRECT = "https://app-private.aula.dk"
_OIDC_CLIENT = "_99949a54b8b65423862aac1bf629599ed64231607a"
_SCOPE = "aula-sensitive"
_UA = (
    "Mozilla/5.0 (Linux; Android 14; sdk_gphone64_x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Mobile Safari/537.36"
)


class AulaAuthenticator:
    """
    Authenticate against Aula using MitID.

    Parameters
    ----------
    username : str
        MitID user-id.
    password : str | None
        MitID password (TOKEN method only).
    token_code : str | None
        6-digit TOTP code (TOKEN method only).
    method : ``"APP"`` | ``"TOKEN"``
        Which MitID authenticator to use.
    timeout : int
        HTTP request timeout in seconds.
    identity_chooser : callable | None
        ``fn(names: list[str]) -> int`` (1-based) for multi-identity pages.
    """

    def __init__(
        self,
        username: str,
        password: Optional[str] = None,
        token_code: Optional[str] = None,
        method: str = "APP",
        timeout: int = 30,
        identity_chooser: Optional[Callable] = None,
    ) -> None:
        self._username = username
        self._password = password
        self._token_code = token_code
        self._method = method.upper()
        self._timeout = timeout
        self._identity_chooser = identity_chooser

        self._http = requests.Session()
        self._http.headers.update({
            "User-Agent": _UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Upgrade-Insecure-Requests": "1",
        })

        self._pkce_verifier: Optional[str] = None
        self._pkce_challenge: Optional[str] = None
        self._oauth_state: Optional[str] = None
        self.tokens: Optional[Dict] = None

        # Expose the active MitIDSession for QR-code access
        self.mitid_session: Optional[MitIDSession] = None

    # ══════════════════════════════════════════════════════════════════
    #  Top-level entry points
    # ══════════════════════════════════════════════════════════════════
    def run(self) -> Dict:
        """
        Execute the full authentication pipeline and return token dict.

        Raises :class:`AulaAuthError` (or a subclass) on failure.
        """
        _LOG.info("Starting Aula authentication flow")
        redirect = self._begin_oauth()
        vt, mitid_url = self._navigate_to_mitid(redirect)
        auth_code = self._perform_mitid_auth(vt)
        self.mitid_session = None  # clear after auth to stop QR exposure
        saml = self._complete_mitid(vt, auth_code)
        broker_saml = self._broker_flow(saml)
        callback = self._submit_aula_saml(broker_saml)
        self.tokens = self._exchange_code(callback)
        _LOG.info("Authentication completed successfully")
        return self.tokens

    def refresh(self) -> bool:
        """
        Renew the access token via the stored refresh token.

        Returns ``True`` on success.
        """
        if not self.tokens or "refresh_token" not in self.tokens:
            _LOG.error("No refresh token available")
            return False
        try:
            data = {
                "grant_type": "refresh_token",
                "refresh_token": self.tokens["refresh_token"],
                "client_id": _OIDC_CLIENT,
            }
            r = self._http.post(
                f"{_AUTH_HOST}/simplesaml/module.php/oidc/token.php",
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
                timeout=self._timeout,
            )
            if r.status_code != 200:
                _LOG.error("Token refresh failed: HTTP %s", r.status_code)
                return False

            new = r.json()
            self.tokens["access_token"] = new["access_token"]
            if "refresh_token" in new:
                self.tokens["refresh_token"] = new["refresh_token"]
            if "expires_in" in new:
                self.tokens["expires_in"] = new["expires_in"]
                self.tokens["expires_at"] = time.time() + new["expires_in"]
            _LOG.info("Token refreshed — expires in %ss", new.get("expires_in", "?"))
            return True
        except Exception as exc:
            _LOG.error("Token refresh exception: %s", exc)
            return False

    def token_status(self) -> Dict:
        """Return ``{valid: bool, expires_in: float, ...}``."""
        if not self.tokens or "access_token" not in self.tokens:
            return {"valid": False, "reason": "No token"}
        try:
            parts = self.tokens["access_token"].split(".")
            if len(parts) < 2:
                return {"valid": False, "reason": "Malformed JWT"}
            payload = parts[1] + "=" * (4 - len(parts[1]) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload))
            exp = claims.get("exp", 0)
            remaining = exp - time.time()
            if remaining < 300:
                return {"valid": False, "reason": f"Expires in {int(remaining)}s", "expires_in": remaining}
            return {"valid": True, "expires_in": remaining, "expires_at": exp}
        except Exception as exc:
            return {"valid": False, "reason": str(exc)}

    def verify_api_access(self) -> bool:
        """Quick GET against Aula to confirm the token works."""
        if not self.tokens:
            return False
        url = (
            "https://www.aula.dk/api/v22/?method=profiles.getProfileContext"
            "&portalrole=guardian"
        )
        try:
            r = self._http.get(
                url,
                headers={"Authorization": f"Bearer {self.tokens['access_token']}"},
                timeout=10,
            )
            return r.status_code == 200
        except Exception:
            return False

    # ── QR helpers for HA GUI ───────────────────────────────────────
    def qr_svg_pair(self) -> Optional[Tuple[str, str]]:
        """Return ``(svg1, svg2)`` or ``None`` if no QR available."""
        ms = self.mitid_session
        if not ms:
            return None
        pair = ms.qr_codes
        if not pair:
            return None
        return (self._render_svg(pair[0]), self._render_svg(pair[1]))

    @staticmethod
    def _render_svg(qr) -> str:
        matrix = qr.get_matrix()
        sz = len(matrix)
        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{sz * 10}" height="{sz * 10}" viewBox="0 0 {sz} {sz}">',
            '<rect width="100%" height="100%" fill="white"/>',
        ]
        for y, row in enumerate(matrix):
            for x, cell in enumerate(row):
                if cell:
                    parts.append(f'<rect x="{x}" y="{y}" width="1" height="1" fill="black"/>')
        parts.append("</svg>")
        return "".join(parts)

    # ── convenience properties ──────────────────────────────────────
    @property
    def access_token(self) -> Optional[str]:
        return self.tokens.get("access_token") if self.tokens else None

    @property
    def refresh_token(self) -> Optional[str]:
        return self.tokens.get("refresh_token") if self.tokens else None

    @property
    def is_authenticated(self) -> bool:
        return bool(self.tokens and self.tokens.get("access_token"))

    # ══════════════════════════════════════════════════════════════════
    #  Internal pipeline steps
    # ══════════════════════════════════════════════════════════════════
    def _begin_oauth(self) -> str:
        """Kick off the OIDC authorise request and return the first redirect URL."""
        verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).decode().rstrip("=")
        self._pkce_verifier = verifier
        self._pkce_challenge = challenge
        self._oauth_state = base64.urlsafe_b64encode(secrets.token_bytes(16)).decode().rstrip("=")

        params = {
            "response_type": "code",
            "client_id": _OIDC_CLIENT,
            "scope": _SCOPE,
            "redirect_uri": _APP_REDIRECT,
            "state": self._oauth_state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        url = f"{_AUTH_HOST}/simplesaml/module.php/oidc/authorize.php"
        try:
            r = self._http.get(url, params=params, allow_redirects=False, timeout=self._timeout)
        except requests.RequestException as exc:
            raise TransportError(f"OAuth start failed: {exc}")

        if r.status_code in (301, 302, 303, 307, 308):
            return r.headers["Location"]
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            form = soup.find("form")
            if form and form.get("action"):
                return form["action"]
            meta = soup.find("meta", {"http-equiv": "refresh"})
            if meta:
                content = meta.get("content", "")
                if "url=" in content.lower():
                    return content.split("url=", 1)[1]
            raise FlowError("OAuth 200 but no redirect")
        raise FlowError(f"Unexpected OAuth response: {r.status_code}")

    def _navigate_to_mitid(self, start_url: str) -> Tuple[str, str]:
        """Follow redirects through broker → NemLogin and return (verification_token, url)."""
        url = start_url
        for _ in range(12):
            try:
                r = self._http.get(url, allow_redirects=False, timeout=self._timeout)
            except requests.RequestException as exc:
                raise TransportError(f"Redirect chain failed: {exc}")

            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")

                if "broker.unilogin.dk" in r.url:
                    url = self._pick_idp(soup, r)
                    continue

                if "mitid.dk" in r.url or "nemlog-in" in r.url:
                    tok_inp = soup.find("input", {"name": "__RequestVerificationToken"})
                    if not tok_inp:
                        raise FlowError("Missing __RequestVerificationToken on MitID page")
                    return tok_inp["value"], r.url

                raise FlowError(f"Unexpected destination: {r.url}")

            if r.status_code in (301, 302, 303, 307, 308):
                url = urljoin(url, r.headers.get("Location", ""))
                continue

            raise FlowError(f"HTTP {r.status_code} during redirect chain")

        raise FlowError("Too many redirects navigating to MitID")

    def _pick_idp(self, soup: BeautifulSoup, response) -> str:
        """Submit the UniLogin broker form selecting NemLogin3 as IdP."""
        form = soup.find("form")
        if not form:
            raise FlowError("No form on broker page")

        action = form.get("action", "")
        fields: Dict[str, str] = {}
        for inp in form.find_all("input"):
            n = inp.get("name")
            if n:
                fields[n] = inp.get("value", "")
        fields["selectedIdp"] = "nemlogin3"

        if not action.startswith("http"):
            action = f"{_BROKER_HOST}{action}"

        try:
            r = self._http.post(action, data=fields, allow_redirects=False, timeout=self._timeout)
        except requests.RequestException as exc:
            raise TransportError(f"Broker IdP select failed: {exc}")

        if r.status_code in (301, 302, 303, 307, 308):
            return r.headers["Location"]

        if r.status_code == 200:
            # May have landed on the NemLogin page already
            return r.url

        raise FlowError(f"Broker form returned HTTP {r.status_code}")

    def _perform_mitid_auth(self, verification_token: str) -> str:
        """Initialise MitID and run the chosen authenticator; return the auth code."""
        try:
            init_r = self._http.post(
                "https://nemlog-in.mitid.dk/login/mitid/initialize",
                headers={
                    "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "x-requested-with": "XMLHttpRequest",
                    "origin": "https://nemlog-in.mitid.dk",
                    "referer": "https://nemlog-in.mitid.dk/login/mitid",
                },
                data={"__RequestVerificationToken": verification_token},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise TransportError(f"MitID init failed: {exc}")

        body = init_r.json()
        if isinstance(body, str):
            body = json.loads(body)
        aux_b64 = body.get("Aux")
        if not aux_b64:
            raise IdentityProviderError("No 'Aux' in MitID init response")
        aux = json.loads(base64.b64decode(aux_b64))

        client_hash = binascii.hexlify(
            base64.b64decode(aux["coreClient"]["checksum"])
        ).decode()
        sid = aux["parameters"]["authenticationSessionId"]

        ms = MitIDSession(client_hash, sid, self._http)
        self.mitid_session = ms

        available = ms.identify_user(self._username)
        _LOG.debug("Available MitID methods: %s", list(available))

        if self._method == "TOKEN":
            if "TOKEN" not in available:
                raise CredentialError("TOKEN method not available for this user")
            if not self._password or not self._token_code:
                raise CredentialError("Password and token code required for TOKEN auth")
            ms.submit_token_code(self._token_code)
            ms.submit_password(self._password)
        elif self._method == "APP":
            if "APP" not in available:
                raise CredentialError("APP method not available for this user")
            ms.run_app_auth_blocking()
        else:
            raise CredentialError(f"Unknown auth method: {self._method}")

        return ms.get_authorisation_code()

    def _complete_mitid(self, verification_token: str, auth_code: str) -> Dict:
        """POST back to NemLogin with the auth code; extract RelayState + SAMLResponse."""
        fields = {
            "__RequestVerificationToken": verification_token,
            "NewCulture": "",
            "MitIDUseConfirmed": "True",
            "MitIDAuthCode": auth_code,
            "MitIDAuthenticationCancelled": "",
            "MitIDCoreClientError": "",
            "SessionStorageActiveSessionUuid": self._http.cookies.get("SessionUuid", ""),
            "SessionStorageActiveChallenge": self._http.cookies.get("Challenge", ""),
        }
        try:
            r = self._http.post(
                "https://nemlog-in.mitid.dk/login/mitid",
                data=fields,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise TransportError(f"MitID completion failed: {exc}")

        soup = BeautifulSoup(r.text, "html.parser")

        if r.url == "https://nemlog-in.mitid.dk/loginoption":
            r, soup = self._handle_identity_page(r, soup)

        saml_inp = soup.find("input", {"name": "SAMLResponse"})
        relay_inp = soup.find("input", {"name": "RelayState"})
        if not saml_inp:
            raise FlowError("No SAMLResponse after MitID completion")
        return {
            "saml": saml_inp["value"],
            "relay": relay_inp["value"] if relay_inp else "",
        }

    def _handle_identity_page(self, response, soup):
        """Choose an identity when the user has multiple logins."""
        fields: Dict[str, str] = {}
        for inp in soup.form.select("input"):
            try:
                fields[inp["name"]] = inp.get("value", "")
            except KeyError:
                pass

        options = soup.select("a.list-link")
        names = [
            (o.select_one("div.list-link-text") or o).get_text(strip=True)
            for o in options
        ]

        choice = 1
        if self._identity_chooser and callable(self._identity_chooser):
            choice = self._identity_chooser(names)

        idx = int(choice) - 1
        if 0 <= idx < len(options):
            link_el = options[idx]
            fields["ChosenOptionJson"] = link_el.get("data-loginoptions", "")
        else:
            raise FlowError(f"Invalid identity selection: {choice}")

        r = self._http.post(
            response.url, data=fields,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": response.url},
            allow_redirects=True, timeout=self._timeout,
        )
        return r, BeautifulSoup(r.text, "html.parser")

    def _broker_flow(self, saml: Dict) -> Dict:
        """Submit SAML to the UniLogin broker; return the final SAML for Aula."""
        try:
            r = self._http.post(
                f"{_BROKER_HOST}/auth/realms/broker/broker/nemlogin3/endpoint",
                data={"RelayState": saml["relay"], "SAMLResponse": saml["saml"]},
                allow_redirects=False,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise TransportError(f"Broker SAML post failed: {exc}")

        if "Location" not in r.headers:
            raise FlowError("No redirect from broker SAML endpoint")

        r2 = self._http.get(r.headers["Location"], timeout=self._timeout)
        soup = BeautifulSoup(r2.text, "html.parser")

        # Extract broker form fields
        url2, fields2 = self._extract_form(soup, r2.url)

        # Handle role selection (kontakt/elev)
        if "selected-aktoer" in fields2:
            fields2["selected-aktoer"] = "KONTAKT"

        r3 = self._http.post(
            url2, data=fields2, allow_redirects=False, timeout=self._timeout,
        )

        # Handle intermediate confirmation
        if r3.status_code == 200:
            soup3 = BeautifulSoup(r3.text, "html.parser")
            btn = soup3.find("button", {"id": "confirmation-button"})
            if btn:
                form = btn.find_parent("form")
                if form:
                    action = form.get("action", "")
                    if not action.startswith("http"):
                        action = urljoin(r3.url, action)
                    conf_fields = {}
                    for inp in form.find_all("input"):
                        if inp.get("name"):
                            conf_fields[inp["name"]] = inp.get("value", "")
                    r3 = self._http.post(
                        action, data=conf_fields, allow_redirects=False, timeout=self._timeout,
                    )

        if "Location" not in r3.headers:
            raise FlowError(f"No redirect after broker post (HTTP {r3.status_code})")

        r4 = self._http.get(r3.headers["Location"], timeout=self._timeout)
        soup4 = BeautifulSoup(r4.text, "html.parser")

        saml_inp = soup4.find("input", {"name": "SAMLResponse"})
        relay_inp = soup4.find("input", {"name": "RelayState"})
        form4 = soup4.find("form")
        if not saml_inp:
            raise FlowError("No SAMLResponse in broker final page")

        return {
            "saml": saml_inp["value"],
            "relay": relay_inp["value"] if relay_inp else "",
            "action": form4.get("action", "") if form4 else "",
        }

    def _submit_aula_saml(self, saml: Dict) -> str:
        """POST the final SAML to Aula's SP and follow redirects to the OAuth callback."""
        endpoint = saml.get("action") or (
            f"{_AUTH_HOST}/simplesaml/module.php/saml/sp/saml2-acs.php/uni-sp"
        )
        try:
            r = self._http.post(
                endpoint,
                data={"SAMLResponse": saml["saml"], "RelayState": saml["relay"]},
                allow_redirects=False,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise TransportError(f"Aula SAML post failed: {exc}")

        if "Location" not in r.headers:
            raise FlowError("No redirect from Aula SAML endpoint")

        return self._chase_oauth_callback(r.headers["Location"])

    def _chase_oauth_callback(self, start: str) -> str:
        """Follow redirects until we land on ``app-private.aula.dk?code=…``."""
        url = start
        for _ in range(12):
            try:
                r = self._http.get(url, allow_redirects=False, timeout=self._timeout)
            except requests.RequestException as exc:
                raise TransportError(f"Callback redirect failed: {exc}")

            for candidate in (r.url, r.headers.get("Location", "")):
                if _APP_REDIRECT in candidate and "code=" in candidate:
                    return candidate

            if r.status_code == 200:
                if _APP_REDIRECT in r.url and "code=" in r.url:
                    return r.url
                raise FlowError(f"Landed on {r.url} without OAuth code")

            if r.status_code in (301, 302, 303, 307, 308):
                loc = r.headers.get("Location", "")
                url = loc if loc.startswith("http") else urljoin(url, loc)
                continue

            raise FlowError(f"HTTP {r.status_code} chasing OAuth callback")

        raise FlowError("Too many redirects looking for OAuth callback")

    def _exchange_code(self, callback_url: str) -> Dict:
        """Exchange the authorisation code for tokens."""
        qs = parse_qs(urlparse(callback_url).query)
        code = qs.get("code", [None])[0]
        if not code:
            raise TokenError("No code in callback URL")

        state = qs.get("state", [None])[0]
        if not state or state != self._oauth_state:
            raise TokenError("OAuth state mismatch or missing")

        try:
            r = self._http.post(
                f"{_AUTH_HOST}/simplesaml/module.php/oidc/token.php",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": _OIDC_CLIENT,
                    "redirect_uri": _APP_REDIRECT,
                    "code_verifier": self._pkce_verifier,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise TransportError(f"Token exchange failed: {exc}")

        if r.status_code != 200:
            raise TokenError(f"Token endpoint returned HTTP {r.status_code}")

        tokens = r.json()
        if "expires_in" in tokens:
            tokens["expires_at"] = time.time() + tokens["expires_in"]
        _LOG.info("Tokens obtained — expires in %ss", tokens.get("expires_in", "?"))
        return tokens

    # ── small helpers ───────────────────────────────────────────────
    @staticmethod
    def _extract_form(soup: BeautifulSoup, page_url: str) -> Tuple[str, Dict[str, str]]:
        form = soup.find("form")
        if not form:
            raise FlowError("No form found on page")
        action = form.get("action", "")
        if not action.startswith("http"):
            action = urljoin(page_url, action) if action else page_url
        fields = {}
        for inp in form.find_all("input"):
            n = inp.get("name")
            if n:
                fields[n] = inp.get("value", "")
        return action, fields
