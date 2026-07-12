import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration
import os

SENSITIVE_KEYS = {
    "AccessToken", "RefreshToken", "Password", "password",
    "ClientProof", "ClientEphemeral", "ServerEphemeral",
    "TwoFactorCode", "code", "wg_private_key"
}

SENSITIVE_HEADERS = {"Authorization", "x-pm-uid"}

def scrub_data(data):
    """Recursively redact sensitive keys from dictionaries/lists."""
    if isinstance(data, dict):
        return {
            k: ("[REDACTED]" if k in SENSITIVE_KEYS else scrub_data(v))
            for k, v in data.items()
        }
    elif isinstance(data, list):
        # Also limit huge lists like server lists
        if len(data) > 100:
            return ["[LIST_TOO_LARGE_REDACTED]"]
        return [scrub_data(item) for item in data]
    return data

def before_send(event, hint):
    """Sentry hook to filter events before sending."""
    # Scrub headers in request
    request = event.get("request")
    if request and "headers" in request:
        headers = request["headers"]
        for h in SENSITIVE_HEADERS:
            if h in headers:
                headers[h] = "[REDACTED]"
            # Case insensitive check for headers
            for k in list(headers.keys()):
                if k.lower() == h.lower():
                    headers[k] = "[REDACTED]"

    # Scrub extra data and breadcrumbs
    if "breadcrumbs" in event:
        for bc in event["breadcrumbs"].get("values", []):
            if "data" in bc:
                bc["data"] = scrub_data(bc["data"])

    # Scrub body if present
    if request and "data" in request:
        request["data"] = scrub_data(request["data"])

    return event

def init_sentry(dsn=None):
    if not dsn:
        dsn = "https://96860d53cda2f7921ea1e3c91750d17b@o4511097624199168.ingest.de.sentry.io/4511723918196816"

    # Disable Sentry in dev if desired, but user specifically asked for it.
    # We can check environment variable to skip it if needed.
    if os.environ.get("PVPN_DISABLE_SENTRY") == "1":
        return

    sentry_sdk.init(
        dsn=dsn,
        integrations=[FlaskIntegration()],
        before_send=before_send,
        traces_sample_rate=0.1, # 10% for performance monitoring
        send_default_pii=False, # We handle it manually via before_send
        release="1.0.0"
    )
