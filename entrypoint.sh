#!/usr/bin/env bash
set -e

# Start the permissions bot in the background if configured
if [ -n "$PERMISSIONS_BOT_TOKEN" ] && [ -n "$PERMISSIONS_BOT_OWNER_ID" ]; then
    echo "Starting permissions bot..."
    python permissions_bot.py &
    PERMISSIONS_PID=$!
    echo "Permissions bot started (PID: $PERMISSIONS_PID)"
fi

# Start the MCP SSE server (foreground)
echo "Starting MCP SSE server..."
exec python src/server_sse.py
