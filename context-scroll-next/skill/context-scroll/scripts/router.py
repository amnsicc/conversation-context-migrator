"""Installed public Hook entry. Only opted-in root chats perform work."""
import json
from pathlib import Path
import sys
from hooks import Hooks, root_identity, EVENTS, RESEND
from store import Store, StoreError, read_json, encode, now, runtime_digest


def dispatch(event):
    if event.get('hook_event_name') not in EVENTS:
        return {}
    # Child hooks may carry the parent's UUID. Validate root provenance before any write.
    if event.get('agent_id') or event.get('agent_type'):
        return {}
    project, thread = event.get('cwd'), event.get('session_id')
    config = {'schema_version': '0.1', 'enabled': True, 'manual_resend': True,
              'project': project, 'threads': [thread]}
    if not root_identity(event, config):
        return {}
    store = Store(project, thread)
    # Unused chats create no archive and have no reminders or restriction.
    if not store.safe(store.home).exists():
        return {}
    monitor_path = store.safe(store.home / '.状态/monitor.json')
    protection_path = store.safe(store.home / '.状态/protection.json')
    monitor = read_json(monitor_path) if monitor_path.exists() else {}
    protection = read_json(protection_path) if protection_path.exists() else {}
    for value in (monitor, protection):
        if value:
            if value.get('thread_id') != thread or value.get('project') != str(store.project):
                raise StoreError('Monitoring/protection identity conflict', 3)
    state = store.load()
    guard = bool(protection.get('armed') and protection.get('base_revision') == (state or {}).get('revision')) or bool(
        state and state.get('protect_compaction') and store.interruption_active(state))
    interrupted = store.interruption_path.exists() and read_json(store.interruption_path).get('status') == 'awaiting_user'
    pending = bool(store.pending_compactions())
    if not (monitor.get('enabled') or guard or interrupted or pending):
        return {}
    config.update(monitoring=bool(monitor.get('enabled')), audit=True)
    # Small thresholds are possible only through an explicit isolated runtime fixture.
    if monitor.get('isolated_test') is True:
        config.update(isolated_test=True, test_limits=monitor.get('test_limits'))
    result = Hooks(config).handle(event)
    with store.locked():
        store.write(store.home / '.状态/runtime-receipt.json', encode({
            'schema_version': '0.1', 'thread_id': thread, 'project': str(store.project),
            'event': event['hook_event_name'], 'observed_at': now(),
            'handler_sha256': __import__('hashlib').sha256(Path(__file__).read_bytes()).hexdigest(),
            'runtime_sha256': runtime_digest(),
            'monitoring': config['monitoring'], 'guard': guard}))
    return result


def main():
    event = {}
    try:
        event = json.load(sys.stdin)
        output = dispatch(event)
    except (StoreError, OSError, ValueError, KeyError, TypeError) as exc:
        reason = '上下文管理异常：' + str(exc) + '。请检查状态并处理。'
        if event.get('hook_event_name') == 'UserPromptSubmit':
            output = {'decision': 'block', 'reason': reason + RESEND}
        elif event.get('hook_event_name') in ('PostCompact', 'SessionStart'):
            output = {'continue': False, 'stopReason': reason, 'systemMessage': reason}
        else:
            print(reason, file=sys.stderr)
            output = {}
    print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
