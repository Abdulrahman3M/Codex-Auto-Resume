"""Pure decisions. Unknown state never authorizes a continuation."""
from dataclasses import dataclass
import hashlib
import json
import math


class UnsupportedState(ValueError):
    pass


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    turn_id: str = ''
    task_state: str = 'unknown'


def latest_turn(state):
    history = state.get('turnHistory')
    if isinstance(history, dict) and history.get('kind') == 'canonical':
        hist = history.get('history', {})
        islands = hist.get('islands', [])
        if not islands:
            raise UnsupportedState('No verifiable recent conversation history')
        tail = islands[-1]
        if tail.get('newerBoundary', {}).get('status') != 'exhausted':
            raise UnsupportedState('The end of the history is incomplete; the latest turn cannot be determined')
        entries = tail.get('entries', [])
        if not entries:
            raise UnsupportedState('The latest history segment is empty')
        turn = hist.get('entitiesByKey', {}).get(entries[-1].get('value'))
    else:
        turns = state.get('turns')
        if not isinstance(turns, list) or not turns:
            raise UnsupportedState('Conversation turn data is missing')
        turn = turns[-1]
    if not isinstance(turn, dict):
        raise UnsupportedState('Unsupported conversation history structure')
    return turn


def quota_failure(turn):
    error = turn.get('error')
    if not isinstance(error, dict):
        return False
    info = error.get('codexErrorInfo')
    # These are protocol enum values, not a fuzzy match on user/assistant text.
    return info in ('usageLimitExceeded', 'UsageLimitExceeded') if isinstance(info, str) else False



def interrupted_context_picker(state, request):
    """Recognize ONLY a verified orphan of the App's canceled folder picker.

    No approval request, other tool, current turn, unknown turn, or merely old
    request qualifies. Evidence must be in the same contiguous canonical tail
    as the latest turn. This does not answer or remove the request in the App.
    """
    if not isinstance(request, dict) or request.get('method') != 'item/tool/call':
        return False
    params = request.get('params')
    if not isinstance(params, dict):
        return False
    if params.get('namespace') != 'codex_app' or params.get('tool') != 'setup_codex_context_picker':
        return False
    if not isinstance(state.get('id'), str) or params.get('threadId') != state['id']:
        return False
    tid = params.get('turnId')
    if not isinstance(tid, str) or not tid:
        return False
    history = state.get('turnHistory')
    if not isinstance(history, dict) or history.get('kind') != 'canonical':
        return False
    hist = history.get('history')
    if not isinstance(hist, dict):
        return False
    islands, entities = hist.get('islands'), hist.get('entitiesByKey')
    if not isinstance(islands, list) or not islands or not isinstance(entities, dict):
        return False
    tail = islands[-1]
    if not isinstance(tail, dict) or not isinstance(tail.get('newerBoundary'), dict):
        return False
    entries = tail.get('entries')
    if tail['newerBoundary'].get('status') != 'exhausted' or not isinstance(entries, list) or len(entries) < 2:
        return False
    turns = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get('value'), str):
            return False
        turn = entities.get(entry['value'])
        if not isinstance(turn, dict) or not isinstance(turn.get('turnId'), str):
            return False
        turns.append(turn)
    if turns[-1]['turnId'] == tid:
        return False
    matches = [turn for turn in turns[:-1] if turn['turnId'] == tid]
    return len(matches) == 1 and matches[0].get('status') == 'interrupted'


def decide(state):
    if not isinstance(state, dict) or state.get('hostId') != 'local':
        return Decision('stop', 'Only local App conversations are supported', task_state='unsupported')
    if state.get('ephemeral') is not False:
        return Decision('stop', 'Temporary or unknown conversation types are not supported', task_state='unsupported')
    if state.get('resumeState') != 'resumed':
        return Decision('wait', 'The conversation is still loading; waiting for state synchronization', task_state='connecting')
    if not isinstance(state.get('requests'), list):
        return Decision('stop', 'Approval or user-input state is missing')
    if any(not interrupted_context_picker(state, request) for request in state['requests']):
        return Decision('stop', 'The task is waiting for approval or user input', task_state='needs_attention')
    if state.get('threadGoalResumeConfirmation'):
        return Decision('stop', 'Resuming the task requires user confirmation', task_state='needs_attention')
    goal = state.get('threadGoal')
    if goal is not None and (not isinstance(goal, dict) or goal.get('status') != 'active'):
        return Decision('stop', 'The task goal is paused, blocked, or finished', task_state='needs_attention')
    if state.get('queuedFollowUps'):
        return Decision('stop', 'A user message is already queued', task_state='needs_attention')
    # New Desktop builds preserve uncertain submissions separately from turns.
    # The App itself refuses a start while any earlier submission is present.
    if state.get('unconfirmedTurnSubmissions'):
        return Decision('stop', 'The App has an unconfirmed send; check the original conversation first', task_state='needs_attention')
    runtime = state.get('threadRuntimeStatus')
    if not isinstance(runtime, dict):
        return Decision('stop', 'Run state is missing')
    runtime_type = runtime.get('type')
    if runtime_type == 'active':
        if runtime.get('activeFlags'):
            return Decision('stop', 'The task is waiting for approval, input, or another prerequisite', task_state='needs_attention')
        return Decision('wait', 'The task is running; after enrollment, Auto Resume will wait if it stops because usage is exhausted', task_state='running')
    # A real usage-limit failure is exposed by the pinned App as systemError,
    # not idle.  Only these two known terminal containers may be interpreted;
    # other/transitional runtime types retain enrollment but never authorize a send.
    if runtime_type not in ('idle', 'systemError'):
        return Decision('wait', 'The task state is syncing; monitoring is retained and will be checked again later', task_state='connecting')
    try:
        turn = latest_turn(state)
    except UnsupportedState as exc:
        return Decision('stop', str(exc))
    tid = turn.get('turnId')
    if not isinstance(tid, str) or not tid:
        return Decision('stop', 'The latest turn has no stable identifier')
    status = turn.get('status')
    if status == 'inProgress':
        return Decision('wait', 'The current turn state is syncing', task_state='running')
    if status == 'completed':
        if isinstance(goal, dict) and goal.get('status') == 'active':
            return Decision('wait', "The long-running task goal is still active; waiting for the App's next step. No extra message is sent when this turn ends", tid, 'running')
        return Decision('stop', 'This turn has finished and no task is running; monitoring is not needed', tid, 'idle')
    if status == 'interrupted':
        return Decision('stop', 'This turn was stopped and will not restart automatically', tid, 'interrupted')
    if status != 'failed' or not quota_failure(turn):
        return Decision('stop', 'This is not a usage-limit failure with structured evidence', tid, 'other_failure')
    return Decision('resume', 'The task paused because usage was exhausted; continue in the original conversation after confirming usage has reset', tid, 'quota_limited')


def fingerprint(state):
    """Ignore token counters and titles; bind the actual work and execution settings."""
    keys = ('id', 'hostId', 'cwd', 'latestModel', 'latestReasoningEffort',
            'latestCollaborationMode', 'currentPermissions', 'latestThreadSettings',
            'threadGoal', 'threadGoalResumeConfirmation', 'requests', 'queuedFollowUps',
            'threadRuntimeStatus', 'resumeState', 'unconfirmedTurnSubmissions',
            'environments', 'environmentSelectionEvidence', 'threadAccess')
    data = {k: state.get(k) for k in keys}
    data['turn'] = latest_turn(state)
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def quota_status(response, now, limit_id=None, stale_count=0):
    """Return (ready, next_check_epoch, explanation). Always re-read after reset.

    stale_count tracks consecutive 'data already expired' results so the caller
    can detect persistent clock skew (local clock ahead of server) and stop
    waiting instead of looping indefinitely.
    """
    _MAX_STALE = 60  # ~1 hour of 60-second retries before giving up
    buckets = response.get('rateLimitsByLimitId')
    if isinstance(buckets, dict) and buckets:
        if limit_id is None and set(buckets) != {'codex'}:
            return False, None, 'Multiple usage buckets were found; specify --limit-id explicitly'
        bucket = buckets.get(limit_id or 'codex')
    else:
        bucket = response.get('rateLimits')
        if isinstance(bucket, dict) and limit_id and bucket.get('limitId') != limit_id:
            bucket = None
    if not isinstance(bucket, dict):
        return False, None, 'The selected usage bucket is missing'
    if bucket.get('spendControlReached') or bucket.get('individualLimit') is not None:
        return False, None, 'The account has another usage restriction that requires manual review'
    reached = bucket.get('rateLimitReachedType')
    if reached not in (None, 'rate_limit_reached'):
        return False, None, 'The account has a non-window usage restriction'
    exhausted = []
    windows = [bucket.get('primary'), bucket.get('secondary')]
    if windows[0] is None:
        return False, None, 'Primary usage-window data is missing'
    for window in windows:
        if window is None:
            continue
        if not isinstance(window, dict):
            return False, None, 'Unsupported usage-window structure'
        used, reset, duration = (window.get(k) for k in ('usedPercent', 'resetsAt', 'windowDurationMins'))
        if not numeric(used) or not 0 <= used <= 100 or not numeric(reset) or reset <= 0 or not numeric(duration) or duration <= 0:
            return False, None, 'Usage data is incomplete; availability time cannot be inferred'
        if used >= 100:
            exhausted.append(reset)
        elif reset <= now:
            if stale_count >= _MAX_STALE:
                return False, None, (
                    f'Usage data has been stale for {stale_count} consecutive checks (the local clock may be ahead of the server); '
                    'synchronize the system clock, then restart monitoring'
                )
            return False, now + 60, 'Usage data is stale; it will be read again later'
    if exhausted:
        return False, max(now + 60, max(exhausted) + 15), 'Usage is still insufficient; waiting for the actual reset before checking again'
    if reached:
        return False, now + 60, 'The service still reports usage as exhausted; checking again later'
    return True, now, 'All windows in the selected usage bucket have remaining usage'
