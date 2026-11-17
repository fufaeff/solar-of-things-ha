## [2.5.1] - 2026-11-17

### Fixed
- **Monthly sensors always 0**: the yearly station summary is now parsed in its real shape (`data.properties[].property.key` + `timePoints[]` with `time == "YYYY-MM"`). Monthly PV Generated, Total Consumption and Grid Import now come from `pvGeneratedEnergy`, `consumeElectricityQuantity` and `buyElectricityQuantity`. Values missing from the response are reported as unknown instead of 0.
- **Monthly Solar Coverage** is now the share of consumption not bought from the grid: `(consumption − grid import) / consumption`, limited to 0–100 %.
- Monthly energy sensors use `state_class: total_increasing`, so the reset at the start of a month is not recorded as negative energy.

### Added
- Monthly Grid Export, Monthly Battery Charge and Monthly Battery Discharge sensors (`sellElectricityQuantity`, `chargeElectricityQuantity`, `dischargeElectricityQuantity`).

## [2.5.0] - 2026-11-17

### Fixed
- **Expired tokens now trigger re-auth**: HTTP 401 and token-invalid business codes (10002/10003) from any data endpoint force a token refresh and one retry; if the token cannot be refreshed (e.g. legacy IOT-token mode) `TokenExpiredError` is raised and the re-auth flow starts instead of the data silently freezing.
- **Bounded stale data**: coordinators keep previous data for at most 5 consecutive failed device updates (2 for the station); after that entities become unavailable.
- **Energy integrators no longer over-count during outages**: accumulated energy sensors only integrate freshly fetched power values.
- **No reload on token refresh**: the update listener reloads the entry only when options or credentials change, not when refreshed tokens are persisted.
- **Device registry**: inverter identifiers are now valid `(domain, id)` pairs (existing devices are migrated in place); the station device is registered before inverters reference it via `via_device`.

### Changed
- Requires Home Assistant 2024.12 or newer (`ConfigFlowResult`, options flow without explicit `config_entry`, `async_update_reload_and_abort`).
- Time-series polling requests a 15-minute window (max 100 points) instead of 1 hour / 2000 points.
- Documentation links in the config flow point to this repository.

### Development
- CI runs hassfest, HACS validation, ruff and the test suite; test failures now fail the build.
- New tests for the API client (signing, login, token refresh, parsing) and for setup/coordinators/energy integration.

## [2.3.5] - 2026-06-16

### Fixed
- **Monthly Summary Sensors**: Fixed `SolarOfThingsStationMonthlySensor` to read from coordinator `monthly` statistics dictionary instead of non-existent `latest_state`.
- **Derived Power Sensors**: Added fallback in `SolarOfThingsDeviceSensor` to `time_series` telemetry for calculated sensors (`batteryPower`, `gridPower`, `loadPower`).
- **CI/CD GitHub Actions**: Fixed paths in `.github/workflows/validate.yml` and `.github/workflows/release.yml` to target `custom_components/solar_of_things/` following repository reorganization; added `master` branch trigger.
- **48V Battery Calibration (Anern MAX/EVO-10200)**: Calibrated voltage setting ranges for 48V battery systems in `const.py`, `number.py`, and `api.py`:
  - `batteryEqualizationVoltageSetting`: 48–62 V (was 24–30 V)
  - `batteryRechargeVoltageSetting` (SBU Utility Takeover): 44–54 V (was 22–25.5 V)
  - `batteryRedischargeVoltageSetting` (SBU Battery Takeover): 48–58 V (was 24–29 V)
  - `lowBatteryAlarmVoltageSetting` (Battery Low Alarm): 40–54 V (was 20–27 V)

### Changed
- Bumped manifest version and `api.py` User-Agent to 2.3.5.

## [2.3.4] - 2026-05-15

### Changed
- **HACS Repository Structure**: Adjusted the repository structure to be compliant with HACS standards by moving integration files into `custom_components/solar_of_things/`. (by filipsworks)
- `hacs.json` updated to reflect the new repository structure, removing `domains`, `zip_release`, and `filename` fields as they are no longer required. (by filipsworks)

## [2.3.3] - 2026-03-07

### Fixed
- **404 Not Found on device settings**: The settings endpoints `/api/device/settings/v1`
  and `/api/device/settings/update/v1` do not exist on `solar.siseli.com`. Replaced with the
  correct remote-config API endpoints discovered from the live portal JS bundle:
  - **Read**: `POST /apis/remote/device/configs/cache/get?deviceId=<id>` (plain `IOT-Token` header)
  - **Write**: `POST /apis/remote/device/config/write?deviceId=<id>` (body `{deviceId, key, value}`)

### Changed
- `select.py` — **Operating Mode** select now uses real API key `outputSourcePrioritySetting`
  with options `Utility First (USO)` / `Solar First (SUB)` / `Solar+Battery First (SBU)`.
- `select.py` — **Battery Priority** select now uses real API key `chargerSourcePrioritySetting`
  with options `Solar + Utility (CSO)` / `Solar First (SNU)` / `Solar Only (OSO)`.
- `switch.py` — **Grid Feed-In** switch maps to `batteryPowerLimitingSetting` (0=OFF, 1=ON).
- `switch.py` — **Grid Charging** switch maps to `acInputRangeSetting` (0=Appliance/ON, 1=UPS/OFF).
- `switch.py` — **Backup Mode** switch maps to `outputSourcePrioritySetting` SBU=2 (ON) / SUB=1 (OFF).
- `api.py` — `get_device_settings` / `fetch_settings` now POST to the correct URL with query
  parameter and plain `IOT-Token` header (no IOT-Open signing required for this endpoint).
- `api.py` — `_write_setting` helper added; `set_*` control methods updated with correct
  API key names and integer value mappings (no longer sending arbitrary string names).

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.3.2] - 2026-03-07

### Fixed
- **`fetch_settings` AttributeError** -- `__init__.py` called `api.fetch_settings()`
  but `api.py` only exposed `get_device_settings()`. Added `fetch_settings` as a
  class-level alias so both names work.
- **Missing control helper methods** -- `select.py` and `switch.py` called five
  methods that did not exist on `SolarOfThingsAPI`:
  `set_operating_mode`, `set_battery_priority`, `set_grid_charging`,
  `set_grid_feed_in`, `set_backup_mode`. All five are now implemented as thin
  wrappers over `update_device_settings`.

## [2.3.1] - 2026-03-07

### Fixed
- **Correct production AppID and server** -- the IOT Open Platform AppID has been
  corrected to `rBrTRfAPXz` (production) targeting `https://solar.siseli.com`.
  The previous release used the test-environment AppID (`JO4DAiNeys`) which was
  only accepted by `test.solar.siseli.com`, causing "account error" (code 20007)
  for every real user account.
- **Password is now MD5-hashed before transmission** -- the Siseli portal sends
  `MD5(plaintext_password)` (lowercase hex) in the login payload. Sending plain
  text returned code 7 "password error" even with valid credentials. The login
  method now applies `hashlib.md5(password).hexdigest()` before building the
  JSON body, matching the portal behaviour confirmed by live API testing.

### Changed
- `API_AUTH_BASE_URL` changed from `https://test.solar.siseli.com` to `https://solar.siseli.com`.
- `IOT_APP_ID` changed from `JO4DAiNeys` to `rBrTRfAPXz`.
- `IOT_APP_SECRET_ENC` updated to the production-AppID encrypted secret.

## [2.3.0] - 2026-03-07

### Changed
- **Authentication now uses User ID / Account** instead of email address.
  The `CONF_EMAIL` config key is replaced by `CONF_USER_ID` (`user_id`).
  The login payload now sends `{"account": ..., "password": ...}` matching
  the Siseli portal's account-login flow.

### Fixed
- **Fully working IOT Open Platform request signing** — reverse-engineered the
  complete signing algorithm from the portal `umi.js` bundle:
  - Secret decryption: `AES-128-CBC(base64(enc_secret), key=MD5(appID)[:16], iv=MD5(appID)[16:])`.
  - Signature: `MD5(HMAC-SHA256(base64(sorted_qs_headers), decrypted_secret))`.
  - The algorithm was **live-tested** against `test.solar.siseli.com/apis/login/account`
    and confirmed to return code `20007` (invalid credentials) rather than the
    previous code `44` (sign error) — proving the signing is now accepted by the server.
- **Correct API base URLs**:
  - Auth/login endpoints: `https://test.solar.siseli.com` (as hardcoded in portal JS).
  - Data endpoints: `https://solar.siseli.com` (unchanged).
  - Login path fixed from `/login/account` (404) → `/apis/login/account` (200).

### Added
- `API_AUTH_BASE_URL`, `IOT_APP_ID`, `IOT_APP_SECRET_ENC` constants in `const.py`.
- `_decrypt_app_secret()`, `_compute_iot_sign()`, `_make_signed_headers()` helpers in `api.py`.
- `pycryptodome>=3.9.0` added to `manifest.json` requirements for AES decryption.

### Migration note
Existing entries that used `email` for auth will need to re-authenticate:
Home Assistant will show a re-auth prompt where you enter your **User ID** and password.
## [2.2.0] - 2026-03-06

### Added
- **Email + Password authentication** (recommended) — the integration now logs in with Siseli portal credentials (`email`, `password`) and handles the full token lifecycle automatically. Users no longer need to copy IOT tokens from DevTools.
- **Automatic token refresh** — `SolarOfThingsAPI._ensure_token_valid()` proactively refreshes the access token 5 minutes before expiry using `POST /login/refresh/access/token` (mirroring the portal JS behaviour). Falls back to re-login with stored credentials if the refresh token has also expired.
- **HA re-auth flow** — if all refresh strategies fail, coordinators call `entry.async_start_reauth()` so HA displays a notification and the user is guided through re-authentication without losing their config.
- **`on_token_refreshed` callback** — fresh tokens are persisted back to the config entry after every refresh, so the integration survives HA restarts without a new login.
- **Legacy IOT-token mode preserved** — users who prefer not to store credentials can still paste a token manually; re-auth is triggered when it expires.
- New `const.py` keys: `CONF_EMAIL`, `CONF_PASSWORD`, `CONF_REFRESH_TOKEN`, `CONF_ACCESS_TOKEN_EXPIRES`, `CONF_REFRESH_TOKEN_EXPIRES`, `API_LOGIN`, `API_REFRESH_TOKEN`, `TOKEN_REFRESH_LEAD_SECONDS`.
- New config-flow steps: `password`, `token`, `reauth_confirm` (step `user` now offers a mode selector).
- `TokenExpiredError` and `AuthenticationError` custom exceptions in `api.py`.

### Changed
- `SolarOfThingsAPI.__init__` signature updated: keyword-only arguments (`email`, `password`, `iot_token`, `refresh_token`, etc.).
- `__init__.py` coordinators now catch `TokenExpiredError` and trigger re-auth instead of silently failing.
- `strings.json` / `translations/en.json` updated for new flow steps and error messages.
- `manifest.json` version bumped to **2.2.0** (`config_flow` version bumped to **3**).

### Known issues
- The `loginType` field value and exact shape of the `/login/account` response are inferred from portal JS analysis; tested against live API but may need adjustment for non-email login types.


## [2.2.0] - 2026-03-06

### Added
- **Email + Password authentication** (recommended) — logs in automatically; no manual token copying needed.
- **Automatic token refresh** — proactively refreshes access token 5 min before expiry via `POST /login/refresh/access/token`; falls back to re-login with stored credentials.
- **HA re-auth flow** — on total token failure, coordinators call `entry.async_start_reauth()` to prompt user without losing config.
- **`on_token_refreshed` callback** — fresh tokens persisted to config entry after every refresh (survives HA restarts).
- **Legacy IOT-token mode preserved** — paste-a-token still works; re-auth triggered on expiry.
- New constants: `CONF_EMAIL`, `CONF_PASSWORD`, `CONF_REFRESH_TOKEN`, `CONF_ACCESS_TOKEN_EXPIRES`, `CONF_REFRESH_TOKEN_EXPIRES`, `API_LOGIN`, `API_REFRESH_TOKEN`, `TOKEN_REFRESH_LEAD_SECONDS`.
- New config-flow steps: `password`, `token`, `reauth_confirm`; step `user` is now a mode selector.
- `TokenExpiredError` and `AuthenticationError` custom exceptions in `api.py`.

### Changed
- `SolarOfThingsAPI.__init__` uses keyword-only arguments.
- Coordinators catch `TokenExpiredError` and trigger re-auth.
- `strings.json`/`translations/en.json` updated for new flow steps.
- `manifest.json` version **2.2.0**, config flow `VERSION = 3`.

## [2.1.1] - 2026-03-05

### Added
- `.github/PULL_REQUEST_TEMPLATE.md` — standardised PR template for HACS default submission.

### Changed
- `hacs.json` updated: added all integration platforms (`sensor`, `number`, `select`, `switch`), enabled `zip_release: true`, and set `filename: solar-of-things-ha.zip` so HACS fetches the correct release asset.
- `manifest.json` version bumped to 2.1.1.

## [2.1.0] - 2026-02-26

### Added
- Auto-discover all device IDs under a station using `POST /apis/device/list` (no need to manually enter deviceId).
- Config flow support for optional `device_id`:
  - Leave blank to create entities for *all* devices found under the station.
  - Provide a `device_id` to restrict HA entities to a single device.

### Changed
- Manifest metadata updated (documentation/issue tracker URLs) and integration version bumped to 2.1.0.

## [2.0.0] - 2024-02-10

### Added
- **Control Features**: Full system control capabilities
  - Number entities for battery charge/discharge limits (0-100%)
  - Number entity for grid charge power limit (0-5000W)
  - Select entity for operating mode (Self-Use, Time-of-Use, Backup, Grid-Tie, Off-Grid)
  - Select entity for battery priority (Solar First, Battery First, Grid First)
  - Switch entity for grid charging enable/disable
  - Switch entity for grid feed-in enable/disable
  - Switch entity for backup mode enable/disable
- Settings API integration for reading and updating device configuration
- Comprehensive control documentation (CONTROL_FEATURES.md)
- Automation examples for time-of-use optimization
- Weather-based battery management examples
- Emergency backup preparation automations
- Dashboard control panel examples

### Changed
- Updated coordinator to fetch device settings
- Enhanced API client with control methods
- Updated README with control features documentation
- Extended translations for new entities

### Technical Details
- New platforms: Number, Select, Switch
- API endpoints: `/api/device/settings/v1`, `/api/device/settings/update/v1`
- Immediate coordinator refresh after control changes
- Proper error handling for control operations

## [1.0.0] - 2024-02-10

### Added
- Initial release of Solar of Things integration
- Real-time sensor monitoring via API
- Support for multiple stations/devices configuration
- Config flow for easy setup via UI
- Comprehensive sensor suite:
  - PV Input Power
  - AC Output Power
  - Battery Discharge/Charge Current
  - Battery Voltage
  - Battery State of Charge
  - Battery Power (calculated)
  - Grid Feed-in Power
  - Grid Import Power (calculated)
  - Load Power
  - Monthly PV Generation (when station ID provided)
- Auto-calculated metrics for battery power and grid usage
- 5-minute update interval with configurable options
- Home Assistant Energy Dashboard compatibility
- Device information for each configured station
- Proper device classes and state classes for all sensors
- Error handling and connection validation
- Support for IOT-Token authentication
- Multi-station support through multiple config entries
- Comprehensive documentation and examples

### Technical Details
- API integration with Solar of Things (Siseli) platform
- Uses time-series and monthly summary API endpoints
- Automatic data fetching via coordinator pattern
- Proper unit conversions (W, A, V, kWh, %)
- Timezone handling (GMT+8)
- Request throttling and error recovery

## [Unreleased]

### Planned Features
- Historical data import for energy statistics
- Configurable calculation methods for derived sensors
- Additional monthly statistics sensors
- Support for multiple devices per station
- Advanced battery cycle tracking
- Cost calculation sensors
- Solar forecast integration
- Customizable sensor polling rates per sensor
- Support for additional inverter models
- Webhook support for real-time updates
- MQTT integration option
- RESTful sensor alternatives
- Diagnostic sensors for API health
- Binary sensors for status alerts

### Known Issues
- Grid import calculation may not exactly match utility bills
- Monthly API doesn't track grid import/export (always returns 0)
- Timezone hardcoded to Asia/Manila (GMT+8)
- Token expiration requires manual renewal
- No retry logic for failed API calls (handled by coordinator)

### Considerations for Future Versions
- Add support for custom timezone configuration
- Implement automatic token refresh mechanism
- Add support for historical data backfill
- Create services for on-demand data refresh
- Add diagnostic mode for troubleshooting
- Support for multiple simultaneous time ranges
- Caching layer for API responses
- Rate limiting protection
- Support for alternative authentication methods

---

## Version History

### How to Upgrade

To upgrade to a new version:

1. **Via HACS**:
   - Go to HACS → Integrations
   - Find "Solar of Things"
   - Click "Update"
   - Restart Home Assistant

2. **Manual**:
   - Download the new version
   - Replace files in `/config/custom_components/solar_of_things/`
   - Restart Home Assistant

### Breaking Changes

None yet (version 1.0.0)

### Migration Guide

None required for version 1.0.0

---

## Release Notes Format

Each release includes:
- **Added**: New features
- **Changed**: Changes in existing functionality
- **Deprecated**: Soon-to-be removed features
- **Removed**: Removed features
- **Fixed**: Bug fixes
- **Security**: Security improvements

---

## Support

For questions about changes or upgrade issues:
- Check the [README](README.md)
- Review [INSTALLATION.md](INSTALLATION.md)
- Open an issue on GitHub
