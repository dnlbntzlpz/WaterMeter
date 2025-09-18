"""
app.py — Water Meter Dashboard API (single-file Flask app)

Purpose
-------
This app serves a simple dashboard (index.html) and exposes APIs to:
  1) Accept camera uploads and expose the latest snapshot.
  2) Coordinate a "capture now" request between the frontend and a device.
  3) Run OCR for mechanical water meters using an OpenAI vision model.

Design Notes
------------
- Single-file by design (no extra modules).
- Legacy routes kept for backward compatibility; unused/duplicate code removed.
- Configuration centralized in Config (env vars preferred).
- Logging used instead of print(); JSON error handlers included.
- Route groups are separated with headers for readability.

Backwards Compatibility
-----------------------
- Legacy simple "sequence-based" capture flow is preserved.
- Newer "token-based" capture flow is canonical, but the /api/watermeter/capture
  POST now returns both "seq" (legacy) and "token" (new) to support both clients.
- Legacy /upload and /api/watermeter/latest endpoints are preserved.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
import secrets
from pathlib import Path
from datetime import datetime, timedelta
from threading import Lock
from typing import Any, Dict, Optional, Tuple
import threading
import random

from flask import (
    Flask,
    jsonify,
    make_response,
    request,
    send_file,
    send_from_directory,
)
from werkzeug.utils import secure_filename

# ---- Optional: load .env early so env vars are available for Config ----
try:
    from dotenv import load_dotenv
    # Load .env from the directory of this file, regardless of current working dir
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except Exception:
    # .env is optional; ignore if not installed or missing.
    pass

# ---- Configuration -------------------------------------------------------


class Config:
    """App configuration (env driven). Keep everything in-process for a single-file app."""

    # Flask
    DEBUG: bool = os.getenv("DEBUG", "false").strip().lower() == "true"
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "5000"))

    # Files/paths
    BASE_DIR: str = os.path.dirname(os.path.abspath(__file__))
    STATIC_FOLDER: str = os.getenv("STATIC_FOLDER", "static")
    TEMPLATE_FOLDER: str = os.getenv("TEMPLATE_FOLDER", "templates")
    UPLOAD_DIR: str = os.getenv("UPLOAD_DIR", os.path.join(BASE_DIR, "uploads"))

    # OpenAI
    OPENAI_API_KEY: Optional[str] = os.getenv("OPENAI_API_KEY")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # HTTP
    JSONIFY_PRETTYPRINT_REGULAR: bool = os.getenv(
        "JSONIFY_PRETTYPRINT_REGULAR", "false"
    ).strip().lower() == "true"

    # Capture behavior
    CAPTURE_TTL_MS: int = int(os.getenv("CAPTURE_TTL_MS", "20000"))  # drop stale requests

    # Auto-cycle relay (server-initiated) configuration
    AUTOCYCLE_ENABLED: bool = os.getenv("AUTOCYCLE_ENABLED", "true").strip().lower() == "true"
    AUTOCYCLE_MIN_MIN: int = int(os.getenv("AUTOCYCLE_MIN_MIN", "20"))
    AUTOCYCLE_MAX_MIN: int = int(os.getenv("AUTOCYCLE_MAX_MIN", "60"))
    QUIET_HOURS_ENABLED: bool = os.getenv("QUIET_HOURS_ENABLED", "true").strip().lower() == "true"
    QUIET_START_HOUR: int = int(os.getenv("QUIET_START_HOUR", "20"))  # 20:00 local
    QUIET_END_HOUR: int = int(os.getenv("QUIET_END_HOUR", "9"))       # 09:00 local


# Shared prompt for water meter OCR across endpoints
METER_OCR_PROMPT = (
    "You are a utility meter OCR assistant. The image shows a mechanical water meter. "
    'Return ONLY strict JSON with keys: reading (string), confidence (0..1), notes (string). '
    "reading must include leading zeros and the decimal if present. "
    #"Convert the readings from cubic meters to liters."
    "The red parts of the meter are the decimal part."
    "The reading should consist of 5 integer digits and 3 decimal digits."
    "If uncertain about a wheel transition, choose the most probable and lower confidence. "
    'Example: {"reading":"01234.567","confidence":0.86,"notes":"..."}'
)


# ---- Logging setup (do this before creating the app) ---------------------

logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("watermeter.app")

# Quiet noisy request logs from the development server
logging.getLogger("werkzeug").setLevel(logging.WARNING)


# ---- Flask app -----------------------------------------------------------

app = Flask(
    __name__,
    static_folder=Config.STATIC_FOLDER,
    static_url_path="",
    template_folder=Config.TEMPLATE_FOLDER,
)
app.config.from_object(Config)

# Ensure upload directory exists
UPLOAD_DIR = Path(app.config["UPLOAD_DIR"])
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ---- OpenAI client (lazy import to avoid hard failure if key missing) ----
_openai_client = None


def _get_openai_client():
    """
    Lazy-initialize and cache the OpenAI client so the app starts
    even if the key isn't present (routes will still 500 appropriately).
    """
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI  # local import to avoid import cost if unused

        _openai_client = OpenAI(api_key=app.config["OPENAI_API_KEY"])
    return _openai_client


# ---- Global in-memory state ---------------------------------------------
# NOTE: In a real multi-process deployment, consider external state (Redis/DB).

CAP_LOCK = Lock()

# Token/state machine for the "new" capture flow
CAPTURE: Dict[str, Any] = {
    "token": None,  # active token for an in-flight capture
    "state": "IDLE",  # IDLE|REQUESTED|ACKED|UPLOADED|PUBLISHED
    "ts_requested": None,
    "ts_acked": None,
    "ts_uploaded": None,
    "ts_published": None,
    "image_url": None,
}

# Simple "legacy" sequence counter for the old polling flow
CAPTURE_SEQ: int = 0  # increments on each requested capture

# Relay activation sequence counter (legacy-style trigger the device polls)
RELAY_SEQ: int = 0

# Latest image tracking
LATEST_META: Dict[str, Any] = {"ts": 0}
LATEST_PATH = UPLOAD_DIR / "latest.jpg"
LATEST_TMP = UPLOAD_DIR / "latest.tmp"


# ---- Helpers -------------------------------------------------------------


def nocache_resp(resp):
    """Apply no-store headers to a response to fight stale caching."""
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


def _ext_from_name(name: str) -> str:
    """Return a normalized image extension for a filename (default jpeg)."""
    name = (name or "").lower()
    for ext in ("png", "jpg", "jpeg", "webp"):
        if name.endswith(ext):
            return "jpeg" if ext == "jpg" else ext
    return "jpeg"


def _coerce_json(s: str) -> Dict[str, Any]:
    """
    Attempt to coerce a model response to JSON.
    If parsing fails, extract the first {...} block; if still failing, return {"raw": s}.
    """
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
        return {"raw": s}


def _new_token() -> str:
    """Generate a short token (hex) used to correlate image uploads."""
    return secrets.token_hex(8)


def _bump_relay_seq_locked() -> int:
    """Increment RELAY_SEQ under CAP_LOCK and return new seq."""
    global RELAY_SEQ
    RELAY_SEQ += 1
    return RELAY_SEQ


def _autocycle_worker():
    """Background worker that periodically requests relay activation.

    Picks a random interval in [AUTOCYCLE_MIN_MIN, AUTOCYCLE_MAX_MIN] minutes,
    then signals the device via RELAY_SEQ increment.
    """
    min_m = max(1, int(app.config["AUTOCYCLE_MIN_MIN"]))
    max_m = max(min_m, int(app.config["AUTOCYCLE_MAX_MIN"]))
    logger.info("Auto-cycle: enabled (min=%sm, max=%sm)", min_m, max_m)

    def is_quiet_now():
        if not app.config.get("QUIET_HOURS_ENABLED", False):
            return False
        now = datetime.now()
        start = int(app.config["QUIET_START_HOUR"]) % 24
        end = int(app.config["QUIET_END_HOUR"]) % 24
        h = now.hour
        if start < end:
            return start <= h < end
        else:
            # window crosses midnight
            return h >= start or h < end

    def seconds_until_quiet_end():
        if not app.config.get("QUIET_HOURS_ENABLED", False):
            return 0
        now = datetime.now()
        end_h = int(app.config["QUIET_END_HOUR"]) % 24
        quiet = is_quiet_now()
        if not quiet:
            return 0
        end_dt = now.replace(hour=end_h, minute=0, second=0, microsecond=0)
        if end_dt <= now:
            end_dt += timedelta(days=1)
        return max(0, int((end_dt - now).total_seconds()))

    while True:
        try:
            wait_min = random.randint(min_m, max_m)
            wait_sec = wait_min * 60
            logger.info("Auto-cycle: next relay activation in %s minutes", wait_min)
            # Sleep in chunks to avoid very long sleeps
            remaining = wait_sec
            while remaining > 0:
                # If we enter quiet hours, pause until they end
                q = seconds_until_quiet_end()
                if q > 0:
                    logger.info("Auto-cycle: in quiet hours, pausing %ss", q)
                    # sleep in small chunks during quiet as well
                    while q > 0:
                        sl = min(30, q)
                        time.sleep(sl)
                        q -= sl
                    continue
                chunk = min(30, remaining)
                time.sleep(chunk)
                remaining -= chunk

            # Before triggering, ensure we are not within quiet window
            if seconds_until_quiet_end() > 0:
                logger.info("Auto-cycle: skipped trigger due to quiet hours")
                continue

            with CAP_LOCK:
                seq = _bump_relay_seq_locked()
            logger.info("Auto-cycle: relay activation requested (seq=%s)", seq)
        except Exception as e:
            logger.warning("Auto-cycle worker error: %s", e)
            time.sleep(5)


def _write_latest_atomically(img_bytes: bytes) -> int:
    """
    Atomically replace latest.jpg to avoid partial reads from the frontend.
    Returns a millisecond timestamp used by the UI for cache-busting.
    """
    with open(LATEST_TMP, "wb") as f:
        f.write(img_bytes)
        f.flush()
        os.fsync(f.fileno())
    os.replace(LATEST_TMP, LATEST_PATH)
    ts = int(time.time() * 1000)
    LATEST_META["ts"] = ts
    return ts


def _analyze_image_bytes(img_bytes: bytes, filename_hint: str = "latest.jpg") -> Dict[str, Any]:
    """
    Run the OpenAI Vision model on the provided image bytes and return a dict.
    The dict will contain either {reading, confidence, notes} or {raw, warning}.
    Raises on hard OpenAI errors so callers can decide how to handle.
    """
    if not app.config["OPENAI_API_KEY"]:
        raise RuntimeError("OPENAI_API_KEY not set")

    b64 = base64.b64encode(img_bytes).decode("utf-8")
    ext = _ext_from_name(filename_hint)
    image_url = f"data:image/{ext};base64,{b64}"

    client = _get_openai_client()
    resp = client.chat.completions.create(
        model=app.config["OPENAI_MODEL"],
        temperature=0,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": METER_OCR_PROMPT},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ],
    )
    text = (resp.choices[0].message.content or "").strip()
    data = _coerce_json(text)
    if "reading" not in data:
        data = {"raw": text, "warning": "Model did not return strict JSON; see 'raw'."}
    return data


def _store_latest_ocr_result(result: Dict[str, Any]) -> None:
    """Merge OCR result into LATEST_META under the same lock used elsewhere."""
    with CAP_LOCK:
        if isinstance(result, dict):
            # Only allow a small, known subset through to avoid bloat
            reading = result.get("reading")
            confidence = result.get("confidence")
            notes = result.get("notes") or result.get("warning")
            raw = result.get("raw")
            if reading is not None:
                LATEST_META["reading"] = reading
            if confidence is not None:
                LATEST_META["confidence"] = confidence
            if notes is not None:
                LATEST_META["notes"] = notes
            if raw is not None and "raw" not in LATEST_META:
                # keep raw only if we don't already have one
                LATEST_META["raw"] = raw


# =========================================================================
# ### Healthcheck & Static
# =========================================================================


@app.get("/healthz")
def healthcheck():
    """
    Healthcheck endpoint.

    Returns
    -------
    JSON: {"ok": true, "ts": <ms>}
    """
    return jsonify(ok=True, ts=int(time.time() * 1000))


@app.get("/")
def root():
    """
    Serve the dashboard index.html.

    Behavior
    --------
    - Tries the configured static folder first.
    - Falls back to the app base directory if not found.
    """
    index_candidates = [
        Path(app.static_folder or "") / "index.html",
        Path(app.config["BASE_DIR"]) / "index.html",
    ]
    for p in index_candidates:
        if p.exists():
            return send_from_directory(p.parent.as_posix(), p.name)

    # If we get here, neither location had index.html
    logger.warning("index.html not found in expected locations.")
    return jsonify(error="index.html not found"), 404


@app.get("/uploads/<path:filename>")
def serve_upload(filename: str):
    """Serve files from the uploads directory with no-store headers."""
    fp = (UPLOAD_DIR / filename).resolve()
    try:
        # ensure path stays within uploads dir
        fp.relative_to(UPLOAD_DIR.resolve())
    except Exception:
        return jsonify(error="invalid path"), 400
    if not fp.exists():
        return jsonify(error="not found"), 404
    resp = make_response(send_file(fp.as_posix()))
    return nocache_resp(resp)


# =========================================================================
# ### API: Water Meter OCR (OpenAI Vision)
# =========================================================================


@app.post("/api/watermeter/analyze")
def analyze_watermeter():
    """
    Analyze a mechanical water meter image via OpenAI Vision.

    Request
    -------
    multipart/form-data with key 'image'.

    Response
    --------
    200 JSON:
      {"reading": "<string>", "confidence": <0..1>, "notes": "<string>"}
    or (fallback if strict JSON not returned by model):
      {"raw": "<model text>", "warning": "Model did not return strict JSON; see 'raw'."}

    Errors
    ------
    400 if no image provided or empty.
    500 if model invocation fails or OPENAI_API_KEY missing.
    """
    if "image" not in request.files:
        return jsonify({"error": "no file field 'image'"}), 400

    f = request.files["image"]
    filename = secure_filename(f.filename or "upload.jpg")
    img_bytes = f.read()
    if not img_bytes:
        return jsonify({"error": "empty file"}), 400

    if not app.config["OPENAI_API_KEY"]:
        logger.error("OPENAI_API_KEY not set.")
        return jsonify({"error": "OPENAI_API_KEY not set"}), 500

    # Build a data URL for the vision model. Using data URL avoids temp files.
    b64 = base64.b64encode(img_bytes).decode("utf-8")
    ext = _ext_from_name(filename)
    image_url = f"data:image/{ext};base64,{b64}"

    instruction = METER_OCR_PROMPT

    try:
        client = _get_openai_client()  # ensure client initialized for helpful 500
        # Reuse the same implementation as auto-OCR helper
        data = _analyze_image_bytes(img_bytes, filename)
        return jsonify(data), 200
    except Exception as e:
        logger.exception("Error during meter analysis: %s", e)
        return jsonify({"error": str(e)}), 500


# =========================================================================
# ### API: Capture Coordination (Legacy+Token flow, unified)
# =========================================================================


@app.post("/api/watermeter/capture")
def capture_request():
    """
    Request a new capture from the device.

    Behavior
    --------
    - Increments the legacy sequence counter for older polling clients.
    - Generates a new token for the newer token-based flow and resets CAPTURE state.

    Response (superset for backward compatibility)
    ----------------------------------------------
    200 JSON: {"ok": true, "seq": <int>, "token": "<hex>"}
    """
    global CAPTURE_SEQ
    now_ms = int(time.time() * 1000)
    with CAP_LOCK:
        # Coalesce if a fresh request is already pending
        if CAPTURE.get("state") in ("REQUESTED", "ACKED") and (
            now_ms - (CAPTURE.get("ts_requested") or 0)
        ) <= app.config["CAPTURE_TTL_MS"]:
            token = CAPTURE.get("token") or _new_token()
            # Still bump seq for legacy UI feedback but keep same token/state/time
            CAPTURE_SEQ += 1
        else:
            CAPTURE_SEQ += 1  # legacy clients poll /capture/next?since=<seq>
            token = _new_token()
            CAPTURE.update(
                {
                    "token": token,
                    "state": "REQUESTED",
                    "ts_requested": now_ms,
                    "ts_acked": None,
                    "ts_uploaded": None,
                    "ts_published": None,
                    "image_url": None,
                }
            )
    logger.info("Capture requested: seq=%s token=%s", CAPTURE_SEQ, token)
    return jsonify({"ok": True, "seq": CAPTURE_SEQ, "token": token})


# =========================================================================
# ### API: Relay Control (sequence-based trigger)
# =========================================================================


@app.post("/api/device/relay/activate")
def relay_activate():
    """
    Request the ESP32 to activate a relay for a fixed duration (device-side).

    Behavior
    --------
    - Increments a monotonic sequence number that the device polls.

    Response
    --------
    200 JSON: {"ok": true, "seq": <int>}
    """
    global RELAY_SEQ
    with CAP_LOCK:
        RELAY_SEQ += 1
        seq = RELAY_SEQ
    logger.info("Relay activation requested: seq=%s", seq)
    return jsonify({"ok": True, "seq": seq})


@app.get("/api/device/relay/next")
def relay_next():
    """
    Device long-poll (relay flow):
    Ask if a relay activation has been requested since a given sequence number.

    Query Params
    ------------
    since : int (default 0)

    Response
    --------
    200 JSON: {"activate": <bool>, "seq": <int>}
    """
    try:
        since = int(request.args.get("since", "0"))
    except Exception:
        since = 0
    with CAP_LOCK:
        activate_needed = RELAY_SEQ > since
        seq = RELAY_SEQ
    logger.debug("Relay poll since=%s -> activate=%s seq=%s", since, activate_needed, seq)
    return jsonify(activate=activate_needed, seq=seq)


@app.get("/api/watermeter/capture/next")
def capture_next():
    """
    Device long-poll (legacy flow):
    Ask if a new capture has been requested since a given sequence number.

    Query Params
    ------------
    since : int (default 0)

    Response
    --------
    200 JSON: {"capture": <bool>, "seq": <int>}
    """
    try:
        since = int(request.args.get("since", "0"))
    except Exception:
        since = 0
    now_ms = int(time.time() * 1000)
    with CAP_LOCK:
        # Only signal capture if there is a newer request AND it is still fresh
        capture_needed = CAPTURE_SEQ > since and (
            CAPTURE.get("state") in ("REQUESTED", "ACKED") and
            (now_ms - (CAPTURE.get("ts_requested") or 0)) <= app.config["CAPTURE_TTL_MS"]
        )
        seq = CAPTURE_SEQ
    logger.debug("Device poll since=%s -> capture=%s seq=%s", since, capture_needed, seq)
    return jsonify(capture=capture_needed, seq=seq)


@app.post("/api/watermeter/capture/ack")
def capture_ack():
    """
    Device acknowledges it is about to capture (token flow).

    Query/Form/JSON
    ---------------
    token : str

    Response
    --------
    200 JSON: {"ok": true}  on success
    400 JSON: {"ok": false, "reason": "bad-token-or-state"} otherwise
    """
    tok = request.args.get("token") or (request.json or {}).get("token")
    with CAP_LOCK:
        if CAPTURE["token"] == tok and CAPTURE["state"] == "REQUESTED":
            CAPTURE["state"] = "ACKED"
            CAPTURE["ts_acked"] = int(time.time() * 1000)
            logger.info("Capture ACK: token=%s", tok)
            return jsonify({"ok": True})
    logger.warning("Capture ACK rejected: token=%s state=%s", tok, CAPTURE.get("state"))
    return jsonify({"ok": False, "reason": "bad-token-or-state"}), 400


@app.post("/api/watermeter/upload")
def upload_image():
    """
    Device uploads the captured image (token flow). Also updates /latest.jpg.

    Query/Form Data
    ---------------
    token : str  (query or form)
    image : file (form-data)

    Response
    --------
    200 JSON: {"ok": true, "ts": <ms>, "image_url": "/uploads/<token>.jpg"}
    400 plain: "missing token or image"
    409 plain: "unexpected token/state"
    """
    tok = request.args.get("token") or request.form.get("token")
    f = request.files.get("image")
    if not tok or not f:
        return "missing token or image", 400

    # Save to a temp file first to avoid partial writes
    tmp_path = UPLOAD_DIR / f"tmp_{int(time.time() * 1000)}.jpg"
    f.save(tmp_path.as_posix())

    with CAP_LOCK:
        if CAPTURE["token"] != tok or CAPTURE["state"] not in ("ACKED", "REQUESTED"):
            # Clean up orphaned temp file
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
            logger.warning("Upload rejected: token/state mismatch token=%s", tok)
            return "unexpected token/state", 409

        # 1) Publish a token-named file (stable URL per capture)
        token_path = UPLOAD_DIR / f"{tok}.jpg"
        os.replace(tmp_path, token_path)

        CAPTURE["state"] = "UPLOADED"
        CAPTURE["ts_uploaded"] = int(time.time() * 1000)
        CAPTURE["image_url"] = f"/uploads/{tok}.jpg"

        # 2) Update latest.jpg atomically AFTER token file exists
        #    to maintain consistent frontend behavior.
        #    We then (re)create token.jpg from latest for robustness.
        os.replace(token_path, LATEST_PATH)
        try:
            # Try hardlink back to token.jpg for consistency (Unix)
            os.link(LATEST_PATH, token_path)
        except Exception:
            # Fallback: copy bytes
            with open(LATEST_PATH, "rb") as src, open(token_path, "wb") as dst:
                dst.write(src.read())

        ts = int(time.time() * 1000)
        LATEST_META["ts"] = ts
        CAPTURE["state"] = "PUBLISHED"
        CAPTURE["ts_published"] = ts

    # After publishing, try OCR in background-like manner (still in-request for simplicity)
    try:
        with open(LATEST_PATH, "rb") as fp:
            img_b = fp.read()
        result = _analyze_image_bytes(img_b, f"{tok}.jpg")
        _store_latest_ocr_result(result)
        logger.info("OCR stored for token=%s reading=%s conf=%s", tok, result.get("reading"), result.get("confidence"))
    except Exception as e:
        logger.warning("OCR failed post-upload: %s", e)

    logger.info("Image uploaded and published: token=%s ts=%s", tok, LATEST_META["ts"])
    return jsonify({"ok": True, "ts": LATEST_META["ts"], "image_url": CAPTURE["image_url"]})


@app.get("/api/watermeter/capture/state")
def capture_state():
    """
    Query capture state for the current token (token flow).

    Query Params
    ------------
    token : str

    Response
    --------
    200 JSON: {"ok": true, ... CAPTURE state ..., "latest_ts": <ms>}
    404 JSON: {"ok": false, "reason": "unknown-token"}
    """
    tok = request.args.get("token")
    with CAP_LOCK:
        if tok and tok == CAPTURE["token"]:
            return jsonify({"ok": True, **CAPTURE, "latest_ts": LATEST_META["ts"]})
    return jsonify({"ok": False, "reason": "unknown-token"}), 404


# =========================================================================
# ### API: Upload (legacy) + Latest
# =========================================================================


@app.post("/upload")
def upload_legacy():
    """
    Legacy upload endpoint (no token). Writes directly to /latest.jpg.

    Request
    -------
    Either:
      - multipart/form-data with key 'image'
      - or raw body bytes (image/jpeg)

    Response
    --------
    200 JSON: {"ok": true, "ts": <ms>}
    400 JSON: {"ok": false, "error": "no image"}
    """
    img_bytes: bytes = (
        request.files["image"].read() if "image" in request.files else request.data
    )
    if not img_bytes:
        return jsonify(ok=False, error="no image"), 400

    ts = _write_latest_atomically(img_bytes)
    logger.info("Legacy upload -> latest.jpg updated ts=%s", ts)

    # Bridge legacy flow -> mark current token as published if a request is active
    with CAP_LOCK:
        if CAPTURE.get("token") and CAPTURE.get("state") in ("REQUESTED", "ACKED", "UPLOADED"):
            CAPTURE["state"] = "PUBLISHED"
            CAPTURE["ts_published"] = ts
            CAPTURE["image_url"] = "/latest.jpg"

    # Kick OCR for legacy uploads as well
    try:
        result = _analyze_image_bytes(img_bytes, "latest.jpg")
        _store_latest_ocr_result(result)
        logger.info("OCR stored (legacy) reading=%s conf=%s", result.get("reading"), result.get("confidence"))
    except Exception as e:
        logger.warning("OCR failed (legacy upload): %s", e)

    return jsonify(ok=True, ts=ts)


@app.get("/api/watermeter/latest")
def latest_meta():
    """
    Return the latest image metadata and URL for the UI.

    Response
    --------
    200 JSON:
      {
        "hasImage": <bool>,
        "imageUrl": "/latest.jpg" | null,
        "result": {"ts": <ms>}
      }
    """
    exists = LATEST_PATH.exists()
    resp = make_response(
        jsonify(
            hasImage=exists,
            imageUrl="/latest.jpg" if exists else None,
            result=LATEST_META,
        )
    )
    return nocache_resp(resp)


@app.get("/latest.jpg")
def latest_jpg():
    """
    Serve the latest image with no-store caching.

    Response
    --------
    200 image/jpeg
    404 plain: "no image"
    """
    if not LATEST_PATH.exists():
        return "no image", 404
    resp = make_response(send_file(LATEST_PATH.as_posix(), mimetype="image/jpeg"))
    return nocache_resp(resp)


# =========================================================================
# ### Error Handlers (JSON)
# =========================================================================


@app.errorhandler(400)
def handle_400(err):
    """Return JSON for 400 errors."""
    logger.warning("400 Bad Request: %s", getattr(err, "description", err))
    return jsonify(error="Bad Request", message=str(err)), 400


@app.errorhandler(404)
def handle_404(err):
    """Return JSON for 404 errors."""
    logger.warning("404 Not Found: %s", getattr(err, "description", err))
    return jsonify(error="Not Found", message=str(err)), 404


@app.errorhandler(500)
def handle_500(err):
    """Return JSON for 500 errors."""
    logger.exception("500 Internal Server Error: %s", getattr(err, "description", err))
    return jsonify(error="Internal Server Error", message="Unexpected server error"), 500


# =========================================================================
# ### Main
# =========================================================================

if __name__ == "__main__":
    # IMPORTANT: use_reloader=False to avoid double-running worker code in dev
    logger.info(
        "Starting dev server on %s:%s (debug=%s)", app.config["HOST"], app.config["PORT"], app.config["DEBUG"]
    )
    # Start auto-cycle worker if enabled
    if app.config.get("AUTOCYCLE_ENABLED", False):
        t = threading.Thread(target=_autocycle_worker, name="autocycle", daemon=True)
        t.start()
    app.run(
        host=app.config["HOST"],
        port=app.config["PORT"],
        debug=app.config["DEBUG"],
        use_reloader=False,
    )
