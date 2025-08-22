import os, json, base64, re
from flask import Flask, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename

# Optional: load .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from openai import OpenAI

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)

app = Flask(__name__, static_folder="static", static_url_path="", template_folder="templates")

# Serve the dashboard
@app.get("/")
def root():
    return send_from_directory(app.static_folder, "index.html")

# Water meter analyzer API
@app.post("/api/watermeter/analyze")
def analyze_watermeter():
    """
    Accepts multipart/form-data with key 'image'.
    Returns JSON: {"reading": "12345.678", "confidence": 0.92, "notes": "..."}
    """
    if "image" not in request.files:
        return jsonify({"error": "no file field 'image'"}), 400

    f = request.files["image"]
    filename = secure_filename(f.filename or "upload.jpg")
    img_bytes = f.read()
    if not img_bytes:
        return jsonify({"error": "empty file"}), 400

    if not OPENAI_API_KEY:
        return jsonify({"error": "OPENAI_API_KEY not set"}), 500

    # Build data URL for the vision model
    b64 = base64.b64encode(img_bytes).decode("utf-8")
    ext = _ext_from_name(filename)
    image_url = f"data:image/{ext};base64,{b64}"

    instruction = (
        "You are a utility meter OCR assistant. The image shows a mechanical water meter. "
        "Return ONLY strict JSON with keys: reading (string), confidence (0..1), notes (string). "
        "reading must include leading zeros and the decimal if present. "
        "If uncertain about a wheel transition, choose the most probable and lower confidence. "
        "Example: {\"reading\":\"01234.567\",\"confidence\":0.86,\"notes\":\"...\"}"
    )

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": instruction},
                    {"type": "image_url", "image_url": {"url": image_url}}
                ]
            }]
        )
        text = (resp.choices[0].message.content or "").strip()
        data = _coerce_json(text)
        if "reading" not in data:
            data = {"raw": text, "warning": "Model did not return strict JSON; see 'raw'."}
        return jsonify(data), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _ext_from_name(name: str) -> str:
    name = name.lower()
    for ext in ("png", "jpg", "jpeg", "webp"):
        if name.endswith(ext):
            return "jpeg" if ext == "jpg" else ext
    return "jpeg"

def _coerce_json(s: str):
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

if __name__ == "__main__":
    # dev server
    app.run(host="0.0.0.0", port=5000, debug=True)
