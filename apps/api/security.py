"""Authentification : hachage des mots de passe et jetons signes.

Implementation volontairement sans dependance externe (bibliotheque standard
uniquement) : PBKDF2-HMAC-SHA256 pour les mots de passe, jeton signe HMAC pour
les sessions. En production Azure, ce module est le point de branchement vers
Entra ID / MSAL — les appels restent identiques cote routeurs.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta

from platform_core.config import settings

_PBKDF2_ROUNDS = 240_000


# --------------------------------------------------------------------------
# Mots de passe
# --------------------------------------------------------------------------


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_hex, digest_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        expected = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds)
        )
        return hmac.compare_digest(expected.hex(), digest_hex)
    except (ValueError, AttributeError):
        return False


# --------------------------------------------------------------------------
# Jetons
# --------------------------------------------------------------------------


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def create_token(user_id: str, role: str, org_id: str, ttl_seconds: int | None = None) -> str:
    """Emet un jeton signe portant l'identite et le role."""
    ttl = ttl_seconds if ttl_seconds is not None else settings.token_ttl_seconds
    payload = {
        "sub": user_id,
        "role": role,
        "org": org_id,
        "exp": int((datetime.now(UTC) + timedelta(seconds=ttl)).timestamp()),
        "jti": secrets.token_hex(8),
    }
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    signature = hmac.new(settings.secret_key.encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64e(signature)}"


class TokenError(Exception):
    """Jeton absent, mal forme, altere ou expire."""


def decode_token(token: str) -> dict:
    try:
        body, signature = token.split(".")
    except ValueError as exc:
        raise TokenError("Jeton mal forme") from exc

    expected = hmac.new(settings.secret_key.encode(), body.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(_b64d(signature), expected):
        raise TokenError("Signature invalide")

    payload = json.loads(_b64d(body))
    if payload.get("exp", 0) < int(datetime.now(UTC).timestamp()):
        raise TokenError("Jeton expire")
    return payload


def generate_access_code(length: int = 10) -> str:
    """Code d'acces d'un lien nominatif CAWI (sans caracteres ambigus)."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))
