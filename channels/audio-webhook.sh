#!/bin/sh
# Audio webhook channel: transcribe authenticated uploads and send them to main.
set -eu
token=${AUDIO_WEBHOOK_TOKEN:-}
conversation=audio-${AUDIO_WEBHOOK_CONVERSATION:-}

if [ -z "${GATEWAY_INTERFACE:-}" ]; then
    case "$conversation" in
        audio-|*[!a-zA-Z0-9_-]*) echo 'Set AUDIO_WEBHOOK_CONVERSATION to letters, digits, _ or -' >&2; exit 1 ;;
    esac
    [ "${#token}" -ge 32 ] || { echo 'Set AUDIO_WEBHOOK_TOKEN to at least 32 characters' >&2; exit 1; }
    # busybox httpd only runs CGI scripts from a cgi-bin folder.
    mkdir -p /run/shoebox/audio-webhook/cgi-bin
    ln -sf "$(readlink -f "$0")" /run/shoebox/audio-webhook/cgi-bin/audio
    httpd -p "${AUDIO_WEBHOOK_PORT:-8081}" -h /run/shoebox/audio-webhook
    [ "$#" -eq 0 ] || exec "$@"
    exit 0
fi

reply() {
    printf 'Status: %s\r\nContent-Length: 0\r\n\r\n' "$1"
    exit 0
}

# Compare digests so a mismatch does not leak how much of the token was right.
[ "$(printf '%s' "${HTTP_AUTHORIZATION:-}" | sha256sum)" = "$(printf 'Bearer %s' "$token" | sha256sum)" ] || reply 401
length=${CONTENT_LENGTH:-}
case "$length" in ''|0|*[!0-9]*) reply 411 ;; esac
[ "$length" -le 20000000 ] || reply 413
case "${CONTENT_TYPE:-}" in
    audio/wav*) extension=wav ;;
    audio/ogg*) extension=ogg ;;
    *) reply 415 ;;
esac

temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT
audio=$temporary/audio.$extension
head -c "$length" > "$audio"
[ "$(wc -c < "$audio")" -eq "$length" ] || reply 400

key=${AUDIO_WEBHOOK_TRANSCRIPTION_API_KEY:-${OPENAI_API_KEY:-}}
[ -n "$key" ] || key=$(sed -n 's/^OPENAI_API_KEY=\(..*\)/\1/p' /data/shared/.env /data/agents/main/.env 2>/dev/null | tail -n 1)
transcript=$(curl -q -fsS --max-time 120 https://api.openai.com/v1/audio/transcriptions \
    -H "Authorization: Bearer $key" \
    --form-string "model=${AUDIO_WEBHOOK_TRANSCRIPTION_MODEL:-gpt-transcribe}" \
    -F "file=@$audio" 2>/dev/null | jq -er '.text | select(type == "string" and test("\\S"))') || reply 502
rm -rf "$temporary"

# The prefix stops Pi from running a transcript that starts with a /command.
printf 'Audio transcript:\n%s' "$transcript" | chat-main "$conversation" >/dev/null 2>&1 &
reply 202
