"""Small authenticated companion UI. No shell commands or App RPC exposed over HTTP."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit, parse_qs
import uuid

STATIC = Path(__file__).with_name('static')
ASSETS = {'/': ('index.html', 'text/html; charset=utf-8'),
          '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
          '/style.css': ('style.css', 'text/css; charset=utf-8'),
          '/icon.svg': ('icon.svg', 'image/svg+xml')}


class APIError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def thread_id(value):
    try:
        if not isinstance(value, str):
            raise ValueError()
        return str(uuid.UUID(value))
    except ValueError:
        raise APIError('Choose a valid task UUID.')


class Backend:
    """Reuse the guarded CLI, never accept arbitrary paths, prompts or commands."""

    # Only these CLI commands read the engine; status and stop never do.
    ENGINE_COMMANDS = ('doctor', 'list', 'check', 'start')

    def __init__(self, args):
        self.args = args
        self.quota_lock = threading.Lock()
        self.mutation_lock = threading.Lock()
        self.cached_quota = None
        self.cached_at = 0

    def command(self, *parts):
        cmd = [sys.executable, '-m', 'codex_resume', '--home', str(self.args.home)]
        # Let the CLI rediscover the engine for each operation after App updates.
        if self.args.app is not None and parts[0] in self.ENGINE_COMMANDS:
            cmd += ['--app', str(self.args.app)]
        cmd += ['--state-dir', str(self.args.state_dir), *parts]
        try:
            result = subprocess.run(cmd, cwd=Path(__file__).resolve().parent.parent,
                                    env=dict(os.environ, PYTHONIOENCODING='utf-8'),
                                    capture_output=True, encoding='utf-8', timeout=65)
        except subprocess.TimeoutExpired:
            # start may already have spawned a watcher. Never retry a mutation here.
            raise APIError('Action timed out and the result is unconfirmed. Refresh monitoring records; do not click Start again.', 504)
        if result.returncode:
            message = result.stderr.strip()
            if not message or len(message) > 300 or 'Traceback' in message:
                message = 'The action did not succeed. Check that the App is open, the task is readable, and the local tool logs.'
            raise APIError(message, 409)
        try:
            return json.loads(result.stdout)
        except (ValueError, TypeError):
            raise APIError('The local tool returned an unrecognized result; check the monitoring records.', 502)

    def watches(self):
        return self.command('status')

    def threads(self):
        rows = self.command('list')
        return [{'id': row['id'], 'title': row.get('name') or (row.get('preview') or 'Untitled task')[:120],
                 'cwd': row.get('cwd', ''), 'archived': row.get('archived', False),
                 'updatedAt': row.get('updatedAt')} for row in rows]

    def check(self, value):
        return self.command('check', thread_id(value))

    def quota(self):
        with self.quota_lock:
            if self.cached_quota is None or time.monotonic() - self.cached_at >= 45:
                raw = self.command('doctor')
                limits = raw.get('rateLimits') or {}
                self.cached_quota = {'app': raw['app'], 'ipc': raw['ipc'],
                    'ready': raw['ready'], 'reason': raw['reason'],
                    'primary': limits.get('primary'), 'secondary': limits.get('secondary'),
                    'limitId': limits.get('limitId'), 'checkedAt': time.time()}
                self.cached_at = time.monotonic()
            return self.cached_quota

    def mutate(self, action, data):
        if not isinstance(data, dict):
            raise APIError('Request must be a JSON object.')
        allowed = {'threadId'} if action == 'stop' else {'threadId', 'maxResumes', 'limitId', 'confirmed'}
        if set(data) - allowed:
            raise APIError('Request contains unsupported fields.')
        target = thread_id(data.get('threadId'))
        if action == 'stop':
            # Serialize stop after an in-flight start, so a late arm cannot undo stop.
            # The CLI start has a bounded timeout; do not report cancellation before it finishes.
            with self.mutation_lock:
                return self.command('stop', target)
        if action != 'start':
            raise APIError('Unsupported action.', 404)
        maximum = data.get('maxResumes', 3)
        if type(maximum) is not int or not 1 <= maximum <= 100:
            raise APIError('The cumulative resume limit must be an integer from 1 to 100.')
        if data.get('confirmed') is not True:
            raise APIError('Confirm that only the selected task will be monitored and review the limits of the stop action.')
        limit = data.get('limitId')
        if limit is not None and (not isinstance(limit, str) or not 1 <= len(limit) <= 100
                                  or limit.startswith('-') or not all(c.isalnum() or c in '_-.' for c in limit)):
            raise APIError('Usage bucket ID has an invalid format.')
        if not self.mutation_lock.acquire(blocking=False):
            raise APIError('Another start request is still processing. Check monitoring records; do not submit again.', 409)
        try:
            args = ['start', target, '--max-resumes', str(maximum)]
            if limit:
                args += ['--limit-id', limit]
            return self.command(*args)
        finally:
            self.mutation_lock.release()


class CompanionServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    max_workers = 16
    def __init__(self, address, backend, token=None):
        self.backend = backend
        self.token = token or secrets.token_urlsafe(32)
        self.worker_slots = threading.BoundedSemaphore(self.max_workers)
        super().__init__(address, Handler)
        host, port = self.server_address[:2]
        self.authority = f'{host}:{port}'
        self.origin = f'http://{self.authority}'

    def process_request(self, request, client_address):
        # Never queue an unlimited number of sockets/threads. Handshake and HTTP
        # readers consume the same bounded pool; the accept loop never waits.
        if not self.worker_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.worker_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        except OSError:
            self.shutdown_request(request)
        finally:
            self.worker_slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version = 'CodexRelay'

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *_args):
        pass  # No URLs, titles, credentials or request bodies in access logs.

    def send(self, status, body, content_type='application/json; charset=utf-8'):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; "
                         "img-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
                         "frame-ancestors 'none'; form-action 'self'")
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def guard(self, api=False):
        # Exact authority blocks DNS rebinding and proxy Host confusion. No CORS.
        if self.headers.get_all('Host') != [self.server.authority]:
            raise APIError('Address mismatch; use the address printed at startup.', 403)
        origin = self.headers.get('Origin')
        if origin is not None and origin != self.server.origin:
            raise APIError('Requests from other websites are rejected.', 403)
        if api:
            tokens = self.headers.get_all('X-Resume-Token') or []
            if len(tokens) != 1 or not secrets.compare_digest(tokens[0], self.server.token):
                raise APIError('Connection credential is invalid or expired; reconnect.', 401)
        if self.headers.get('Sec-Fetch-Site') not in (None, 'none', 'same-origin'):
            raise APIError('Open the local dashboard directly.', 403)

    def do_GET(self):
        try:
            path = urlsplit(self.path)
            self.guard(path.path.startswith('/api/'))
            if path.path in ASSETS and not path.query:
                filename, content_type = ASSETS[path.path]
                self.send(200, (STATIC / filename).read_bytes(), content_type)
                return
            if path.path == '/api/watches':
                value = self.server.backend.watches()
            elif path.path == '/api/threads':
                value = self.server.backend.threads()
            elif path.path == '/api/quota':
                value = self.server.backend.quota()
            elif path.path == '/api/check':
                params = parse_qs(path.query)
                if set(params) != {'threadId'} or len(params['threadId']) != 1:
                    raise APIError('An explicit task UUID is required.')
                value = self.server.backend.check(params['threadId'][0])
            else:
                raise APIError('Page or endpoint not found.', 404)
            self.send(200, value)
        except APIError as exc:
            self.send(exc.status, {'error': str(exc)})
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass
        except Exception:
            self.send(500, {'error': 'The local service is temporarily unavailable; check its terminal.'})

    def do_POST(self):
        try:
            self.guard(api=True)
            if self.path not in ('/api/start', '/api/stop'):
                raise APIError('Unsupported action.', 404)
            if self.headers.get_content_type() != 'application/json':
                raise APIError('Only JSON requests are accepted.', 415)
            lengths = self.headers.get_all('Content-Length') or []
            if len(lengths) != 1 or not lengths[0].isdigit() or self.headers.get('Transfer-Encoding'):
                raise APIError('Invalid request length.')
            length = int(lengths[0])
            if not 0 < length <= 2048:
                raise APIError('Request body is too large or empty.', 413)
            try:
                data = json.loads(self.rfile.read(length))
            except (ValueError, UnicodeError):
                raise APIError('Request is not valid JSON.')
            self.send(200, self.server.backend.mutate(self.path.rsplit('/', 1)[1], data))
        except APIError as exc:
            self.send(exc.status, {'error': str(exc)})
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass
        except Exception:
            self.send(500, {'error': 'Action result is unconfirmed. Refresh monitoring records; do not retry automatically.'})


def serve(args):
    with CompanionServer(('127.0.0.1', args.port), Backend(args)) as server:
        print(f'Dashboard URL: {server.origin}/', flush=True)
        print(f'Connection credential: {server.token}', flush=True)
        print('Use this credential only on your own device. It expires when the service restarts; closing the page does not stop monitoring.', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
