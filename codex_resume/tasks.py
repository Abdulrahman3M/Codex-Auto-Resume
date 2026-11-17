"""Conversation discovery and enrollment, separate from permission to send."""
import socket
import time
import uuid

from .app import ConnectionUnavailable, Desktop, open_selected_thread
from .policy import decide, interrupted_context_picker, quota_failure
from .rpc import ReadOnlyServer
from .runtime import codex_binary

SOURCES = ['cli', 'vscode', 'exec', 'appServer', 'subAgent', 'subAgentReview',
           'subAgentCompact', 'subAgentThreadSpawn', 'subAgentOther', 'unknown']
LIVE_REOPEN_ATTEMPTS = 9
LIVE_REOPEN_RETRY_INTERVAL = 0.25
LIVE_REOPEN_TIMEOUT = 5.0
LIVE_REOPEN_ATTEMPT_TIMEOUT = 0.75


def list_conversations(server, archive_filters=(False, True), timeout=55):
    """Consume all pages, including archives; don't silently return only page one.

    timeout: total seconds allowed for all pages across all archive filters.
    Callers with large accounts can raise this via --list-timeout.
    """
    rows, ids = [], set()
    deadline = time.monotonic() + timeout
    for archived in archive_filters:
        cursor, seen = None, set()
        while True:
            if time.monotonic() >= deadline:
                raise RuntimeError('The conversation list is incomplete; try again later')
            params = {'limit': 100, 'archived': archived, 'sourceKinds': SOURCES,
                      'sortKey': 'updated_at', 'modelProviders': []}
            if cursor is not None:
                params['cursor'] = cursor
            page = server.query('thread/list', params)
            if not isinstance(page, dict) or not isinstance(page.get('data'), list) or 'nextCursor' not in page:
                raise RuntimeError('The conversation list is incomplete; later pages cannot be verified')
            for row in page['data']:
                if not isinstance(row, dict) or not isinstance(row.get('id'), str):
                    raise RuntimeError('Unsupported conversation-list format')
                if row['id'] not in ids:
                    ids.add(row['id'])
                    rows.append({key: row.get(key) for key in ('id', 'name', 'preview', 'cwd', 'updatedAt')})
                    rows[-1]['archived'] = archived
            cursor = page['nextCursor']
            if cursor is None:
                break
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise RuntimeError('Conversation pagination cursor is invalid; an incomplete list was not returned')
            seen.add(cursor)
    return rows


def stored_assessment(thread, thread_id):
    """Persisted turns may qualify for enrollment, NEVER for dispatch."""
    if not isinstance(thread, dict) or thread.get('id') != thread_id:
        raise RuntimeError('The result does not match the selected conversation')
    result = {'threadId': thread_id, 'title': thread.get('name') or thread.get('preview'),
              'source': 'history', 'connection': 'waiting', 'decision': 'stop',
              'canMonitor': False, 'taskState': 'unknown',
              'reason': 'Task state cannot be confirmed right now; no automatic resume will be sent', 'checkedAt': time.time()}
    if thread.get('ephemeral') is not False:
        return result
    turns = thread.get('turns')
    if not isinstance(turns, list):
        return result
    if not turns:
        return dict(result, taskState='empty', reason='This conversation has no task; monitoring is not needed')
    turn = turns[-1]
    if not isinstance(turn, dict) or not isinstance(turn.get('id'), str) or not turn['id']:
        return result
    status = turn.get('status')
    if status == 'completed':
        return dict(result, taskState='idle', reason='The latest turn finished and no task is running; monitoring is not needed')
    if status == 'interrupted':
        return dict(result, taskState='interrupted', reason='The latest turn was stopped and will not restart automatically')
    if status == 'inProgress' or (status == 'failed' and quota_failure(turn)):
        return dict(result, taskState='running' if status == 'inProgress' else 'quota_limited',
                    decision='wait', canMonitor=True,
                    reason='History shows the task is still running' if status == 'inProgress' else 'History shows the task paused because usage was exhausted')
    if status == 'failed':
        return dict(result, taskState='other_failure', reason='The task stopped because of another error and is not eligible for usage-reset resumes')
    return result


def live_assessment(home, app_path, thread_id, timeout=None):
    if timeout is None:
        desktop = Desktop(home, app_path)
    else:
        desktop = Desktop(home, app_path, timeout=timeout)
    with desktop as client:
        state = client.snapshot(thread_id)
    decision = decide(state)
    if decision.task_state == 'connecting':
        return None
    return {'threadId': thread_id, 'title': state.get('title'),
            'decision': decision.action, 'reason': decision.reason,
            'taskState': decision.task_state,
            'canMonitor': decision.task_state in ('running', 'quota_limited'),
            'source': 'live', 'connection': 'connected', 'checkedAt': time.time(),
            'runtime': state.get('threadRuntimeStatus'), 'model': state.get('latestModel'),
            'ignoredInterruptedPickerRequests': sum(interrupted_context_picker(state, r)
                for r in state.get('requests', [])) if isinstance(state.get('requests'), list) else 0}


def inspect_task(home, app_path, thread_id, list_timeout=55):
    thread_id = str(uuid.UUID(thread_id))
    # Archiving is a user stop signal, even if the last persisted turn was still
    # in progress. Never navigate to or enroll an archived conversation.
    with ReadOnlyServer(codex_binary(app_path), home) as server:
        archived = next((r for r in list_conversations(server, (True,), timeout=list_timeout) if r['id'] == thread_id), None)
    if archived:
        return {'threadId': thread_id, 'title': archived.get('name') or archived.get('preview'),
                'decision': 'stop', 'canMonitor': False, 'taskState': 'archived',
                'source': 'history', 'connection': 'not_checked', 'checkedAt': time.time(),
                'reason': 'This conversation is archived; it will not be monitored or opened automatically'}
    try:
        result = live_assessment(home, app_path, thread_id)
        if result is not None:
            return result
    except (ConnectionUnavailable, ConnectionError, FileNotFoundError, TimeoutError, socket.timeout):
        pass
    # A selected thread can be active in the App while the read-only index
    # still reports it as notLoaded and the persisted turn tail is stale.
    # Opening the exact UUID is navigation only; fresh live state is still
    # required before enrollment. App loading is asynchronous, so poll briefly
    # rather than making one timing-sensitive snapshot attempt.
    try:
        open_selected_thread(app_path, thread_id)
    except (ConnectionUnavailable, ConnectionError, FileNotFoundError, TimeoutError, socket.timeout):
        pass
    else:
        transient = (ConnectionUnavailable, ConnectionError, FileNotFoundError,
                     TimeoutError, socket.timeout)
        deadline = time.monotonic() + LIVE_REOPEN_TIMEOUT
        for attempt in range(LIVE_REOPEN_ATTEMPTS):
            remaining = deadline - time.monotonic()
            if remaining <= 0.05:
                break
            # Snapshot uses one App request and one stream wait; halve the
            # remaining budget so a slow attempt cannot overrun the whole poll.
            timeout = min(LIVE_REOPEN_ATTEMPT_TIMEOUT, remaining / 2)
            try:
                result = live_assessment(home, app_path, thread_id, timeout=timeout)
            except transient:
                result = None
            if result is not None:
                return dict(result, openedSelectedThread=True)
            remaining = deadline - time.monotonic()
            if attempt + 1 < LIVE_REOPEN_ATTEMPTS and remaining > 0:
                time.sleep(min(LIVE_REOPEN_RETRY_INTERVAL, remaining))
    with ReadOnlyServer(codex_binary(app_path), home) as server:
        response = server.query('thread/read', {'threadId': thread_id, 'includeTurns': True})
    return stored_assessment(response.get('thread'), thread_id)
