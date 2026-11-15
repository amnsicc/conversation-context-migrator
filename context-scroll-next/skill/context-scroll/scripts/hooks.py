"""Explicit project hooks. No model API, no automatic summarization or compaction."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid
from store import Store, StoreError, encode, read_json, require


EVENTS = ['SessionStart', 'UserPromptSubmit', 'PreModelRequest', 'PostModelRequest',
          'Stop', 'PreCompact', 'PostCompact']
START = '$context-scroll 整理'
SKIP = '$context-scroll 本周期跳过'
STATUS = '$context-scroll 查看进度'
MONITOR_CONTROLS = ('$context-monitor', '$context-monitor 开启', '$context-monitor 状态',
                    '$context-monitor 关闭', '$context-monitor 本周期跳过')
RESEND = '这条请求尚未执行。整理完成或选择本周期跳过后，请重新发送原文字和附件；不会自动续接。'


def tail_records(path):
    with Path(path).open('rb') as f:
        end = f.seek(0, 2)
        start = max(0, end - 1024 * 1024)
        f.seek(start)
        lines = f.read().splitlines()
    if start:
        lines = lines[1:]
    values = []
    for line in lines:
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                values.append(row)
        except (ValueError, UnicodeError):
            continue
    return values


def root_identity(event, config):
    if event.get('hook_event_name') not in EVENTS or event.get('agent_id') or event.get('agent_type'):
        return False
    project = Path(config['project']).resolve()
    if not project.samefile(event['cwd']):
        return False
    sid = event['session_id']
    if str(uuid.UUID(sid)) != sid or sid not in config['threads']:
        return False
    with Path(event['transcript_path']).open('rb') as f:
        first = f.readline(256 * 1024)
    meta = json.loads(first)
    p = meta.get('payload', {})
    source = p.get('source')
    return (meta.get('type') == 'session_meta' and (p.get('id') or p.get('session_id')) == sid
            and Path(p['cwd']).samefile(project) and isinstance(source, str)
            and source in ('cli', 'exec', 'vscode', 'app-server', 'appServer', 'desktop', 'codex')
            and p.get('agent_path') in (None, '/root') and not p.get('parent_thread_id'))


def recent_usage(records, turn=None, session=None):
    latest = None
    for row in reversed(records):
        p = row.get('payload') or {}
        if row.get('type') == 'compacted':
            return None
        if row.get('type') == 'event_msg' and p.get('type') == 'task_started' and turn is not None:
            return None
        if row.get('type') == 'event_msg' and p.get('type') == 'token_count' and latest is None:
            info = p.get('info') or {}
            used = (info.get('last_token_usage') or {}).get('total_tokens')
            window = info.get('model_context_window')
            if type(used) is int and used >= 0 and type(window) is int and window > 0:
                latest = (used, window)
        if row.get('type') == 'token_usage_record':
            root_owned = (p.get('root_turn_id') == p.get('turn_id') and p.get('turn_id') is not None
                          and (session is None or (p.get('thread_id', session) == session and p.get('session_id', session) == session)))
            if root_owned and latest and (turn is None or p.get('turn_id') == turn) and (p.get('usage') or {}).get('total_tokens') == latest[0]:
                context = next((r.get('payload') or {} for r in reversed(records)
                                if r.get('type') == 'turn_context' and (r.get('payload') or {}).get('turn_id') == p.get('turn_id')), {})
                return {'used': latest[0], 'window': latest[1], 'turn_id': p.get('turn_id'),
                        'model': p.get('model') or context.get('model')}
            return None
    return None


def compact_marker(records):
    for row in reversed(records):
        if row.get('type') == 'compacted':
            return hashlib.sha256(encode(row)).hexdigest()
    return None


def root_turn_known(records, event):
    turn = event.get('turn_id')
    for row in reversed(records):
        p = row.get('payload') or {}
        if row.get('type') not in ('turn_context', 'token_usage_record') or p.get('turn_id') != turn:
            continue
        if p.get('root_turn_id') != turn:
            return False
        if p.get('thread_id', event['session_id']) != event['session_id'] or p.get('session_id', event['session_id']) != event['session_id']:
            return False
        return True
    return False


def reminder_state(store):
    return store.reminder_state()


def reconcile_compactions(store):
    return store.reconcile_compactions()


class Hooks:
    def __init__(self, config):
        require(config.get('schema_version') == '0.1', 'Unsupported hooks config')
        require(config.get('manual_resend') is True, 'Manual resend behavior must be explicitly configured')
        require(isinstance(config.get('threads'), list) and config['threads'], 'Explicit thread list required')
        for sid in config['threads']:
            require(str(uuid.UUID(sid)) == sid, 'Invalid configured thread ID')
        self.config = config
        self.limits = config.get('test_limits') if config.get('isolated_test') is True else None
        self.limits = self.limits or {'prepare': 400000, 'organize': 475000, 'health_step': 50000, 'minimum_window': 600000}
        require(all(type(self.limits.get(k)) is int and self.limits[k] > 0 for k in
                    ('prepare', 'organize', 'health_step', 'minimum_window')), 'Invalid limits')
        require(self.limits['prepare'] < self.limits['organize'], 'Invalid threshold order')

    def handle(self, event):
        if not self.config.get('enabled'):
            return {}
        if not root_identity(event, self.config):
            return {}
        store = Store(self.config['project'], event['session_id'])
        is_compact = event['hook_event_name'] == 'PostCompact' or (
            event['hook_event_name'] == 'SessionStart' and event.get('source') == 'compact')
        notification = store.queue_compaction(event, compact_marker(tail_records(event['transcript_path']))) if is_compact else None
        with store.locked():
            reconcile_compactions(store)
            path, s = reminder_state(store)
            result = (read_json(store.safe(store.home / '.状态/compact-receipts' / (notification + '.json')))['output']
                      if is_compact else self.process(store, s, event))
            store.write(path, encode(s))
            if self.config.get('audit'):
                audit = store.safe(store.home / '.状态/hook-events.jsonl')
                with audit.open('a', encoding='utf-8') as f:
                    f.write(json.dumps({'event': event['hook_event_name'], 'turn_id': event.get('turn_id'),
                        'used': s['used'], 'window': s['window'], 'cycle': s['cycle'], 'output': result}, ensure_ascii=False) + '\n')
            return result

    def health(self, s):
        if s['notice'] == s['health_warning']:
            s['notice'] = None
        if s['window'] is None:
            warning = '当前窗口读数暂缺，取得真实用量后补查；不使用猜测窗口。'
        elif s['window'] < self.limits['minimum_window']:
            warning = '当前可用窗口偏小（' + str(s['window']) + '），固定整理线可能余量不足，请检查窗口设置。'
        else:
            warning = None
        if warning and warning != s['health_warning']:
            s['notice'] = (s['notice'] + ' ' + warning) if s['notice'] else warning
        s['health_warning'] = warning
        s['health_at'] = s['used']

    def usage(self, s, used, window, event):
        if s['model'] != event.get('model'):
            s['window'] = None
            s['health_at'] = None
        s.update(used=used, model=event.get('model'), usage_turn=event.get('turn_id'), data_issue=None)
        changed_window = window is not None and window != s['window']
        if window is not None:
            s['window'] = window
        if (s['health_at'] is None or changed_window or used >= s['health_at'] + self.limits['health_step']):
            self.health(s)
        if s['skip_cycle'] == s['cycle']:
            return
        if used >= self.limits['organize'] and not s['organize_issued']:
            s['notice'] = '上下文已到整理线，请显式调用 ' + START + '。下一条普通工作请求会先暂停。'
        elif used >= self.limits['prepare'] and not s['prepare_issued']:
            s['notice'] = '上下文已到准备线，请准备收尾，给整理和双检查留出余量。'

    def compact(self, store, s, event, marker):
        return store.apply_compaction(s, event, marker)

    def process(self, store, s, event):
        name = event['hook_event_name']
        turn = event.get('turn_id')
        records = tail_records(event['transcript_path'])
        if name == 'UserPromptSubmit' and event.get('prompt', '').strip() in MONITOR_CONTROLS:
            return {'hookSpecificOutput': {'hookEventName': name, 'additionalContext':
                '仅执行明确调用的监控管理动作。关闭监控不解除活动整理批次的压缩暂停。'}}
        if self.config.get('monitoring', True) is False and name not in (
                'PreCompact', 'PostCompact', 'UserPromptSubmit') and not (name == 'SessionStart' and event.get('source') == 'compact'):
            return {}
        if name == 'PreCompact':
            require(turn and event.get('trigger') in ('manual', 'auto'), 'Compaction start missing identity', 3)
            s['pre_compact'] = turn + ':' + event['trigger']
        elif name == 'PostCompact' or (name == 'SessionStart' and event.get('source') == 'compact'):
            return self.compact(store, s, event, compact_marker(records))
        elif name == 'SessionStart':
            usage = recent_usage(records, session=event['session_id'])
            if usage:
                self.usage(s, usage['used'], usage['window'] if usage['model'] == event.get('model') else None, event)
            self.health(s)
            if s['notice']:
                # At session start no model call is added just to turn a warning into prose.
                return {'systemMessage': s['notice']}
        elif name == 'PreModelRequest':
            s['open_turn'] = turn
        elif name == 'PostModelRequest':
            if (event.get('status') != 'completed' or s['open_turn'] not in (None, turn)
                    or not root_turn_known(records, event)):
                return {}
            used = (event.get('token_usage') or {}).get('total_tokens')
            if type(used) is not int or used < 0:
                issue = 'completed request has no usable token count'
                if s['data_issue'] != issue:
                    s['notice'] = '本次完成请求缺少有效占用读数，不能把上次数据当作最新值；请检查用量记录。'
                s['data_issue'] = issue
                return {}
            usage = recent_usage(records, turn, event['session_id'])
            self.usage(s, used, usage['window'] if usage and usage['used'] == used and usage['model'] == event.get('model') else None, event)
        elif name == 'Stop':
            if s['open_turn'] not in (None, turn):
                return {}
            usage = recent_usage(records, turn, event['session_id'])
            if usage:
                self.usage(s, usage['used'], usage['window'] if usage['model'] == event.get('model') else None, event)
            else:
                s['data_issue'] = 'No fresh matching Stop usage/window in bounded transcript tail'
                s['window'] = None
                self.health(s)
            if event.get('stop_hook_active'):
                s['notice'] = None
                s['pending_reminder_reason'] = None
                return {}
            if s['notice']:
                notice = s['notice']
                s['notice'] = None
                if s['used'] is not None and s['used'] >= self.limits['organize']:
                    s['organize_issued'] = s['prepare_issued'] = True
                elif s['used'] is not None and s['used'] >= self.limits['prepare']:
                    s['prepare_issued'] = True
                if s['health_warning'] and s['health_warning'] not in notice:
                    notice += ' ' + s['health_warning']
                reason = '仅作为独立聊天回复发送以下提醒，不调用工具，不整理或压缩：' + notice
                s['pending_reminder_reason'] = reason
                return {'decision': 'block', 'reason': reason}
        elif name == 'UserPromptSubmit':
            require(turn, 'Prompt turn ID missing')
            s['open_turn'] = turn
            prompt = event.get('prompt', '').strip()
            if prompt == s.get('pending_reminder_reason'):
                s['pending_reminder_reason'] = None
                return {}
            if prompt in (SKIP, '$context-monitor 本周期跳过'):
                if s.get('cycle_uncertain'):
                    return {'decision': 'block', 'reason': '压缩周期身份待核定，不能假定跳过成功，请检查事件或停用配置。' + RESEND}
                s['skip_cycle'] = s['cycle']
                s['notice'] = None
                return {'hookSpecificOutput': {'hookEventName': name, 'additionalContext':
                    '用户仅跳过当前压缩周期的整理提醒，不永久关闭。只确认并提醒重新发送之前被阻止的文字和附件，不自动续接。'}}
            if prompt in (START, '$context-scroll', STATUS, '$context-scroll 处理中断', '/compact'):
                return {}
            if s.get('cycle_uncertain'):
                return {'decision': 'block', 'reason': '压缩周期尚无法核定，暂停新工作，请检查事件或显式停用配置。' + RESEND}
            state = store.load()
            if store.interruption_path.exists() and read_json(store.interruption_path).get('status') == 'awaiting_user':
                return {'hookSpecificOutput': {'hookEventName': name, 'additionalContext':
                    '压缩中断尚未处理。仅向用户报告保存位置、实际进度及处理选项，等明确指示；不继续整理或业务。'}}
            if state and state['status'] not in ('published', 'cancelled'):
                return {'hookSpecificOutput': {'hookEventName': name, 'additionalContext':
                    '当前处于明确启动的整理批次。仅把本消息用于该批澄清/修订/放行，不能开始无关业务；批准仍须主模型明确判断。'}}
            if self.config.get('monitoring', True) is False:
                return {}
            if s['used'] is not None and s['used'] >= self.limits['organize'] and s['skip_cycle'] != s['cycle']:
                saved = store.safe(store.home / '.状态/blocked' / (str(uuid.UUID(turn)) + '.json'))
                blocked = {
                    'thread_id': store.thread, 'turn_id': turn, 'cycle': s['cycle'],
                    'prompt': event.get('prompt'), 'attachments': 'not supplied by hook; user must resend'}
                require(not saved.exists() or read_json(saved) == blocked, 'Blocked turn text changed; preserve original and inspect', 3)
                store.write(saved, encode(blocked))
                return {'decision': 'block', 'reason': '该整理上下文了。可发送“' + START + '”或“' + SKIP + '”。' + RESEND}
        return {}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    event = {}
    try:
        event = json.load(sys.stdin)
        output = Hooks(read_json(args.config)).handle(event)
    except (OSError, ValueError, TypeError, KeyError, StoreError) as exc:
        reason = '上下文提醒处理异常：' + type(exc).__name__ + '。请检查配置/日志。'
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
