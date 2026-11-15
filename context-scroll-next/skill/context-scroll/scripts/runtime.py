"""Current-chat monitor/protection management. Explicit user invocation required by skills."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from store import Store, StoreError, encode, read_json, require, now, runtime_digest


def identity(project=None, thread=None):
    project = project or os.getcwd()
    thread = thread or os.environ.get('CODEX_THREAD_ID')
    require(thread, 'Current thread ID unavailable; verify current chat metadata', 2)
    return Store(str(Path(project).resolve()), thread)


def manage(store, action, instruction=None):
    monitor_path = store.safe(store.home / '.状态/monitor.json')
    protection_path = store.safe(store.home / '.状态/protection.json')
    receipt_path = store.safe(store.home / '.状态/runtime-receipt.json')
    if action == 'status' and not store.root.exists():
        return {'project': str(store.project), 'thread_id': store.thread,
                'monitoring': False, 'protection': False, 'hook_observed': False, 'archive': {'status': 'idle'}}
    with store.locked():
        monitor = read_json(monitor_path) if monitor_path.exists() else {}
        protection = read_json(protection_path) if protection_path.exists() else {}
        if action != 'status':
            require(instruction and instruction.get('message_ref') and instruction.get('user_text'),
                    'Actual explicit instruction and message locator required', 2)
            identity_fields = {'schema_version': '0.1', 'thread_id': store.thread, 'project': str(store.project),
                               'updated_at': now(), 'instruction': instruction}
            if action in ('on', 'off'):
                was_enabled = monitor.get('enabled') is True
                monitor = {**identity_fields, 'enabled': action == 'on'}
                store.write(monitor_path, encode(monitor))
                if action == 'on' and not was_enabled:
                    path, reminder = store.reminder_state()
                    reminder.update(skip_cycle=None, prepare_issued=False, organize_issued=False,
                                    health_at=None, notice=None)
                    store.write(path, encode(reminder))
            elif action in ('arm', 'disarm'):
                if action == 'disarm':
                    state = store.load()
                    require(not state or not store.interruption_active(state),
                            'Finish/cancel active batch before disarming protection', 3)
                protection = {**identity_fields, 'armed': action == 'arm'}
                protection['base_revision'] = (store.load() or {}).get('revision')
                store.write(protection_path, encode(protection))
            elif action == 'skip':
                require(monitor.get('enabled'), 'Monitoring is not enabled', 3)
                store.reconcile_compactions()
                path, reminder = store.reminder_state()
                require(not reminder.get('cycle_uncertain'), 'Compaction identity is unresolved', 3)
                reminder.update(skip_cycle=reminder['cycle'], notice=None)
                store.write(path, encode(reminder))
            elif action == 'resolve':
                state = store.load()
                require(not state or not store.interruption_active(state),
                        'Active batch requires ack-interruption with its exact batch ID', 3)
                store.reconcile_compactions()
                path, reminder = store.reminder_state()
                require(reminder.get('cycle_uncertain'), 'No unknown compaction to resolve', 3)
                reminder.update(cycle_uncertain=False, skip_cycle=None, used=None, window=None,
                                health_at=None, notice=None, prepare_issued=False, organize_issued=False)
                store.write(path, encode(reminder))
                store.write(store.home / '.状态/preflight-resolution.json', encode(identity_fields))
            else:
                raise StoreError('Unknown management action', 2)
        state = store.load()
        guard = bool(protection.get('armed') and protection.get('base_revision') == (state or {}).get('revision')) or bool(state and state.get('protect_compaction') and store.interruption_active(state))
        receipt = read_json(receipt_path) if receipt_path.exists() else None
        current = runtime_digest()
        return {'project': str(store.project), 'thread_id': store.thread, 'monitoring': bool(monitor.get('enabled')),
                'protection': guard, 'hook_observed': bool(receipt and receipt.get('runtime_sha256') == current),
                'hook_receipt': receipt, 'archive': store.status(state),
                'review_hint': '首次使用请在客户端审核公共Hook。hook_observed表示实际收到过本版事件，需结合审核状态判断。'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project')
    parser.add_argument('--thread')
    parser.add_argument('action', choices=['status', 'on', 'off', 'arm', 'disarm', 'skip', 'resolve'])
    parser.add_argument('--instruction-file')
    args = parser.parse_args()
    try:
        store = identity(args.project, args.thread)
        instruction = read_json(args.instruction_file) if args.instruction_file else None
        result = manage(store, args.action, instruction)
        print(json.dumps({'ok': True, **result}, ensure_ascii=False))
        return 0
    except (StoreError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        return exc.code if isinstance(exc, StoreError) else 4


if __name__ == '__main__':
    sys.exit(main())
