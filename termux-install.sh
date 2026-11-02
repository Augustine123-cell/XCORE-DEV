#!/data/data/com.termux/files/usr/bin/bash
set -e
pkg update -y
pkg install python git nano -y
python -m venv .venv
.venv/bin/pip install -r requirements.txt
cp -n .env.example .env 2>/dev/null || true
echo "Fill TELEGRAM_BOT_TOKEN and BAN_CHECKER_API_KEY in .env"
echo "Then run: .venv/bin/python -m xcore"
