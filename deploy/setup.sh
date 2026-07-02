#!/usr/bin/env bash
# Installazione del bot su un server Linux (Ubuntu/Debian o Amazon Linux).
# Uso:
#   curl -sL https://raw.githubusercontent.com/valentino500x/Telegram-Token-Bot/claude/aster-dex-trading-bot-7wg68p/deploy/setup.sh | bash
set -e

BRANCH="claude/aster-dex-trading-bot-7wg68p"
REPO="https://github.com/valentino500x/Telegram-Token-Bot"
DIR="$HOME/aster-bot"

echo "== 1/4 Installo le dipendenze di sistema =="
if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -y -q
    sudo apt-get install -y -q python3 python3-pip python3-venv git nano
elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y -q python3 python3-pip git nano
elif command -v yum >/dev/null 2>&1; then
    sudo yum install -y -q python3 python3-pip git nano
else
    echo "Gestore pacchetti non riconosciuto: installa python3, pip e git a mano" >&2
    exit 1
fi

echo "== 2/4 Scarico il bot =="
if [ -d "$DIR/.git" ]; then
    git -C "$DIR" fetch origin "$BRANCH"
    git -C "$DIR" checkout "$BRANCH"
    git -C "$DIR" pull --ff-only origin "$BRANCH"
else
    git clone --branch "$BRANCH" "$REPO" "$DIR"
fi

cd "$DIR"
echo "== 3/4 Installo le librerie Python =="
python3 -m venv .venv
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt -q

if [ ! -f .env ]; then
    cp .env.example .env
fi

echo "== 4/4 Configuro l'avvio automatico (systemd) =="
sudo tee /etc/systemd/system/asterbot.service >/dev/null <<EOF
[Unit]
Description=Aster DEX Telegram trading bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$DIR
ExecStart=$DIR/.venv/bin/python BOT.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable asterbot >/dev/null

echo ""
echo "===================================================================="
echo " Installazione completata!"
echo ""
echo " 1) Configura le chiavi:    nano $DIR/.env"
echo "    (incolla i valori dal .env del tuo PC, poi CTRL+O Invio, CTRL+X)"
echo " 2) Avvia il bot:           sudo systemctl start asterbot"
echo " 3) Controlla che giri:     sudo systemctl status asterbot"
echo " 4) Log in diretta:         sudo journalctl -u asterbot -f"
echo ""
echo " Il bot si riavvia da solo in caso di crash e parte a ogni reboot."
echo " Per fermarlo:              sudo systemctl stop asterbot"
echo " Per aggiornarlo:           rilancia questo stesso script"
echo "===================================================================="
