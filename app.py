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
from threading import Lock
from typing import Any, Dict, Optional, Tuple

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

    load_dotenv()
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


# ---- Logging setup (do this before creating the app) ---------------------

logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("watermeter.app")


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

    instruction = (
        "You are a utility meter OCR assistant. The image shows a mechanical water meter. "
        'Return ONLY strict JSON with keys: reading (string), confidence (0..1), notes (string). '
        "reading must include leading zeros and the decimal if present. "
        "If uncertain about a wheel transition, choose the most probable and lower confidence. "
        'Example: {"reading":"01234.567","confidence":0.86,"notes":"..."}'
    )

    try:
        client = _get_openai_client()
        resp = client.chat.completions.create(
            model=app.config["OPENAI_MODEL"],
            temperature=0,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": instruction},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            ],
        )
        text = (resp.choices[0].message.content or "").strip()
        data = _coerce_json(text)
        if "reading" not in data:
            # Preserve model text but give callers a structured warning.
            data = {
                "raw": text,
                "warning": "Model did not return strict JSON; see 'raw'.",
            }
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
    with CAP_LOCK:
        CAPTURE_SEQ += 1  # legacy clients poll /capture/next?since=<seq>
        token = _new_token()
        CAPTURE.update(
            {
                "token": token,
                "state": "REQUESTED",
                "ts_requested": int(time.time() * 1000),
                "ts_acked": None,
                "ts_uploaded": None,
                "ts_published": None,
                "image_url": None,
            }
        )
    logger.info("Capture requested: seq=%s token=%s", CAPTURE_SEQ, token)
    return jsonify({"ok": True, "seq": CAPTURE_SEQ, "token": token})


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
    with CAP_LOCK:
        capture_needed = CAPTURE_SEQ > since
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
        jsonify(hasImage=exists, imageUrl="/latest.jpg" if exists else None, result=LATEST_META)
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
    app.run(
        host=app.config["HOST"],
        port=app.config["PORT"],
        debug=app.config["DEBUG"],
        use_reloader=False,
    )
