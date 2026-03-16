from flask import Flask, request, jsonify
from flask_cors import CORS
from librus_api import LibrusAPI
import asyncio
import logging
import uuid
import time
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = Flask(__name__)
CORS(app)

# In-memory session storage (for free tier simplicity)
# In production, use Redis or database
sessions = {}

# Session timeout (30 minutes)
SESSION_TIMEOUT = 30 * 60

def make_trace_id() -> str:
    return uuid.uuid4().hex[:8]

def librus_error_response(error_code: str, message: str | None = None):
    mapping = {
        "invalid_credentials": (401, "Nieprawidlowy login lub haslo"),
        "session_expired": (401, "Sesja wygasla"),
        "timeout": (504, "Librus odpowiadal zbyt dlugo. Sprobuj ponownie za chwile."),
        "request_timeout": (504, "Librus odpowiadal zbyt dlugo. Sprobuj ponownie za chwile."),
        "connection_error": (502, "Nie udalo sie polaczyc z Librusem."),
        "upstream_unavailable": (502, "Librus jest chwilowo niedostepny."),
        "oauth_init_failed": (502, "Nie udalo sie rozpoczec logowania w Librusie."),
        "grant_failed": (502, "Librus nie zakonczyl procesu logowania."),
        "activation_failed": (502, "Nie udalo sie aktywowac sesji Librusa."),
        "login_verification_failed": (502, "Nie udalo sie potwierdzic sesji Librusa."),
        "no_data": (502, "Librus nie zwrocil danych."),
        "session_missing": (401, "Brak aktywnej sesji Librusa."),
        "internal_error": (500, "Wewnetrzny blad serwera."),
    }
    status, default_message = mapping.get(error_code, (500, "Blad komunikacji z Librusem."))
    return jsonify({"success": False, "error": message or default_message, "code": error_code}), status

def cleanup_old_sessions():
    """Remove expired sessions."""
    current_time = time.time()
    expired = [k for k, v in sessions.items() if current_time - v.get("created", 0) > SESSION_TIMEOUT]
    for k in expired:
        del sessions[k]

def get_session(token: str) -> dict:
    """Get session by token, return None if expired."""
    cleanup_old_sessions()
    session = sessions.get(token)
    if session:
        if time.time() - session.get("created", 0) > SESSION_TIMEOUT:
            del sessions[token]
            return None
    return session

# ========== LIBRUS ENDPOINTS ==========

@app.route('/librus/login', methods=['POST'])
def librus_login():
    """Login to Librus and return session token."""
    trace_id = make_trace_id()
    started_at = time.monotonic()
    app.logger.info("[%s] POST /librus/login started", trace_id)

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"success": False, "error": "No data provided"}), 400
    
    login = data.get("login")
    password = data.get("password")
    
    if not login or not password:
        return jsonify({"success": False, "error": "Missing login or password"}), 400
    
    async def do_login():
        api = LibrusAPI(trace_id=trace_id)
        return await api.login(login, password)
    
    try:
        result = asyncio.run(do_login())
    except Exception:
        app.logger.exception("[%s] POST /librus/login crashed", trace_id)
        return librus_error_response("internal_error")
    
    if result.get("success"):
        # Create session token
        token = str(uuid.uuid4())
        sessions[token] = {
            "cookies": result["cookies"],
            "user": result.get("user"),
            "created": time.time()
        }
        
        app.logger.info("[%s] POST /librus/login finished in %.2fs", trace_id, time.monotonic() - started_at)
        return jsonify({
            "success": True,
            "token": token,
            "user": result.get("user"),
            "message": "Zalogowano pomyślnie"
        })

    duration = time.monotonic() - started_at
    app.logger.warning(
        "[%s] POST /librus/login failed in %.2fs code=%s",
        trace_id,
        duration,
        result.get("code", "unknown")
    )
    return librus_error_response(result.get("code", "internal_error"), result.get("error"))

@app.route('/librus/attendances', methods=['GET'])
def get_attendances():
    """Get attendance data."""
    trace_id = make_trace_id()
    started_at = time.monotonic()
    app.logger.info("[%s] GET /librus/attendances started", trace_id)

    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        return jsonify({"success": False, "error": "No authorization"}), 401
    
    token = auth.replace("Bearer ", "")
    session = get_session(token)
    
    if not session:
        return jsonify({"success": False, "error": "Session expired"}), 401
    
    async def fetch():
        api = LibrusAPI(cookies=session["cookies"], trace_id=trace_id)
        return await api.get_attendances()
    
    try:
        result = asyncio.run(fetch())
    except Exception:
        app.logger.exception("[%s] GET /librus/attendances crashed", trace_id)
        return librus_error_response("internal_error")
    
    if "error" in result:
        if result["error"] == "session_expired":
            sessions.pop(token, None)
        app.logger.warning(
            "[%s] GET /librus/attendances failed in %.2fs code=%s",
            trace_id,
            time.monotonic() - started_at,
            result["error"]
        )
        return librus_error_response(result["error"])

    app.logger.info("[%s] GET /librus/attendances finished in %.2fs", trace_id, time.monotonic() - started_at)
    
    return jsonify({"success": True, **result})

@app.route('/librus/grades', methods=['GET'])
def get_grades():
    """Get grades data."""
    trace_id = make_trace_id()
    started_at = time.monotonic()
    app.logger.info("[%s] GET /librus/grades started", trace_id)

    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        return jsonify({"success": False, "error": "No authorization"}), 401
    
    token = auth.replace("Bearer ", "")
    session = get_session(token)
    
    if not session:
        return jsonify({"success": False, "error": "Session expired"}), 401
    
    async def fetch():
        api = LibrusAPI(cookies=session["cookies"], trace_id=trace_id)
        return await api.get_grades()
    
    try:
        result = asyncio.run(fetch())
    except Exception:
        app.logger.exception("[%s] GET /librus/grades crashed", trace_id)
        return librus_error_response("internal_error")
    
    if "error" in result:
        if result["error"] == "session_expired":
            sessions.pop(token, None)
        app.logger.warning(
            "[%s] GET /librus/grades failed in %.2fs code=%s",
            trace_id,
            time.monotonic() - started_at,
            result["error"]
        )
        return librus_error_response(result["error"])

    app.logger.info("[%s] GET /librus/grades finished in %.2fs", trace_id, time.monotonic() - started_at)
    
    return jsonify({"success": True, **result})

@app.route('/librus/me', methods=['GET'])
def get_me():
    """Get current user info."""
    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        return jsonify({"success": False, "error": "No authorization"}), 401
    
    token = auth.replace("Bearer ", "")
    session = get_session(token)
    
    if not session:
        return jsonify({"success": False, "error": "Session expired"}), 401
    
    return jsonify({"success": True, "user": session.get("user")})

@app.route('/librus/logout', methods=['POST'])
def logout():
    """Logout and invalidate session."""
    auth = request.headers.get("Authorization")
    if auth and auth.startswith("Bearer "):
        token = auth.replace("Bearer ", "")
        if token in sessions:
            del sessions[token]
    
    return jsonify({"success": True, "message": "Wylogowano"})

# ========== EDUPAGE PROXY ==========

EDUPAGE_BASE = "https://zs2ostrzeszow.edupage.org"

@app.route('/edupage/proxy', methods=['POST'])
def edupage_proxy():
    """Proxy requests to EduPage."""
    data = request.json
    if not data:
        return jsonify({"error": "No data provided"}), 400
    
    path = data.get("path", "")
    body = data.get("body", {})
    
    try:
        resp = requests.post(
            EDUPAGE_BASE + path,
            json=body,
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        
        try:
            return jsonify(resp.json())
        except:
            return resp.text, resp.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ========== HEALTH CHECK ==========

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "ok",
        "sessions": len(sessions),
        "time": time.strftime("%Y-%m-%d %H:%M:%S")
    })

@app.route('/', methods=['GET'])
def home():
    """Home page."""
    return jsonify({
        "name": "SchoolTimetable API",
        "version": "1.0.0",
        "endpoints": [
            "POST /librus/login",
            "GET /librus/attendances",
            "GET /librus/grades",
            "GET /librus/me",
            "POST /librus/logout",
            "POST /edupage/proxy",
            "GET /health"
        ]
    })
