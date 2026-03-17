"""SRP-6a key-agreement with AES-GCM channel binding for the MitID protocol."""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
from typing import Tuple

from Crypto.Cipher import AES

# ── Byte / hex helpers ──────────────────────────────────────────────
_BLK = 16


def _i2b(n: int) -> bytes:
    return n.to_bytes((n.bit_length() + 7) // 8, "big")


def _b2i(b: bytes) -> int:
    return int.from_bytes(b, "big")


def _b2h(b: bytes) -> str:
    return binascii.hexlify(b).decode()


def _h2b(h: str) -> bytes:
    return binascii.unhexlify(h)


def _h2i(h: str) -> int:
    return int(h, 16)


def _i2h(n: int) -> str:
    return format(n, "x")


def _pkcs7(data: str) -> str:
    pad_len = _BLK - len(data) % _BLK
    return data + chr(pad_len) * pad_len


# ── AES-GCM primitives ─────────────────────────────────────────────
def _aes_gcm_decrypt(ct_b64: str | bytes, key: bytes) -> bytes:
    raw = base64.b64decode(ct_b64)
    iv, body, tag = raw[:_BLK], raw[_BLK:-_BLK], raw[-_BLK:]
    return AES.new(key, AES.MODE_GCM, iv).decrypt_and_verify(body, tag)


def _aes_gcm_encrypt(plain: bytes, key: bytes) -> bytes:
    iv = os.urandom(_BLK)
    cipher = AES.new(key, AES.MODE_GCM, iv)
    ct, tag = cipher.encrypt_and_digest(plain)
    return iv + ct + tag


# ── Large safe prime used by MitID (2048-bit) ──────────────────────
_N = int(
    "4983313092069490398852700692508795473567251422586244806694940877242664573189"
    "9031929377974469920688180999869580549980123317208691362967809360095087004877"
    "8996242916151585354155671959334695992953115070645733842905892650581784752485"
    "5862259333438239756474464759974189984231409170758360686392625635632084395639"
    "1432298898620415286359069909130872458179594609483453363330867846088230847889"
    "0668986556662101517542469153571152027378626198985136086866906710110895615953"
    "0739641990220546209432953829448997561743719584980402874346226230488627145977"
    "6083898587063918581382006186313852103044299028477021415874705133369054493513"
    "2712208646472514397031305435865048824116713154469234912338133320451563760865"
    "6643608393788598011108539679620836313915590459891513992208387515629240292926"
    "5708943211654826085440301739754527816237918051965463269967905362073591435271"
    "82077625412731080411108775183565594553871817639221414953634530830290393130518"
    "228654795859"
)
_G = 2


class SRPKeyExchange:
    """
    Single-use SRP-6a key exchange.

    Typical sequence::

        kx = SRPKeyExchange()
        public_a = kx.generate_public_value()         # → send A to server
        proof = kx.compute_proof(salt, server_b,       # ← receive (s, B)
                                  password, identity)
        ok = kx.verify_server_proof(server_m2)         # ← receive M2
        ct = kx.encrypt(plaintext)                     # channel-bound msg
    """

    def __init__(self) -> None:
        self._a = int.from_bytes(os.urandom(32), "big") % _N
        self._A = pow(_G, self._a, _N)
        self._session_key: bytes | None = None
        self._m1_hex: str | None = None

    # ── public API ──────────────────────────────────────────────────
    def generate_public_value(self) -> str:
        """Return the client's public ephemeral value *A* as a hex string."""
        return _i2h(self._A)

    def compute_proof(
        self,
        salt_hex: str,
        server_b_hex: str,
        password_hex: str,
        identity: str,
    ) -> str:
        """
        Derive the shared session key and return the client proof *M1*.

        Parameters
        ----------
        salt_hex : str   – server-provided SRP salt (hex).
        server_b_hex : str – server's public ephemeral *B* (hex).
        password_hex : str – pre-processed password material (hex).
        identity : str   – authenticator session id used as SRP identity.
        """
        B = _h2i(server_b_hex)
        if B == 0 or B % _N == 0:
            raise ValueError("Server public value B failed SRP safety check")

        # x = H(salt ‖ password)
        x = _h2i(hashlib.sha256((salt_hex + password_hex).encode()).hexdigest())

        # u = H(A ‖ B)   (both zero-padded to N length)
        n_len = len(_i2b(_N))
        u = _h2i(
            hashlib.sha256(
                _i2b(self._A).rjust(n_len, b"\x00")
                + _i2b(B).rjust(n_len, b"\x00")
            ).hexdigest()
        ) % _N

        # k = H(N ‖ pad(g))
        k = _h2i(
            hashlib.sha256(
                str(_N).encode() + _i2b(_G).rjust(n_len, b"\x00")
            ).hexdigest()
        )

        # S = (B - k·g^x)^(a + u·x)  mod N
        exp = u * x + self._a
        S = pow(B - k * pow(_G, x, _N), exp, _N)

        self._session_key = hashlib.sha256(str(S).encode()).digest()

        # M1 = H( (H(N) xor H(g)) ‖ H(I) ‖ salt ‖ A ‖ B ‖ K )
        hn = _h2i(hashlib.sha256(str(_N).encode()).hexdigest())
        hg = _h2i(hashlib.sha256(str(_G).encode()).hexdigest())
        hi = hashlib.sha256(identity.encode()).hexdigest()

        self._m1_hex = hashlib.sha256(
            (
                str(hn ^ hg)
                + hi
                + salt_hex
                + str(self._A)
                + str(B)
                + _b2h(self._session_key)
            ).encode()
        ).hexdigest()

        return self._m1_hex

    def verify_server_proof(self, server_m2_hex: str) -> bool:
        """Return *True* if the server's proof M2 is valid."""
        expected = hashlib.sha256(
            (
                str(self._A)
                + str(int(self._m1_hex, 16))
                + _b2h(self._session_key)
            ).encode()
        ).hexdigest()
        return expected == server_m2_hex

    def encrypt(self, plain: bytes) -> bytes:
        """AES-GCM encrypt *plain* under the derived session key."""
        return _aes_gcm_encrypt(plain, self._session_key)

    def decrypt(self, ct_b64: str | bytes) -> bytes:
        """AES-GCM decrypt *ct_b64* under the derived session key."""
        return _aes_gcm_decrypt(ct_b64, self._session_key)

    @property
    def derived_key_hex(self) -> str:
        """Hex-encoded session key (K) — needed for flow-value proofs."""
        return _b2h(self._session_key)

    @property
    def derived_key(self) -> bytes:
        return self._session_key


# Re-export for backward compat inside this package
hex_to_bytes = _h2b
bytes_to_hex = _b2h
pkcs7_pad = _pkcs7
