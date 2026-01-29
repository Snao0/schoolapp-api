from flask import Flask, request, jsonify
from flask_cors import CORS
from librus_api import LibrusAPI
import asyncio
import uuid
import time
import requests

app = Flask(__name__)
CORS(app)

# In-memory session storage (for free tier simplicity)
# In production, use Redis or database
sessions = {}

# Session timeout (30 minutes)
SESSION_TIMEOUT = 30 * 60

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
    data = request.json
    if not data:
        return jsonify({"success": False, "error": "No data provided"}), 400
    
    login = data.get("login")
    password = data.get("password")
    
    if not login or not password:
        return jsonify({"success": False, "error": "Missing login or password"}), 400
    
    async def do_login():
        api = LibrusAPI()
        return await api.login(login, password)
    
    result = asyncio.run(do_login())
    
    if result.get("success"):
        # Create session token
        token = str(uuid.uuid4())
        sessions[token] = {
            "cookies": result["cookies"],
            "user": result.get("user"),
            "created": time.time()
        }
        
        return jsonify({
            "success": True,
            "token": token,
            "user": result.get("user"),
            "message": "Zalogowano pomyślnie"
        })
    else:
        return jsonify(result), 401

@app.route('/librus/attendances', methods=['GET'])
def get_attendances():
    """Get attendance data."""
    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        return jsonify({"success": False, "error": "No authorization"}), 401
    
    token = auth.replace("Bearer ", "")
    session = get_session(token)
    
    if not session:
        return jsonify({"success": False, "error": "Session expired"}), 401
    
    async def fetch():
        api = LibrusAPI(cookies=session["cookies"])
        return await api.get_attendances()
    
    result = asyncio.run(fetch())
    
    if "error" in result:
        if result["error"] == "session_expired":
            return jsonify({"success": False, "error": "Session expired"}), 401
        return jsonify({"success": False, "error": result["error"]}), 500
    
    return jsonify({"success": True, **result})

@app.route('/librus/grades', methods=['GET'])
def get_grades():
    """Get grades data."""
    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        return jsonify({"success": False, "error": "No authorization"}), 401
    
    token = auth.replace("Bearer ", "")
    session = get_session(token)
    
    if not session:
        return jsonify({"success": False, "error": "Session expired"}), 401
    
    async def fetch():
        api = LibrusAPI(cookies=session["cookies"])
        return await api.get_grades()
    
    result = asyncio.run(fetch())
    
    if "error" in result:
        if result["error"] == "session_expired":
            return jsonify({"success": False, "error": "Session expired"}), 401
        return jsonify({"success": False, "error": result["error"]}), 500
    
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