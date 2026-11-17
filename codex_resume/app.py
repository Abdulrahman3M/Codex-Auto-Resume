"""Runtime-checked Desktop IPC. Never owns threads or responds to approvals."""
import json
import os
from pathlib import Path
import plistlib
import select
import socket
import stat
import struct
import subprocess
import time
import uuid

from .policy import decide, fingerprint
from .runtime import WINDOWS_PIPE, codex_binary, platform_name, windows_package_version

MAX_FRAME = 32 * 1024 * 1024
# Bilingual so the model handles both Chinese and English conversations correctly.
CONTINUATION = (
    'After usage resets, continue the unfinished work in this conversation. First check the current progress and workspace. '
    'Do not repeat completed steps, expand scope, or bypass approvals. If the work is complete or needs a user decision, explain clearly and stop.\n\n'
    'Your usage limit has reset. Please continue the original unfinished work in this session. '
    'First review the current progress and workspace, do not repeat completed steps, '
    'do not expand scope, and do not bypass approvals. '
    'If the work is already done or requires a user decision, state that clearly and stop.'
)


class AppError(RuntimeError):
    pass


class ConnectionUnavailable(AppError):
    """Transient loss of transport; never evidence authorizing a send."""


class ThreadUnavailable(ConnectionUnavailable):
    """App is connected, but the selected conversation is not loaded."""


def check_version(app_path, system=None):
    """Identify the installation and read diagnostic metadata, without an allowlist.

    A version number does not authorize sending; live protocol and policy checks do.
    """
    system = system or platform_name()
    if system == 'windows':
        binary = codex_binary(app_path, system)
        if not binary.is_file():
            raise AppError('Could not find the Windows Codex App codex.exe; start the Codex App once first')
        try:
            result = subprocess.run([str(binary), '--version'], capture_output=True, text=True,
                                    timeout=5, stdin=subprocess.DEVNULL)
            cli_version = result.stdout.strip().removeprefix('codex-cli ').strip()
            app_version = windows_package_version()
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            raise AppError(str(exc) or 'Could not verify the Windows Codex App version') from exc
        if result.returncode or not cli_version:
            raise AppError('Could not read the CLI version bundled with the Windows App; check the installation')
        return {'platform': 'windows', 'version': app_version, 'cliVersion': cli_version}
    path = Path(app_path)
    try:
        info = plistlib.loads((path / 'Contents/Info.plist').read_bytes())
    except (OSError, ValueError) as exc:
        raise AppError('Could not find a recognizable Codex App') from exc
    version = (info.get('CFBundleShortVersionString'), info.get('CFBundleVersion'))
    if info.get('CFBundleIdentifier') != 'com.openai.codex' or not all(isinstance(v, str) and v for v in version):
        raise AppError('Could not identify the Codex App or obtain its complete version information')
    return {'platform': 'macos', 'version': version[0], 'build': version[1]}


def open_selected_thread(app_path, thread_id, system=None):
    """Navigate to an existing selected conversation. Never prefill/send a prompt.

    The App's localConversation deep-link handler checks thread existence
    then navigates to its original route. This does not make this client an owner.
    Success here only means navigation was requested; a fresh snapshot is required.
    """
    system = system or platform_name()
    check_version(app_path, system)
    target = str(uuid.UUID(thread_id))
    command = (['cmd.exe', '/d', '/s', '/c', 'start', '', 'codex://threads/' + target]
               if system == 'windows' else
               ['/usr/bin/open', '-g', '-a', str(app_path), 'codex://threads/' + target])
    subprocess.run(command,
                   check=True, timeout=5, stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class WindowsPipe:
    """Small stdlib-only byte-stream client for the App's Windows named pipe."""
    def __init__(self, endpoint=WINDOWS_PIPE, timeout=12, api=None):
        if api is None:
            import _winapi
            api = _winapi
        self.api, self.timeout, self.handle = api, timeout, None
        deadline = time.monotonic() + timeout
        while self.handle is None:
            remaining = max(1, int((deadline - time.monotonic()) * 1000))
            if remaining <= 1 and time.monotonic() >= deadline:
                raise TimeoutError('Timed out connecting to the App named pipe')
            try:
                api.WaitNamedPipe(endpoint, min(1000, remaining))
                self.handle = api.CreateFile(endpoint, api.GENERIC_READ | api.GENERIC_WRITE,
                    0, api.NULL, api.OPEN_EXISTING, api.FILE_FLAG_OVERLAPPED, api.NULL)
            except OSError as exc:
                if getattr(exc, 'winerror', None) not in (api.ERROR_SEM_TIMEOUT, api.ERROR_PIPE_BUSY):
                    raise ConnectionUnavailable('Could not connect to the Windows Codex App named pipe') from exc

    def settimeout(self, value):
        self.timeout = value

    def _complete(self, operation, error):
        if error == self.api.ERROR_IO_PENDING:
            result = self.api.WaitForMultipleObjects([operation.event], False,
                max(1, int(self.timeout * 1000)))
            if result == self.api.WAIT_TIMEOUT:
                operation.cancel()
                raise TimeoutError('Timed out waiting for an App response')
            if result != getattr(self.api, 'WAIT_OBJECT_0', 0):
                operation.cancel()
                raise ConnectionUnavailable('Waiting on the Windows Codex App pipe failed')
        count, error = operation.GetOverlappedResult(True)
        if error not in (0, getattr(self.api, 'ERROR_MORE_DATA', 234)):
            raise ConnectionUnavailable('Reading from or writing to the Windows Codex App pipe failed')
        return count

    def sendall(self, data):
        view = memoryview(data)
        while view:
            operation, error = self.api.WriteFile(self.handle, view, overlapped=True)
            count = self._complete(operation, error)
            if count <= 0:
                raise ConnectionUnavailable('The Windows Codex App pipe was disconnected')
            view = view[count:]

    def recv(self, size):
        try:
            operation, error = self.api.ReadFile(self.handle, size, overlapped=True)
            count = self._complete(operation, error)
            return bytes(operation.getbuffer()[:count])
        except OSError as exc:
            if getattr(exc, 'winerror', None) == self.api.ERROR_BROKEN_PIPE:
                return b''
            raise

    def has_data(self):
        try:
            return self.api.PeekNamedPipe(self.handle)[0] > 0
        except OSError as exc:
            raise ConnectionUnavailable('The Windows Codex App pipe was disconnected') from exc

    def close(self):
        if self.handle is not None:
            self.api.CloseHandle(self.handle)
            self.handle = None


class Desktop:
    def __init__(self, home, app_path='/Applications/ChatGPT.app', timeout=12, system=None,
                 transport_factory=None, read_only=False):
        self.home, self.app_path, self.timeout = Path(home), Path(app_path), timeout
        self.system = system or platform_name()
        self.transport_factory = transport_factory
        self.read_only = read_only
        self.app_info = None
        self.sock = None
        self.client_id = 'initializing-client'
        self.owner = None
        self.thread_id = None
        self.snapshot_value = None
        self.revision = None
        self.state_generation = 0

    def __enter__(self):
        self.app_info = check_version(self.app_path, self.system)
        if self.system == 'windows':
            self.sock = (self.transport_factory or WindowsPipe)(WINDOWS_PIPE, self.timeout)
            try:
                result = self.request('initialize', {'clientType': 'codex-auto-resume'}, 0)
                self.client_id = result['result']['clientId']
                return self
            except Exception:
                self.close()
                raise
        path = self.home / 'ipc/ipc.sock'
        try:
            directory, endpoint = path.parent.lstat(), path.lstat()
            if not stat.S_ISDIR(directory.st_mode) or not stat.S_ISSOCK(endpoint.st_mode):
                raise AppError('The IPC path is not a socket in a regular directory')
            if any(s.st_uid != os.getuid() or s.st_mode & 0o077 for s in (directory, endpoint)):
                raise AppError('IPC permissions or ownership are unsafe; permissions will not be changed automatically')
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.settimeout(self.timeout)
            self.sock.connect(str(path))
            result = self.request('initialize', {'clientType': 'codex-auto-resume'}, 0)
            self.client_id = result['result']['clientId']
            return self
        except Exception:
            self.close()
            raise

    def close(self):
        if self.sock:
            self.sock.close()
            self.sock = None

    def __exit__(self, *args):
        self.close()

    def _send(self, msg):
        if self.read_only:
            kind, method = msg.get('type'), msg.get('method')
            allowed = ((kind == 'request' and method in ('initialize', 'thread-owner-discovery'))
                       or (kind == 'broadcast' and method == 'thread-stream-following-changed')
                       or kind == 'client-discovery-response'
                       or (kind == 'response' and msg.get('resultType') == 'error'))
            if not allowed:
                raise AppError('Task actions are prohibited on a read-only diagnostic connection')
        data = json.dumps(msg, ensure_ascii=False).encode()
        if not 0 < len(data) <= MAX_FRAME:
            raise AppError('IPC message is too large')
        self.sock.sendall(struct.pack('<I', len(data)) + data)

    def _exact(self, n, deadline):
        chunks, remaining = [], n
        while remaining:
            timeout = deadline - time.monotonic()
            if timeout <= 0:
                raise TimeoutError('Timed out waiting for an App response')
            self.sock.settimeout(timeout)
            chunk = self.sock.recv(remaining)
            if not chunk:
                raise ConnectionUnavailable('The App connection was lost')
            chunks.append(chunk)
            remaining -= len(chunk)
        return b''.join(chunks)

    def _read(self, deadline):
        size = struct.unpack('<I', self._exact(4, deadline))[0]
        if not 0 < size <= MAX_FRAME:
            raise AppError('Unsupported IPC message length')
        msg = json.loads(self._exact(size, deadline))
        if not isinstance(msg, dict):
            raise AppError('IPC message is not an object')
        if msg.get('type') == 'client-discovery-request':
            self._send({'type': 'client-discovery-response', 'requestId': msg['requestId'],
                        'response': {'canHandle': False}})
        if msg.get('type') == 'request':
            # In particular: never become an approval handler or thread owner.
            self._send({'type': 'response', 'requestId': msg['requestId'],
                        'resultType': 'error', 'error': 'unsupported-by-auto-resume'})
        if msg.get('type') == 'broadcast' and msg.get('method') == 'thread-stream-state-changed':
            params = msg.get('params', {})
            if params.get('conversationId') == self.thread_id and params.get('hostId') == 'local' and msg.get('sourceClientId') == self.owner:
                if msg.get('version') != 11:
                    raise AppError('The App state protocol version changed')
                self.state_generation += 1
                change = params.get('change', {})
                if change.get('type') == 'snapshot':
                    state = change.get('conversationState')
                    # Verified v11 semantics: ephemeral is optional; JSON omits
                    # undefined and the App treats only === true as ephemeral.
                    # Apply only to this validated owner/local snapshot protocol,
                    # independent of App release. Explicit null/true stay unsafe;
                    # all other required state is still checked by the policy.
                    if isinstance(state, dict) and 'ephemeral' not in state:
                        state = dict(state, ephemeral=False)
                    self.snapshot_value = state
                    self.revision = change.get('revision')
                elif change.get('type') == 'patches':
                    # Do not infer current state from a partial/missed patch stream.
                    self.snapshot_value = None
                else:
                    raise AppError('Unsupported App state change format')
        return msg

    def request(self, method, params, version, target=None):
        request_id = str(uuid.uuid4())
        msg = {'type': 'request', 'requestId': request_id, 'sourceClientId': self.client_id,
               'method': method, 'params': params, 'version': version,
               'timeoutMs': int(self.timeout * 1000)}
        if target:
            msg['targetClientId'] = target
        self._send(msg)
        deadline = time.monotonic() + self.timeout
        while True:
            reply = self._read(deadline)
            if reply.get('type') != 'response' or reply.get('requestId') != request_id:
                continue
            if reply.get('resultType') != 'success':
                # Only emit safe error codes, never raw remote error strings.
                code = reply.get('error')
                if code == 'no-client-found':
                    raise ThreadUnavailable('The selected conversation is not connected to the App')
                raise AppError('The App rejected the request or the interface is incompatible; no automatic retry was attempted')
            if reply.get('method') != method:
                raise AppError('The App response method does not match')
            return reply

    def follow(self, enabled):
        self._send({'type': 'broadcast', 'method': 'thread-stream-following-changed',
                    'sourceClientId': self.client_id, 'targetClientIds': [self.owner], 'version': 1,
                    'params': {'hostId': 'local', 'conversationId': self.thread_id, 'following': enabled}})

    def snapshot(self, thread_id):
        self.thread_id = str(uuid.UUID(thread_id))
        reply = self.request('thread-owner-discovery', {'hostId': 'local', 'conversationId': self.thread_id}, 1)
        self.owner = reply.get('handledByClientId')
        if not isinstance(self.owner, str):
            raise AppError('App task owner is missing')
        self.follow(False)
        self.snapshot_value = None
        self.follow(True)
        deadline = time.monotonic() + self.timeout
        while self.snapshot_value is None:
            self._read(deadline)
        state = self.snapshot_value
        if not isinstance(state, dict) or state.get('id') != self.thread_id:
            raise AppError('The App returned a mismatched task state')
        return state

    def _drain_pending(self):
        """Consume already-arrived events before dispatch, never apply patches.

        This closes the queued-event window, not the inherent non-atomic gap
        between the App's state check and its start-turn operation. A busy or
        partial stream fails closed rather than allowing an unbounded drain.
        """
        deadline = time.monotonic() + self.timeout
        for _ in range(128):
            pending = self.sock.has_data() if hasattr(self.sock, 'has_data') else bool(select.select([self.sock], [], [], 0)[0])
            if not pending:
                return
            self._read(deadline)
        raise AppError('App state is still changing; canceling this resume')

    def resume(self, thread_id, expected_fingerprint, message_id, dispatch_guard=None):
        if self.read_only:
            raise AppError('Automatic resumes are prohibited on a read-only diagnostic connection')
        current_app = check_version(self.app_path, self.system)
        # App/CLI updates must not reuse an already-open connection:
        # Reconnect and revalidate live state after an update, even when protocol
        # versions appear unchanged.
        if self.app_info is not None and current_app != self.app_info:
            raise AppError('The App/CLI updated during the connection; canceling this resume. Reconnect and try again')
        state = self.snapshot(thread_id)
        if decide(state).action != 'resume' or fingerprint(state) != expected_fingerprint:
            raise AppError('Task state changed before sending; canceling this resume')
        generation = self.state_generation
        self._drain_pending()
        if generation != self.state_generation:
            raise AppError('A new task state arrived before sending; canceling this resume')
        request = {'threadId': thread_id, 'clientUserMessageId': message_id,
                   'input': [{'type': 'text', 'text': CONTINUATION, 'text_elements': []}]}
        if dispatch_guard is not None and not dispatch_guard():
            raise AppError('Monitoring stopped or send authorization changed; canceling this resume')
        reply = self.request('thread-follower-start-turn',
                             {'conversationId': thread_id, 'turnStart': {'request': request}},
                             2, self.owner)
        try:
            turn_id = reply['result']['result']['turn']['id']
            if not isinstance(turn_id, str) or not turn_id:
                raise KeyError('id')
        except (KeyError, TypeError) as exc:
            raise AppError('Resume result is unconfirmed; check the App. It will not be resent automatically') from exc
        return turn_id
