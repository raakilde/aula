"""
Aula OAuth2 Token Sniffer — fanger access + refresh tokens fra iPad/iPhone.

Kør:
    python3 sniff_aula.py

iPad setup:
    1. WiFi → HTTP Proxy → Manuel → <IP>:8080
    2. Safari → http://mitm.it → installer certifikat
    3. Indstillinger → Generelt → Om → Certifikatbetroelser → slå til
    4. Åbn Aula-appen → log ind
    5. Tokens vises i terminalen
"""

import asyncio
import json
import os
import re
import subprocess
import sys
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

try:
    import requests as _requests
except ImportError:
    print("Installerer requests...")
    os.system(f"{sys.executable} -m pip install requests")
    try:
        import requests as _requests
    except ImportError:
        _requests = None

# Installer mitmproxy hvis nødvendigt
try:
    from mitmproxy import http, options
    from mitmproxy.tools.dump import DumpMaster
except ImportError:
    print("Installerer mitmproxy...")
    os.system(f"{sys.executable} -m pip install mitmproxy")
    print("\nGenstart: python3 sniff_aula.py")
    sys.exit(0)

SAVE_FILE = os.path.join(os.path.dirname(__file__), ".aula_tokens.json")
FLOW_LOG_FILE = os.path.join(os.path.dirname(__file__), "aula_flow_after_auth.jsonl")

FOUND = {
    "access_token": None,
    "refresh_token": None,
    "device_id": None,
    "expires_in": None,
    "token_type": None,
    "id_token": None,
    "scope": None,
    "all_keys": [],
}


class AulaOAuthSniffer:
    """Fanger OAuth2 tokens fra Aula mobilapp-trafik."""

    def __init__(self):
        """Initialize runtime state for flow logging."""
        self._flow_logging_enabled = False

    @staticmethod
    def _redact_url(url: str) -> str:
        """Redact sensitive query params from URLs used in logs/files."""
        try:
            split = urlsplit(url)
            redacted = []
            sensitive = {
                "access_token",
                "refresh_token",
                "id_token",
                "code",
                "authorization",
            }
            for key, value in parse_qsl(split.query, keep_blank_values=True):
                if key.lower() in sensitive:
                    display = f"{value[:16]}..." if value else ""
                    redacted.append((key, display))
                else:
                    redacted.append((key, value))
            return urlunsplit(
                (
                    split.scheme,
                    split.netloc,
                    split.path,
                    urlencode(redacted),
                    split.fragment,
                )
            )
        except Exception:
            return url

    @staticmethod
    def _is_oauth_token_endpoint(url: str) -> bool:
        """Return True for real OAuth token endpoint requests only."""
        split = urlsplit(url)
        host = split.netloc.lower()
        path = split.path.lower()
        return "login.aula.dk" in host and path.endswith("/oidc/token.php")

    @staticmethod
    def _has_auth_bundle() -> bool:
        """Check whether access, refresh and device id are all captured."""
        return bool(
            FOUND.get("access_token")
            and FOUND.get("refresh_token")
            and FOUND.get("device_id")
        )

    def _append_flow_event(self, event: dict):
        """Append one redacted flow event to the post-auth JSONL file."""
        with open(FLOW_LOG_FILE, "a") as f:
            f.write(json.dumps(event, ensure_ascii=True) + "\n")

    def _enable_flow_logging_if_ready(self):
        """Enable flow logging after access+refresh+device_id are available."""
        if self._flow_logging_enabled:
            return
        if not self._has_auth_bundle():
            return

        self._flow_logging_enabled = True
        self._append_flow_event(
            {
                "event": "auth_bundle_detected",
                "note": "Post-auth flow logging enabled",
                "device_id": FOUND.get("device_id"),
            }
        )
        print(f"\n🧭 Flow log enabled: {FLOW_LOG_FILE}")

    def _log_flow_request(self, flow: http.HTTPFlow, url: str):
        """Write request event to flow log when enabled."""
        if not self._flow_logging_enabled:
            return

        split = urlsplit(url)
        self._append_flow_event(
            {
                "event": "request",
                "method": flow.request.method,
                "host": split.netloc,
                "path": split.path,
                "url": self._redact_url(url),
                "timestamp": flow.request.timestamp_start,
            }
        )

    def _log_flow_response(self, flow: http.HTTPFlow, url: str):
        """Write response event to flow log when enabled."""
        if not self._flow_logging_enabled:
            return

        split = urlsplit(url)
        self._append_flow_event(
            {
                "event": "response",
                "method": flow.request.method,
                "host": split.netloc,
                "path": split.path,
                "url": self._redact_url(url),
                "status": flow.response.status_code,
                "timestamp": flow.response.timestamp_end,
            }
        )

    def request(self, flow: http.HTTPFlow):
        """Log og fang OAuth-relaterede requests, cookies og headers."""
        url = flow.request.pretty_url
        host = flow.request.pretty_host

        # Capture device id as early as possible from request metadata.
        device_id = self._extract_device_id(flow)
        if device_id and FOUND.get("device_id") != device_id:
            FOUND["device_id"] = device_id
            print(f"\n📱 DEVICE ID: {device_id}")
            self._save()

        # Gem alle cookies og headers for Aula-domæner
        if "aula.dk" in host or "login.aula.dk" in host or "api.aula.dk" in host:
            # Convert cookies and headers to plain dicts with string values for JSON serialization
            cookies = {k: str(v) for k, v in flow.request.cookies.items()}
            headers = {k: str(v) for k, v in flow.request.headers.items()}
            log_url = self._redact_url(url)
            spoof_data = {
                "url": log_url,
                "method": flow.request.method,
                "cookies": cookies,
                "headers": headers,
            }
            with open(
                os.path.join(os.path.dirname(__file__), "updated_cookies.json"), "w"
            ) as f:
                json.dump(spoof_data, f, indent=2)
            print(f"\n🍪 Cookies og headers gemt for spoofing: {log_url}")
            self._enable_flow_logging_if_ready()
            self._log_flow_request(flow, url)

        # Log authorize requests (OAuth flow start)
        if "authorize" in url and "aula" in url:
            print(f"\n📤 OAuth authorize: {self._redact_url(url)[:100]}...")
            for param in (
                "scope",
                "code_challenge",
                "redirect_uri",
                "client_id",
                "response_type",
            ):
                val = flow.request.query.get(param, "")
                if val:
                    print(f"   {param}: {val}")

        # Log token requests
        if self._is_oauth_token_endpoint(url):
            print(f"\n📤 Token request: {flow.request.method} {self._redact_url(url)}")
            if flow.request.urlencoded_form:
                for k, v in flow.request.urlencoded_form.items():
                    if k == "code":
                        print(f"   {k}: {v[:30]}...")
                    elif k in ("grant_type", "redirect_uri", "client_id", "scope"):
                        print(f"   {k}: {v}")

    def _extract_device_id(self, flow: http.HTTPFlow):
        """Extract real device id from URL/query/body/headers.

        Important: Do not use generic values like device type (e.g. "Tablet")
        or User-Agent as device id.
        """
        url = flow.request.pretty_url

        # 1) Query string: ...&deviceId=<value>
        match = re.search(r"(?:\?|&)deviceId=([A-Za-z0-9._\-]+)", url)
        if match:
            return match.group(1)

        # 2) Form body: deviceId=<value>
        if flow.request.urlencoded_form:
            form_device_id = flow.request.urlencoded_form.get("deviceId")
            if form_device_id:
                return str(form_device_id)

        # 3) Headers with explicit device-id semantics only
        headers = dict(flow.request.headers.items())
        for key in ("App-Device-Id", "X-Device-Id", "Device-Id"):
            value = headers.get(key)
            if value:
                return str(value)

        return None

    def response(self, flow: http.HTTPFlow):
        """Fang tokens, cookies, headers og deviceId fra responses."""
        url = flow.request.pretty_url
        host = flow.request.pretty_host

        # Udtræk deviceId fra request (ikke device type/User-Agent)
        device_id = self._extract_device_id(flow)
        if device_id and FOUND.get("device_id") != device_id:
            FOUND["device_id"] = device_id
            print(f"\n📱 DEVICE ID: {device_id}")
            self._save()
            self._enable_flow_logging_if_ready()

        # Gem alle cookies og headers for Aula-domæner (også fra response)
        if "aula.dk" in host or "login.aula.dk" in host or "api.aula.dk" in host:
            cookies = {k: str(v) for k, v in flow.response.cookies.items()}
            headers = {k: str(v) for k, v in flow.response.headers.items()}
            log_url = self._redact_url(url)
            spoof_data = {
                "url": log_url,
                "status": flow.response.status_code,
                "cookies": cookies,
                "headers": headers,
                "device_id": device_id,
            }
            with open(
                os.path.join(os.path.dirname(__file__), "updated_cookies.json"), "w"
            ) as f:
                json.dump(spoof_data, f, indent=2)
            print(f"\n🍪 Response-cookies og headers gemt for spoofing: {log_url}")
            self._enable_flow_logging_if_ready()
            self._log_flow_response(flow, url)

        # NOTE: Do NOT capture access_token from Bearer headers.
        # The API gateway uses a different internal token set that won't work
        # for the Home Assistant integration. Only use tokens from the OAuth
        # token endpoint response (login.aula.dk/oidc/token.php).

        # Parse JSON responses
        content_type = flow.response.headers.get("content-type", "")
        if "json" not in content_type and "javascript" not in content_type:
            return

        try:
            body = json.loads(flow.response.get_text())
        except (json.JSONDecodeError, ValueError, TypeError):
            return

        # === PRIMÆRT MÅL: Token endpoint response ===
        if isinstance(body, dict) and (
            "access_token" in body or "refresh_token" in body
        ):
            is_relogin = FOUND.get("access_token") is not None

            print(f"\n{'🎉' * 25}")
            if is_relogin:
                print("🔄 NY LOGIN / TOKEN FORNYELSE OPDAGET!")
            else:
                print("OAUTH2 TOKENS FANGET!")
            print(f"URL: {self._redact_url(url)}")
            print(f"Status: {flow.response.status_code}")
            print("-" * 50)

            for key in (
                "access_token",
                "refresh_token",
                "device_id",
                "expires_in",
                "token_type",
                "id_token",
                "scope",
            ):
                val = body.get(key)
                if val is not None:
                    FOUND[key] = val
                    display = str(val)[:60] + "..." if len(str(val)) > 60 else val
                    print(f"  {key:20s}: {display}")

            FOUND["all_keys"] = list(body.keys())
            print(f"  {'alle_nøgler':20s}: {FOUND['all_keys']}")
            print("-" * 50)

            # On re-login reset flow logging so the new session is captured fresh
            if is_relogin:
                self._flow_logging_enabled = False
                print("\n🔄 Flow logging nulstillet — ny session logges fra start")
                # Truncate the flow log so it reflects the new session only
                open(FLOW_LOG_FILE, "w").close()

            if body.get("refresh_token"):
                print("\n🔑 REFRESH TOKEN:")
                print(f"   {body['refresh_token']}")
                if FOUND.get("device_id"):
                    print("\n📱 DEVICE ID:")
                    print(f"   {FOUND['device_id']}")
                print("\n✅ Kopier refresh token ovenfor til HA config flow!")
                print("✅ Kopier også device id ovenfor til HA config flow!")
                print("   Du kan stoppe proxyen nu (Ctrl+C)")
            else:
                print("\n⚠️  Intet refresh token — OAuth endpoint gav ikke et")

            # Validate the captured tokens immediately against the Aula API.
            # Only use tokens from the OAuth endpoint — not API gateway tokens.
            if self._has_auth_bundle():
                self._validate_captured_tokens()
                self._save_to_ha_store()

            print(f"{'🎉' * 25}")
            self._save()
            self._enable_flow_logging_if_ready()
            return

        # === SEKUNDÆRT: Token i Aula API response (f.eks. widget data) ===
        if isinstance(body, dict) and "Token" in body:
            token = body["Token"]
            if FOUND["access_token"] != token:
                FOUND["access_token"] = token
                print(f"\n🔑 ACCESS TOKEN (fra API response): {str(token)[:60]}...")
                self._save()

    def _validate_captured_tokens(self):
        """Validate the OAuth-captured tokens against the Aula API.

        Uses profiles.getProfileContext which is the first call in the
        post-auth sequence. Auth via ?access_token= URL param (not Bearer header)
        — matches what the iOS app and the HA integration do.
        """
        if _requests is None:
            print("\n⚠️  'requests' ikke installeret — kan ikke validere tokens")
            return

        access_token = FOUND.get("access_token")
        device_id = FOUND.get("device_id")

        if not access_token or not device_id:
            return

        print("\n🔍 Validerer tokens mod Aula API...")

        # Try API versions in order — v23 is current as of 2026
        for version in ("23", "22", "24", "25", "21"):
            url = (
                f"https://www.aula.dk/api/v{version}/"
                f"?method=profiles.getProfileContext"
                f"&portalrole=guardian"
                f"&deviceId={device_id}"
                f"&access_token={access_token}"
            )
            try:
                resp = _requests.get(url, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("status", {}).get("message") == "OK":
                        print(f"✅ Tokens GYLDIGE mod API v{version}!")
                        print("   Disse tokens virker direkte i HA-integrationen.")
                        return
                    else:
                        print(
                            f"⚠️  API v{version} svarede 200 men status var ikke OK: "
                            f"{data.get('status')}"
                        )
                elif resp.status_code == 401:
                    print(f"❌ API v{version}: Token afvist (401 Unauthorized)")
                elif resp.status_code == 403:
                    print(f"⚠️  API v{version}: Adgang nægtet (403) — forkerte params?")
                elif resp.status_code == 410:
                    # Version deprecated — try next silently
                    continue
                else:
                    print(f"⚠️  API v{version}: HTTP {resp.status_code}")
            except Exception as exc:
                print(f"⚠️  API v{version}: Fejl — {exc}")

        print("❌ Tokens kunne IKKE valideres mod nogen API-version.")
        print("   Brug tokens alligevel — de kan stadig virke ved fornyet forsøg.")

    def _save_to_ha_store(self):
        """Write captured tokens directly into the HA aula_tokens store.

        This means you don't need to manually copy tokens into HA after capture.
        The integration will pick them up on next reload.
        """
        import time

        ha_store_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "..",
            ".haconfig",
            ".storage",
            "aula_tokens",
        )
        ha_store_path = os.path.normpath(ha_store_path)

        store = {
            "version": 1,
            "minor_version": 1,
            "key": "aula_tokens",
            "data": {
                "access_token": FOUND.get("access_token"),
                "refresh_token": FOUND.get("refresh_token"),
                "token_expires_at": time.time() + (FOUND.get("expires_in") or 3600),
                "expires_at": time.time() + (FOUND.get("expires_in") or 3600),
                "device_id": FOUND.get("device_id"),
            },
        }

        try:
            with open(ha_store_path, "w") as f:
                json.dump(store, f, indent=2)
            print(f"\n💾 Tokens gemt direkte til HA store: {ha_store_path}")
            print("   Genindlæs Aula-integrationen i HA for at bruge dem.")
        except Exception as exc:
            print(f"\n⚠️  Kunne ikke gemme til HA store: {exc}")
            print(f"   Gem manuelt fra: {SAVE_FILE}")

    def _save(self):
        """Gem fundne tokens til fil."""
        save_data = {k: v for k, v in FOUND.items() if v is not None}
        with open(SAVE_FILE, "w") as f:
            json.dump(save_data, f, indent=2)
        os.chmod(SAVE_FILE, 0o600)


def get_local_ips():
    """Find lokale IP-adresser."""
    result = subprocess.run(["hostname", "-I"], capture_output=True, text=True)
    return [ip for ip in result.stdout.strip().split() if not ip.startswith("127.")]


async def start():
    opts = options.Options(
        listen_host="0.0.0.0",
        listen_port=8080,
        ssl_insecure=True,
        ignore_hosts=[
            r".*nemlog-in\.mitid\.dk",
            r".*mitid\.dk",
            r".*nemlog-in\.dk",
            r".*signicat\.com",
            r".*broker\.unilogin\.dk",
            r".*unilogin\.dk",
            r".*netseidbroker\.dk",
            r".*login\.ewii\.dk",
        ],
    )
    master = DumpMaster(opts)
    master.addons.add(AulaOAuthSniffer())

    ips = get_local_ips()

    print("=" * 60)
    print("🔍 Aula OAuth2 Token Sniffer")
    print("=" * 60)
    print()
    print("📱 OPSÆTNING PÅ IPAD/IPHONE:")
    print()
    print("  TRIN 1 — Sæt proxy:")
    print("    Indstillinger → WiFi → tryk (i) ved dit netværk")
    print("    → HTTP Proxy → Manuel")
    for ip in ips:
        print(f"      Server: {ip}")
    print("      Port: 8080")
    print()
    print("  TRIN 2 — Installer certifikat:")
    print("    Åbn Safari → http://mitm.it")
    print("    → Tryk 'Get mitmproxy-ca-cert.pem' (Apple)")
    print("    → 'Tillad' når den spørger")
    print()
    print("  TRIN 3 — Aktiver certifikat:")
    print("    Indstillinger → Generelt → VPN og enhedshåndtering")
    print("    → mitmproxy → Installer")
    print()
    print("  TRIN 4 — Tillid certifikat:")
    print("    Indstillinger → Generelt → Om")
    print("    → Certifikatbetroelser → Slå 'mitmproxy' TIL ✅")
    print()
    print("  TRIN 5 — Log ind:")
    print("    Åbn Aula-appen → Log ind med MitID")
    print("    Tokens vises her automatisk")
    print()
    print("⚠️  HUSK at fjerne proxy fra iPad bagefter!")
    print("=" * 60)
    print()
    print("Venter på trafik...")
    print()

    await master.run()


def main():
    try:
        asyncio.run(start())
    except KeyboardInterrupt:
        print("\n\n" + "=" * 60)
        print("Sniffer stoppet")
        print("=" * 60)

        if os.path.exists(SAVE_FILE):
            with open(SAVE_FILE) as f:
                data = json.load(f)

            print(f"\nGemte tokens i {SAVE_FILE}:\n")
            for k, v in data.items():
                if k == "all_keys":
                    continue
                display = str(v)[:60] + "..." if v and len(str(v)) > 60 else v
                print(f"  {k:20s}: {display}")

            if data.get("refresh_token"):
                print(f"\n🔑 REFRESH TOKEN:\n   {data['refresh_token']}")
                if data.get("device_id"):
                    print(f"\n📱 DEVICE ID:\n   {data['device_id']}")
                else:
                    print("\n⚠️  Device ID ikke fundet endnu")
            else:
                print("\n⚠️  Intet refresh token fanget")
                print("   Prøv at logge helt ud af Aula-appen og log ind igen")
        else:
            print("\nIngen tokens fanget.")

        print(
            "\n⚠️  Fjern proxy fra iPad: Indstillinger → WiFi → (i) → HTTP Proxy → Slået fra"
        )


if __name__ == "__main__":
    main()
