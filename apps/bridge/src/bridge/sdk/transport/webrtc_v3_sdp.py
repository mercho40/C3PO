"""V3 SDP exchange for modern Unitree firmware (Go2 ≥ 1.1.15, G1 ≥ 1.5.1).

Two-step flow on port 9991:

    POST /con_notify
        → base64(JSON{data1, data2})
        - data2 is the encryption mode marker:
            2  → AES-GCM with the firmware-embedded fixed key (legacy V3)
            3  → AES-GCM with the per-device key (cloud-derived, not handled here)
        - data1 (after decrypt) is a string of the form:
            "<10 chars padding>" + <base64 RSA public key> + "<10 chars padding>"
          The 10-char trailing padding also encodes a 5-digit path ending.

    POST /con_ing_<path>
        Body: JSON{
            data1: base64(AES-ECB-encrypt(JSON{sdp, type:"offer", id:"..."}, k)),
            data2: base64(RSA-encrypt(k_hex_ascii, robot_public_key)),
        }
        where `k` is a random 16-byte AES key (we encode it as 32 hex chars).
        → base64(AES-ECB(JSON{sdp, type:"answer"}, k))

All wire constants and the fixed AES-GCM key come from
legion1581/unitree_ui/src/{crypto,connection/local-connector}.ts (MIT).
"""

from __future__ import annotations

import base64
import json
import secrets
from dataclasses import dataclass
from typing import Final

import httpx
import structlog
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

log = structlog.get_logger(__name__)


# AES-GCM key for `data2 === 2` (legacy V3 — Go2 / G1 < 1.5.1).
# Matches `AESGCMUtil.keyBytes` in Unitree's APK.
_CON_NOTIFY_KEY: Final[bytes] = bytes(
    [232, 86, 130, 189, 22, 84, 155, 0, 142, 4, 166, 104, 43, 179, 235, 227],
)

# Path-ending lookup — last 10 chars of decrypted data1 contain alternating
# letters that decode to a 5-digit suffix appended to `/con_ing_`.
_PATH_LOOKUP: Final[str] = "ABCDEFGHIJ"

NEW_FIRMWARE_PORT: Final[int] = 9991


# ---------------------------------------------------------------------------
# Crypto primitives — Python ports of the legion1581 helpers.
# ---------------------------------------------------------------------------


def aes_gcm_decrypt(encrypted_b64: str, key: bytes = _CON_NOTIFY_KEY) -> bytes:
    """Decrypt a base64 AES-GCM payload.

    Firmware layout: `[ciphertext | nonce(12) | tag(16)]`, all base64-encoded.
    Returns the raw plaintext bytes (caller decodes as UTF-8 / JSON as needed).
    """
    raw = base64.b64decode(encrypted_b64)
    if len(raw) < 28:
        raise ValueError(f"AES-GCM payload too short ({len(raw)} bytes)")
    tag = raw[-16:]
    nonce = raw[-28:-16]
    ciphertext = raw[:-28]
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext + tag, associated_data=None)


def aes_ecb_encrypt(plaintext: str, key_ascii: str) -> str:
    """AES-ECB encrypt a UTF-8 string. Key is the 32-hex-char string as
    ASCII bytes (matches legion1581's `aesEncrypt(data, key)` where the
    `key` argument is the hex-encoded random key passed through as text).

    Returns base64-encoded ciphertext.
    """
    key_bytes = key_ascii.encode("ascii")
    data = plaintext.encode("utf-8")
    # PKCS#7 padding
    pad_len = 16 - (len(data) % 16)
    data += bytes([pad_len]) * pad_len
    cipher = Cipher(algorithms.AES(key_bytes), modes.ECB())  # noqa: S305 — required by firmware
    encryptor = cipher.encryptor()
    ct = encryptor.update(data) + encryptor.finalize()
    return base64.b64encode(ct).decode("ascii")


def aes_ecb_decrypt(ciphertext_b64: str, key_ascii: str) -> str:
    """Inverse of `aes_ecb_encrypt`."""
    key_bytes = key_ascii.encode("ascii")
    ct = base64.b64decode(ciphertext_b64)
    cipher = Cipher(algorithms.AES(key_bytes), modes.ECB())  # noqa: S305
    decryptor = cipher.decryptor()
    raw = decryptor.update(ct) + decryptor.finalize()
    # Strip PKCS#7
    pad_len = raw[-1]
    if pad_len < 1 or pad_len > 16:
        raise ValueError(f"AES-ECB: bad PKCS#7 pad len {pad_len}")
    return raw[:-pad_len].decode("utf-8")


def rsa_encrypt_pkcs1(plaintext: str, public_key_b64: str) -> str:
    """RSA-PKCS1v15 encrypt a short string with the robot's public key.

    Robot ships its public key as base64-encoded DER inside `data1`. The
    APK uses PKCS#1 v1.5 padding (not OAEP).
    """
    der = base64.b64decode(public_key_b64)
    pubkey = serialization.load_der_public_key(der)
    ct = pubkey.encrypt(plaintext.encode("utf-8"), padding.PKCS1v15())
    return base64.b64encode(ct).decode("ascii")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def extract_path_ending(data1: str) -> str:
    """Decode the 5-digit path suffix from the trailing 10 chars of `data1`.

    Algorithm (from legion1581 `extractPathEnding`):
        tail = data1[-10:]
        for each pair (even, odd):
            ch = tail[odd]
            digit = "ABCDEFGHIJ".index(ch)
            result += str(digit) if digit >= 0 else "0"
    """
    tail = data1[-10:]
    out = []
    for i in range(0, len(tail), 2):
        if i + 1 >= len(tail):
            break
        ch = tail[i + 1]
        idx = _PATH_LOOKUP.find(ch)
        out.append(str(idx) if idx >= 0 else "0")
    return "".join(out)


def generate_random_aes_key_hex() -> str:
    """16 random bytes encoded as 32 hex chars (lowercase). The hex string
    itself is what gets fed to AES-ECB as the key (ASCII bytes).
    """
    return secrets.token_bytes(16).hex()


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class ConNotifyResponse:
    """Decoded `/con_notify` response."""

    raw_b64: str
    data2: int  # 2 → fixed-key AES-GCM (handled here); 3 → per-device key (not handled)
    data1_encrypted: str


@dataclass
class V3SdpResult:
    """Outcome of a successful V3 SDP exchange."""

    answer_sdp: str
    path_ending: str  # the 5-digit suffix used; useful for logs


# ---------------------------------------------------------------------------
# Top-level exchange
# ---------------------------------------------------------------------------


async def exchange_sdp_v3(
    host: str,
    offer_sdp: str,
    *,
    port: int = NEW_FIRMWARE_PORT,
    sdp_timeout_s: float = 10.0,
    client_id: str = "C3PO",
) -> V3SdpResult:
    """Perform the V3 (encrypted) SDP exchange against firmware ≥ 1.1.15.

    Only `data2 == 2` (fixed firmware key) is supported. `data2 == 3`
    would need a per-device 16-byte key from `device/bind/list` on the
    Unitree cloud API — a separate feature (`AES Key` flow in the
    legion1581 UI). Raises `NotImplementedError` if we see `data2 == 3`.
    """
    base_url = f"http://{host}:{port}"

    async with httpx.AsyncClient(timeout=sdp_timeout_s) as http:
        # Step 1 — con_notify: fetch the per-session public key.
        log.info("go2.v3sdp.con_notify.send", url=base_url + "/con_notify")
        notify_resp = await http.post(f"{base_url}/con_notify")
        notify_resp.raise_for_status()
        notify_b64 = notify_resp.text
        notify_json = json.loads(base64.b64decode(notify_b64).decode("utf-8"))
        notify = ConNotifyResponse(
            raw_b64=notify_b64,
            data2=int(notify_json["data2"]),
            data1_encrypted=notify_json["data1"],
        )
        log.info(
            "go2.v3sdp.con_notify.recv",
            data2=notify.data2,
            data1_bytes=len(notify.data1_encrypted),
        )

        if notify.data2 == 3:
            raise NotImplementedError(
                "Robot requires per-device AES-128 key (data2=3). Use the Unitree "
                "cloud account to derive the per-SN key, then call with key=hexbytes. "
                "Not implemented in this transport yet.",
            )
        if notify.data2 != 2:
            raise RuntimeError(f"Unknown con_notify data2={notify.data2}")

        # Step 2 — decrypt data1 with the fixed key; split off pub key + path.
        data1_plain = aes_gcm_decrypt(notify.data1_encrypted).decode("utf-8")
        if len(data1_plain) < 21:
            raise RuntimeError(f"con_notify data1 too short ({len(data1_plain)} chars)")
        pub_key_b64 = data1_plain[10:-10]
        path_ending = extract_path_ending(data1_plain)
        log.info(
            "go2.v3sdp.pubkey.parsed",
            pub_key_bytes=len(pub_key_b64),
            path_ending=path_ending,
        )

        # Step 3 — generate per-session AES key, wrap SDP offer and key.
        aes_key_hex = generate_random_aes_key_hex()
        offer_payload = json.dumps({"sdp": offer_sdp, "type": "offer", "id": client_id})
        encrypted_offer = aes_ecb_encrypt(offer_payload, aes_key_hex)
        encrypted_key = rsa_encrypt_pkcs1(aes_key_hex, pub_key_b64)

        # Step 4 — POST con_ing_<path>.
        con_ing_url = f"{base_url}/con_ing_{path_ending}"
        body = json.dumps({"data1": encrypted_offer, "data2": encrypted_key})
        log.info("go2.v3sdp.con_ing.send", url=con_ing_url, body_bytes=len(body))
        ing_resp = await http.post(
            con_ing_url,
            content=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        ing_resp.raise_for_status()

        # Step 5 — decrypt response, pull answer SDP.
        encrypted_answer = ing_resp.text
        answer_plaintext = aes_ecb_decrypt(encrypted_answer, aes_key_hex)
        answer = json.loads(answer_plaintext)
        sdp = answer.get("sdp")
        if not isinstance(sdp, str):
            raise RuntimeError(f"unexpected con_ing response shape: {answer!r}")
        log.info("go2.v3sdp.done", answer_bytes=len(sdp))

        return V3SdpResult(answer_sdp=sdp, path_ending=path_ending)
