"""
MitID authenticator — handles APP and TOKEN authentication flows
against the MitID core-client backend via SRP-6a key exchange.
"""

from __future__ import annotations

import base64
import hashlib
import hmac as _hmac
import json
import logging
import threading
import time
from typing import Dict, List, Optional, Tuple

import qrcode
import requests

from .crypto import SRPKeyExchange, bytes_to_hex, hex_to_bytes, pkcs7_pad
from .errors import CredentialError, IdentityProviderError

_LOG = logging.getLogger(__name__)

# ── API base URLs ───────────────────────────────────────────────────
_CORE_URL = "https://www.mitid.dk/mitid-core-client-backend"
_APP_URL = "https://www.mitid.dk/mitid-code-app-auth/v1/authenticator-sessions/web"
_TOKEN_URL = "https://www.mitid.dk/mitid-code-token-auth/v1/authenticator-sessions"
_PWD_URL = "https://www.mitid.dk/mitid-password-auth/v1/authenticator-sessions"

# Combination-id ↔ human label mapping
_COMBO_TO_LABEL = {"S4": "APP", "S3": "APP", "L2": "APP", "S1": "TOKEN"}
_LABEL_TO_COMBO = {"APP": "S3", "TOKEN": "S1"}


class MitIDSession:
    """
    Drives a single MitID authentication session (APP *or* TOKEN).

    Instantiated once per login attempt; not reusable.
    """

    def __init__(
        self,
        core_client_hash: str,
        session_id: str,
        http: requests.Session,
    ) -> None:
        self._http = http
        self._client_hash = core_client_hash
        self._session_id = session_id
        self._qr_lock = threading.Lock()
        self._qr_pair: Optional[Tuple] = None
        self._finalization_id: Optional[str] = None

        # Fetch session metadata
        r = self._http.get(f"{_CORE_URL}/v1/authentication-sessions/{session_id}")
        if r.status_code != 200:
            raise IdentityProviderError(
                f"Could not open MitID session {session_id} (HTTP {r.status_code})"
            )
        meta = r.json()
        self._broker_ctx = meta["brokerSecurityContext"]
        self._sp_name = meta["serviceProviderName"]
        self._ref_header = meta["referenceTextHeader"]
        self._ref_body = meta["referenceTextBody"]

        _LOG.info("MitID session opened for %s", self._sp_name)

    # ── public helpers ──────────────────────────────────────────────
    @property
    def qr_codes(self) -> Optional[Tuple]:
        with self._qr_lock:
            return self._qr_pair

    # ── user identification ─────────────────────────────────────────
    def identify_user(self, username: str) -> Dict[str, str]:
        """Submit *username* and return ``{method_label: display_name}``."""
        r = self._http.put(
            f"{_CORE_URL}/v1/authentication-sessions/{self._session_id}",
            json={"identityClaim": username},
        )
        if r.status_code != 200:
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            code = body.get("errorCode", "")
            if code == "control.identity_not_found":
                raise CredentialError(f"MitID user '{username}' not found")
            if code == "control.authentication_session_not_found":
                raise IdentityProviderError("MitID session expired")
            raise IdentityProviderError(f"MitID identify failed (HTTP {r.status_code})")

        r2 = self._http.post(
            f"{_CORE_URL}/v2/authentication-sessions/{self._session_id}/next",
            json={"combinationId": ""},
        )
        if r2.status_code != 200:
            raise IdentityProviderError(
                f"Could not list authenticators (HTTP {r2.status_code})"
            )
        data = r2.json()
        self._apply_next_authenticator(data)

        combos = data.get("combinations", [])
        return {
            _COMBO_TO_LABEL.get(c["id"], c["id"]): c["combinationItems"][0]["name"]
            for c in combos
        }

    # ── APP flow (push / QR) ────────────────────────────────────────
    def begin_app_auth(self) -> dict:
        """Initiate APP authentication; returns poll metadata."""
        self._switch_method("APP")
        r = self._http.post(
            f"{_APP_URL}/{self._auth_session_id}/init-auth", json={}
        )
        if r.status_code != 200:
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if body.get("errorCode") == "auth.codeapp.authentication.parallel_sessions_detected":
                raise IdentityProviderError("Another MitID app session is already active")
            raise IdentityProviderError(f"APP init failed (HTTP {r.status_code})")
        body = r.json()
        self._poll_url = body["pollUrl"]
        self._ticket = body["ticket"]
        return {"poll_url": self._poll_url}

    def poll_app_status(self) -> dict:
        """Single poll; returns ``{status, message, ...}``."""
        r = self._http.post(self._poll_url, json={"ticket": self._ticket})
        if r.status_code != 200:
            return {"status": "error", "message": "Poll request failed"}
        body = r.json()
        st = body.get("status")

        if st == "timeout":
            return {"status": "waiting", "message": "Waiting…"}

        if st == "channel_validation_otp":
            otp = body["channelBindingValue"]
            return {"status": "otp_shown", "message": f"OTP: {otp}", "otp": otp}

        if st == "channel_validation_tqr":
            cb = body["channelBindingValue"]
            uc = body["updateCount"]
            self._build_qr_pair(cb, uc)
            return {"status": "qr_shown", "message": "Scan QR with MitID app"}

        if st == "channel_verified":
            return {"status": "verified", "message": "Verified – approve in app"}

        if st == "OK" and body.get("confirmation"):
            self._app_response = body["payload"]["response"]
            self._app_sig = body["payload"]["responseSignature"]
            return {"status": "done", "message": "Approved"}

        return {"status": "rejected", "message": "Not accepted"}

    def finish_app_auth(self) -> str:
        """Complete the SRP handshake after a successful APP poll and return finalisation-id."""
        kx = SRPKeyExchange()
        public_a = kx.generate_public_value()

        r = self._http.post(
            f"{_APP_URL}/{self._auth_session_id}/init",
            json={"randomA": {"value": public_a}},
        )
        if r.status_code != 200:
            raise IdentityProviderError(f"APP SRP init failed (HTTP {r.status_code})")
        srv = r.json()

        # password = SHA-256(response ‖ flowKey)
        pwd = hashlib.sha256(
            base64.b64decode(self._app_response)
            + self._flow_key.encode()
        ).hexdigest()

        m1 = kx.compute_proof(
            srv["srpSalt"]["value"], srv["randomB"]["value"],
            pwd, self._auth_session_id,
        )
        fvp = self._flow_value_proof(kx, prefix="flowValues")

        r2 = self._http.post(
            f"{_APP_URL}/{self._auth_session_id}/complete",
            json={"M1": {"value": m1}, "flowValueProof": {"value": fvp}},
        )
        if r2.status_code != 200:
            raise IdentityProviderError(f"APP SRP complete failed (HTTP {r2.status_code})")

        self._finalization_id = r2.json()["authenticationSessionId"]
        _LOG.info("APP auth accepted — ready to finalise")
        return self._finalization_id

    def run_app_auth_blocking(self) -> str:
        """Convenience: begin → poll loop → finish.  Returns finalisation-id."""
        self._switch_method("APP")
        r = self._http.post(
            f"{_APP_URL}/{self._auth_session_id}/init-auth", json={}
        )
        if r.status_code != 200:
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if body.get("errorCode") == "auth.codeapp.authentication.parallel_sessions_detected":
                raise IdentityProviderError("Another MitID app session is already active")
            raise IdentityProviderError(f"APP init failed (HTTP {r.status_code})")

        body = r.json()
        poll_url = body["pollUrl"]
        ticket = body["ticket"]
        _LOG.info("Waiting for MitID app approval…")

        qr_thread = None
        qr_stop = None

        while True:
            r = self._http.post(poll_url, json={"ticket": ticket})
            if r.status_code != 200:
                raise IdentityProviderError("APP poll failed")
            data = r.json()
            st = data.get("status")

            if st == "timeout":
                continue
            if st == "channel_validation_otp":
                _LOG.info("OTP code: %s", data["channelBindingValue"])
                continue
            if st == "channel_validation_tqr":
                self._build_qr_pair(data["channelBindingValue"], data["updateCount"])
                continue
            if st == "channel_verified":
                if qr_thread and qr_thread.is_alive():
                    qr_stop.set()
                    qr_thread.join()
                _LOG.info("QR/OTP verified — waiting for approval")
                continue
            if st == "OK" and data.get("confirmation"):
                break
            raise IdentityProviderError("APP login was not accepted")

        response_b64 = data["payload"]["response"]
        sig_b64 = data["payload"]["responseSignature"]

        # SRP handshake
        t0 = time.monotonic()
        kx = SRPKeyExchange()
        public_a = kx.generate_public_value()
        t1 = time.monotonic()

        r = self._http.post(
            f"{_APP_URL}/{self._auth_session_id}/init",
            json={"randomA": {"value": public_a}},
        )
        if r.status_code != 200:
            raise IdentityProviderError(f"APP SRP init failed (HTTP {r.status_code})")
        t2 = time.monotonic()

        srv = r.json()
        pwd = hashlib.sha256(
            base64.b64decode(response_b64) + self._flow_key.encode()
        ).hexdigest()

        m1 = kx.compute_proof(
            srv["srpSalt"]["value"], srv["randomB"]["value"],
            pwd, self._auth_session_id,
        )
        fvp = self._flow_value_proof(kx, prefix="flowValues")
        t3 = time.monotonic()

        r2 = self._http.post(
            f"{_APP_URL}/{self._auth_session_id}/prove",
            json={"m1": {"value": m1}, "flowValueProof": {"value": fvp}},
        )
        if r2.status_code != 200:
            raise IdentityProviderError(f"APP prove failed (HTTP {r2.status_code})")

        # Verify server M2
        m2 = r2.json()["m2"]["value"]
        if not kx.verify_server_proof(m2):
            raise IdentityProviderError("Server M2 verification failed")

        enc_sig = base64.b64encode(
            kx.encrypt(base64.b64decode(pkcs7_pad(sig_b64)))
        ).decode()
        t4 = time.monotonic()

        elapsed_ms = int((t1 - t0 + t3 - t2 + t4 - t3) * 1000)
        r3 = self._http.post(
            f"{_APP_URL}/{self._auth_session_id}/verify",
            json={"encAuth": enc_sig, "frontEndProcessingTime": elapsed_ms},
        )
        if r3.status_code != 204:
            raise IdentityProviderError(f"APP verify failed (HTTP {r3.status_code})")

        r4 = self._http.post(
            f"{_CORE_URL}/v2/authentication-sessions/{self._session_id}/next",
            json={"combinationId": ""},
        )
        if r4.status_code != 200:
            raise IdentityProviderError(f"APP finalise step failed (HTTP {r4.status_code})")
        result = r4.json()
        if result.get("errors"):
            raise IdentityProviderError("APP login could not be proven")

        self._finalization_id = result["nextSessionId"]
        _LOG.info("APP login accepted")
        return self._finalization_id

    # ── TOKEN + PASSWORD flow ───────────────────────────────────────
    def submit_token_code(self, digits: str) -> None:
        """Submit a 6-digit TOTP code for TOKEN authentication."""
        self._switch_method("TOKEN")

        t0 = time.monotonic()
        kx = SRPKeyExchange()
        public_a = kx.generate_public_value()
        t1 = time.monotonic()

        r = self._http.post(
            f"{_TOKEN_URL}/{self._auth_session_id}/codetoken-init",
            json={"randomA": {"value": public_a}},
        )
        if r.status_code != 200:
            raise IdentityProviderError(f"TOKEN init failed (HTTP {r.status_code})")

        t2 = time.monotonic()
        srv = r.json()

        m1 = kx.compute_proof(
            srv["srpSalt"]["value"],
            srv["randomB"]["value"],
            bytes_to_hex(self._flow_key.encode()),
            self._auth_session_id,
        )

        proof_input = self._raw_flow_proof()
        key_material = "OTP" + digits + kx.derived_key_hex
        key_hash = hashlib.sha256(key_material.encode()).digest()
        fvp = _hmac.new(key_hash, proof_input, hashlib.sha256).hexdigest()

        t3 = time.monotonic()
        elapsed_ms = int((t1 - t0 + t3 - t2) * 1000)

        r2 = self._http.post(
            f"{_TOKEN_URL}/{self._auth_session_id}/codetoken-prove",
            json={
                "m1": {"value": m1},
                "flowValueProof": {"value": fvp},
                "frontEndProcessingTime": elapsed_ms,
            },
        )
        if r2.status_code != 204:
            raise IdentityProviderError(f"TOTP submission failed (HTTP {r2.status_code})")

        r3 = self._http.post(
            f"{_CORE_URL}/v2/authentication-sessions/{self._session_id}/next",
            json={"combinationId": ""},
        )
        if r3.status_code != 200:
            raise IdentityProviderError(f"TOTP advance failed (HTTP {r3.status_code})")
        data = r3.json()

        if data.get("errors"):
            errs = data["errors"]
            if errs[0].get("errorCode") == "TOTP_INVALID":
                raise CredentialError("Invalid TOTP code")
            raise IdentityProviderError(errs[0].get("message", "Unknown TOTP error"))

        nxt = data.get("nextAuthenticator", {})
        if nxt.get("authenticatorType") != "PASSWORD":
            raise IdentityProviderError("Expected PASSWORD step after TOTP")

        self._apply_next_authenticator(data)
        _LOG.info("TOTP accepted — password step required next")

    def submit_password(self, password: str) -> None:
        """Submit the MitID password (called after :meth:`submit_token_code`)."""
        if self._auth_type != "PASSWORD":
            raise CredentialError("Password step not active; submit token code first")

        t0 = time.monotonic()
        kx = SRPKeyExchange()
        public_a = kx.generate_public_value()
        t1 = time.monotonic()

        r = self._http.post(
            f"{_PWD_URL}/{self._auth_session_id}/init",
            json={"randomA": {"value": public_a}},
        )
        if r.status_code != 200:
            raise IdentityProviderError(f"Password init failed (HTTP {r.status_code})")

        t2 = time.monotonic()
        srv = r.json()

        # PBKDF2-derive the password
        derived_pwd = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode(),
            hex_to_bytes(srv["pbkdf2Salt"]["value"]),
            20000,
            32,
        ).hex()

        m1 = kx.compute_proof(
            srv["srpSalt"]["value"],
            srv["randomB"]["value"],
            derived_pwd,
            self._auth_session_id,
        )

        proof_input = self._raw_flow_proof()
        key_material = "flowValues" + kx.derived_key_hex
        key_hash = hashlib.sha256(key_material.encode()).digest()
        fvp = _hmac.new(key_hash, proof_input, hashlib.sha256).hexdigest()

        t3 = time.monotonic()
        elapsed_ms = int((t1 - t0 + t3 - t2) * 1000)

        r2 = self._http.post(
            f"{_PWD_URL}/{self._auth_session_id}/password-prove",
            json={
                "m1": {"value": m1},
                "flowValueProof": {"value": fvp},
                "frontEndProcessingTime": elapsed_ms,
            },
        )
        if r2.status_code != 204:
            raise IdentityProviderError(f"Password submit failed (HTTP {r2.status_code})")

        r3 = self._http.post(
            f"{_CORE_URL}/v2/authentication-sessions/{self._session_id}/next",
            json={"combinationId": ""},
        )
        if r3.status_code != 200:
            raise IdentityProviderError(f"Password advance failed (HTTP {r3.status_code})")
        data = r3.json()

        if data.get("errors"):
            errs = data["errors"]
            ec = errs[0].get("errorCode", "")
            msg = errs[0].get("message", "Unknown error")
            if ec == "PASSWORD_INVALID":
                raise CredentialError("Invalid MitID password")
            raise IdentityProviderError(msg)

        self._finalization_id = data["nextSessionId"]
        _LOG.info("Password accepted — ready to finalise")

    # ── finalisation ────────────────────────────────────────────────
    def get_authorisation_code(self) -> str:
        """Exchange the finalised session for an authorisation code."""
        if not self._finalization_id:
            raise IdentityProviderError("No completed authentication to finalise")
        r = self._http.put(
            f"{_CORE_URL}/v1/authentication-sessions/{self._finalization_id}/finalization",
        )
        if r.status_code != 200:
            raise IdentityProviderError(
                f"Finalisation failed (HTTP {r.status_code})"
            )
        return r.json()["authorizationCode"]

    # ── internal helpers ────────────────────────────────────────────
    def _switch_method(self, label: str) -> None:
        if self._auth_type == label:
            return
        combo = _LABEL_TO_COMBO.get(label)
        if not combo:
            raise CredentialError(f"Unknown method: {label}")

        r = self._http.post(
            f"{_CORE_URL}/v2/authentication-sessions/{self._session_id}/next",
            json={"combinationId": combo},
        )
        if r.status_code != 200:
            raise IdentityProviderError(f"Method switch failed (HTTP {r.status_code})")
        data = r.json()
        if data.get("errors"):
            raise IdentityProviderError(
                data["errors"][0].get("userMessage", {}).get("text", {}).get("text", "Switch failed")
            )
        self._apply_next_authenticator(data)
        if self._auth_type != label:
            raise IdentityProviderError(f"Expected {label} but got {self._auth_type}")

    def _apply_next_authenticator(self, payload: dict) -> None:
        nxt = payload.get("nextAuthenticator", {})
        self._auth_type = nxt.get("authenticatorType", "")
        self._flow_key = nxt.get("authenticatorSessionFlowKey", "")
        self._eafe_hash = nxt.get("eafeHash", "")
        self._auth_session_id = nxt.get("authenticatorSessionId", "")

    def _raw_flow_proof(self) -> bytes:
        ctx_hash = hashlib.sha256(self._broker_ctx.encode()).hexdigest()
        hdr_b64 = base64.b64encode(self._ref_header.encode()).decode()
        body_b64 = base64.b64encode(self._ref_body.encode()).decode()
        sp_b64 = base64.b64encode(self._sp_name.encode()).decode()
        return (
            f"{self._auth_session_id},{self._flow_key},{self._client_hash},"
            f"{self._eafe_hash},{ctx_hash},{hdr_b64},{body_b64},{sp_b64}"
        ).encode()

    def _flow_value_proof(self, kx: SRPKeyExchange, *, prefix: str) -> str:
        proof_input = self._raw_flow_proof()
        key_material = prefix + kx.derived_key_hex
        key_hash = hashlib.sha256(key_material.encode()).hexdigest()
        return base64.b64encode(
            _hmac.new(hex_to_bytes(key_hash), proof_input, hashlib.sha256).digest()
        ).decode()

    def _build_qr_pair(self, channel_val: str, update_count: int) -> None:
        mid = len(channel_val) // 2
        q1 = qrcode.QRCode(border=1)
        q1.add_data(json.dumps(
            {"v": 1, "p": 1, "t": 2, "h": channel_val[:mid], "uc": update_count},
            separators=(",", ":"),
        ))
        q1.make()
        q2 = qrcode.QRCode(border=1)
        q2.add_data(json.dumps(
            {"v": 1, "p": 2, "t": 2, "h": channel_val[mid:], "uc": update_count},
            separators=(",", ":"),
        ))
        q2.make()
        with self._qr_lock:
            self._qr_pair = (q1, q2)
