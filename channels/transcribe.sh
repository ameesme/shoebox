#!/bin/sh
# Submit a local recording to OpenAI; callers validate the returned JSON text.
set -eu
key=${TRANSCRIPTION_API_KEY:-${OPENAI_API_KEY:-}}
if [ -z "$key" ]; then
    for credentials in /data/shared/.env /data/agents/main/.env; do
        [ -f "$credentials" ] || continue
        value=$(awk '/^OPENAI_API_KEY=/ {sub(/^OPENAI_API_KEY=/, ""); key=$0} END {print key}' "$credentials")
        [ -z "$value" ] || key=$value
    done
fi
[ -n "$key" ] || exit 78
exec curl -q -fsS --max-time 120 https://api.openai.com/v1/audio/transcriptions \
    -H "Authorization: Bearer $key" --form-string "model=$2" -F "file=@$1" 2>/dev/null
