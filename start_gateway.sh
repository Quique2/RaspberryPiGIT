#!/bin/bash
# Digital Twin Gateway — arranca backend FastAPI + túnel Cloudflare
# La URL pública wss:// aparece en la consola para pegarla en el frontend.
#
# Uso:
#   ./start_gateway.sh              # túnel temporal (URL cambia cada arranque)
#   ./start_gateway.sh --stable     # túnel estable (requiere cuenta Cloudflare, ver abajo)

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

BACKEND_PORT=8000
TUNNEL_LOG=/tmp/cloudflared.log

cleanup() {
    echo ""
    echo "  Deteniendo gateway..."
    kill "$BACKEND_PID" 2>/dev/null
    kill "$TUNNEL_PID"  2>/dev/null
    exit 0
}
trap cleanup INT TERM

# 1. Arrancar backend FastAPI
echo ""
echo "  ┌─────────────────────────────────────────────────┐"
echo "  │   Digital Twin Gateway — Lexium Cobot           │"
echo "  └─────────────────────────────────────────────────┘"
echo ""
echo "  [1/2] Arrancando backend FastAPI en puerto $BACKEND_PORT ..."
python3 -m uvicorn backend:app --host 0.0.0.0 --port $BACKEND_PORT > /tmp/backend.log 2>&1 &
BACKEND_PID=$!
sleep 6

# Verificar que el backend está corriendo
if ! curl -sf http://localhost:$BACKEND_PORT/health > /dev/null; then
    echo "  ERROR: backend no respondió. Revisa los logs."
    kill "$BACKEND_PID" 2>/dev/null
    exit 1
fi
echo "  ✓ Backend activo — http://localhost:$BACKEND_PORT"

# 2. Arrancar túnel Cloudflare
echo "  [2/2] Abriendo túnel Cloudflare (wss://)..."
cloudflared tunnel --url http://localhost:$BACKEND_PORT --no-autoupdate 2>&1 | tee "$TUNNEL_LOG" &
TUNNEL_PID=$!

# Esperar hasta que aparezca la URL pública
echo "  Esperando URL pública..."
PUBLIC_URL=""
for i in $(seq 1 30); do
    PUBLIC_URL=$(grep -oP 'https://[a-z0-9\-]+\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | head -1)
    if [ -n "$PUBLIC_URL" ]; then
        break
    fi
    sleep 1
done

echo ""
if [ -n "$PUBLIC_URL" ]; then
    WS_URL="${PUBLIC_URL/https:\/\//wss://}/ws/cobot"
    REST_URL="$PUBLIC_URL/api/cobot/state"
    echo "  ╔═══════════════════════════════════════════════════╗"
    echo "  ║  GATEWAY ACTIVO — pega esto en el frontend:      ║"
    echo "  ╠═══════════════════════════════════════════════════╣"
    echo "  ║  WebSocket : $WS_URL"
    echo "  ║  REST      : $REST_URL"
    echo "  ╚═══════════════════════════════════════════════════╝"
    echo ""
    echo "  Health  : $PUBLIC_URL/health"
    echo ""
else
    echo "  ADVERTENCIA: No se pudo obtener la URL pública en 30s."
    echo "  Revisa: $TUNNEL_LOG"
fi

# Mantener vivo hasta Ctrl+C
wait "$BACKEND_PID"
