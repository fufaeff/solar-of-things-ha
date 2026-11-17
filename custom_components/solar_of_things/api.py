"""API client for Solar of Things (solar.siseli.com).

Authentication strategy
─────────────────────────────────────────────────────────────────────────────
The Siseli portal uses a dual-token system.  Login requires the IOT Open
Platform signing scheme discovered via JS bundle analysis (umi.js):

  Endpoint : POST https://test.solar.siseli.com/apis/login/account
  Signed   : yes — IOT-Open-AppID, IOT-Open-Nonce, IOT-Open-Body-Hash,
                    IOT-Open-Sign headers

Signing algorithm (reverse-engineered from portal umi.js):
  1. Build a dict of signing headers:
       {"IOT-Open-AppID": appId,
        "IOT-Open-Body-Hash": sha256(body_bytes).lower(),
        "IOT-Open-Nonce": random_32_char_hex}
  2. Sort keys alphabetically, join as k1=v1&k2=v2 (no URL-encoding).
  3. base64-encode the resulting string.
  4. HMAC-SHA256(b64_str, decrypted_app_secret)  [bytes result]
  5. MD5(hmac_bytes).hexdigest()  → IOT-Open-Sign value

App secret decryption (qe() in umi.js):
  key = MD5(appId).lower()[:16]  treated as ASCII bytes  (16 bytes = AES-128)
  iv  = MD5(appId).lower()[16:]  treated as ASCII bytes  (16 bytes)
  AES-128-CBC-ZeroPadding decrypt of base64(encrypted_secret)

After successful login the server returns an accessToken (used as
IOT-Token header for data requests) and a refreshToken.

This class supports three auth modes, tried in priority order:

  1. User-ID + password  (recommended)
     • Call login() at startup → stores both tokens in memory.
     • _ensure_token_valid() checks expiry before every API call and
       proactively refreshes (TOKEN_REFRESH_LEAD_SECONDS = 5 min before
       expiry, mirroring the portal JS behaviour).
     • If refresh fails, raises TokenExpiredError so the HA integration
       can trigger a re-auth flow.

  2. Token-pair (accessToken + refreshToken) without password
     • User pastes both tokens from DevTools.
     • Same proactive-refresh logic; re-auth needed when refreshToken
       expires.

  3. Legacy IOT-token only (backwards compatibility)
     • No refresh possible; raises TokenExpiredError on 401 so HA can
       prompt the user to re-enter a fresh token.

Usage in Home Assistant
─────────────────────────────────────────────────────────────────────────────
  api = SolarOfThingsAPI(
      user_id="myaccount",
      password="secret",          # or omit and pass iot_token=
      time_zone="Asia/Manila",
      on_token_refreshed=_save_tokens_callback,
  )
  await hass.async_add_executor_job(api.login)
  data = await hass.async_add_executor_job(api.fetch_latest_data, device_id)
"""

from __future__ import annotations

import base64
import hashlib
import hmac as _hmac
import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import requests

try:
    from Crypto.Cipher import AES as _AES

    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

from .const import (
    API_AUTH_BASE_URL,
    API_BASE_URL,
    API_DEVICE_LIST,
    API_LATEST_STATE,
    API_LOGIN,
    API_MONTHLY_SUMMARY,
    API_SETTINGS_GET,
    API_SETTINGS_SET,
    API_TIME_SERIES,
    BATTERY_TYPE_MAP,
    CHARGER_PRIORITY_MAP,
    GRID_CONNECTION_PROTOCOL_TYPE_MAP,
    IOT_APP_ID,
    IOT_APP_SECRET_ENC,
    MONTHLY_SUMMARY_KEYS,
    OUTPUT_MODE_MAP,
    RATED_FREQUENCY_MAP,
    RATED_VOLTAGE_MAP,
    TIME_SERIES_MAX_POINTS,
    TIME_SERIES_WINDOW_MINUTES,
    TOKEN_INVALID_CODES,
    TOKEN_REFRESH_LEAD_SECONDS,
)
from .const import (
    API_REFRESH_TOKEN as API_REFRESH_TOKEN_ENDPOINT,
)

_LOGGER = logging.getLogger(__name__)

_DEFAULT_TZ = "Asia/Manila"


# ──────────────────────────────────────────────────────────────────────────────
# Custom exceptions
# ──────────────────────────────────────────────────────────────────────────────


class TokenExpiredError(Exception):
    """Raised when the access token has expired and cannot be refreshed.

    The HA integration should catch this and call
    config_entry.async_start_reauth() so the user can re-enter credentials.
    """


class AuthenticationError(Exception):
    """Raised when login credentials are rejected by the server."""


def _is_ok_code(code: Any) -> bool:
    """Return True for a successful business-level response code."""
    return code in (0, None, "0")


def _is_token_invalid_code(code: Any) -> bool:
    """Return True if a business-level response code means 'token invalid'."""
    try:
        return int(code) in TOKEN_INVALID_CODES
    except (TypeError, ValueError):
        return False


def _parse_monthly_properties(payload: dict[str, Any], month_key: str) -> dict[str, float]:
    """Extract per-property values for one month from a yearly summary payload.

    Response shape::

        {"properties": [
            {"property": {"key": "pvGeneratedEnergy", ...},
             "timePoints": [{"time": "2026-11", "value": 201.06,
                             "isRealValue": true}, ...]},
            ...]}

    A property that is present but has no real value for the month is 0.0;
    properties absent from the response are left out (sensor → unknown).
    """
    values: dict[str, float] = {}
    properties = payload.get("properties")
    if not isinstance(properties, list):
        return values

    for prop in properties:
        if not isinstance(prop, dict):
            continue
        key = (prop.get("property") or {}).get("key")
        if not key:
            continue
        value = 0.0
        for point in prop.get("timePoints") or []:
            if point.get("time") == month_key and point.get("isRealValue", True):
                try:
                    value = float(point.get("value") or 0)
                except (TypeError, ValueError):
                    value = 0.0
                break
        values[key] = value
    return values


def _is_auth_failure(resp: requests.Response) -> bool:
    """Return True if a data-endpoint response signals an invalid/expired token."""
    if resp.status_code == 401:
        return True
    if resp.status_code != 200:
        return False
    try:
        data = resp.json()
    except ValueError:
        return False
    return isinstance(data, dict) and _is_token_invalid_code(data.get("code"))


# ──────────────────────────────────────────────────────────────────────────────
# Signing helpers  (reverse-engineered from portal umi.js)
# ──────────────────────────────────────────────────────────────────────────────


def _decrypt_app_secret(app_id: str, encrypted_b64: str) -> str:
    """AES-128-CBC decrypt the embedded app secret.

    Key derivation mirrors the portal qe() function:
      key = MD5(app_id).lower()[:16]  as ASCII bytes
      iv  = MD5(app_id).lower()[16:]  as ASCII bytes
    The ciphertext is the base64-decoded encrypted_b64 value.
    """
    if not _CRYPTO_AVAILABLE:
        raise RuntimeError(
            "pycryptodome is not installed. "
            "Add 'pycryptodome' to the integration requirements."
        )
    md5_hex = hashlib.md5(app_id.encode("utf-8")).hexdigest()
    key = md5_hex[:16].encode("ascii")  # 16 bytes — AES-128
    iv = md5_hex[16:].encode("ascii")  # 16 bytes — CBC IV
    ciphertext = base64.b64decode(encrypted_b64)
    cipher = _AES.new(key, _AES.MODE_CBC, iv)
    plaintext = cipher.decrypt(ciphertext).rstrip(b"\x00")
    return plaintext.decode("utf-8")


def _compute_iot_sign(app_id: str, nonce: str, body_hash: str, secret: str) -> str:
    """Compute the IOT-Open-Sign header value.

    Algorithm (Ye() in portal umi.js):
      1. Sort signing headers alphabetically by key.
      2. qs.stringify → "IOT-Open-AppID=X&IOT-Open-Body-Hash=Y&IOT-Open-Nonce=Z"
      3. Base64-encode the qs string.
      4. HMAC-SHA256(b64_qs, secret) → raw bytes.
      5. MD5(hmac_bytes).hexdigest() → sign value.
    """
    sign_headers = {
        "IOT-Open-AppID": app_id,
        "IOT-Open-Body-Hash": body_hash,
        "IOT-Open-Nonce": nonce,
    }
    qs_str = "&".join(f"{k}={sign_headers[k]}" for k in sorted(sign_headers.keys()))
    b64_qs = base64.b64encode(qs_str.encode("utf-8")).decode("ascii")
    hmac_bytes = _hmac.new(
        secret.encode("utf-8"), b64_qs.encode("utf-8"), hashlib.sha256
    ).digest()
    return hashlib.md5(hmac_bytes).hexdigest()


def _make_signed_headers(
    body_bytes: bytes, extra: dict[str, str] | None = None
) -> dict[str, str]:
    """Build the complete set of IOT Open Platform signed request headers.

    Returns headers suitable for POST to API_AUTH_BASE_URL endpoints.
    """
    secret = _decrypt_app_secret(IOT_APP_ID, IOT_APP_SECRET_ENC)
    nonce = os.urandom(16).hex()  # 32-char hex nonce
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    sign = _compute_iot_sign(IOT_APP_ID, nonce, body_hash, secret)

    headers: dict[str, str] = {
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8",
        "Origin": "https://solar.siseli.com",
        "Referer": "https://solar.siseli.com/",
        "IOT-Open-AppID": IOT_APP_ID,
        "IOT-Open-Nonce": nonce,
        "IOT-Open-Body-Hash": body_hash,
        "IOT-Open-Sign": sign,
    }
    if extra:
        headers.update(extra)
    return headers


# ──────────────────────────────────────────────────────────────────────────────
# Helper: parse Siseli ISO expiry strings safely
# ──────────────────────────────────────────────────────────────────────────────


def _parse_expiry(value: str | None) -> datetime | None:
    """Return an aware UTC datetime from an ISO-8601 string, or None."""
    if not value:
        return None
    try:
        # Python 3.7+ fromisoformat doesn't handle trailing 'Z'
        cleaned = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Main API client
# ──────────────────────────────────────────────────────────────────────────────


class SolarOfThingsAPI:
    """Solar of Things API wrapper with automatic token refresh.

    Parameters
    ----------
    user_id:            Siseli portal login account / user-ID (preferred auth method).
    password:           Siseli portal password.
    iot_token:          Legacy/manual IOT-Token (used when user_id/password absent).
    refresh_token:      Stored refresh token (persisted between HA restarts).
    access_token_expires: ISO-8601 string of current access-token expiry.
    refresh_token_expires: ISO-8601 string of current refresh-token expiry.
    time_zone:          IOT-Time-Zone header value.
    on_token_refreshed: Optional callback(access_token, refresh_token,
                        access_expires_iso, refresh_expires_iso) called after
                        every successful token refresh so the HA entry can
                        persist the new tokens without restarting.
    """

    def __init__(
        self,
        *,
        user_id: str | None = None,
        password: str | None = None,
        iot_token: str | None = None,
        refresh_token: str | None = None,
        access_token_expires: str | None = None,
        refresh_token_expires: str | None = None,
        time_zone: str | None = None,
        on_token_refreshed: Callable[[str, str, str, str], None] | None = None,
    ) -> None:
        self._user_id = user_id
        self._password = password
        self._time_zone = time_zone or _DEFAULT_TZ
        self._on_token_refreshed = on_token_refreshed

        # Token state
        self._access_token: str = iot_token or ""
        self._refresh_token: str = refresh_token or ""
        self._access_expires: datetime | None = _parse_expiry(access_token_expires)
        self._refresh_expires: datetime | None = _parse_expiry(refresh_token_expires)

        # Thread-safety for concurrent refresh calls
        self._refresh_lock = threading.Lock()

        # Determine auth mode
        if user_id and password:
            self._auth_mode = "password"
        elif iot_token and refresh_token:
            self._auth_mode = "token_pair"
        elif iot_token:
            self._auth_mode = "legacy"
        else:
            raise ValueError("Provide either (user_id + password) or iot_token.")

        # HTTP session (headers updated after every token refresh)
        self.session = requests.Session()
        self._apply_token_headers()

    # ─── Session headers ───────────────────────────────────────────────────────

    def _apply_token_headers(self) -> None:
        """Write the current access token into the session headers."""
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json; charset=utf-8",
                "IOT-Token": self._access_token,
                "IOT-Time-Zone": self._time_zone,
                "Origin": "https://solar.siseli.com",
                "Referer": "https://solar.siseli.com/",
                "User-Agent": (
                    "HomeAssistant-SolarOfThings/2.5.1 "
                    "(+https://github.com/fufaeff/solar-of-things-ha)"
                ),
            }
        )

    # ─── Public auth helpers ───────────────────────────────────────────────────

    def login(self) -> None:
        """Authenticate with user-ID + password and store the resulting tokens.

        Uses the IOT Open Platform signed request format discovered from the
        portal JS bundle.  The login endpoint is:
          POST https://solar.siseli.com/apis/login/account

        The password is sent as MD5(plaintext_password) lowercase hex — this
        is how the portal processes it before transmitting.

        Raises AuthenticationError on bad credentials, or requests.RequestException
        on network failure.  Safe to call from a background thread.
        """
        if self._auth_mode not in ("password",):
            raise RuntimeError("login() requires user_id + password auth mode.")

        _LOGGER.debug("SolarOfThings: logging in as %s", self._user_id)

        import json as _json

        # The portal sends the password as MD5(plaintext_password) lowercase hex.
        # Sending plaintext returns code 7 "password error" even with valid creds.
        password_md5 = hashlib.md5(self._password.encode("utf-8")).hexdigest()
        payload = {
            "account": self._user_id,
            "password": password_md5,
        }
        body_bytes = _json.dumps(payload, separators=(",", ":")).encode("utf-8")

        headers = _make_signed_headers(body_bytes)

        resp = requests.post(
            f"{API_AUTH_BASE_URL}{API_LOGIN}",
            data=body_bytes,
            headers=headers,
            timeout=30,
        )
        try:
            resp.raise_for_status()
        except requests.HTTPError as http_err:
            try:
                err_data = resp.json()
                msg = err_data.get("message") or err_data.get("msg")
                code = err_data.get("code")
                if msg:
                    _LOGGER.warning("SolarOfThings login failed: code=%s, msg=%s", code, msg)
                    raise AuthenticationError(
                        f"Login failed (HTTP {resp.status_code}, code {code}): {msg}"
                    ) from http_err
            except AuthenticationError:
                raise
            except Exception:
                pass
            raise

        data = resp.json()

        if data.get("code") not in (0, None, "0"):
            msg = data.get("message") or data.get("msg") or str(data)
            _LOGGER.warning("SolarOfThings login rejected: code=%s, msg=%s", data.get("code"), msg)
            raise AuthenticationError(f"Login failed: {msg}")

        self._store_tokens(data.get("data") or data)

    def refresh_access_token(self) -> None:
        """Use the stored refresh token to obtain a new access token.

        Raises TokenExpiredError if the refresh token is also expired or invalid.
        """
        if not self._refresh_token:
            raise TokenExpiredError("No refresh token available.")

        _LOGGER.debug("SolarOfThings: refreshing access token")

        import json as _json

        payload = {"refreshToken": self._refresh_token}
        body_bytes = _json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "Origin": "https://solar.siseli.com",
            "Referer": "https://solar.siseli.com/",
        }

        url = f"{API_AUTH_BASE_URL}{API_REFRESH_TOKEN_ENDPOINT}"
        resp = requests.post(url, data=body_bytes, headers=headers, timeout=30)

        # Fallback to alternative endpoint if 404 (e.g. if path prefix changed)
        if resp.status_code == 404:
            alt_endpoint = (
                "/login/refresh/access/token"
                if API_REFRESH_TOKEN_ENDPOINT.startswith("/apis")
                else "/apis/login/refresh/access/token"
            )
            _LOGGER.debug(
                "SolarOfThings: refresh endpoint %s 404, trying %s",
                API_REFRESH_TOKEN_ENDPOINT,
                alt_endpoint,
            )
            resp = requests.post(
                f"{API_AUTH_BASE_URL}{alt_endpoint}",
                data=body_bytes,
                headers=headers,
                timeout=30,
            )

        # Fallback to signed request if unsigned was rejected
        if resp.status_code == 403 or _is_auth_failure(resp):
            try:
                signed_headers = _make_signed_headers(body_bytes)
                resp = requests.post(
                    url,
                    data=body_bytes,
                    headers=signed_headers,
                    timeout=30,
                )
            except Exception as sign_err:
                _LOGGER.debug("SolarOfThings: signed refresh attempt failed: %s", sign_err)

        if resp.status_code in (401, 403):
            raise TokenExpiredError(
                "Refresh token rejected by server (expired or invalid)."
            )

        resp.raise_for_status()
        data = resp.json()

        if data.get("code") not in (0, None, "0"):
            raise TokenExpiredError(
                f"Refresh failed: code={data.get('code')} message={data.get('message')}"
            )

        self._store_tokens(data.get("data") or data)

    # ─── Internal token management ─────────────────────────────────────────────

    def _store_tokens(self, payload: dict[str, Any]) -> None:
        """Extract tokens from a login/refresh response payload and persist them."""
        access = (
            payload.get("accessToken")
            or payload.get("iotToken")
            or payload.get("token")
            or ""
        )
        refresh = payload.get("refreshToken") or ""
        access_exp = (
            payload.get("accessTokenWillExpiredAt")
            or payload.get("accessTokenExpiredAt")
            or ""
        )
        refresh_exp = (
            payload.get("refreshTokenWillExpiredAt")
            or payload.get("refreshTokenExpiredAt")
            or ""
        )

        if not access:
            raise AuthenticationError(
                f"Login/refresh response did not contain an access token. "
                f"Keys received: {list(payload.keys())}"
            )

        self._access_token = access
        self._refresh_token = refresh
        self._access_expires = _parse_expiry(access_exp)
        self._refresh_expires = _parse_expiry(refresh_exp)

        # Update session header immediately
        self._apply_token_headers()

        _LOGGER.debug(
            "SolarOfThings: token updated, expires=%s",
            self._access_expires.isoformat() if self._access_expires else "unknown",
        )

        # Notify the HA integration so it can persist the new token state
        if self._on_token_refreshed:
            try:
                self._on_token_refreshed(
                    self._access_token,
                    self._refresh_token,
                    self._access_expires.isoformat() if self._access_expires else "",
                    self._refresh_expires.isoformat() if self._refresh_expires else "",
                )
            except Exception as cb_err:  # pragma: no cover
                _LOGGER.warning("Token-refresh callback raised: %s", cb_err)

    def _token_needs_refresh(self) -> bool:
        """Return True if the access token is absent or about to expire."""
        if not self._access_token:
            return True
        if self._access_expires is None:
            # Unknown expiry: only refresh if we already have a refresh token
            return bool(self._refresh_token)
        lead = timedelta(seconds=TOKEN_REFRESH_LEAD_SECONDS)
        return datetime.now(UTC) >= (self._access_expires - lead)

    def _ensure_token_valid(
        self, *, force: bool = False, stale_token: str | None = None
    ) -> None:
        """Refresh the access token if needed (or unconditionally with force=True).

        force=True is used after the server rejected ``stale_token``; if another
        thread has already replaced that token in the meantime, nothing is done.

        Thread-safe: uses a lock so parallel coordinator updates don't
        trigger multiple simultaneous refresh calls.

        Raises TokenExpiredError when all refresh strategies are exhausted.
        """
        if not force and not self._token_needs_refresh():
            return

        with self._refresh_lock:
            # Double-check inside the lock (another thread may have refreshed)
            if force:
                if stale_token is not None and self._access_token != stale_token:
                    return
            elif not self._token_needs_refresh():
                return

            _LOGGER.info("SolarOfThings: access token expiring; attempting refresh")

            # Strategy 1: use refresh token
            if self._refresh_token:
                try:
                    self.refresh_access_token()
                    return
                except TokenExpiredError:
                    _LOGGER.warning(
                        "SolarOfThings: refresh token expired/invalid; "
                        "attempting re-login"
                    )
                except Exception as err:
                    _LOGGER.warning(
                        "SolarOfThings: token refresh request failed (%s); attempting re-login",
                        err,
                    )

            # Strategy 2: re-login with stored credentials
            if self._auth_mode == "password" and self._user_id and self._password:
                try:
                    self.login()
                    return
                except AuthenticationError as err:
                    raise TokenExpiredError(
                        f"Re-login failed (credentials rejected): {err}"
                    ) from err
                except Exception as err:
                    raise TokenExpiredError(
                        f"Re-login failed (network error): {err}"
                    ) from err

            # Strategy 3: nothing left — tell HA to trigger re-auth
            raise TokenExpiredError(
                "Access token expired and no refresh strategy succeeded. "
                "Please re-authenticate in Home Assistant."
            )

    # ─── Internal HTTP helper ──────────────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        """Call a data endpoint and return the decoded JSON body.

        If the server rejects the token (HTTP 401 or a TOKEN_INVALID_CODES
        business code) the token is force-refreshed and the request retried
        once.  If that is impossible or the retry is rejected as well,
        TokenExpiredError is raised so the integration can start re-auth.
        Uses API_BASE_URL (solar.siseli.com) — not the auth base URL.
        """
        self._ensure_token_valid()
        url = f"{API_BASE_URL}{path}"
        used_token = self._access_token

        resp = self.session.request(
            method, url, params=params, json=json, timeout=timeout
        )

        if _is_auth_failure(resp):
            _LOGGER.warning(
                "SolarOfThings: token rejected by %s (HTTP %s); forcing refresh",
                path,
                resp.status_code,
            )
            self._ensure_token_valid(force=True, stale_token=used_token)
            resp = self.session.request(
                method, url, params=params, json=json, timeout=timeout
            )
            if _is_auth_failure(resp):
                raise TokenExpiredError(
                    f"Token rejected by {path} even after refresh "
                    f"(HTTP {resp.status_code})."
                )

        resp.raise_for_status()
        return resp.json()

    def _post(
        self, path: str, payload: dict[str, Any], *, timeout: int = 30
    ) -> dict[str, Any]:
        """POST a JSON payload to a data endpoint (see _request)."""
        return self._request("POST", path, json=payload, timeout=timeout)

    # ─── Public properties (for persistence in HA config entry) ───────────────

    @property
    def access_token(self) -> str:
        return self._access_token

    @property
    def refresh_token(self) -> str:
        return self._refresh_token

    @property
    def access_token_expires_iso(self) -> str:
        return self._access_expires.isoformat() if self._access_expires else ""

    @property
    def refresh_token_expires_iso(self) -> str:
        return self._refresh_expires.isoformat() if self._refresh_expires else ""

    # ─── Time helpers ──────────────────────────────────────────────────────────

    def _now(self) -> datetime:
        if ZoneInfo:
            try:
                return datetime.now(tz=ZoneInfo(self._time_zone))
            except Exception:
                return datetime.now()
        return datetime.now()

    def _format_time(self, dt: datetime) -> str:
        if ZoneInfo:
            try:
                dt = dt.astimezone(ZoneInfo(self._time_zone))
            except Exception:
                pass
        return dt.replace(microsecond=0).isoformat()

    # ─── Station → device listing ──────────────────────────────────────────────

    def list_devices(
        self, station_id: str | None = None, page_size: int = 50
    ) -> list[dict[str, Any]]:
        """Return all devices under a station or under the entire account (paginated)."""
        devices: list[dict[str, Any]] = []
        page = 1
        total: int | None = None

        while True:
            payload: dict[str, Any] = {"page": page, "count": page_size}
            if station_id:
                payload["stationId"] = station_id

            data = self._post(API_DEVICE_LIST, payload)

            if not _is_ok_code(data.get("code")):
                raise RuntimeError(
                    f"Device list error code={data.get('code')} "
                    f"message={data.get('message')}"
                )

            d = data.get("data") or {}
            total = d.get("total", total)
            batch = d.get("list") or []
            if not isinstance(batch, list):
                batch = []

            devices.extend(batch)

            if total is None:
                if len(batch) < page_size:
                    break
            else:
                if len(devices) >= int(total):
                    break
            if not batch:
                break
            page += 1

        return devices

    def discover_stations(self) -> list[dict[str, Any]]:
        """Discover all stations and their devices for this account.

        Returns a list of dicts:
          [{"station_id": str, "station_name": str, "devices": list[dict]}, ...]
        """
        all_devices = self.list_devices()
        stations_map: dict[str, dict[str, Any]] = {}

        for dev in all_devices:
            st_id = str(dev.get("stationId") or dev.get("station_id") or "").strip()
            st_name = str(
                dev.get("stationName")
                or dev.get("station_name")
                or f"Station {st_id}"
            ).strip()
            if not st_id:
                continue
            if st_id not in stations_map:
                stations_map[st_id] = {
                    "station_id": st_id,
                    "station_name": st_name,
                    "devices": [],
                }
            stations_map[st_id]["devices"].append(dev)

        return list(stations_map.values())

    # ─── Time-series (per device) ──────────────────────────────────────────────

    def fetch_latest_data(self, device_id: str) -> dict[str, Any]:
        """Fetch the latest readings for a device.

        Only the newest sample of each key is used, so a short window is
        requested to keep the per-minute polling cheap for the cloud.
        """
        end_time = self._now()
        start_time = end_time - timedelta(minutes=TIME_SERIES_WINDOW_MINUTES)

        keys = [
            "pvInputPower",
            "pv2InputPower",
            "acOutputActivePower",
            "batteryDischargeCurrent",
            "batteryChargingCurrent",
            "batteryVoltage",
            "feedInPower",
            "batterySOC",
        ]

        data = self._post(
            API_TIME_SERIES,
            {
                "deviceId": device_id,
                "count": TIME_SERIES_MAX_POINTS,
                "page": 1,
                "fromTime": self._format_time(start_time),
                "toTime": self._format_time(end_time),
                "orderByTimeAsc": True,
                "keys": keys,
            },
        )

        if not _is_ok_code(data.get("code")):
            raise RuntimeError(
                f"Timeseries error code={data.get('code')} "
                f"message={data.get('message')}"
            )

        payload_data = (data.get("data") or {}).get("payload") or {}
        fields = payload_data.get("fields") or {}

        latest_values: dict[str, Any] = {}
        for key, arr in fields.items():
            if isinstance(arr, list) and arr:
                latest_values[key] = arr[-1]

        # Unit normalisation: acOutputActivePower is kW in API → W
        if "acOutputActivePower" in latest_values:
            try:
                latest_values["acOutputActivePower"] = (
                    float(latest_values["acOutputActivePower"]) * 1000.0
                )
            except Exception:
                pass

        # Derived values
        voltage = float(latest_values.get("batteryVoltage") or 0)
        discharge = float(latest_values.get("batteryDischargeCurrent") or 0)
        charge = float(latest_values.get("batteryChargingCurrent") or 0)
        latest_values["batteryChargingPower"] = round(charge * voltage, 2)
        latest_values["batteryDischargePower"] = round(discharge * voltage, 2)
        latest_values["batteryPower"] = (discharge - charge) * voltage

        pv_power = float(latest_values.get("pvInputPower") or 0)
        pv2_power = float(latest_values.get("pv2InputPower") or 0)
        total_pv = round(pv_power + pv2_power, 2)
        latest_values["totalPvPower"] = total_pv

        ac_output = float(latest_values.get("acOutputActivePower") or 0)
        feed_in = float(latest_values.get("feedInPower") or 0)
        battery_power = float(latest_values.get("batteryPower") or 0)

        latest_values["gridPower"] = max(
            0.0, ac_output - total_pv + battery_power + feed_in
        )
        latest_values["loadPower"] = ac_output

        return latest_values

    def fetch_latest_state(self, device: str) -> dict[str, Any]:
        """Fetch the latest state (all fields) for a device via GET with retry."""
        last_error: Exception | None = None
        for attempt in range(2):
            if attempt > 0:
                time.sleep(1.5)
            try:
                data = self._request(
                    "GET", API_LATEST_STATE, params={"deviceId": device}
                )

                if not _is_ok_code(data.get("code")):
                    last_error = RuntimeError(
                        f"Latest state error code={data.get('code')} "
                        f"message={data.get('message')}"
                    )
                    continue

                fields = (data.get("data") or {}).get("fields") or {}
                latest_values: dict[str, Any] = {}
                for key, field_info in fields.items():
                    latest_values[key] = {
                        "value": field_info.get("value"),
                        "unit": field_info.get("unit"),
                        "nameDisplay": field_info.get("nameDisplay"),
                    }

                return latest_values
            except TokenExpiredError:
                raise
            except Exception as err:
                last_error = err

        if last_error:
            raise last_error
        return {}

    # ─── Monthly summary (station) ─────────────────────────────────────────────

    def fetch_monthly_summary(self, station_id: str) -> dict[str, Any]:
        """Fetch monthly PV summary for the current month."""
        now = self._now()
        year = now.year
        month_key = f"{year}-{str(now.month).zfill(2)}"

        data = self._request(
            "POST",
            API_MONTHLY_SUMMARY,
            params={
                "stationId": station_id,
                "summaryCategoryKey": "pvInverterElectricityQuantityClass",
            },
            json={"time": str(year)},
        )

        if not _is_ok_code(data.get("code")):
            raise RuntimeError(
                f"Monthly summary error code={data.get('code')} "
                f"message={data.get('message')}"
            )

        values = _parse_monthly_properties(data.get("data") or {}, month_key)
        _LOGGER.debug(
            "SolarOfThings station %s: monthly summary %s → %s",
            station_id,
            month_key,
            values,
        )

        monthly: dict[str, Any] = {
            sensor_key: values.get(api_key)
            for sensor_key, api_key in MONTHLY_SUMMARY_KEYS.items()
        }

        # Share of consumption not bought from the grid (solar direct + battery).
        consumption = monthly.get("monthly_total_consumption")
        grid_import = monthly.get("monthly_grid_import")
        if consumption and grid_import is not None:
            coverage = 100.0 * (consumption - grid_import) / consumption
            monthly["monthly_solar_percentage"] = round(min(max(coverage, 0.0), 100.0), 1)
        else:
            monthly["monthly_solar_percentage"] = None

        return monthly

    # ─── Batch device settings read ─────────────────────────────────────────────
    # Replaces individual reads for settings not returned by the cached endpoint.
    # Flow: POST to initiate → poll GET until isFinished=true → extract configAttributeStates

    API_BATCH_READ_INIT = (
        "/apis/remote/device/configs/read"  # POST, returns batchReadId
    )
    API_BATCH_READ_DETAILS = (
        "/apis/remote/device/configs/read/details"  # GET, polling endpoint
    )

    def fetch_settings_batch(self, device_id: str) -> dict[str, Any]:
        """Fetch all device settings via the batch read API.

        This replaces individual reads for settings not returned by the cached
        settings endpoint.  The process is:
          1. POST to initiate a batch read → returns a batchReadId
          2. Poll GET /read/details?batchReadId=<id> every ~5s until isFinished=true
          3. Extract configAttributeStates as a flat {key: value} dict

        Returns the same format as fetch_settings() (flat dict of setting objects).
        """
        # Step 1: Initiate batch read
        data = self._request(
            "POST", self.API_BATCH_READ_INIT, params={"deviceId": device_id}, json={}
        )

        if not _is_ok_code(data.get("code")):
            raise RuntimeError(
                f"Batch read init error code={data.get('code')} "
                f"message={data.get('message')}"
            )

        batch_read_id = (data.get("data") or {}).get("id")
        if not batch_read_id:
            _LOGGER.warning(
                "SolarOfThings device %s: batch read init returned no id, "
                "falling back to cached settings",
                device_id,
            )
            return self.fetch_settings(device_id)

        # Step 2: Poll for results (with ~2.5 second delays between requests, ~20s total max)
        max_poll_attempts = 8
        poll_delay = 2.5  # seconds

        for attempt in range(1, max_poll_attempts + 1):
            time.sleep(poll_delay)

            data = self._request(
                "GET",
                self.API_BATCH_READ_DETAILS,
                params={"batchReadId": batch_read_id},
            )

            if not _is_ok_code(data.get("code")):
                _LOGGER.debug(
                    "SolarOfThings device %s: batch read poll on attempt %d returned code=%s message=%s",
                    device_id,
                    attempt,
                    data.get("code"),
                    data.get("message"),
                )
                continue

            result_data = data.get("data") or {}
            is_finished = result_data.get("isFinished")

            if is_finished:
                # Step 3: Extract configAttributeStates
                return self._extract_settings_from_batch(result_data)

        # Timeout — fall back to cached settings
        _LOGGER.debug(
            "SolarOfThings device %s: batch read finished without complete response, "
            "falling back to cached settings",
            device_id,
        )
        return self.fetch_settings(device_id)

    def _extract_settings_from_batch(
        self, result_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Extract a flat settings dict from batch read response data.

        The configAttributeStates has the same structure as our cached endpoint:
          {key: {"value": ..., "unit": ..., ...}, ...}
        """
        states = (result_data.get("configAttributeStates") or {}).copy()

        # Also merge any values from targetConfig that have {t, v} format
        # These are settings that were resolved but not yet in configAttributeStates
        target_config = result_data.get("targetConfig") or {}
        for key, val in target_config.items():
            if key not in states and isinstance(val, dict) and "v" in val:
                states[key] = {"value": val["v"]}

        return states

    def _write_setting(self, device_id: str, key: str, value: Any) -> None:
        """Write a single device setting key=value via the remote config write API."""
        payload = {"deviceId": device_id, "key": key, "value": value}
        data = self._request(
            "POST", API_SETTINGS_SET, params={"deviceId": device_id}, json=payload
        )
        if not _is_ok_code(data.get("code")):
            raise RuntimeError(
                f"Settings write error code={data.get('code')} "
                f"message={data.get('message')} (key={key})"
            )

    def get_device_settings(self, device_id: str) -> dict[str, Any]:
        """Fetch the cached device settings from the remote config API.

        Returns a flat dict of {settingKey: settingObject} where each value
        contains at least 'key', 'value', and 'valueDisplay' fields.
        The endpoint accepts a plain IOT-Token header (no IOT-Open-Sign).
        """
        data = self._request(
            "POST", API_SETTINGS_GET, params={"deviceId": device_id}, json={}
        )
        if not _is_ok_code(data.get("code")):
            raise RuntimeError(
                f"Settings fetch error code={data.get('code')} "
                f"message={data.get('message')}"
            )
        return data.get("data") or {}

    # Alias used by the coordinator in __init__.py
    fetch_settings = get_device_settings

    def read_device_setting(self, device_id: str, key: str) -> Any:
        """Read a single device setting via the config/read endpoint.

        Used for settings that are not returned by the cached settings endpoint.
        """
        payload = {"id": device_id, "key": key}
        data = self._request(
            "POST",
            "/apis/remote/device/config/read",
            params={"deviceId": device_id},
            json=payload,
        )
        if not _is_ok_code(data.get("code")):
            raise RuntimeError(
                f"Setting read error code={data.get('code')} "
                f"message={data.get('message')} (key={key})"
            )
        setting_data = data.get("data") or {}
        return setting_data.get("value")

    def update_device_settings(self, device_id: str, settings: dict[str, Any]) -> None:
        """Write multiple settings (one API call per key)."""
        for key, value in settings.items():
            self._write_setting(device_id, key, value)

    def set_operating_mode(self, device_id: str, mode: str) -> None:
        """Set Output Source Priority.  mode is one of OUTPUT_MODE_MAP keys."""
        value = OUTPUT_MODE_MAP.get(mode)
        if value is None:
            raise ValueError(
                f"Unknown operating mode: {mode!r}. "
                f"Valid options: {list(OUTPUT_MODE_MAP)!r}"
            )
        self._write_setting(device_id, "outputSourcePrioritySetting", value)

    def set_battery_priority(self, device_id: str, mode: str) -> None:
        """Set Charger Priority.  mode is one of CHARGER_PRIORITY_MAP keys."""
        value = CHARGER_PRIORITY_MAP.get(mode)
        if value is None:
            raise ValueError(
                f"Unknown battery priority: {mode!r}. "
                f"Valid options: {list(CHARGER_PRIORITY_MAP)!r}"
            )
        self._write_setting(device_id, "chargerSourcePrioritySetting", value)

    def set_grid_charging(self, device_id: str, enabled: bool) -> None:
        """Set AC Input Range: Appliance (0, grid charging allowed) / UPS (1, bypass)."""
        self._write_setting(device_id, "acInputRangeSetting", 0 if enabled else 1)

    def set_grid_feed_in(self, device_id: str, enabled: bool) -> None:
        """Enable or disable the GRID grid switch (batteryPowerLimitingSetting)."""
        self._write_setting(
            device_id, "batteryPowerLimitingSetting", 1 if enabled else 0
        )

    def set_backup_mode(self, device_id: str, enabled: bool) -> None:
        """Set Output Source Priority to SBU (backup/off-grid priority) when True,
        or SUB (solar-first, grid-supplemented) when False."""
        value = 2 if enabled else 1  # SBU=2 (battery before grid), SUB=1
        self._write_setting(device_id, "outputSourcePrioritySetting", value)

    def set_maximum_total_charging_current(self, device_id: str, value: int) -> None:
        """Set Maximum Total Charging Current (10-160 A)."""
        self._write_setting(device_id, "setMaxChargingCurrent", value)

    def set_max_utility_charge_current(self, device_id: str, value: int) -> None:
        """Set Max Utility Charge Current (10-140 A)."""
        self._write_setting(device_id, "setUtilityMaxChargingCurrent", value)

    # ─── New setting setters ──────────────────────────────────────────────────

    def set_bms_function_enable(self, device_id: str, enabled: bool) -> None:
        """Enable/disable BMS function (0=Off, 1=On)."""
        self._write_setting(device_id, "bmsFunctionEnableSetting", 1 if enabled else 0)

    def set_bms_lock_machine_battery_capacity(self, device_id: str, value: int) -> None:
        """Set BMS Inverter Cutoff (5-95 %)."""
        self._write_setting(device_id, "bmsLockMachineBatteryCapacity", value)

    def set_battery_constant_charging_voltage(
        self, device_id: str, value: float
    ) -> None:
        """Set Bulk Voltage (48-61 V)."""
        self._write_setting(device_id, "setBatteryCVChargeVoltage", value)

    def set_battery_equalization_interval(self, device_id: str, value: int) -> None:
        """Set Equalization interval (0-90 days)."""
        self._write_setting(device_id, "batteryEqualizationIntervalSetting", value)

    def set_battery_equalization_mode_enable(
        self, device_id: str, enabled: bool
    ) -> None:
        """Enable/disable equalization (0=Off, 1=On)."""
        self._write_setting(
            device_id, "batteryEqualizationModeEnableSetting", 1 if enabled else 0
        )

    def set_battery_equalization_time(self, device_id: str, value: int) -> None:
        """Set Equalization time (5-900 min)."""
        self._write_setting(device_id, "batteryEqualizationTimeSetting", value)

    def set_battery_equalization_timeout(self, device_id: str, value: int) -> None:
        """Set Equalization timeout (5-900 min)."""
        self._write_setting(device_id, "batteryEqualizationTimeoutSetting", value)

    def set_battery_equalization_voltage(self, device_id: str, value: float) -> None:
        """Set Equalization voltage (48-62 V)."""
        self._write_setting(device_id, "batteryEqualizationVoltageSetting", value)

    def set_battery_float_charging_voltage(self, device_id: str, value: float) -> None:
        """Set Float Voltage (48-61 V)."""
        self._write_setting(device_id, "setBatteryFloatChargingVoltage", value)

    def set_battery_recharge_voltage(self, device_id: str, value: float) -> None:
        """Set SBU Utility Takeover voltage (44-54 V)."""
        self._write_setting(device_id, "batteryRechargeVoltageSetting", value)

    def set_battery_redischarge_voltage(self, device_id: str, value: float) -> None:
        """Set SBU Battery Takeover voltage (48-58 V)."""
        self._write_setting(device_id, "batteryRedischargeVoltageSetting", value)

    def set_battery_type(self, device_id: str, mode: str) -> None:
        """Set Battery Type.  mode is one of BATTERY_TYPE_MAP keys."""
        value = BATTERY_TYPE_MAP.get(mode)
        if value is None:
            raise ValueError(
                f"Unknown battery type: {mode!r}. "
                f"Valid options: {list(BATTERY_TYPE_MAP)!r}"
            )
        self._write_setting(device_id, "batteryTypeSetting", value)

    def set_ct_zero_power(self, device_id: str, value: float) -> None:
        """Set Main input power (0.01-0.5 kW)."""
        self._write_setting(device_id, "ctZeroPower", value)

    def set_discharge_current_limit(self, device_id: str, value: int) -> None:
        """Set Maximum Battery Discharge Current (20-200 A)."""
        self._write_setting(device_id, "dischargeCurrentLimit", value)

    def set_inverter_startup_battery_capacity(self, device_id: str, value: int) -> None:
        """Set Battery startup SOC (5-100 %)."""
        self._write_setting(device_id, "inverterStartupBatteryCapacity", value)

    def set_low_battery_alarm_voltage(self, device_id: str, value: float) -> None:
        """Set Battery Low Alarm voltage (40-54 V)."""
        self._write_setting(device_id, "lowBatteryAlarmVoltageSetting", value)

    def set_rated_frequency(self, device_id: str, mode: str) -> None:
        """Set Output Frequency.  mode is one of RATED_FREQUENCY_MAP keys."""
        value = RATED_FREQUENCY_MAP.get(mode)
        if value is None:
            raise ValueError(
                f"Unknown frequency: {mode!r}. "
                f"Valid options: {list(RATED_FREQUENCY_MAP)!r}"
            )
        self._write_setting(device_id, "ratedFrequencySetting", value)

    def set_rated_voltage(self, device_id: str, mode: str) -> None:
        """Set Output Voltage.  mode is one of RATED_VOLTAGE_MAP keys."""
        value = RATED_VOLTAGE_MAP.get(mode)
        if value is None:
            raise ValueError(
                f"Unknown voltage: {mode!r}. Valid options: {list(RATED_VOLTAGE_MAP)!r}"
            )
        self._write_setting(device_id, "ratedVoltageSetting", value)

    def set_grid_connection_protocol_type(self, device_id: str, mode: str) -> None:
        """Set Grid Connection Type.  mode is one of GRID_CONNECTION_PROTOCOL_TYPE_MAP keys."""
        value = GRID_CONNECTION_PROTOCOL_TYPE_MAP.get(mode)
        if value is None:
            raise ValueError(
                f"Unknown grid connection type: {mode!r}. "
                f"Valid options: {list(GRID_CONNECTION_PROTOCOL_TYPE_MAP)!r}"
            )
        self._write_setting(device_id, "gridConnectionProtocolTypeSetting", value)

    def set_restore_battery_discharging_capacity(
        self, device_id: str, value: int
    ) -> None:
        """Set Battery DC Takeover capacity (5-100 %)."""
        self._write_setting(
            device_id, "restoreBatteryDischargingBatteryCapacity", value
        )

    def set_restore_mains_charging_capacity(self, device_id: str, value: int) -> None:
        """Set Battery Mains Charging Takeover capacity (5-95 %)."""
        self._write_setting(device_id, "restoreMainsChargingBatteryCapacity", value)

    def set_backlight_on(self, device_id: str, enabled: bool) -> None:
        """Enable/disable LCD backlight (0=Disable, 1=Enable)."""
        self._write_setting(device_id, "backlightOn", 1 if enabled else 0)

    def set_buzzer_on(self, device_id: str, enabled: bool) -> None:
        """Enable/disable buzzer (0=Disable, 1=Enable)."""
        self._write_setting(device_id, "buzzerOn", 1 if enabled else 0)

    def set_display_auto_homepage(self, device_id: str, enabled: bool) -> None:
        """Enable/disable LCD auto return to homepage (0=Disable, 1=Enable)."""
        self._write_setting(
            device_id, "displayAutomaticallyReturnsToHomepage", 1 if enabled else 0
        )

    def set_eco_mode(self, device_id: str, enabled: bool) -> None:
        """Enable/disable ECO mode (0=Disable, 1=Enable)."""
        self._write_setting(device_id, "ecoMode", 1 if enabled else 0)

    def set_input_source_beep(self, device_id: str, enabled: bool) -> None:
        """Enable/disable input source change beep (0=Disable, 1=Enable)."""
        self._write_setting(
            device_id, "inputSourceDetectionPromptSound", 1 if enabled else 0
        )

    def set_overheat_restart(self, device_id: str, enabled: bool) -> None:
        """Enable/disable overheat restart (0=Disable, 1=Enable)."""
        self._write_setting(
            device_id, "overTemperatureAutomaticRestart", 1 if enabled else 0
        )

    def set_overload_bypass(self, device_id: str, enabled: bool) -> None:
        """Enable/disable overload bypass (0=Disable, 1=Enable)."""
        self._write_setting(device_id, "overloadToBypassOperation", 1 if enabled else 0)

    def set_toggle_setting(self, device_id: str, key: str, enabled: bool) -> None:
        """Generic setter for toggle settings (writes 0 or 1)."""
        self._write_setting(device_id, key, 1 if enabled else 0)

    def test_connection(self, station_id: str | None = None) -> bool:
        """Return True if we can reach the device-list endpoint successfully."""
        try:
            self.list_devices(station_id, page_size=1)
            return True
        except Exception as err:
            _LOGGER.error("SolarOfThings: connection test failed: %s", err)
            return False
