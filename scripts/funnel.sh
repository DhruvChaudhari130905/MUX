#!/usr/bin/env bash
# One command to share MUX with teammates on other computers: opens the Cloudflare quick tunnels
# (like scripts/tunnel.sh), then starts the API and web app already pointed at the public addresses.
# Ctrl+C stops everything. The addresses change on every run.
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
server_dir="$root/mux/server"
web_dir="$root/mux/web"

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared isn't installed. On macOS: brew install cloudflared" >&2
  exit 1
fi

for port in 3000 8000; do
  if lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port $port is already in use. Stop the running \`make server\` / \`make web\` first (Ctrl+C in their terminals)." >&2
    exit 1
  fi
done

logs=$(mktemp -d)
cleanup() {
  trap - EXIT INT TERM
  rm -rf "$logs"
  kill 0 2>/dev/null || true # everything this script started shares its process group
}
trap cleanup EXIT INT TERM

start_tunnel() { # port name
  cloudflared tunnel --no-autoupdate --url "http://localhost:$1" >"$logs/$2.log" 2>&1 &
}

wait_for_url() { # name
  for _ in $(seq 1 60); do
    url=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$logs/$1.log" | head -1 || true)
    if [ -n "$url" ]; then echo "$url"; return 0; fi
    sleep 0.5
  done
  echo "Timed out waiting for the $1 tunnel; its log:" >&2
  cat "$logs/$1.log" >&2
  return 1
}

echo "Opening tunnels..."
start_tunnel 8000 api
start_tunnel 3000 web
api_url=$(wait_for_url api)
web_url=$(wait_for_url web)

(cd "$server_dir" && WEB_APP_URL="$web_url" .venv/bin/uvicorn mux.main:app --reload --reload-dir mux --port 8000) &
(cd "$web_dir" && NEXT_PUBLIC_API_URL="$api_url" npm run dev) &

cat <<EOF

  Web app: $web_url
  API:     $api_url

  Open $web_url, sign in, and share room links from there.
  (Once only: Supabase dashboard > Authentication > URL Configuration > Redirect URLs,
   add https://*.trycloudflare.com/** )

  Ctrl+C stops the tunnels, the API and the web app.

EOF

wait
