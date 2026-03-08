#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# run.sh — Start (or restart) the SRE Agent server.
#
# Behaviour:
#   1. If sre_agent.pid exists and the PID is still alive → kill it.
#   2. Activate the project venv.
#   3. Launch uvicorn in the background.
#   4. Write the new PID to sre_agent.pid.
#   5. Tail the log so you can see startup output.
# ─────────────────────────────────────────────────────────────

set -euo pipefail

# ── Paths ─────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/sre_agent.pid"
VENV_DIR="$SCRIPT_DIR/venv"
LOG_DIR="$(dirname "$SCRIPT_DIR")/logs"
LOG_FILE="$LOG_DIR/sre_agent.log"

# ── Ensure logs directory exists ──────────────────────────────
mkdir -p "$LOG_DIR"

# ── Kill existing process if running ─────────────────────────
if [[ -f "$PID_FILE" ]]; then
    OLD_PID="$(cat "$PID_FILE")"
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "[run.sh] Stopping existing process (PID $OLD_PID)..."
        kill "$OLD_PID"
        # Wait up to 10 seconds for clean shutdown
        for i in $(seq 1 10); do
            if ! kill -0 "$OLD_PID" 2>/dev/null; then
                echo "[run.sh] Process $OLD_PID stopped."
                break
            fi
            sleep 1
        done
        # Force-kill if still alive
        if kill -0 "$OLD_PID" 2>/dev/null; then
            echo "[run.sh] Force-killing PID $OLD_PID..."
            kill -9 "$OLD_PID" || true
        fi
    else
        echo "[run.sh] Stale PID file found (PID $OLD_PID not running). Removing."
    fi
    rm -f "$PID_FILE"
fi

# ── Activate virtualenv ───────────────────────────────────────
if [[ ! -f "$VENV_DIR/bin/activate" ]]; then
    echo "[run.sh] ERROR: venv not found at $VENV_DIR"
    echo "         Run: python -m venv venv && pip install -r requirements.txt"
    exit 1
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# ── Start uvicorn in background ───────────────────────────────
echo "[run.sh] Starting SRE Agent..."
echo "[run.sh] Logs → $LOG_FILE"

nohup uvicorn main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --log-level info \
    >> "$LOG_FILE" 2>&1 &

APP_PID=$!

# ── Write PID file ────────────────────────────────────────────
echo "$APP_PID" > "$PID_FILE"
echo "[run.sh] SRE Agent started (PID $APP_PID). PID saved to $PID_FILE"

# ── Verify startup (give it 3 seconds) ───────────────────────
sleep 3
if kill -0 "$APP_PID" 2>/dev/null; then
    echo "[run.sh] Process is running. Tailing log (Ctrl-C to detach)..."
    tail -f "$LOG_FILE"
else
    echo "[run.sh] ERROR: Process $APP_PID died immediately. Check $LOG_FILE for details."
    rm -f "$PID_FILE"
    exit 1
fi
