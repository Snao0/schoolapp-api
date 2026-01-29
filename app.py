from flask import Flask, request, jsonify
from flask_cors import CORS
from librus_api import LibrusAPI
import asyncio
import uuid
import time
import requests
from collections import defaultdict

app = Flask(__name__)
CORS(app)

# In-memory session storage (for free tier simplicity)
# In production, use Redis or database
sessions = {}

# Session timeout (15 minutes - security improvement)
SESSION_TIMEOUT = 15 * 60

# Rate limiting for login attempts
login_attempts = defaultdict(list)
MAX_LOGIN_ATTEMPTS = 5  # Max attempts per IP
LOGIN_WINDOW = 300  # 5 minutes window

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

def check_rate_limit(ip: str) -> bool:
    """Check if IP is rate limited. Returns True if allowed, False if blocked."""
    current_time = time.time()
    # Clean old attempts
    login_attempts[ip] = [t for t in login_attempts[ip] if current_time - t < LOGIN_WINDOW]
    
    if len(login_attempts[ip]) >= MAX_LOGIN_ATTEMPTS:
        return False
    return True

def record_login_attempt(ip: str):
    """Record a login attempt for rate limiting."""
    login_attempts[ip].append(time.time())

def get_client_ip():
    """Get client IP, considering proxies."""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr or 'unknown'

# ========== LIBRUS ENDPOINTS ==========

@app.route('/librus/login', methods=['POST'])
def librus_login():
    """Login to Librus and return session token."""
    client_ip = get_client_ip()
    
    # Check rate limit
    if not check_rate_limit(client_ip):
        return jsonify({
            "success": False, 
            "error": "Zbyt wiele prób logowania. Spróbuj za 5 minut."
        }), 429
    
    data = request.json
    if not data:
        return jsonify({"success": False, "error": "No data provided"}), 400
    
    login = data.get("login")
    password = data.get("password")
    
    if not login or not password:
        return jsonify({"success": False, "error": "Missing login or password"}), 400
    
    # Record this attempt for rate limiting
    record_login_attempt(client_ip)
    
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
        return jsonify({"error": "Request failed"}), 500

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
        "version": "1.1.0",
        "security": "Rate limited, 15min sessions",
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