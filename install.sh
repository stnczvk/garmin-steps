#!/usr/bin/env bash
# Instalacja / aktualizacja Centrum social mediów (osobna aplikacja obok dziennika).
# Użycie: bash install.sh bartek-social.duckdns.org
set -euo pipefail
DOMAIN="${1:-}"
HOME_DIR=/root/social
SRC=$HOME_DIR/src
BRANCH=social-app
PORT=8200
say(){ printf '\n\033[1;32m== %s\033[0m\n' "$*"; }

say "1/5 Kod aplikacji"
mkdir -p "$HOME_DIR"
if [ -d "$SRC/.git" ]; then git -C "$SRC" fetch -q --depth 1 origin "$BRANCH" && git -C "$SRC" reset -q --hard FETCH_HEAD
else git clone -q --depth 1 -b "$BRANCH" https://github.com/stnczvk/garmin-steps "$SRC"; fi

say "2/5 Środowisko Pythona"
[ -d "$HOME_DIR/venv" ] || python3 -m venv "$HOME_DIR/venv"
"$HOME_DIR/venv/bin/pip" install -q --upgrade pip
"$HOME_DIR/venv/bin/pip" install -q fastapi 'uvicorn[standard]' httpx pywebpush

say "3/5 Hasło"
touch "$HOME_DIR/.env"; chmod 600 "$HOME_DIR/.env"
if ! grep -q '^SOCIAL_PASS=' "$HOME_DIR/.env"; then
  while true; do
    read -rsp "Ustaw hasło do centrum social mediów (min. 8 znaków): " P1; echo
    read -rsp "Powtórz hasło: " P2; echo
    [ "$P1" = "$P2" ] && [ ${#P1} -ge 8 ] && break
    echo "Hasła różne albo za krótkie – jeszcze raz."
  done
  H=$(P="$P1" "$HOME_DIR/venv/bin/python" -c 'import os,hashlib,secrets;s=secrets.token_bytes(16);print("scrypt$"+s.hex()+"$"+hashlib.scrypt(os.environ["P"].encode(),salt=s,n=2**14,r=8,p=1).hex())')
  echo "SOCIAL_PASS=$H" >> "$HOME_DIR/.env"; unset P1 P2 H
  echo "Hasło zapisane."
else echo "Hasło już ustawione – zostawiam."; fi
if [ -n "$DOMAIN" ]; then sed -i '/^SOCIAL_DOMAIN=/d' "$HOME_DIR/.env"; echo "SOCIAL_DOMAIN=$DOMAIN" >> "$HOME_DIR/.env"; fi
DOMAIN=$(grep '^SOCIAL_DOMAIN=' "$HOME_DIR/.env" | cut -d= -f2)

say "4/5 Usługa i HTTPS"
cat > /etc/systemd/system/social.service <<EOF
[Unit]
Description=Centrum social mediow
After=network-online.target
[Service]
Environment=SOCIAL_HOME=$HOME_DIR
WorkingDirectory=$SRC/app
ExecStart=$HOME_DIR/venv/bin/uvicorn server:app --host 127.0.0.1 --port $PORT --proxy-headers
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable -q social.service
systemctl restart social.service
if [ -n "$DOMAIN" ] && command -v caddy >/dev/null; then
  mkdir -p /etc/caddy/sites
  printf '%s {\n\tencode zstd gzip\n\treverse_proxy 127.0.0.1:%s\n}\n' "$DOMAIN" "$PORT" > /etc/caddy/sites/social.caddy
  grep -q 'import /etc/caddy/sites' /etc/caddy/Caddyfile || sed -i '1i import /etc/caddy/sites/*.caddy\n' /etc/caddy/Caddyfile
  systemctl reload caddy 2>/dev/null || systemctl restart caddy
fi
cat > /usr/local/bin/social-update <<'UPD'
#!/usr/bin/env bash
# Aktualizacja Centrum social mediów do najnowszej wersji z GitHuba
git -C /root/social/src fetch -q --depth 1 origin social-app && git -C /root/social/src reset -q --hard FETCH_HEAD && bash /root/social/src/install.sh
UPD
chmod +x /usr/local/bin/social-update

say "5/5 Sprawdzenie"
sleep 3
systemctl is-active --quiet social.service && echo "Aplikacja: działa" || { echo "Aplikacja: BŁĄD"; journalctl -u social -n 20 --no-pager; }
curl -s -o /dev/null -w "Lokalnie: HTTP %{http_code}\n" http://127.0.0.1:$PORT/ || true
[ -n "$DOMAIN" ] && { sleep 5; curl -s -o /dev/null -w "Przez internet (https://$DOMAIN): HTTP %{http_code}\n" "https://$DOMAIN/" || echo "HTTPS jeszcze nie odpowiada – certyfikat może się generować, sprawdź za minutę."; }
echo; echo "Gotowe. Otwórz: https://$DOMAIN"
