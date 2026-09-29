#!/usr/bin/env bash
# Instalacja / aktualizacja prywatnego Dziennika treningowego.
# Użycie: bash install.sh bartek-dziennik.duckdns.org
set -euo pipefail
DOMAIN="${1:-}"
HOME_DIR=/root/dziennik
SRC=/root/dziennik/src
BRANCH=dziennik-app

say(){ printf '\n\033[1;32m== %s\033[0m\n' "$*"; }

say "1/6 Pobieram kod aplikacji"
mkdir -p "$HOME_DIR"
if [ -d "$SRC/.git" ]; then git -C "$SRC" fetch -q --depth 1 origin "$BRANCH" && git -C "$SRC" reset -q --hard FETCH_HEAD
else git clone -q --depth 1 -b "$BRANCH" https://github.com/stnczvk/garmin-steps "$SRC"; fi

say "1b Baza produktów (pierwszy raz ok. 200 MB)"
if [ -d "$HOME_DIR/data/.git" ]; then git -C "$HOME_DIR/data" fetch -q --depth 1 origin dziennik-data && git -C "$HOME_DIR/data" reset -q --hard FETCH_HEAD
else git clone -q --depth 1 -b dziennik-data https://github.com/stnczvk/garmin-steps "$HOME_DIR/data"; fi
ls "$HOME_DIR/data"

say "2/6 Środowisko Pythona"
[ -d "$HOME_DIR/venv" ] || python3 -m venv "$HOME_DIR/venv" || { apt-get update -qq && apt-get install -y -qq python3-venv && python3 -m venv "$HOME_DIR/venv"; }
"$HOME_DIR/venv/bin/pip" install -q --upgrade pip
"$HOME_DIR/venv/bin/pip" install -q fastapi 'uvicorn[standard]' httpx pillow python-multipart pywebpush

say "3/6 Hasło do aplikacji"
touch "$HOME_DIR/.env"; chmod 600 "$HOME_DIR/.env"
if ! grep -q '^DZ_PASS=' "$HOME_DIR/.env"; then
  while true; do
    read -rsp "Ustaw hasło do dziennika (min. 8 znaków): " P1; echo
    read -rsp "Powtórz hasło: " P2; echo
    [ "$P1" = "$P2" ] && [ ${#P1} -ge 8 ] && break
    echo "Hasła różne albo za krótkie – jeszcze raz."
  done
  H=$(P="$P1" "$HOME_DIR/venv/bin/python" -c 'import os,hashlib,secrets;s=secrets.token_bytes(16);print("scrypt$"+s.hex()+"$"+hashlib.scrypt(os.environ["P"].encode(),salt=s,n=2**14,r=8,p=1).hex())')
  echo "DZ_PASS=$H" >> "$HOME_DIR/.env"; unset P1 P2 H
  echo "Hasło zapisane."
else echo "Hasło już ustawione – zostawiam."; fi
grep -q '^ANTHROPIC_API_KEY=sk-' "$HOME_DIR/.env" && echo "Klucz API: jest." || echo "UWAGA: brak klucza API – odczyt etykiet i zrzutów nie zadziała."
sed -i '/^FOOD_DIR=/d' "$HOME_DIR/.env"
[ -n "$DOMAIN" ] && { sed -i '/^DZ_DOMAIN=/d' "$HOME_DIR/.env"; echo "DZ_DOMAIN=$DOMAIN" >> "$HOME_DIR/.env"; }
grep -q '^GARMIN_STEPS=' "$HOME_DIR/.env" || echo "GARMIN_STEPS=/root/garmin-sync/repo/steps.json" >> "$HOME_DIR/.env"

say "4/6 Usługa systemowa"
cat > /etc/systemd/system/dziennik.service <<EOF
[Unit]
Description=Dziennik treningowy
After=network-online.target
[Service]
Environment=DZ_HOME=$HOME_DIR
WorkingDirectory=$SRC/app
ExecStart=$HOME_DIR/venv/bin/uvicorn server:app --host 127.0.0.1 --port 8100 --proxy-headers
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable -q dziennik.service
systemctl restart dziennik.service

say "5/6 HTTPS (Caddy)"
if ! command -v caddy >/dev/null; then
  apt-get update -qq
  apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https curl gnupg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq && apt-get install -y -qq caddy
fi
if [ -n "$DOMAIN" ]; then
  cat > /etc/caddy/Caddyfile <<EOF
$DOMAIN {
	encode zstd gzip
	reverse_proxy 127.0.0.1:8100
}
EOF
  systemctl reload caddy 2>/dev/null || systemctl restart caddy
fi

say "6/6 Sprawdzenie"
sleep 3
systemctl is-active --quiet dziennik.service && echo "Aplikacja: działa" || { echo "Aplikacja: BŁĄD"; journalctl -u dziennik -n 20 --no-pager; }
curl -s -o /dev/null -w "Lokalnie: HTTP %{http_code}\n" http://127.0.0.1:8100/ || true
[ -n "$DOMAIN" ] && { sleep 5; curl -s -o /dev/null -w "Przez internet (https://$DOMAIN): HTTP %{http_code}\n" "https://$DOMAIN/" || echo "HTTPS jeszcze się nie odpowiada – certyfikat może się generować, sprawdź za minutę."; }
echo; echo "Gotowe. Otwórz: https://$DOMAIN"
