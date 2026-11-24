#!/usr/bin/env python3
# Audio webhook channel: transcribe authenticated uploads and send them to Main.
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time
import wave


MAX_BYTES = 20_000_000
UPLOAD_SECONDS = 30
FORMATS = {'audio/wav': 'wav', 'audio/ogg': 'ogg'}


def openai_key(key):
    key = key or os.environ.get('OPENAI_API_KEY', '')
    if key:
        return key
    for credentials in ('/data/shared/.env', '/data/agents/main/.env'):
        try:
            lines = Path(credentials).read_text().splitlines()
        except OSError:
            continue
        values = [line.removeprefix('OPENAI_API_KEY=') for line in lines if line.startswith('OPENAI_API_KEY=')]
        if values and values[-1]:
            key = values[-1]
    return key


def supported(path, extension):
    if extension == 'wav':
        try:
            with wave.open(str(path), 'rb') as audio:
                if (not audio.getnframes() or not audio.getframerate()
                        or audio.getsampwidth() not in (1, 2, 3, 4)):
                    return False
                audio.setpos(audio.getnframes() - 1)
                return len(audio.readframes(1)) == audio.getnchannels() * audio.getsampwidth()
        except (EOFError, RuntimeError, wave.Error):
            return False
    # Reject mislabeled files before upload: the first Ogg page must begin an Opus stream.
    with path.open('rb') as audio:
        header = audio.read(27)
        if len(header) != 27 or not header.startswith(b'OggS\x00\x02') or not header[26]:
            return False
        segments = audio.read(header[26])
        if len(segments) != header[26] or segments[0] < 19:
            return False
        opus = audio.read(19)
        return (len(opus) == 19 and opus.startswith(b'OpusHead\x01') and opus[9] > 0
                and path.stat().st_size > 27 + len(segments) + sum(segments))


class Webhook(BaseHTTPRequestHandler):
    timeout = UPLOAD_SECONDS

    def log_message(self, *_):
        # No access logs: the request target and parser errors are caller-controlled.
        pass

    def reply(self, status):
        self.close_connection = True
        self.send_response(status)
        self.send_header('Connection', 'close')
        if status == 401:
            self.send_header('WWW-Authenticate', 'Bearer')
        if status != 204:
            self.send_header('Content-Length', '0')
        self.end_headers()

    def do_POST(self):
        if self.path != '/audio':
            return self.reply(404)
        auth = self.headers.get_all('Authorization', [])
        if len(auth) != 1 or not hmac.compare_digest(auth[0].encode('latin-1'), self.server.authorization):
            return self.reply(401)
        if 'Transfer-Encoding' in self.headers or 'Content-Encoding' in self.headers:
            return self.reply(400)
        lengths = self.headers.get_all('Content-Length', [])
        if not lengths:
            return self.reply(411)
        if len(lengths) != 1 or not re.fullmatch(r'[0-9]+', lengths[0]):
            return self.reply(400)
        length = lengths[0].lstrip('0')
        if len(length) > len(str(MAX_BYTES)) or (length and int(length) > MAX_BYTES):
            return self.reply(413)
        if not length:
            return self.reply(400)
        types = self.headers.get_all('Content-Type', [])
        extension = FORMATS.get(types[0].split(';', 1)[0].strip().lower()) if len(types) == 1 else None
        if not extension:
            return self.reply(415)

        with tempfile.TemporaryDirectory(prefix='shoebox-audio-') as directory:
            path = Path(directory) / f'audio.{extension}'
            remaining = int(length)
            deadline = time.monotonic() + UPLOAD_SECONDS
            with path.open('wb') as audio:
                while remaining:
                    seconds = deadline - time.monotonic()
                    if seconds <= 0:
                        return self.reply(408)
                    self.connection.settimeout(seconds)
                    try:
                        chunk = self.rfile.read1(min(65536, remaining))
                    except TimeoutError:
                        return self.reply(408)
                    if not chunk:
                        return self.reply(400)
                    audio.write(chunk)
                    remaining -= len(chunk)
            if not supported(path, extension):
                return self.reply(415)
            key = openai_key(self.server.api_key)
            if not key:
                return self.reply(502)
            try:
                result = subprocess.run(['curl', '-q', '-fsS', '--max-time', '120',
                                         'https://api.openai.com/v1/audio/transcriptions',
                                         '-H', f'Authorization: Bearer {key}',
                                         '--form-string', f'model={self.server.model}', '-F', f'file=@{path}'],
                                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, check=True, timeout=125)
                text = json.loads(result.stdout)['text']
            except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
                return self.reply(502)
            if not isinstance(text, str) or not text.strip() or '\x00' in text:
                return self.reply(502)
            try:
                # Pi handles leading slash commands even on its print-mode stdin.
                message = ('Audio transcript:\n' + text).encode('utf-8')
            except UnicodeError:
                return self.reply(502)

        # Same stdin path as typed chat; never parse commands or identities out of a transcript.
        try:
            subprocess.run(['chat-main', self.server.conversation], input=message,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        except (OSError, subprocess.SubprocessError):
            return self.reply(503)
        self.reply(204)


class AudioServer(HTTPServer):
    def handle_error(self, *_):
        # Do not surface request content or tracebacks in channel logs.
        print('audio webhook: request failed', flush=True)


def stop(_signal, _frame):
    raise SystemExit(0)


def main():
    token = os.environ.pop('AUDIO_WEBHOOK_TOKEN', '')
    name = os.environ.get('AUDIO_WEBHOOK_CONVERSATION', '')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{32,}', token) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', name):
        raise SystemExit('audio webhook: set a valid AUDIO_WEBHOOK_TOKEN and AUDIO_WEBHOOK_CONVERSATION')
    signal.signal(signal.SIGTERM, stop)
    api_key = os.environ.pop('AUDIO_WEBHOOK_TRANSCRIPTION_API_KEY', '')
    with AudioServer(('0.0.0.0', int(os.environ.get('AUDIO_WEBHOOK_PORT', '8081'))), Webhook) as server:
        server.authorization = b'Bearer ' + token.encode('ascii')
        server.api_key = api_key
        server.conversation = f'audio-{name}'
        server.model = os.environ.get('AUDIO_WEBHOOK_TRANSCRIPTION_MODEL') or 'gpt-transcribe'
        print(f'audio webhook up on port {server.server_port}', flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
