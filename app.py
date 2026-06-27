"""Provenance Guard - Flask API.

Endpoints:
  POST /submit  -> classify text, return verdict + confidence + transparency label.
  POST /appeal  -> contest a verdict; flips status to under_review and logs it.
  GET  /log     -> structured audit log (reviewer queue / grading visibility).
  GET  /health  -> liveness check.
"""

import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import db
from detection import classify
from labels import generate_label

load_dotenv()

app = Flask(__name__)

# In-memory storage is fine for local dev / grading. See README for chosen limits.
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

db.init_db()


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


@app.route("/", methods=["GET"])
def index():
    return jsonify(
        {
            "service": "Provenance Guard",
            "status": "ok",
            "endpoints": {
                "POST /submit": "classify text {text, creator_id}",
                "POST /appeal": "contest a verdict {content_id, creator_reasoning}",
                "GET /log": "audit log (most recent first)",
                "GET /health": "liveness check",
            },
        }
    )


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute;100 per day")
def submit():
    body = request.get_json(silent=True) or {}
    text = body.get("text")
    creator_id = body.get("creator_id")

    if not text or not isinstance(text, str) or not text.strip():
        return jsonify({"error": "Field 'text' is required and must be non-empty."}), 400
    if not creator_id or not isinstance(creator_id, str):
        return jsonify({"error": "Field 'creator_id' is required."}), 400

    decision = classify(text)
    label = generate_label(decision["attribution"], decision["confidence"])

    content_id = str(uuid.uuid4())
    timestamp = _now_iso()
    excerpt = text.strip()[:160]

    db.insert_submission(
        {
            "content_id": content_id,
            "creator_id": creator_id,
            "timestamp": timestamp,
            "text_excerpt": excerpt,
            "attribution": decision["attribution"],
            "confidence": decision["confidence"],
            "llm_score": decision["llm_score"],
            "style_score": decision["style_score"],
            "combined_p": decision["combined_p"],
            "signals_used": ",".join(decision["signals_used"]),
            "status": "classified",
        }
    )

    return jsonify(
        {
            "content_id": content_id,
            "creator_id": creator_id,
            "timestamp": timestamp,
            "attribution": decision["attribution"],
            "confidence": decision["confidence"],
            "label": label,
            "signals": {
                "llm": decision["llm_score"],
                "stylometry": decision["style_score"],
                "combined_p": decision["combined_p"],
                "signals_used": decision["signals_used"],
                "style_metrics": decision["style_metrics"],
            },
            "status": "classified",
        }
    )


@app.route("/appeal", methods=["POST"])
def appeal():
    body = request.get_json(silent=True) or {}
    content_id = body.get("content_id")
    creator_reasoning = body.get("creator_reasoning")

    if not content_id or not isinstance(content_id, str):
        return jsonify({"error": "Field 'content_id' is required."}), 400
    if not creator_reasoning or not isinstance(creator_reasoning, str) or not creator_reasoning.strip():
        return jsonify({"error": "Field 'creator_reasoning' is required."}), 400

    original = db.get_submission(content_id)
    if original is None:
        return jsonify({"error": f"Unknown content_id: {content_id}"}), 404

    timestamp = _now_iso()
    db.insert_appeal(content_id, creator_reasoning.strip(), timestamp)
    db.update_status(content_id, "under_review")

    return jsonify(
        {
            "content_id": content_id,
            "status": "under_review",
            "message": (
                "Your appeal has been received and the classification is now under "
                "review by a human moderator. The original verdict is unchanged "
                "until that review completes."
            ),
            "appeal_logged_at": timestamp,
        }
    )


@app.route("/log", methods=["GET"])
def log():
    limit = request.args.get("limit", default=50, type=int)
    return jsonify({"entries": db.get_log(limit)})


@app.errorhandler(429)
def ratelimit_handler(e):
    return (
        jsonify(
            {
                "error": "rate_limit_exceeded",
                "message": f"Too many requests. Limit: {e.description}.",
            }
        ),
        429,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
