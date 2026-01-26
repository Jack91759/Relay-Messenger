from flask import Flask, request, session, redirect, render_template_string, jsonify, abort
from werkzeug.utils import secure_filename
import sqlite3, time, hashlib, secrets
import os
import bot


app = Flask(
    __name__,
    static_folder="static",
    static_url_path="/static"
)

app.secret_key = "dev-secret"
DB = "chat.db"
UPLOAD_DIR = "mysite/static/room_icons"
os.makedirs(UPLOAD_DIR, exist_ok=True)

ONLINE_TIMEOUT = 100
TYPING_TIMEOUT = 2

SPECIAL_BADGES = {
    # user_id: ["badge", "badge"]
    2: ["DEV", "FOUNDER", "OG"],
    1: ["OG"],
    5: ["OG", "Beta Tester", "Marketing"],
    4: ["DEV"],
    6: ["DEV"],
    7: ["Bot"],
    9: ["OG"],
    10: ["OG"],
    11: ["OG"],
}

BADGE_ICONS = {
    "DEV": "🛠",
    "FOUNDER": "⭐",
    "OG": "🔥",
    "Marketing": "📈",
    "Beta Tester": "🪲",
    "Bot": "🤖"
}

# ---------------- DB ----------------

def db():
    return sqlite3.connect(DB, check_same_thread=False)

with db() as c:
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY,
        username TEXT UNIQUE,
        password TEXT,
        last_seen REAL,
        is_admin INTEGER DEFAULT 0,
        display_name TEXT,
        message_color TEXT DEFAULT '#949cf7',
        last_notif_id INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS rooms(
        id INTEGER PRIMARY KEY,
        name TEXT,
        is_dm INTEGER DEFAULT 0,
        owner_id INTEGER
    );
    CREATE TABLE IF NOT EXISTS room_members(
        room_id INTEGER,
        user_id INTEGER,
        UNIQUE(room_id,user_id)
    );
    CREATE TABLE IF NOT EXISTS messages(
        id INTEGER PRIMARY KEY,
        room_id INTEGER,
        user_id INTEGER,
        content TEXT,
        ts REAL
    );
    CREATE TABLE IF NOT EXISTS invites(
        token TEXT PRIMARY KEY,
        room_id INTEGER,
        expires REAL
    );
    CREATE TABLE IF NOT EXISTS typing(
        room_id INTEGER,
        user_id INTEGER,
        ts REAL,
        UNIQUE(room_id,user_id)
    );
    CREATE TABLE IF NOT EXISTS friends(
        user_id INTEGER,
        friend_id INTEGER,
        UNIQUE(user_id,friend_id)
    );
    CREATE TABLE IF NOT EXISTS reactions(
        message_id INTEGER,
        user_id INTEGER,
        emoji TEXT,
        UNIQUE(message_id,user_id,emoji)
    );
    """)

with db() as c:
    cols = [r[1] for r in c.execute("PRAGMA table_info(users)").fetchall()]
    if "bio" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN bio TEXT DEFAULT ''")
    if "joined_ts" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN joined_ts REAL DEFAULT 0")
    if "last_notif_id" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN last_notif_id INTEGER DEFAULT 0")
        print('last_notif_id')

with db() as c:
    cols = [r[1] for r in c.execute("PRAGMA table_info(rooms)").fetchall()]
    if "image" not in cols:
        c.execute("ALTER TABLE rooms ADD COLUMN image TEXT")

with db() as c:
    c.execute("""
    CREATE TABLE IF NOT EXISTS games (
        room_id INTEGER PRIMARY KEY,
        game TEXT NOT NULL,
        state TEXT NOT NULL,
        turn INTEGER,
        players TEXT,
        status TEXT
    )
    """)

with db() as c:
    cols = [r[1] for r in c.execute("PRAGMA table_info(games)").fetchall()]
    if "room_id" not in cols:
        c.execute("ALTER TABLE games ADD COLUMN room_id INTEGER PRIMARY KEY''")
    if "game" not in cols:
        c.execute("ALTER TABLE games ADD COLUMN game TEXT")
    if "state" not in cols:
        c.execute("ALTER TABLE games ADD COLUMN state TEXT")
    if "turn" not in cols:
        c.execute("ALTER TABLE games ADD COLUMN turn INTEGER")
    if "players" not in cols:
        c.execute("ALTER TABLE games ADD COLUMN players TEXT")
    if "status" not in cols:
        c.execute("ALTER TABLE games ADD COLUMN status TEXT")


# ---------------- Helpers ----------------

def hash_pw(p):
    return hashlib.sha256(p.encode()).hexdigest()

def require_user():
    if "uid" not in session:
        return None
    with db() as c:
        u = c.execute(
            "SELECT id,username,is_admin,last_seen FROM users WHERE id=?",
            (session["uid"],)
        ).fetchone()
        if u:
            c.execute("UPDATE users SET last_seen=? WHERE id=?", (time.time(), u[0]))
        return u

def is_online(ts):
    return time.time() - ts < ONLINE_TIMEOUT

def is_room_admin(uid, rid):
    with db() as c:
        r = c.execute("SELECT owner_id FROM rooms WHERE id=?", (rid,)).fetchone()
        u = c.execute("SELECT is_admin FROM users WHERE id=?", (uid,)).fetchone()
    return r and (r[0] == uid or u[0] == 1)

def ensure_member(uid, rid):
    with db() as c:
        return c.execute(
            "SELECT 1 FROM room_members WHERE room_id=? AND user_id=?",
            (rid, uid)
        ).fetchone() is not None

# ---------------- Auth ----------------

EXEMPT_PATHS = {
    "/admin/server",
    "/static",
    # "/app",
}

def server_enabled():
    with db() as c:
        row = c.execute(
            "SELECT enabled, reason FROM server_state WHERE id=1"
        ).fetchone()
        return bool(row[0]), row[1]


@app.before_request
def check_server_status():
    enabled, reason = server_enabled()

    # Allow static files
    if request.path.startswith("/static"):
        return

    # Allow admin toggle endpoint
    if request.path.startswith("/admin/server"):
        return

    # If disabled, block EVERYTHING
    if not enabled:
        return render_template_string(
            SERVER_DISABLED_HTML,
            reason=reason or "No reason provided"
        ), 503

@app.route("/admin/server", methods=["GET", "POST"])
def admin_server_control():
    u = require_user()
    #if not u or not u[2]:
    #    abort(403)

    if request.method == "POST":
        enabled = 1 if request.form.get("enabled") == "1" else 0
        reason = request.form.get("reason", "").strip()

        with db() as c:
            c.execute(
                "UPDATE server_state SET enabled=?, reason=? WHERE id=1",
                (enabled, reason)
            )

        return redirect("/admin/server")

    with db() as c:
        enabled, reason = c.execute(
            "SELECT enabled, reason FROM server_state WHERE id=1"
        ).fetchone()

    return render_template_string(
        SERVER_CONTROL_HTML,
        enabled=enabled,
        reason=reason
    )

@app.route("/", methods=["GET","POST"])
def login():
    if request.method=="POST":
        with db() as c:
            u=c.execute(
                "SELECT id FROM users WHERE username=? AND password=?",
                (request.form["username"],hash_pw(request.form["password"]))
            ).fetchone()
            if u:
                session["uid"]=u[0]
                return redirect("/app")
        return render_template_string(LOGIN_HTML, error="Invalid login")
    return render_template_string(LOGIN_HTML, error=None)

@app.route("/login", methods=["GET","POST"])
def logins():
    if request.method=="POST":
        with db() as c:
            u=c.execute(
                "SELECT id FROM users WHERE username=? AND password=?",
                (request.form["username"],hash_pw(request.form["password"]))
            ).fetchone()
            if u:
                session["uid"]=u[0]
                return redirect("/app")
        return render_template_string(LOGIN_HTML, error="Invalid login")
    return render_template_string(LOGIN_HTML, error=None)

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method=="POST":
        try:
            with db() as c:
                c.execute(
                    "INSERT INTO users(username,password,last_seen,joined_ts) VALUES(?,?,?,?)",
                    (request.form["username"], hash_pw(request.form["password"]), time.time(), time.time())
                )
            return redirect("/")
        except sqlite3.IntegrityError:
            return render_template_string(REGISTER_HTML, error="Username taken")
    return render_template_string(REGISTER_HTML, error=None)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# ---------------- Core Pages ----------------

@app.route("/app")
def app_home():
    u=require_user()
    if not u: return redirect("/")
    return render_template_string(APP_HTML, user=u)

@app.route("/notifications")
def notifications():
    u = require_user()
    if not u:
        abort(401)

    uid = u[0]

    with db() as c:
        last_id = c.execute(
            "SELECT last_notif_id FROM users WHERE id=?",
            (uid,)
        ).fetchone()[0] or 0

        rows = c.execute("""
            SELECT
                m.id,
                m.room_id,
                r.name,
                r.is_dm,
                u.username,
                m.content
            FROM messages m
            JOIN rooms r ON r.id = m.room_id
            JOIN users u ON u.id = m.user_id
            JOIN room_members rm ON rm.room_id = m.room_id
            WHERE rm.user_id = ?
              AND m.id > ?
              AND m.user_id != ?
            ORDER BY m.id ASC
        """, (uid, last_id, uid)).fetchall()

        if rows:
            c.execute(
                "UPDATE users SET last_notif_id=? WHERE id=?",
                (rows[-1][0], uid)
            )

    return jsonify([
    {
        "mid": r[0],
        "room_id": r[1],
        "room_name": r[2],
        "is_dm": bool(r[3]),
        "author": r[4],
        "content": r[5]
    }
    for r in rows
])


# ---------------- Search ----------------

@app.route("/search")
def search():
    q = request.args.get("q","").strip()
    u = require_user()
    if not q: return jsonify([])
    with db() as c:
        users = c.execute(
            "SELECT id,username,last_seen FROM users WHERE username LIKE ? LIMIT 15",
            (f"%{q}%",)
        ).fetchall()
        rooms = c.execute(
            "SELECT id,name FROM rooms WHERE name LIKE ? AND is_dm=0 LIMIT 15",
            (f"%{q}%",)
        ).fetchall()
    res = {
        "users": [{"id":x[0],"name":x[1],"online":is_online(x[2])} for x in users],
        "rooms": [{"id":x[0],"name":x[1]} for x in rooms]
    }
    return jsonify(res)

@app.route("/join/<int:rid>")
def join_room(rid):
    u=require_user()
    with db() as c:
        c.execute("INSERT OR IGNORE INTO room_members VALUES(?,?)",(rid,u[0]))
    return "ok"

@app.route("/my/rooms")
def my_rooms():
    u = require_user()
    if not u:
        return redirect("/")

    with db() as c:
        rooms = c.execute("""
            SELECT r.id, r.name, r.is_dm, r.image
            FROM rooms r
            JOIN room_members m ON m.room_id = r.id
            WHERE m.user_id = ?
              AND r.is_dm = 0
        """, (u[0],)).fetchall()

        return jsonify([
            {"id":r[0],"name":r[1],"is_dm":r[2],"image":r[3]}
            for r in rooms
        ])


@app.route("/room_members/<int:rid>")
def room_members(rid):
    u = require_user()
    if not u:
        abort(401)

    with db() as c:
        owner = c.execute("SELECT owner_id FROM rooms WHERE id=?", (rid,)).fetchone()
        members = c.execute("""
            SELECT u.id,u.username,u.last_seen,u.is_admin
            FROM users u
            JOIN room_members m ON m.user_id=u.id
            WHERE m.room_id=?
        """,(rid,)).fetchall()

    data=[]
    for uid,name,last_seen,is_admin in members:
        raw = SPECIAL_BADGES.get(uid, [])
        icons = [BADGE_ICONS[b] for b in raw if b in BADGE_ICONS]

        data.append({
            "id": uid,
            "name": name,
            "online": is_online(last_seen),
            "admin": bool(is_admin),
            "owner": owner and owner[0]==uid,
            "badges": icons
        })
    return jsonify(data)

@app.route("/friend/<int:uid>")
def friend(uid):
    u=require_user()
    with db() as c:
        c.execute("INSERT OR IGNORE INTO friends VALUES(?,?)",(u[0],uid))
        c.execute("INSERT OR IGNORE INTO friends VALUES(?,?)",(uid,u[0]))
    return "ok"

@app.route("/api/game/start", methods=["POST"])
def start_game():
    u = require_user()
    room = int(request.form["room"])
    game = request.form["game"]

    if not ensure_member(u[0], room):
        abort(403)

    if game == "chess":
        state = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR"
    elif game == "checkers":
        state = "bbbbbbbb/8/bbbbbbbb/8/8/rrrrrrrr/8/rrrrrrrr"
    elif game == "wordle":
        state = secrets.choice(["apple","grape","brick","smile","plant"])
    else:
        abort(400)

    with db() as c:
        c.execute("""
        INSERT OR REPLACE INTO games
        VALUES (?,?,?,?,?,?)
        """, (
            room,
            game,
            state,
            u[0],
            str(u[0]),
            "waiting"
        ))

        # system message
        c.execute("""
        INSERT INTO messages(room_id,user_id,content,ts)
        VALUES(?,?,?,?)
        """, (
            room,
            0,
            f"🎮 **{u[1]} started {game.upper()}** — Join to play!",
            time.time()
        ))

    return jsonify(ok=True)

@app.route("/api/game/join", methods=["POST"])
def join_game():
    u = require_user()
    room = int(request.form["room"])

    with db() as c:
        g = c.execute(
            "SELECT players,status FROM games WHERE room_id=?",
            (room,)
        ).fetchone()

        if not g:
            abort(404)

        players = g[0].split(",")
        if str(u[0]) not in players:
            players.append(str(u[0]))

        status = "active" if len(players) >= 2 else "waiting"

        c.execute("""
        UPDATE games SET players=?, status=?
        WHERE room_id=?
        """, (",".join(players), status, room))

    return jsonify(ok=True)

@app.route("/api/game/ttt", methods=["POST"])
def ttt_move():
    u = require_user()
    room = int(request.form["room"])
    idx = int(request.form["cell"])

    with db() as c:
        g = c.execute(
            "SELECT state,turn FROM games WHERE room_id=? AND game='ttt'",
            (room,)
        ).fetchone()

        if not g or g[1] != u[0]:
            abort(403)

        board = list(g[0])
        if board[idx] != ".":
            abort(400)

        board[idx] = "X"
        c.execute(
            "UPDATE games SET state=?, turn=0 WHERE room_id=?",
            ("".join(board), room)
        )

    return jsonify(board=board)

@app.route("/api/game/guess", methods=["POST"])
def guess():
    u = require_user()
    room = int(request.form["room"])
    guess = int(request.form["guess"])

    with db() as c:
        g = c.execute(
            "SELECT state FROM games WHERE room_id=? AND game='guess'",
            (room,)
        ).fetchone()

    number = int(g[0])

    if guess == number:
        return jsonify(result="correct 🎉")
    elif guess < number:
        return jsonify(result="too low")
    else:
        return jsonify(result="too high")

# ---------------- Admin ----------------

@app.route("/admin")
def admin():
    u = require_user()
    if not u or not u[2]:
        abort(403)

    # Fetch users and rooms as before
    with db() as c:
        users = c.execute("SELECT id, username, is_admin, last_seen FROM users").fetchall()
        rooms = c.execute("SELECT id, name FROM rooms WHERE is_dm=0").fetchall()

    return render_template_string(ADMIN_HTML, users=users, rooms=rooms, online=is_online)


@app.route("/admin/db")
def db_browser():
    u = require_user()
    if not u or not u[2]:
        abort(403)

    tables = [
        "users", "rooms",
        "room_members", "invites",
        "friends", "reactions", "messages"
    ]

    data = {}

    with db() as c:
        for table in tables:
            cur = c.execute(f"SELECT * FROM {table} LIMIT 100")
            rows = cur.fetchall()
            columns = [d[0] for d in cur.description]
            data[table] = {
                "rows": rows,
                "columns": columns
            }

    return render_template_string(DB_BROWSER_HTML, data=data)

@app.route("/admin/db/update", methods=["POST"])
def update_db():
    u = require_user()
    if not u or not u[2]:
        abort(403)

    table = request.form["table"]
    row_id = request.form["id"]
    column = request.form["column"]
    new_value = request.form["new_value"]

    # Update the row in the database
    with db() as c:
        c.execute(f"UPDATE {table} SET {column} = ? WHERE id = ?", (new_value, row_id))

    return redirect(f"/admin/db")


@app.route("/admin/promote/<int:uid>")
def promote(uid):
    u=require_user()
    if not u or not u[2]:
        abort(403)
    with db() as c:
        c.execute("UPDATE users SET is_admin=1 WHERE id=?", (uid,))
    return redirect("/admin")

# ---------------- Rooms ----------------

@app.route("/create_room",methods=["POST"])
def create_room():
    u=require_user()
    name=request.form["name"].strip()
    if not name: return "empty",400
    with db() as c:
        c.execute(
            "INSERT INTO rooms(name,is_dm,owner_id) VALUES(?,?,?)",
            (name,0,u[0])
        )
        rid=c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.execute("INSERT INTO room_members VALUES(?,?)",(rid,u[0]))
    return jsonify({"room":rid})

@app.route("/api/rooms/<int:rid>/rename",methods=["POST"])
def rename_room(rid):
    u=require_user()
    if not u or not is_room_admin(u[0],rid):
        abort(403)
    name=request.json.get("name","").strip()
    if not name:
        abort(400)
    with db() as c:
        c.execute("UPDATE rooms SET name=? WHERE id=?", (name,rid))
    return jsonify(ok=True)

@app.route("/api/rooms/<int:rid>/leave",methods=["POST"])
def leave_room(rid):
    u=require_user()
    if not u: abort(401)
    with db() as c:
        c.execute("DELETE FROM room_members WHERE room_id=? AND user_id=?", (rid,u[0]))
    return jsonify(ok=True)

@app.route("/api/rooms/<int:rid>",methods=["DELETE"])
def delete_room(rid):
    u=require_user()
    if not u or not is_room_admin(u[0],rid):
        abort(403)
    with db() as c:
        c.execute("DELETE FROM messages WHERE room_id=?", (rid,))
        c.execute("DELETE FROM room_members WHERE room_id=?", (rid,))
        c.execute("DELETE FROM rooms WHERE id=?", (rid,))
    return jsonify(deleted=True)

# ---------------- Invites ----------------

@app.route("/api/rooms/<int:rid>/invite")
def create_invite(rid):
    u=require_user()
    if not u or not is_room_admin(u[0],rid):
        abort(403)
    token=secrets.token_urlsafe(8)
    with db() as c:
        c.execute("INSERT INTO invites VALUES(?,?,?)",(token,rid,time.time()+86400))
    return jsonify(link=f"/invite/{token}")

@app.route("/invite/<token>")
def invite(token):
    u=require_user()
    if not u: return redirect("/")
    with db() as c:
        r=c.execute(
            "SELECT room_id,expires FROM invites WHERE token=?",
            (token,)
        ).fetchone()
        if not r or r[1]<time.time():
            return "Invite expired",400
        c.execute("INSERT OR IGNORE INTO room_members VALUES(?,?)",(r[0],u[0]))
    return redirect("/app")

# ---------------- Messages ----------------

@app.route("/messages/<int:rid>")
def messages(rid):
    u=require_user()
    with db() as c:
        msgs = c.execute("""
            SELECT m.id,u.username,m.content,m.ts
            FROM messages m
            JOIN users u ON u.id=m.user_id
            WHERE room_id=? ORDER BY m.id DESC LIMIT 50
        """,(rid,)).fetchall()[::-1]
        data=[]
        for mid,user,content,ts in msgs:
            reacts=c.execute("""
                SELECT emoji,COUNT(*) FROM reactions WHERE message_id=? GROUP BY emoji
            """,(mid,)).fetchall()
            data.append({"id":mid,"user":user,"content":content,"ts":ts,"reactions":reacts})
    return jsonify(data)

@app.route("/send", methods=["POST"])
def send():
    u = require_user()
    if not u:
        abort(401)

    msg = request.form.get("msg", "").strip()
    room = int(request.form["room"])

    if not msg:
        return "empty", 400

    ts = time.time()

    with db() as c:
        # Get room info
        room_row = c.execute(
            "SELECT name, is_dm FROM rooms WHERE id=?",
            (room,)
        ).fetchone()

        room_name, is_dm = room_row

        # Insert message
        c.execute(
            "INSERT INTO messages(room_id,user_id,content,ts) VALUES(?,?,?,?)",
            (room, u[0], msg, ts)
        )

        mid = c.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Find ONLINE recipients (exclude sender)
        recipients = c.execute("""
            SELECT u.id
            FROM room_members rm
            JOIN users u ON u.id = rm.user_id
            WHERE rm.room_id = ?
              AND u.id != ?
              AND u.last_seen > ?
        """, (room, u[0], time.time() - ONLINE_TIMEOUT)).fetchall()

    return jsonify({
        "ok": True,
        "notify": [
            {
                "mid": mid,
                "room_id": room,
                "room_name": room_name,
                "is_dm": bool(is_dm),
                "author": u[1],
                "content": msg
            }
            for _ in recipients
        ]
    })


@app.route("/react",methods=["POST"])
def react():
    u=require_user()
    mid=request.form["mid"]
    emoji=request.form["emoji"]
    with db() as c:
        row=c.execute("SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?",
                      (mid,u[0],emoji)).fetchone()
        if row:
            c.execute("DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?",(mid,u[0],emoji))
        else:
            c.execute("INSERT OR IGNORE INTO reactions VALUES(?,?,?)",(mid,u[0],emoji))
    return "ok"

# ---------------- Settings ----------------

@app.route("/settings", methods=["GET","POST"])
def settings():
    u = require_user()
    if not u:
        return redirect("/")

    if request.method == "POST":
        bio = request.form.get("bio","").strip()[:200]
        name = request.form["display_name"].strip()
        color = request.form["message_color"]

        if not name:
            return "Invalid name", 400

        with db() as c:
            c.execute("""
                UPDATE users SET display_name=?, message_color=?, bio=?
                WHERE id=?
            """,(name, color, bio, u[0]))


        return redirect("/app")

    return render_template_string(SETTINGS_HTML,
        name=u[2],
        color=u[3],
        bio=""
    )


# ---------------- Typing ----------------

@app.route("/typing",methods=["POST"])
def typing():
    u=require_user()
    with db() as c:
        c.execute("INSERT OR REPLACE INTO typing VALUES(?,?,?)",(request.form["room"],u[0],time.time()))
    return "ok"

@app.route("/typing/<int:rid>")
def typing_users(rid):
    with db() as c:
        return jsonify([r[0] for r in c.execute("""
            SELECT u.username FROM typing t
            JOIN users u ON u.id=t.user_id
            WHERE room_id=? AND ts>?
        """,(rid,time.time()-TYPING_TIMEOUT)).fetchall()])

@app.route("/api/game/state/<int:rid>")
def game_state(rid):
    u = require_user()
    if not u:
        abort(401)

    # Must be a member of the room
    if not ensure_member(u[0], rid):
        abort(403)

    with db() as c:
        g = c.execute("""
            SELECT game, state, turn, players, status
            FROM games
            WHERE room_id=?
        """, (rid,)).fetchone()

    if not g:
        abort(404)

    game, state, turn, players, status = g

    return jsonify({
        "room": rid,
        "game": game,
        "state": state,
        "turn": turn,
        "players": [int(p) for p in players.split(",") if p],
        "status": status
    })

@app.route("/api/game/list/<int:rid>")
def game_list(rid):
    u = require_user()
    if not u or not ensure_member(u[0], rid):
        abort(403)

    with db() as c:
        g = c.execute("""
            SELECT game, status, players
            FROM games
            WHERE room_id=?
        """, (rid,)).fetchone()

    if not g:
        return jsonify(active=False)

    return jsonify({
        "active": True,
        "game": g[0],
        "status": g[1],
        "players": g[2].split(",") if g[2] else []
    })

# ---------------- Friends & DMs ----------------

@app.route("/friends")
def friends():
    u=require_user()
    with db() as c:
        #print(c.execute("""
        #    SELECT u.id,u.username,u.last_seen FROM friends f
        #    JOIN users u ON u.id=f.friend_id
        #    WHERE f.user_id=?
        #""",(u[0],)).fetchall())
        return jsonify(c.execute("""
            SELECT u.id,u.username,u.last_seen FROM friends f
            JOIN users u ON u.id=f.friend_id
            WHERE f.user_id=?
        """,(u[0],)).fetchall())

@app.route("/dm/<int:uid>")
def dm(uid):
    u=require_user()
    with db() as c:
        r=c.execute("""
            SELECT r.id FROM rooms r
            JOIN room_members a ON a.room_id=r.id
            JOIN room_members b ON b.room_id=r.id
            WHERE r.is_dm=1 AND a.user_id=? AND b.user_id=?
        """,(u[0],uid)).fetchone()
        if r: return jsonify({"room":r[0]})
        c.execute("INSERT INTO rooms(name,is_dm) VALUES(?,1)",(f"DM-{u[0]}-{uid}",))
        rid=c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.execute("INSERT INTO room_members VALUES(?,?)",(rid,u[0]))
        c.execute("INSERT INTO room_members VALUES(?,?)",(rid,uid))
    return jsonify({"room":rid})

@app.route("/api/rooms/<int:rid>/icon", methods=["POST"])
def upload_room_icon(rid):
    u = require_user()
    if not u or not is_room_admin(u[0], rid):
        abort(403)

    if "file" not in request.files:
        abort(401)

    file = request.files["file"]
    if file.filename == "":
        abort(400)

    filename = secure_filename(file.filename)
    ext = filename.rsplit(".", 1)[-1].lower()

    if ext not in ("png", "jpg", "jpeg", "webp"):
        return "bad file", 402

    # 🔥 DELETE OLD ICONS FIRST (important)
    for e in ("png", "jpg", "jpeg", "webp"):
        old = os.path.join(UPLOAD_DIR, f"room_{rid}.{e}")
        if os.path.exists(old):
            os.remove(old)

    path = os.path.join(UPLOAD_DIR, f"room_{rid}.{ext}")
    file.save(path)

    web_path = f"/static/room_icons/room_{rid}.{ext}"

    with db() as c:
        c.execute(
            "UPDATE rooms SET image=? WHERE id=?",
            (web_path, rid)
        )

    return jsonify(ok=True, path=web_path)

@app.route("/api/user/<int:uid>")
def user_profile(uid):
    me = require_user()
    if not me:
        abort(401)

    with db() as c:
        u = c.execute("""
            SELECT id, username, bio, joined_ts, last_seen, is_admin
            FROM users WHERE id=?
        """, (uid,)).fetchone()

    if not u:
        abort(404)

    badges = SPECIAL_BADGES.get(uid, [])
    icons = [BADGE_ICONS[b] for b in badges if b in BADGE_ICONS]

    return jsonify({
        "id": u[0],
        "username": u[1],
        "bio": u[2] or "No bio set.",
        "joined": u[3],
        "last_seen": u[4],
        "admin": bool(u[5]),
        "badges": icons
    })

@app.route("/edit_message", methods=["POST"])
def edit_message():
    u = require_user()
    if not u:
        abort(401)

    mid = int(request.form["mid"])
    text = request.form["content"].strip()

    if not text:
        return "empty", 400

    with db() as c:
        row = c.execute(
            "SELECT user_id FROM messages WHERE id=?",
            (mid,)
        ).fetchone()

        if not row or row[0] != u[0]:
            abort(403)

        c.execute(
            "UPDATE messages SET content=?, edited_ts=? WHERE id=?",
            (text, time.time(), mid)
        )

    return jsonify(ok=True)


# ---------------- API Docs ----------------

@app.route("/api/docs")
def docs():
    return render_template_string("""
<!doctype html>
<title>Chat API Docs</title>
<style>
body{
  background:#1e1f22;
  color:white;
  font-family:system-ui,sans-serif;
  padding:30px;
  line-height:1.6
}
h1,h2,h3{margin-top:28px}
code{
  background:#2b2d31;
  padding:6px 10px;
  border-radius:6px;
  display:block;
  margin:6px 0;
  white-space:pre-wrap
}
.section{margin-bottom:30px}
.note{color:#b5bac1;font-size:14px}
.warn{color:#ff8c8c}
</style>

<h1>Chat API Documentation</h1>
<div class="note">
All endpoints require a valid logged-in session (cookie-based auth),
unless explicitly stated otherwise.
</div>

<div class="section">
<h2>Authentication</h2>
<code>POST /</code>
Login (form: username, password)

<code>POST /register</code>
Register new user

<code>GET /logout</code>
Clear session
</div>

<div class="section">
<h2>Rooms</h2>

<code>GET /my/rooms</code>
List rooms the user is a member of

<code>POST /create_room</code>
Create a room (form: name)

<code>GET /join/&lt;room_id&gt;</code>
Join a public room

<code>POST /api/rooms/&lt;room_id&gt;/rename</code>
Rename room (JSON: { name })

<code>POST /api/rooms/&lt;room_id&gt;/leave</code>
Leave room

<code>DELETE /api/rooms/&lt;room_id&gt;</code>
Delete room (admin/owner only)

<code>POST /api/rooms/&lt;room_id&gt;/icon</code>
Upload room icon (multipart/form-data, field: file)

<code>GET /room_members/&lt;room_id&gt;</code>
List members, online status, badges
</div>

<div class="section">
<h2>Invites</h2>

<code>GET /api/rooms/&lt;room_id&gt;/invite</code>
Create invite link (returns token)

<code>GET /invite/&lt;token&gt;</code>
Accept invite (browser redirect)
</div>

<div class="section">
<h2>Messages</h2>

<code>GET /messages/&lt;room_id&gt;</code>
Fetch last 50 messages in room

<code>POST /send</code>
Send message
(form: room, msg)

<code>POST /edit_message</code>
Edit own message
(form: mid, content)

<code>POST /react</code>
Toggle reaction
(form: mid, emoji)
</div>

<div class="section">
<h2>Notifications</h2>

<code>GET /notifications</code>
Fetch unread message notifications since last check
(Updates last_notif_id automatically)
</div>

<div class="section">
<h2>Typing Indicators</h2>

<code>POST /typing</code>
Signal typing status
(form: room)

<code>GET /typing/&lt;room_id&gt;</code>
Get list of users currently typing
</div>

<div class="section">
<h2>Search</h2>

<code>GET /search?q=&lt;text&gt;</code>
Search users and public rooms
</div>

<div class="section">
<h2>Friends & DMs</h2>

<code>GET /friends</code>
List friends

<code>GET /friend/&lt;user_id&gt;</code>
Add friend (bi-directional)

<code>GET /dm/&lt;user_id&gt;</code>
Create or open DM room
</div>

<div class="section">
<h2>User Profiles</h2>

<code>GET /api/user/&lt;user_id&gt;</code>
Fetch public user profile (bio, badges, join date)
</div>

<div class="section">
<h2>Admin (Restricted)</h2>
<div class="warn">Requires admin privileges</div>

<code>GET /admin</code>
Admin panel

<code>GET /admin/db</code>
Database browser

<code>POST /admin/db/update</code>
Update database row

<code>GET /admin/promote/&lt;user_id&gt;</code>
Promote user to admin
</div>

<div class="section">
<h2>Notes for Bot Developers</h2>
<ul>
  <li>Auth is session-cookie based, not token-based</li>
  <li>No WebSockets — polling required</li>
  <li>Notifications endpoint is incremental</li>
  <li>Typing expires after {{TYPING_TIMEOUT}} seconds</li>
  <li>Online status based on {{ONLINE_TIMEOUT}} second heartbeat</li>
</ul>
</div>

</html>
""", TYPING_TIMEOUT=TYPING_TIMEOUT, ONLINE_TIMEOUT=ONLINE_TIMEOUT)


# ---------------- HTML ----------------

LOGIN_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Relay • Login</title>
<link rel="icon" href="https://cdn-icons-png.flaticon.com/512/685/685887.png">

<style>
:root {
  --bg-dark: #050b1a;
  --panel: rgba(15, 23, 42, 0.88);
  --accent: #3b82f6;
  --text: #ffffff;
  --muted: #94a3b8;
  --error: #ff6b6b;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  min-height: 100vh;
  font-family: system-ui, sans-serif;
  background: var(--bg-dark);
  color: var(--text);
  overflow-x: hidden;
}

/* 🌊 Animated Waves */
.waves {
  position: fixed;
  inset: 0;
  z-index: -1;
}

.wave {
  position: absolute;
  width: 200%;
  height: 200%;
  background: radial-gradient(circle at 50% 50%, rgba(59,130,246,0.25), transparent 70%);
  animation: drift 22s linear infinite;
}

.wave:nth-child(2) {
  animation-duration: 30s;
  opacity: 0.5;
}

.wave:nth-child(3) {
  animation-duration: 38s;
  opacity: 0.35;
}

@keyframes drift {
  from { transform: translate(0, 0); }
  to { transform: translate(-50%, -30%); }
}

/* Layout */
.container {
  min-height: 100vh;
  display: grid;
  grid-template-columns: 1.2fr 1fr;
  align-items: center;
  gap: 48px;
  padding: 60px;
  max-width: 1100px;
  margin: auto;
}

/* Hero */
.hero h1 {
  font-size: 3rem;
  margin-bottom: 12px;
}

.hero p {
  color: var(--muted);
  font-size: 1.1rem;
  max-width: 420px;
}

/* Card */
.card {
  background: var(--panel);
  backdrop-filter: blur(14px);
  padding: 34px;
  border-radius: 22px;
  box-shadow: 0 40px 90px rgba(0,0,0,.6);
}

.card h2 {
  margin-top: 0;
  margin-bottom: 18px;
}

input, button {
  width: 100%;
  padding: 14px;
  margin: 10px 0;
  border-radius: 12px;
  border: none;
  font-size: 1rem;
}

input {
  background: #020617;
  color: var(--text);
}

input::placeholder {
  color: #64748b;
}

button {
  background: linear-gradient(135deg, #3b82f6, #2563eb);
  color: white;
  font-weight: 600;
  cursor: pointer;
}

button:hover {
  box-shadow: 0 0 30px rgba(59,130,246,.6);
}

.error {
  background: rgba(255, 107, 107, 0.15);
  color: var(--error);
  padding: 10px;
  border-radius: 10px;
  font-size: .9rem;
  margin-bottom: 10px;
}

.footer-link {
  display: block;
  text-align: center;
  margin-top: 14px;
  color: var(--muted);
  text-decoration: none;
  font-size: .9rem;
}

.footer-link:hover {
  color: white;
}

/* 📱 Mobile Optimization */
@media (max-width: 900px) {
  .container {
    grid-template-columns: 1fr;
    padding: 32px 20px;
    gap: 30px;
    text-align: center;
  }

  .hero h1 {
    font-size: 2.3rem;
  }

  .hero p {
    font-size: 1rem;
    max-width: none;
  }

  .card {
    padding: 28px;
  }
}

@media (max-width: 420px) {
  .hero h1 {
    font-size: 2rem;
  }

  input, button {
    padding: 16px;
    font-size: 1rem;
  }
}
</style>
</head>

<body>

<div class="waves">
  <div class="wave"></div>
  <div class="wave"></div>
  <div class="wave"></div>
</div>

<div class="container">

  <div class="hero">
    <h1>Relay</h1>
    <p>
      A fast, secure chat app for real-time conversations.
      Stay connected anywhere.
    </p>
  </div>

  <div class="card">
    <h2>Login</h2>

    {% if error %}
      <div class="error">{{ error }}</div>
    {% endif %}

    <form method="post">
      <input name="username" placeholder="Username" required>
      <input type="password" name="password" placeholder="Password" required>
      <button type="submit">Sign In</button>
    </form>

    <a class="footer-link" href="/register">
      New to Relay? Create an account
    </a>
  </div>

</div>
</body>
</html>
"""


REGISTER_HTML = LOGIN_HTML.replace(
    "<title>Relay • Login</title>", "<title>Relay • Register</title>"
).replace(
    "<h2>Login</h2>", "<h2>Register</h2>"
).replace(
    "Sign In", "Create Account"
).replace(
    "New to Relay? Create an account",
    "Already have an account? Login"
).replace(
    'href="/register"', 'href="/login"'
)

SETTINGS_HTML = """<!doctype html>
<link rel="icon" href="https://cdn-icons-png.flaticon.com/512/685/685887.png" type="image/x-icon">
<title>Settings</title>
<style>
body{background:#1e1f22;color:white;font-family:sans-serif;
display:flex;align-items:center;justify-content:center;height:100vh}
.card{background:#2b2d31;padding:30px;border-radius:18px;width:320px}
input,button{width:100%;margin:10px 0;padding:12px;border-radius:8px;border:none}
button{background:#5865F2;color:white;font-weight:bold}
.preview{margin-top:10px}
</style>

<div class=card>
<h2>Settings</h2>

<form method=post>
<label>Display name</label>
<input name=display_name value="{{name}}">

<label>Bio</label>
<textarea name="bio" maxlength="200" style="width:100%;height:80px;
background:#1e1f22;color:white;border:none;border-radius:8px;padding:10px;">
{{ bio }}
</textarea>

<label>Message color</label>
<input type=color name=message_color value="{{color}}">

<div class=preview>
<b style="color:{{color}}">{{name}}</b>: Hello world
</div>

<button>Save</button>
</form>
<a href="/app" style="color:#aaa">← Back</a>
<a href="/admin" style="color:#aaa">Admin</a>
</div>
"""

APP_HTML = """
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<link rel="icon" href="https://cdn-icons-png.flaticon.com/512/685/685887.png" type="image/x-icon">
<title>Relay App</title>
<style>
:root{
  --bg-main:#0b1220;
  --bg-panel:#0f172a;
  --bg-hover:#1e293b;
  --bg-input:#020617;

  --blue:#38bdf8;
  --blue-soft:#0ea5e9;
  --blue-dark:#0284c7;

  --text-main:#e5e7eb;
  --text-muted:#94a3b8;

  --border:#1e293b;
  --glow:0 0 12px rgba(56,189,248,.35);
}

/* ---------- ANIMATED BACKGROUND ---------- */
body::before,
body::after{
  content:"";
  position:fixed;
  inset:-50%;
  z-index:-1;
  background:
    radial-gradient(circle at 20% 30%, rgba(56,189,248,.15), transparent 40%),
    radial-gradient(circle at 80% 70%, rgba(14,165,233,.12), transparent 45%),
    radial-gradient(circle at 50% 50%, rgba(2,132,199,.08), transparent 50%);
  animation:floatBg 40s linear infinite;
}

body::after{
  animation-duration:60s;
  animation-direction:reverse;
  filter:blur(40px);
}

@keyframes floatBg{
  0%{transform:translate(0,0) rotate(0deg)}
  50%{transform:translate(4%, -6%) rotate(180deg)}
  100%{transform:translate(0,0) rotate(360deg)}
}

*{box-sizing:border-box}

body{
  margin:0;
  height:100vh;
  display:flex;
  background:#313338;
  color:white;
  font-family:"Trebuchet MS";
  overflow:hidden;
}

/* ---------- LEFT SIDEBAR ---------- */
.sidebar{
  width:280px;
  background:#2b2d31;
  padding:10px;
  display:flex;
  flex-direction:column;
  overflow:auto;
}

.section{margin-bottom:12px}

.item{
  padding:8px;
  border-radius:6px;
  cursor:pointer;
  display:flex;
  align-items:center;
  gap:8px;
}
.item:hover{background:#404249}

.icon{
  width:32px;
  height:32px;
  border-radius:50%;
  background:#5865F2;
  display:flex;
  align-items:center;
  justify-content:center;
  font-weight:bold;
}

/* ---------- ICON BUTTONS ---------- */
.icon-btn{
  position:relative;
  background:#1e1f22;
  border:none;
  color:white;
  width:34px;
  height:34px;
  border-radius:8px;
  cursor:pointer;
  display:flex;
  align-items:center;
  justify-content:center;
}
.icon-btn:hover{background:#404249}

.icon-btn::after{
  content:attr(data-tip);
  position:absolute;
  bottom:-140%;
  left:50%;
  transform:translateX(-50%);
  background:#111;
  padding:5px 8px;
  font-size:12px;
  border-radius:6px;
  opacity:0;
  pointer-events:none;
  white-space:nowrap;
}
.icon-btn:hover::after{opacity:1}

/* ---------- BADGE TOOLTIP ---------- */
.badge{
  position:relative;
  font-size:13px;
  cursor:default;
}

.badge::after{
  content:attr(data-tip);
  position:absolute;
  bottom:-140%;
  left:50%;
  transform:translateX(-50%);
  background:#111;
  padding:4px 7px;
  font-size:11px;
  border-radius:6px;
  opacity:0;
  pointer-events:none;
  white-space:nowrap;
  z-index:50;
}

.badge:hover::after{opacity:1}

/* ---------- MAIN LAYOUT ---------- */
.main{
  flex:1;
  display:flex;
  flex-direction:row;
  min-width:0;
}

/* ---------- CHAT AREA ---------- */
.chat{
  flex:1;
  display:flex;
  flex-direction:column;
  min-width:0;
}

.topbar{
  display:flex;
  justify-content:space-between;
  align-items:center;
  padding:10px;
  background:#2b2d31;
  border-bottom:1px solid #404249;
}

.messages{
  flex:1;
  overflow-y:auto;
  padding:10px;
}

.msg{margin-bottom:10px}
.msg b{color:#949cf7}

.typing{
  font-size:12px;
  color:#b5bac1;
  padding:0 10px;
}

.input{
  padding:10px;
  border-top:1px solid #404249;
}

input{
  width:100%;
  padding:12px;
  border-radius:8px;
  border:none;
  background:#1e1f22;
  color:white;
}

/* ---------- RIGHT MEMBERS ---------- */
.members{
  width:260px;
  background:#2b2d31;
  padding:10px;
  overflow-y:auto;
  border-left:1px solid #404249;
  flex-shrink:0;
}

.member-section{margin-bottom:14px}

.member-section-title{
  font-size:12px;
  color:#b5bac1;
  margin-bottom:6px;
}

.member{
  display:flex;
  align-items:center;
  gap:8px;
  padding:6px;
  border-radius:6px;
}
.member:hover{background:#404249}

.status-dot{
  width:8px;
  height:8px;
  border-radius:50%;
}

.member.online .status-dot{background:#23a55a}
.member.offline .status-dot{background:#80848e}

/* ---------- SEARCH ---------- */
.searchOverlay{
  position:absolute;
  inset:0;
  background:#2b2d31;
  display:none;
  flex-direction:column;
  z-index:10;
}

.searchResults{
  flex:1;
  overflow:auto;
  padding:10px;
}

.result{
  padding:8px;
  border-radius:6px;
  cursor:pointer;
}
.result:hover{background:#404249}

.msg{
  max-width:78%;
  padding:10px 14px;
  border-radius:18px;
  margin-bottom:8px;
  line-height:1.35;
  background:#0f172a;
  border:1px solid #1e293b;
  animation:msgIn .16s ease;
}

.msg b{
  display:block;
  font-size:16px;
  color:var(--blue);
  margin-bottom:2px;
}

.msg.me{
  margin-left:auto;
  background:linear-gradient(135deg,#0ea5e9,#0284c7);
  color:white;
  border:none;
  box-shadow:0 0 14px rgba(14,165,233,.35);
}

.msg.me b{
  display:none;
}

/* ---------- MOBILE ---------- */
@media(max-width:900px){
  .members{display:none}
}

body{
  background:radial-gradient(circle at top,#020617,#020617);
  color:var(--text-main);
}

.sidebar,
.topbar,
.members,
.searchOverlay{
  background:var(--bg-panel);
}

.item:hover,
.member:hover,
.result:hover{
  background:var(--bg-hover);
}

input{
  background:var(--bg-input);
  color:var(--text-main);
}

.msg b{
  color:var(--blue);
}
.item,
.member,
.icon-btn,
.game-card{
  transition:background .2s, transform .15s, box-shadow .15s;
}

.item:hover,
.member:hover{
  transform:translateX(3px);
}

.icon-btn:hover{
  box-shadow:var(--glow);
  transform:translateY(-1px);
}

.icon-btn:active{
  transform:scale(.95);
}
.msg{
  animation:fadeSlide .25s ease;
}

@keyframes fadeSlide{
  from{opacity:0; transform:translateY(6px)}
  to{opacity:1; transform:none}
}
.icon{
  background:linear-gradient(135deg,var(--blue),var(--blue-dark));
  box-shadow:var(--glow);
}

.status-dot{
  box-shadow:0 0 4px currentColor;
}

body.phone .sidebar{
  position:fixed;
  display:flex;
  flex-direction:column;
  align-items:stretch;
  padding:14px 12px;
  gap:12px;
  overflow-y:auto;
  top:0;
  left:0;
  bottom:0;
  width:60vw;
  height: 100vh;
  max-width:340px;
  transform:translateX(-100%);
  transition:transform .25s ease;
  z-index:100;
  box-shadow:20px 0 60px rgba(0,0,0,.6);
}

body.phone .sidebar .section{
  margin-bottom:14px;
}

body.phone .sidebar.open{
  transform:translateX(0);
}

body.phone .main{
  flex:1;
}

@media(max-width:900px){

  body{
    flex-direction:column;
  }

  .sidebar{
    width:100%;
    height:56px;
    flex-direction:row;
    align-items:center;
    gap:10px;
    overflow-x:auto;
    overflow-y:hidden;
  }

  .sidebar b{
    display:none;
  }

  .main{
    flex:1;
  }

  .topbar{
    position:sticky;
    top:0;
    z-index:20;
  }

  .messages{
    padding-bottom:80px;
  }

  .input{
    position:fixed;
    bottom:0;
    left:0;
    right:0;
    background:var(--bg-panel);
    border-top:1px solid var(--border);
    z-index:30;
  }

  .members{
    display:none;
  }

  #gameUI{
    width:92vw;
    height:70vh;
    right:4vw;
    bottom:80px;
  }
}
</style>
</head>
<div id="profilePopup" style="
position:fixed;
display:none;
background:#1e1f22;
border:1px solid #404249;
border-radius:14px;
padding:14px;
width:260px;
z-index:999;
box-shadow:0 10px 30px rgba(0,0,0,.6);
">
  <b id="ppName"></b>
  <div id="ppBadges" style="margin:6px 0"></div>
  <div id="ppBio" style="font-size:13px;color:#b5bac1;margin-bottom:8px"></div>
  <div style="font-size:12px;color:#888">
    Joined: <span id="ppJoined"></span><br>
    Last online: <span id="ppSeen"></span>
  </div>
</div>
<body>

<div class="sidebar">
  <div style="display:flex;justify-content:space-between;align-items:center;">
    <b style="font-size:20px;">💬</b>
    <div style="display:flex;gap:6px;">
      <button class="icon-btn" data-tip="Settings" onclick="location.href='/settings'">⚙</button>
      <button class="icon-btn" data-tip="Search" onclick="showSearch()">🔍</button>
      <button class="icon-btn" data-tip="Create room" onclick="showCreateRoom()">➕</button>
    </div>
  </div>

  <div class="section"><b>Friends</b><div id="friends"></div></div>
  <div class="section"><b>Rooms</b><div id="rooms"></div></div>
  <a href="/logout" style="margin-top:auto;color:#aaa;text-align:center;">Logout</a>
  <a style="color:#aaa;text-align:center;">Made by Jacob Saranen</a>
</div>

<div class="main">

  <div class="chat">
    <div class="topbar">
      <div style="display:flex;align-items:center;gap:8px">
        <button class="icon-btn" onclick="toggleSidebar()">☰</button>
        <div id="currentRoom">Welcome 👋</div>
      </div>

      <div style="display:flex;gap:6px;">
        <button class="icon-btn" data-tip="Games" onclick="showGames()">🎮</button>
        <button class="icon-btn" data-tip="Room image" onclick="uploadRoomImage()">🖼</button>
        <button class="icon-btn" data-tip="Rename room" onclick="renameRoom()">✏️</button>
        <button class="icon-btn" data-tip="Leave room" onclick="leaveRoom()">🚪</button>
        <button class="icon-btn" data-tip="Invite" onclick="invite()">🔗</button>
      </div>
    </div>

    <div class="messages" id="msgs"></div>
    <div class="typing" id="typing"></div>

    <div class="input">
      <input id="msg" placeholder="Message"
        onkeydown="if(event.key==='Enter')send();typingPing()">
    </div>
  </div>

  <div class="members">
    <div class="member-section">
      <div class="member-section-title">ONLINE</div>
      <div id="online"></div>
    </div>
    <div class="member-section">
      <div class="member-section-title">OFFLINE</div>
      <div id="offline"></div>
    </div>
  </div>

</div>

<div class="searchOverlay" id="searchView">
  <div style="padding:10px;border-bottom:1px solid #404249;display:flex;gap:6px">
    <input id="search" placeholder="Search users or rooms…" oninput="doSearch()">
    <button class="icon-btn" onclick="hideSearch()">✖</button>
  </div>
  <div class="searchResults" id="results"></div>
</div>

<style>
.game-card{
  background:#2b2d31;
  border-radius:16px;
  padding:16px;
  cursor:pointer;
  display:flex;
  flex-direction:column;
  gap:6px;
  font-size:26px;
  transition:.18s;
}
.game-card b{
  font-size:15px;
}
.game-card span{
  font-size:12px;
  color:#b5bac1;
}
.game-card:hover{
  transform:translateY(-4px) scale(1.03);
  background:#313338;
}
.game-card:active{
  transform:scale(.97);
}
@media(max-width:700px){

  body{
    height:100dvh;
    overscroll-behavior: none;
  }

  .sidebar{
    height:52px;
    padding:6px 8px;
    border-bottom:1px solid var(--border);
  }

  .main{
    flex-direction:column;
  }

  .chat{
    flex:1;
    display:flex;
    flex-direction:column;
  }

  .topbar{
    padding:8px 10px;
    font-size:14px;
  }

  .messages{
    padding:12px 10px 96px;
    scroll-behavior:smooth;
  }

  .input{
    padding:10px;
  }

  input{
    padding:14px;
    font-size:16px; /* prevents iOS zoom */
    border-radius:14px;
  }

  #gameUI{
    width:94vw;
    height:72vh;
  }
}
</style>

<div id="gamePopup" style="
position:fixed;
inset:0;
background:rgba(0,0,0,.65);
display:none;
align-items:center;
justify-content:center;
z-index:1000;
backdrop-filter: blur(6px);
">

  <div style="
    width:420px;
    background:#1e1f22;
    border-radius:22px;
    padding:22px;
    box-shadow:0 30px 80px rgba(0,0,0,.8);
    animation:pop .25s ease;
  ">

    <div style="display:flex;align-items:center;justify-content:space-between">
      <h2 style="margin:0;font-size:22px">🎮 Games</h2>
      <button onclick="hideGames()" style="
        background:none;
        border:none;
        color:#b5bac1;
        font-size:20px;
        cursor:pointer;
      ">✕</button>
    </div>

    <div style="
      display:grid;
      grid-template-columns:1fr 1fr;
      gap:14px;
      margin-top:18px;
    ">

      <div class="game-card" onclick="startGame('chess')">
        ♟
        <b>Chess</b>
        <span>Classic strategy</span>
      </div>

      <div class="game-card" onclick="startGame('checkers')">
        🔴
        <b>Checkers</b>
        <span>Fast & competitive</span>
      </div>

      <div class="game-card" onclick="startGame('wordle')">
        🟩
        <b>Wordle VS</b>
        <span>Race to solve</span>
      </div>

      <div class="game-card" onclick="startGame('ttt')">
        ❌⭕
        <b>Tic-Tac-Toe</b>
        <span>Quick match</span>
      </div>

    </div>

  </div>
</div>

<div id="gameUI" style="
position:fixed;
right:20px;
bottom:20px;
width:360px;
height:420px;
background:#1e1f22;
border-radius:18px;
box-shadow:0 20px 60px rgba(0,0,0,.7);
display:none;
flex-direction:column;
animation:pop .25s ease;
z-index:999;">
  <div style="padding:12px;border-bottom:1px solid #404249">
    <b id="gameTitle"></b>
    <span id="turnInfo" style="float:right;color:#b5bac1"></span>
  </div>
  <div id="gameBoard" style="flex:1;padding:12px"></div>
</div>

<style>
@keyframes pop{
  from{transform:scale(.8);opacity:0}
  to{transform:scale(1);opacity:1}
}
.cell{
  transition:.15s;
}
.cell:hover{
  transform:scale(1.1);
}
</style>

<script>
  const myId = {{ user[0] }};
</script>

<script>
let lastSeen = {};   // roomId -> last message id seen
let unread = {};    // roomId -> boolean
let room=null;
let lastMsgId = 0;
let isAtBottom = true;

msgs.addEventListener('scroll', () => {
  isAtBottom =
    msgs.scrollTop + msgs.clientHeight >= msgs.scrollHeight - 20;
});

function isPhone(){
  const r = window.innerWidth / window.innerHeight;
  const touch =
    'ontouchstart' in window ||
    navigator.maxTouchPoints > 0;

  return touch && r < 0.85;
}

function setDeviceClass(){
  document.body.classList.toggle('phone', isPhone());
}

setDeviceClass();
window.addEventListener('resize', setDeviceClass);
window.addEventListener('orientationchange', setDeviceClass);

function toggleSidebar(force){
  if(!document.body.classList.contains('phone')) return;

  const sb = document.querySelector('.sidebar');
  sb.classList.toggle('open', force);
}

document.addEventListener('click', e=>{
  if(
    document.body.classList.contains('phone') &&
    !document.querySelector('.sidebar').contains(e.target) &&
    !e.target.closest('.icon-btn')
  ){
    toggleSidebar(false);
  }
});

function pollTyping(roomId) {
  fetch(`/typing/${roomId}`)
    .then(r => r.json())
    .then(users => {
      const el = document.getElementById("typing");
      el.textContent = users.length
        ? `${users.join(", ")} typing...`
        : "";
    });
}

setInterval(() => {
  if (room) pollTyping(room);
}, 1000);

function sendTyping(roomId) {
  if (typingCooldown) return;
  typingCooldown = true;

  fetch("/typing", {
    method: "POST",
    body: new URLSearchParams({ room: roomId })
  });

  setTimeout(() => typingCooldown = false, 1000);
}

function showGames(){
gamePopup.style.display='flex';
}

function hideGames(){
gamePopup.style.display='none';
}

function showGames1(){
  if(!currentRoom) return;

  fetch(`/api/game/list/${room}`)
    .then(r=>r.json())
    .then(data=>{
      const box = document.getElementById("gamePopup");
      let html = "";

      if(data.active){
        html = `
          <div class="game-card">
            🎮 <b>${data.game.toUpperCase()}</b>
            <span>Status: ${data.status}</span>
            <button onclick="joinGame()">Join</button>
          </div>
        `;
      } else {
        html = `
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
            <div class="game-card" onclick="startGame('chess')">♟<b>Chess</b></div>
            <div class="game-card" onclick="startGame('checkers')">⚫<b>Checkers</b></div>
            <div class="game-card" onclick="startGame('wordle')">🟩<b>Wordle</b></div>
          </div>
        `;
      }

      box.querySelector("div").innerHTML = html;
      box.style.display="flex";
    });
}

function startGame(game){
  fetch("/api/game/start",{
    method:"POST",
    headers:{"Content-Type":"application/x-www-form-urlencoded"},
    body:`room=${currentRoom}&game=${game}`
  }).then(()=>location.reload());
}

function joinGame() {
  fetch("/api/game/join", {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded"
    },
    body: `room=${currentRoom}`
  })
  .then(r => r.json())
  .then(() => {
    closeGamePopup();
    loadGameState();   // refresh UI without reloading page
  });
}

async function openGame(){
  let g = await fetch('/api/game/state/'+room).then(r=>r.json());

  gameUI.style.display='flex';
  gameTitle.textContent = g.game.toUpperCase();
  turnInfo.textContent = g.turn == myId ? "Your turn" : "Opponent's turn";

  if(g.game === "wordle") renderWordle();
  if(g.game === "chess") renderChess(g.state);
  if(g.game === "checkers") renderCheckers(g.state);
}

function renderWordle(){
  gameBoard.innerHTML = '';
  for(let r=0;r<6;r++){
    let row=document.createElement('div');
    row.style.display='flex';
    for(let c=0;c<5;c++){
      let cell=document.createElement('div');
      cell.className='cell';
      cell.style.width='48px';
      cell.style.height='48px';
      cell.style.border='2px solid #444';
      cell.style.margin='3px';
      row.appendChild(cell);
    }
    gameBoard.appendChild(row);
  }
}

function renderChess(state){
  gameBoard.innerHTML='';
  let rows = state.split('/');
  rows.forEach(r=>{
    let row=document.createElement('div');
    row.style.display='flex';
    for(let i=0;i<8;i++){
      let c=document.createElement('div');
      c.className='cell';
      c.style.width='40px';
      c.style.height='40px';
      c.style.background=(i%2?'#769656':'#eeeed2');
      row.appendChild(c);
    }
    gameBoard.appendChild(row);
  });
}

setInterval(()=>{
  if(gameUI.style.display==='flex') openGame();
}, 2000);

async function showProfile(uid, ev){
  let p = await fetch('/api/user/'+uid).then(r=>r.json());

  ppName.textContent = p.username;
  ppBio.textContent = p.bio;
  ppBadges.innerHTML = p.badges.map(b=>`<span class="badge">${b}</span>`).join(' ');

  ppJoined.textContent = new Date(p.joined*1000).toLocaleDateString();
  ppSeen.textContent = new Date(p.last_seen*1000).toLocaleString();

  let x = ev.clientX - 280;   // LEFT of cursor
  let y = ev.clientY - 20;

  if(x < 10) x = ev.clientX + 20; // fallback right
  if(y < 10) y = 10;

  profilePopup.style.left = x + 'px';
  profilePopup.style.top  = y + 'px';
  profilePopup.style.display = 'block';
}


document.body.onclick = e=>{
  if(!profilePopup.contains(e.target)){
    profilePopup.style.display='none';
  }
}

async function loadMsgs(){
  if(!room) return;

  let m = await fetch('/messages/' + room).then(r => r.json());

  m.forEach(x => {
    if(x.id <= lastMsgId) return;

    let d = document.createElement('div');
    d.className = 'msg';
    d.innerHTML = '<b>'+x.user+'</b><br>'+x.content;

    if(x.content.includes("Join to play")){
      d.innerHTML += `<br><button onclick="joinGame()">Join</button>`;
    }

    d.onclick = async ()=>{
      await navigator.clipboard.writeText(x.content);
      d.style.opacity = 0.5;
      setTimeout(()=>d.style.opacity=1, 200);
    };

    msgs.appendChild(d);
    lastMsgId = x.id;
  });

  if(isAtBottom){
    msgs.scrollTop = msgs.scrollHeight;
  }

  unread[room] = false;
}

async function checkNotifications(){
  let data = await fetch('/notifications').then(r=>r.json());
  data.forEach(showNotification);
}

/* FRIENDS */
async function loadFriends(){
 let f=await fetch('/friends').then(r=>r.json());
 friends.innerHTML='';
 f.forEach(x=>{
  let d=document.createElement('div');
  d.className='item';
  d.innerHTML='<div class=icon>'+x[1][0].toUpperCase()+'</div>'+x[1];
  d.onclick=()=>openDM(x[0],x[1]);
  friends.appendChild(d);
 });
}

/* ROOMS */
async function loadRooms(){
 let r=await fetch('/my/rooms').then(r=>r.json());
 rooms.innerHTML='';
 r.forEach(x=>{
  let d=document.createElement('div');
  d.className='item';

  let icon = x.image
    ? `<img src="${x.image}" style="width:32px;height:32px;border-radius:50%">`
    : `<div class=icon>${x.name[0].toUpperCase()}</div>`;

  d.innerHTML = icon + x.name;
  d.onclick=()=>openRoom(x.id,x.name);
  rooms.appendChild(d);
 });
}

/* MEMBERS */
async function loadMembers(){
 if(!room)return;
 let m=await fetch('/room_members/'+room).then(r=>r.json());
 online.innerHTML=''; offline.innerHTML='';
 m.forEach(u=>{
  let d=document.createElement('div');
  d.onclick = (ev)=>{ ev.stopPropagation(); showProfile(u.id, ev); }
  d.className='member '+(u.online?'online':'offline');
  d.innerHTML='<span class=status-dot></span><b>'+u.name+'</b>';

  if(u.owner) d.innerHTML+=' <span class="badge" data-tip="Server Owner">👑</span>';
  else if(u.admin) d.innerHTML+=' <span class="badge" data-tip="Administrator">🛡</span>';

  u.badges.forEach(b=>{
    if(b == '🛠'){
      d.innerHTML+=' <span class="badge" data-tip="Developer">'+b+'</span>';
      }
    else if(b == '⭐'){
      d.innerHTML+=' <span class="badge" data-tip="Founder">'+b+'</span>';
      }
    else if(b == '🔥'){
      d.innerHTML+=' <span class="badge" data-tip="OG">'+b+'</span>';
      }
    else if(b == '🪲'){
      d.innerHTML+=' <span class="badge" data-tip="Bug Tester">'+b+'</span>';
      }
    else if(b == '🤖'){
      d.innerHTML+=' <span class="badge" data-tip="Bot">'+b+'</span>';
      }
    else{
      d.innerHTML+=' <span class="badge" data-tip="'+b+'">'+b+'</span>';
      }
  });

  (u.online?online:offline).appendChild(d);
 });
}

/* CHAT */
async function openRoom(id,name){
  room = id;
  lastMsgId = 0;
  msgs.innerHTML = '';
  currentRoom.textContent = '# ' + name;
  loadMsgs();
  loadMembers();
}

async function openDM(uid,name){
 let r=await fetch('/dm/'+uid).then(r=>r.json());
 openRoom(r.room,name);
}

function uploadRoomImage(){
  let i=document.createElement('input');
  i.type='file';
  i.accept='image/*';

  i.onchange=async ()=>{
    let f=new FormData();
    f.append("file", i.files[0]);
    await fetch('/api/rooms/'+room+'/icon',{method:'POST',body:f});
    loadRooms();
  };
  i.click();
}

async function send(){
  if(!msg.value || !room) return;

  let res = await fetch('/send', {
    method:'POST',
    body:new URLSearchParams({
      room,
      msg: msg.value
    })
  }).then(r=>r.json());

  msg.value='';
  loadMsgs();

}

function typingPing(){
 if(room)fetch('/typing',{method:'POST',body:new URLSearchParams({room})});
}

/* SEARCH */
function showSearch(){searchView.style.display='flex'}
function hideSearch(){searchView.style.display='none';results.innerHTML=''}

async function doSearch(){
  let q = search.value.trim();
  if(!q){
    results.innerHTML='';
    return;
  }

  let r = await fetch('/search?q='+encodeURIComponent(q)).then(r=>r.json());
  results.innerHTML='';

  /* USERS → click adds friend */
  r.users.forEach(u=>{
    let d=document.createElement('div');
    d.className='result';
    d.innerHTML = `👤 <b>${u.name}</b> ${u.online ? '🟢' : '⚪'}`;
    d.onclick = async ()=>{
      await fetch('/friend/'+u.id);   // ✅ correct method
      loadFriends();
      hideSearch();
    };
    results.appendChild(d);
  });

  /* ROOMS → click JOIN + OPEN */
  r.rooms.forEach(rm=>{
    let d=document.createElement('div');
    d.className='result';
    d.innerHTML = `🏠 <b>${rm.name}</b>`;
    d.onclick = async ()=>{
      await fetch('/join/'+rm.id);     // ✅ ACTUALLY JOINS ROOM
      loadRooms();
      openRoom(rm.id, rm.name);
      hideSearch();
    };
    results.appendChild(d);
  });
}

if(window.visualViewport){
  const inputBar = document.querySelector('.input');

  function adjustForKeyboard(){
    const vv = window.visualViewport;
    const offset = window.innerHeight - vv.height - vv.offsetTop;

    inputBar.style.bottom = offset > 0 ? offset + 'px' : '0';
  }

  visualViewport.addEventListener('resize', adjustForKeyboard);
  visualViewport.addEventListener('scroll', adjustForKeyboard);
}

/* ROOM ACTIONS */
function showCreateRoom(){
 let n=prompt('Room name');
 if(n)fetch('/create_room',{method:'POST',body:new URLSearchParams({name:n})}).then(loadRooms);
}

async function renameRoom(){
 let n=prompt('New name');
 if(n)fetch('/api/rooms/'+room+'/rename',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:n})});
}

async function leaveRoom(){
 await fetch('/api/rooms/'+room+'/leave',{method:'POST'});
 room=null;
}

async function invite(){
 let r=await fetch('/api/rooms/'+room+'/invite').then(r=>r.json());
 alert(location.origin+r.link);
}

if ("Notification" in window) {
  Notification.requestPermission();
}

function showNotification(n){
  if (Notification.permission !== "granted") return;

  let title = n.is_dm
    ? `DM from ${n.author}`
    : `#${n.room_name}`;

  new Notification(title, {
    body: `${n.author}: ${n.content}`,
    icon: "https://cdn-icons-png.flaticon.com/512/685/685887.png"
  });
}


loadRooms();
loadFriends();
setInterval(loadMsgs,1000);
setInterval(loadMembers,2000);
// poll notifications every 3 seconds
setInterval(checkNotifications, 3000);
</script>

</body>
</html>
"""

ADMIN_HTML = """
<!doctype html>
<title>Admin Panel</title>
<h1>Admin Panel</h1>

<h2>Users</h2>
{% for u in users %}
<div>{{ u[1] }} {% if not u[2] %}<a href="/admin/promote/{{ u[0] }}">Promote</a>{% endif %}</div>
{% endfor %}

<h2>Rooms</h2>
{% for r in rooms %}
<div>{{ r[1] }}</div>
{% endfor %}
<h2><a href="/admin/server">Server Control</a></h2>
<h2><a href="/admin/db">Database Browser</a></h2>  <!-- Link to the DB browser -->
"""

DB_BROWSER_HTML = """
<!doctype html>
<title>Database Browser</title>
<style>
body {
    font-family: sans-serif;
    background-color: #333;
    color: white;
    padding: 20px;
}

h1 {
    text-align: center;
}

table {
    width: 100%;
    margin: 20px 0;
    border-collapse: collapse;
}

table, th, td {
    border: 1px solid #fff;
}

th, td {
    padding: 8px;
    text-align: left;
}

th {
    background-color: #444;
}

td {
    background-color: #555;
}

input[type="text"] {
    padding: 5px;
    border-radius: 4px;
    border: none;
    margin: 5px 0;
    width: 90%;
}

button {
    padding: 5px 10px;
    background-color: #5865F2;
    color: white;
    border: none;
    border-radius: 4px;
    cursor: pointer;
}

button:hover {
    background-color: #4a65f2;
}
</style>

<h1>Database Browser</h1>

{% for table, table_data in data.items() %}
    <h2>{{ table | capitalize }}</h2>
    <table>
        <thead>
            <tr>
                {% for column in table_data.columns %}
                    <th>{{ column }}</th>
                {% endfor %}
                <th>Actions</th>
            </tr>
        </thead>
        <tbody>
            {% for row in table_data.rows %}
                <tr>
                    {% for i in range(table_data.columns|length) %}
                        <td>{{ row[i] }}</td>
                    {% endfor %}
                    <td>
                        <form method="POST" action="/admin/db/update">
                            <input type="hidden" name="table" value="{{ table }}">
                            <input type="hidden" name="id" value="{{ row[0] }}">
                            <select name="column">
                                {% for column in table_data.columns %}
                                    <option value="{{ column }}">{{ column }}</option>
                                {% endfor %}
                            </select>
                            <input type="text" name="new_value" placeholder="New Value" required>
                            <button type="submit">Update</button>
                        </form>
                    </td>
                </tr>
            {% endfor %}
        </tbody>
    </table>
{% endfor %}
"""

SERVER_DISABLED_HTML = """
<!doctype html>
<title>Site Disabled</title>
<style>
body{
  background:#1e1f22;
  color:white;
  font-family:system-ui,sans-serif;
  display:flex;
  align-items:center;
  justify-content:center;
  height:100vh;
}
.card{
  background:#2b2d31;
  padding:40px;
  border-radius:18px;
  max-width:420px;
  text-align:center;
  box-shadow:0 0 30px rgba(0,0,0,.6);
}
h1{margin-bottom:10px}
.reason{
  margin-top:12px;
  color:#ff8c8c;
  font-size:15px;
}
</style>

<div class="card">
  <h1>🚫 Site Disabled</h1>
  <p>This site is currently unavailable.</p>
  <div class="reason">
    <b>Reason:</b><br>
    {{ reason }}
  </div>
</div>
"""

SERVER_CONTROL_HTML = """
<!doctype html>
<title>Server Control</title>
<style>
body{
  background:#1e1f22;
  color:white;
  font-family:sans-serif;
  padding:40px;
}
.card{
  background:#2b2d31;
  padding:30px;
  border-radius:18px;
  max-width:400px;
}
button{
  padding:12px;
  width:100%;
  border:none;
  border-radius:8px;
  font-weight:bold;
  cursor:pointer;
}
.enable{background:#23a55a;color:white}
.disable{background:#ed4245;color:white}
textarea{
  width:100%;
  height:80px;
  background:#1e1f22;
  color:white;
  border:none;
  border-radius:8px;
  padding:10px;
}
</style>

<div class="card">
  <h2>Server Status</h2>

  <p>
    Current state:
    <b style="color:{{ '#23a55a' if enabled else '#ed4245' }}">
      {{ 'ENABLED' if enabled else 'DISABLED' }}
    </b>
  </p>

  <form method="post">
    <label>Disable reason</label>
    <textarea name="reason">{{ reason }}</textarea>

    <input type="hidden" name="enabled" value="{{ 0 if enabled else 1 }}">

    {% if enabled %}
      <button class="disable">Disable Server</button>
    {% else %}
      <button class="enable">Enable Server</button>
    {% endif %}
  </form>

  <br>
  <a href="/admin" style="color:#aaa">← Back to Admin</a>
</div>
"""


if __name__=="__main__":
    app.run(debug=True,host="0.0.0.0",port=5001)
