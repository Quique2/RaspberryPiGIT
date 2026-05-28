#!/bin/bash
# Digital Twin Gateway — arranca backend FastAPI + túnel ngrok estable
#
# URLs fijas (no cambian nunca):
#   WebSocket : wss://unmoral-shrink-cavalry.ngrok-free.dev/ws/cobot
#   REST      : https://unmoral-shrink-cavalry.ngrok-free.dev/api/cobot/state
#
# Uso: ./start_gateway.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

BACKEND_PORT=8000
NGROK_DOMAIN="unmoral-shrink-cavalry.ngrok-free.dev"

cleanup() {
    echo ""
    echo "  Deteniendo gateway..."
    kill "$BACKEND_PID" 2>/dev/null
    kill "$NGROK_PID"   2>/dev/null
    exit 0
}
trap cleanup INT TERM

echo ""
echo "  ┌─────────────────────────────────────────────────┐"
echo "  │   Digital Twin Gateway — Lexium Cobot           │"
echo "  └─────────────────────────────────────────────────┘"
echo ""

# 1. Arrancar backend FastAPI
echo "  [1/2] Arrancando backend FastAPI en puerto $BACKEND_PORT ..."
python3 -m uvicorn backend:app --host 0.0.0.0 --port $BACKEND_PORT > /tmp/backend_gateway.log 2>&1 &
BACKEND_PID=$!

sleep 6

if ! curl -sf http://localhost:$BACKEND_PORT/health > /dev/null; then
    echo "  ERROR: backend no respondió."
    cat /tmp/backend_gateway.log
    kill "$BACKEND_PID" 2>/dev/null
    exit 1
fi
echo "  ✓ Backend activo"

# 2. Arrancar túnel ngrok
echo "  [2/2] Abriendo túnel ngrok (dominio estático)..."
ngrok http --url=$NGROK_DOMAIN $BACKEND_PORT > /tmp/ngrok_gateway.log 2>&1 &
NGROK_PID=$!

sleep 5

echo ""
echo "  ╔═════════════════════════════════════════════════════════════╗"
echo "  ║  GATEWAY ACTIVO — URLs permanentes para el frontend:       ║"
echo "  ╠═════════════════════════════════════════════════════════════╣"
echo "  ║  WebSocket : wss://$NGROK_DOMAIN/ws/cobot  ║"
echo "  ║  REST      : https://$NGROK_DOMAIN/api/cobot/state  ║"
echo "  ╚═════════════════════════════════════════════════════════════╝"
echo ""
echo "  Health : https://$NGROK_DOMAIN/health"
echo "  Cobot  : $(grep -o 'modbus_connected.*' /tmp/backend_gateway.log 2>/dev/null | tail -1 || echo 'conectando...')"
echo ""
echo "  Ctrl+C para detener."
echo ""

wait "$BACKEND_PID"
