#Relay — Flask Chat Server

Relay is a self-hosted, Discord-style chat application built with Flask + SQLite.
It supports rooms, DMs, reactions, typing indicators, friends, notifications, admin tools, and lightweight multiplayer games — all without WebSockets.

This project is intentionally simple, hackable, and server-side rendered where possible.

#Features
#Core Chat
- User registration & login (session-based auth)
- Public chat rooms
- Private DMs
- Message reactions (emoji)
- Message editing (author-only)
- Typing indicators
- Online/offline status tracking
- Incremental notifications API
- Rooms
- Create / rename / delete rooms
- Join rooms manually or via invite links
- Room icons (PNG/JPG/WEBP)
- Room ownership + admin permissions
- Member list with online status & badges
- Friends & DMs
- Bi-directional friends system
- Auto-created DM rooms
- Friend list with online tracking
- User Profiles
- Display names
- Bios
- Message color customization
- Join date & last seen
- Special badges (DEV, OG, Bot, etc.)
- Games (Per-Room)
- Chess
- Checkers
- Wordle
- Guess-the-Number
- Tic-Tac-Toe (partial implementation)
- Games are stored server-side and synced via polling endpoints.
- Admin Tools
- Admin dashboard
- Promote users to admin
- Database browser (read/write)
- Server on/off kill switch with reason message
- Bots
- bot.py hook for automated messages / integrations
- Uses the same database + message pipeline as users
- Tech Stack
- Python 3
- Flask
- SQLite
- Vanilla JS + Fetch (client-side polling)
- No WebSockets
- No Redis
- No external auth providers

#Project Structure
.
├── flask_app.py        # Main Flask application
├── chat.db             # SQLite database (auto-created)
├── bot.py              # Optional bot logic
├── static/
│   └── room_icons/     # Uploaded room icons


Everything lives in one file on purpose. This is not an accident.

Installation
1. Install dependencies
pip install flask werkzeug


(That’s it.)

2. Run the server
python flask_app.py


By default, it runs on:

http://127.0.0.1:5000

3. First Run Notes

The database (chat.db) is created automatically

Tables auto-migrate on startup

There is no default admin

Manually promote a user via the database or /admin/promote/<uid>

Authentication Model

Cookie-based sessions

No tokens

No OAuth

No API keys

If you’re logged in via the browser, you’re authenticated.

API Overview

Full interactive docs available at:

/api/docs


Key endpoints:

POST /send — send message

GET /messages/<room_id> — fetch messages

GET /notifications — unread message notifications

POST /typing — typing indicator

GET /api/game/state/<room_id> — live game state

POST /api/game/start — start a game

POST /api/game/join — join a game

Polling is expected. This app does not use WebSockets.

Badges System

Badges are hardcoded in SPECIAL_BADGES:

SPECIAL_BADGES = {
    2: ["DEV", "FOUNDER", "OG"],
    7: ["Bot"],
}


Icons are mapped separately.
This is intentional — badges are not user-editable.

Security Notes (Read This)

Passwords are SHA-256 hashed (no salt)

No rate limiting

No CSRF protection

Admin DB editor can modify live data

This is not production-hardened

If you expose this to the internet, that’s on you.

Intended Use

Relay is meant for:

Private servers

LAN chat

School / club projects

Bots & automation experiments

Learning Flask by reading real code
