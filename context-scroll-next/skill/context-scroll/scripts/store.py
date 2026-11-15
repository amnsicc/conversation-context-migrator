"""Local storage only. Callers supply real review/approval evidence; no model API."""
from contextlib import contextmanager
from datetime import datetime, timezone
from html import unescape
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time
import uuid
from urllib.parse import quote, unquote, urlsplit


class StoreError(Exception):
    def __init__(self, message, code=3):
        super().__init__(message)
        self.code = code


def require(condition, message, code=2):
    if not condition:
        raise StoreError(message, code)


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_digest(path):
    hasher = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def runtime_digest():
    folder = Path(__file__).parent
    return digest(encode({name: file_digest(folder/name) for name in
                          ('store.py', 'hooks.py', 'router.py', 'runtime.py')}))


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (ValueError, UnicodeError) as exc:
        raise StoreError('Invalid UTF-8 JSON: ' + str(path), 2) from exc
    require(isinstance(value, dict), 'Expected JSON object')
    return value


@contextmanager
def lock_file(path, timeout=3):
    # OS releases locks on process death; the persistent lock file is not a stale lock.
    f = path.open('a+b')
    try:
        if f.seek(0, 2) == 0:
            f.write(b'0')
            f.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                f.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise StoreError('Archive is busy; retry after current operation', 3)
                time.sleep(0.03)
        try:
            yield
        finally:
            f.seek(0)
            if os.name == 'nt':
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    finally:
        f.close()


class Store:
    def __init__(self, project, thread):
        p = Path(project)
        require(p.is_absolute() and p.is_dir(), 'Project must be an existing absolute directory')
        self.project = p.resolve()
        try:
            canonical = str(uuid.UUID(thread))
        except (ValueError, AttributeError) as exc:
            raise StoreError('Thread must be its exact UUID, not a title or path', 2) from exc
        require(thread == canonical, 'Use canonical lowercase thread UUID')
        self.thread = thread
        self.root = self.project / '上下文维护'
        self.home = self.root / thread
        self.state_path = self.home / '.状态/state.json'
        self.journal_path = self.home / '.状态/journal.json'
        self.project_baseline = self.root / '.状态/project-baseline.json'
        self.interruption_path = self.home / '.状态/interruption.json'

    def safe(self, path):
        path = Path(path)
        require(path.is_relative_to(self.root), 'Destination outside archive')
        current = self.project
        for part in path.relative_to(self.project).parts:
            require(part not in ('..', '.'), 'Unsafe destination component')
            current = current / part
            if current.exists() or current.is_symlink():
                st = current.lstat()
                require(not current.is_symlink() and not (
                    getattr(st, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)),
                    'Archive destinations must not be links/reparse points')
        require(path.resolve().is_relative_to(self.project), 'Resolved destination outside project')
        return path

    def mkdir(self, path):
        self.safe(path).mkdir(parents=True, exist_ok=True)
        self.safe(path)

    def write(self, path, data):
        self.safe(path)
        self.mkdir(path.parent)
        temp = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
                temp = Path(f.name)
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            self.safe(path)
            os.replace(temp, path)
        finally:
            if temp is not None and temp.exists():
                temp.unlink()

    def load(self):
        if not self.safe(self.state_path).exists():
            return None
        s = read_json(self.state_path)
        require(s['thread_id'] == self.thread and Path(s['project_root']).samefile(self.project),
                'Stored archive identity mismatch', 3)
        return s

    def copy_material(self, source, target, large_allowed):
        self.mkdir(target.parent)
        temp = None
        size = 0
        hasher = hashlib.sha256()
        try:
            with source.open('rb') as incoming, tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as out:
                temp = Path(out.name)
                for chunk in iter(lambda: incoming.read(1024 * 1024), b''):
                    size += len(chunk)
                    require(large_allowed or size <= 5 * 1024 * 1024, 'Material grew beyond limit; ask user', 3)
                    hasher.update(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            self.safe(target)
            os.replace(temp, target)
            return size, hasher.hexdigest()
        finally:
            if temp is not None and temp.exists():
                temp.unlink()

    @contextmanager
    def locked(self):
        self.mkdir(self.root / '.locks')
        # Project index is shared: all mutations take project then thread lock in that order.
        with lock_file(self.safe(self.root / '.locks/project.lock')):
            with lock_file(self.safe(self.root / '.locks' / (self.thread + '.lock'))):
                yield

    def transaction_issues(self):
        found = {}
        if self.root.exists():
            for folder in self.root.iterdir():
                if folder.name.startswith('.') or not folder.is_dir():
                    continue
                self.safe(folder)
                path = self.safe(folder / '.状态/journal.json')
                if path.exists():
                    try:
                        peer = Store(str(self.project), folder.name)
                        journal = read_json(path)
                        peer.validate_journal(journal)
                        if journal['status'] == 'pending':
                            found[folder.name] = 'Publication pending recovery'
                        else:
                            peer.verify_terminal(journal)
                    except (StoreError, OSError, ValueError, KeyError, TypeError) as exc:
                        found[folder.name] = 'Journal conflict: ' + str(exc)
        return found

    def pending(self):
        return list(self.transaction_issues())

    def save_state(self, s):
        s['revision'] += 1
        s['updated_at'] = now()
        self.write(self.state_path, encode(s))

    def batch_dir(self, batch):
        require(isinstance(batch, str) and re.fullmatch(r'b-[0-9a-f]{32}', batch), 'Invalid batch ID')
        return self.safe(self.home / '.候选' / batch)

    def active(self, s, batch):
        self.batch_dir(batch)
        require(s is not None and s['batch_id'] == batch, 'Not the active batch', 3)
        require(s['status'] not in ('published', 'cancelled'), 'Batch already closed', 3)

    def interruption_active(self, s):
        """A state write alone does not close its unresolved publication transaction."""
        if not s:
            return False
        if s['status'] not in ('published', 'cancelled'):
            return True
        if self.thread not in self.transaction_issues():
            return False
        journal = read_json(self.safe(self.journal_path))
        require(journal.get('thread_id') == self.thread and journal.get('batch_id') == s['batch_id'],
                'Unresolved journal cannot be attributed to the current batch', 3)
        return True

    def queue_compaction(self, event, marker):
        # Only an immutable notification is created outside the main lock. A partial
        # record is a visible conflict, never permission to modify the archive.
        value = {'schema_version': '0.1', 'thread_id': self.thread,
                 'event': {k: event.get(k) for k in ('hook_event_name', 'turn_id', 'trigger', 'source')},
                 'marker': marker}
        key = digest(encode(value))
        receipt = self.safe(self.home / '.状态/compact-receipts' / (key + '.json'))
        # A delivery receipt proves processing, not that a later identity-poor
        # observation is the same compaction. SessionStart must be re-associated
        # against current metadata under lock. An unresolved Post needs this too.
        if (event.get('hook_event_name') == 'SessionStart' or
                receipt.exists() and read_json(receipt).get('identity_known') is not True):
            value['delivery_id'] = uuid.uuid4().hex  # notification ID, NOT a compaction identity
            key = digest(encode(value))
        path = self.safe(self.home / '.状态/compact-inbox' / (key + '.json'))
        self.mkdir(path.parent)
        try:
            with path.open('xb') as f:
                f.write(encode(value))
                f.flush()
                os.fsync(f.fileno())
        except FileExistsError:
            require(read_json(path) == value, 'Compaction notification conflict', 3)
        return key

    def pending_compactions(self):
        folder = self.safe(self.home / '.状态/compact-inbox')
        found = []
        if folder.exists():
            for path in sorted(folder.glob('*.json'), key=lambda p: (p.stat().st_mtime_ns, p.name)):
                self.safe(path)
                try:
                    value = read_json(path)
                    require(value.get('schema_version') == '0.1' and value.get('thread_id') == self.thread
                            and digest(encode(value)) == path.stem, 'Compaction notification identity/hash conflict', 3)
                    event = value['event']
                    require(isinstance(event, dict) and (event.get('hook_event_name') == 'PostCompact' or
                            event.get('hook_event_name') == 'SessionStart' and event.get('source') == 'compact'),
                            'Invalid compaction notification event', 3)
                    receipt = self.safe(self.home / '.状态/compact-receipts' / path.name)
                    if receipt.exists():
                        require(read_json(receipt).get('notification_sha256') == path.stem,
                                'Compaction notification receipt conflict', 3)
                        continue
                    found.append({'id': path.stem, 'notification': value})
                except (StoreError, OSError, KeyError, TypeError, ValueError) as exc:
                    found.append({'id': path.stem, 'error': str(exc)})
        return found

    def reconcile_compactions(self):
        # Only metadata; caller must hold project+thread locks. No Hook import is
        # required when store.py is used as a standalone file-loaded library.
        outputs = {}
        for item in self.pending_compactions():
            require('error' not in item, 'Compaction inbox conflict: ' + item.get('error', ''), 3)
            path, reminder = self.reminder_state()
            value = item['notification']
            result = self.apply_compaction(reminder, value['event'], value['marker'], item['id'])
            self.write(path, encode(reminder))
            self.write(self.home / '.状态/compact-receipts' / (item['id'] + '.json'),
                       encode({'notification_sha256': item['id'],
                               'identity_known': not reminder.get('cycle_uncertain', False), 'output': result}))
            outputs[item['id']] = result
        return outputs

    def reminder_state(self):
        store = self
        path = store.safe(store.home / '.状态/reminder.json')
        s = read_json(path) if path.exists() else {
            'schema_version': '0.1', 'thread_id': store.thread, 'cycle': 0,
            'skip_cycle': None, 'used': None, 'window': None, 'model': None,
            'health_at': None, 'health_warning': None, 'notice': None,
            'prepare_issued': False, 'organize_issued': False, 'open_turn': None,
            'pre_compact': None, 'post_compact': None, 'compactions': [], 'last_marker': None,
            'awaiting_compact_start': False, 'data_issue': None}
        require(s['thread_id'] == store.thread and s['schema_version'] == '0.1',
                'Reminder state identity/version conflict', 3)
        return path, s

    def pause_unknown_compaction(self, s, notification_id):
        s.update(cycle_uncertain=True, skip_cycle=None, used=None, window=None,
                 health_at=None, health_warning=None, notice=None, awaiting_compact_start=False)
        self.mark_compaction_interruption('compact_identity_missing', notification_id or uuid.uuid4().hex)
        reason = '收到压缩完成事件但无法核定周期，已停止；请检查事件并明确指示，不自动继续。' + '需要继续原工作时，请重新发送原文字和附件。'
        return {'continue': False, 'stopReason': reason, 'systemMessage': reason}

    def apply_compaction(self, s, event, marker, notification_id=None):
        store = self
        name = event['hook_event_name']
        marker_keys = s.setdefault('compact_marker_keys', {})
        previous_key = s.get('post_compact')
        if name == 'PostCompact':
            if not isinstance(event.get('turn_id'), str) or not event['turn_id'] or event.get('trigger') not in ('manual', 'auto'):
                return self.pause_unknown_compaction(s, notification_id)
            key = event['turn_id'] + ':' + event['trigger']
            if (marker in marker_keys and marker_keys[marker].startswith('log:')
                    and s.get('pre_compact') != key and key not in s['compactions']):
                # Same old marker alone cannot associate this new turn with log:A.
                return self.pause_unknown_compaction(s, notification_id)
            s['awaiting_compact_start'] = True
        elif s.get('pre_compact') and s['pre_compact'] not in s['compactions']:
            key = s['pre_compact']
            s['awaiting_compact_start'] = False
        elif s['awaiting_compact_start'] and (not marker or not s['last_marker'] or marker == s['last_marker']):
            key = s['post_compact']
            s['awaiting_compact_start'] = False
        elif marker and marker in marker_keys:
            key = marker_keys[marker]
            s['awaiting_compact_start'] = False
        elif marker:
            key = 'log:' + marker
            s['awaiting_compact_start'] = False
        else:
            return self.pause_unknown_compaction(s, notification_id)
        require(key is not None, 'Compaction cycle unavailable', 3)
        if key in s['compactions'] and (s.get('cycle_uncertain') or previous_key and previous_key != key):
            # A delayed old marker must not undo the current event's acknowledgement.
            reason = '收到已处理的旧压缩事件，保留当前周期与处理指示，不自动继续。'
            return {'continue': False, 'stopReason': reason, 'systemMessage': reason}
        if key in s['compactions']:
            if marker:
                marker_keys.setdefault(marker, key)
                s['last_marker'] = marker
            # Proven repeats cannot reopen a pause acknowledged for a later
            # unresolved delivery, or interrupt a batch started after this event.
            reason = '同次压缩事件已处理，保留当前周期与处理指示，不自动继续。'
            return {'continue': False, 'stopReason': reason, 'systemMessage': reason}
        if key not in s['compactions']:
            s['compactions'].append(key)
            s.update(cycle=s['cycle'] + 1, cycle_uncertain=False, skip_cycle=None, used=None, window=None,
                     health_at=None, health_warning=None, notice=None,
                     prepare_issued=False, organize_issued=False, open_turn=None)
        s['post_compact'] = key
        if marker:
            if marker not in marker_keys:
                marker_keys[marker] = key
            s['last_marker'] = marker
        active = self.mark_compaction_interruption(key)
        reason = ('压缩已结束，整理进度已保留，暂停等待指示。' if active else '压缩已结束，本周期跳过和提醒已重置。')
        reason += ' 不会自动继续原工作；需要时请重新发送原文字和附件。'
        return {'continue': False, 'stopReason': reason, 'systemMessage': reason}

    def mark_compaction_interruption(self, key, pause_notification=None):
        store = self
        state = store.load()
        active = store.interruption_active(state)
        if active:
            interruption = {'schema_version': '0.1', 'thread_id': store.thread, 'batch_id': state['batch_id'],
                'status': 'awaiting_user', 'compact_key': key, 'progress_status': state['status'],
                'candidate_directory': str(store.batch_dir(state['batch_id'])), 'reason': '整理途中发生压缩，停止并等待用户指示'}
            if pause_notification is not None:
                interruption['pause_notification'] = pause_notification
            if (not store.interruption_path.exists() or
                    (read_json(store.interruption_path).get('compact_key'), read_json(store.interruption_path).get('batch_id'),
                     read_json(store.interruption_path).get('pause_notification'))
                    != (key, state['batch_id'], pause_notification)):
                store.write(store.interruption_path, encode(interruption))
        return active

    def materials(self, s):
        path = self.batch_dir(s['batch_id']) / 'materials.json'
        return read_json(self.safe(path))

    def assign_stage(self, s, coverage):
        """Bind a batch to a compression stage; old unkeyed archives stay readable."""
        publications = s['publications'] if s else []
        latest = publications[-1] if publications else None
        key = coverage.get('stage_key')
        if key is None:
            require(not latest or 'stage_key' not in latest,
                    'Keyed stages require stage_key on every subsequent batch', 3)
            require('replace_batch' not in coverage, 'Replacement requires stage_key')
            return None  # Compatibility for pre-stage-7 callers, never used by the skill.
        require(isinstance(key, str) and key.strip(), 'stage_key must locate the actual stage boundary')
        _, reminder = self.reminder_state()
        require(not reminder.get('cycle_uncertain'), 'Resolve unknown compaction before assigning stage', 3)
        observed = reminder.get('post_compact')
        replacement = None
        number = (s['phase_number'] if s else 0) + 1
        if latest and latest.get('stage_key') == key:
            require(coverage['start'] == latest['stage_start'] and
                    coverage['previous_compaction'] == latest['previous_compaction'],
                    'Same-stage update must keep the full stage start/boundary', 3)
            require(not observed or observed == latest.get('observed_compaction'),
                    'A new observed compaction requires a new stage', 3)
            number = latest['phase_number']
            replacement = latest['batch_id']
        elif latest and coverage.get('replace_batch') is not None:
            require('stage_key' not in latest and coverage['replace_batch'] == latest['batch_id'],
                    'Legacy adoption must name the latest unkeyed publication', 3)
            old_coverage = read_json(self.safe(self.batch_dir(latest['batch_id']) / 'coverage.json'))
            require(coverage['start'] == old_coverage['start'] and
                    coverage['previous_compaction'] == old_coverage['previous_compaction'],
                    'Legacy adoption must preserve its stage coverage start/boundary', 3)
            number = latest['phase_number']
            replacement = latest['batch_id']
        else:
            require(not any(p.get('stage_key') == key for p in publications),
                    'Cannot reopen an older compression stage', 3)
            if latest and 'stage_key' in latest:
                require(coverage['previous_compaction'] is not None and
                        coverage['previous_compaction'] != latest['previous_compaction'],
                        'A new stage requires a distinct actual compaction boundary', 3)
                # Monitoring/guard can be off between batches: an unchanged Hook
                # observation does not disprove a later source-located compaction.
            require('replace_batch' not in coverage, 'No legacy publication to adopt', 3)
        return {'number': number, 'stage_key': key, 'replaces_batch': replacement,
                'observed_compaction': observed}

    def stage_number(self, s):
        published = next((p for p in s['publications'] if p['batch_id'] == s['batch_id']), None)
        if published:
            return published['phase_number']
        return s['stage_assignment']['number'] if s.get('stage_assignment') else s['phase_number'] + 1

    def snapshot(self, s):
        base = self.batch_dir(s['batch_id'])
        objects = {}
        for key, name in [('stage', 'stage.md'), ('handover', 'handover.md'), ('materials', 'materials.json')]:
            path = self.safe(base / name)
            require(path.is_file(), 'Candidate is incomplete: ' + key, 3)
            objects[key] = {'path': path.relative_to(self.home).as_posix(), 'sha256': digest(path.read_bytes())}
        cov = read_json(self.safe(base / 'coverage.json'))
        require(cov == s['coverage'], 'Coverage changed outside store', 3)
        manifest = self.materials(s)
        for item in manifest['items']:
            path = self.safe(self.home / item['saved_path'])
            require(path.is_file() and file_digest(path) == item['sha256'], 'Saved material missing/changed', 3)
        identity = {'thread': self.thread, 'batch': s['batch_id'], 'coverage': cov,
                    'base_anchor': s['base_anchor'], 'objects': objects}
        if s.get('stage_assignment'):
            identity['stage_assignment'] = s['stage_assignment']
        result = {'objects': objects}
        if s.get('publication_plan'):
            plan = s['publication_plan']
            require(plan['source_objects'] == objects, 'Publication source changed; prepare again', 3)
            expected_targets = {'stage': f'阶段单元/阶段-{self.stage_number(s):03d}.md', 'handover': '接手单元.md'}
            # Published state increments phase_number, but the plan keeps this batch's actual number.
            published = next((p for p in s['publications'] if p['batch_id'] == s['batch_id']), None)
            if published:
                expected_targets['stage'] = f'阶段单元/阶段-{published["phase_number"]:03d}.md'
            for role in ('stage', 'handover'):
                item = plan['outputs'][role]
                require(item['target'] == expected_targets[role] and item['path'] ==
                        f'.候选/{s["batch_id"]}/.待发布/{role}.md', 'Invalid publication plan layout', 3)
                require(self.file_hash(self.home / item['path']) == item['sha256'], 'Prepared publication changed', 3)
            result['publication_plan'] = plan
            identity['publication_plan'] = plan
        result['content_hash'] = digest(encode(identity))
        return result

    def verify_candidate(self, s):
        current = self.snapshot(s)
        require(current == s['candidates'], 'Candidate changed; save again and repeat review/approval', 3)
        return current

    def invalidate(self, s):
        s.update(reviews={}, approval=None, status='drafting', candidates=None, publication_plan=None)

    def render_publication(self, text, source, target, peers):
        """Rebase supported Markdown links; refuse unsupported local HTML/link syntax.

        Top-level fenced/inline code stay literal; indented code is explicitly refused.
        This is a bounded scanner, not a general Markdown parser.
        """
        def reference_key(label):
            return ' '.join(unescape(re.sub(r'\\(.)', r'\1', label)).split()).casefold()

        # Conservatively include definition-shaped lines even in literal areas. A
        # possible shortcut reference inside another label is refused, never guessed.
        reference_labels = {reference_key(label) for label in re.findall(
            r'(?m)^[ \t>]*\[([^\[\]\r\n]+)\]:', text)}

        def rebase(url):
            drive_path = bool(re.match(r'^[A-Za-z]:[/\\]', url))
            if not url or url.startswith('#') or (urlsplit(url).scheme and not drive_path) or url.startswith('//'):
                return url
            parts = urlsplit(url if not drive_path else url[2:])
            require(not parts.query, 'Local link query unsupported; use a plain path', 3)
            path_text = (url[:2] if drive_path else '') + unquote(parts.path)
            path = (source.parent / path_text).resolve()
            path = peers.get(path, path)
            require(path in peers.values() or path.is_file(), 'Broken local publication link: ' + url, 3)
            # A Windows drive mismatch cannot be represented by a relative path.
            try:
                relative = os.path.relpath(path, target.parent).replace('\\', '/')
            except ValueError:
                relative = path.as_posix()
            return quote(relative, safe='/:@-._~') + ('#' + parts.fragment if parts.fragment else '')

        def prose(segment):
            require(not re.search(r'<[^>]+\b(?:href|src)\s*=', segment, re.I),
                    'Use Markdown links instead of HTML href/src before publication', 3)
            # Visit every unescaped '['; balanced labels cannot disappear from both a
            # replacement regex and its identically limited "coverage" counter.
            output = []
            cursor = 0
            while cursor < len(segment):
                char = segment[cursor]
                if char == '\\' and cursor + 1 < len(segment):
                    output.append(segment[cursor:cursor + 2])
                    cursor += 2
                    continue
                if char != '[':
                    output.append(char)
                    cursor += 1
                    continue
                end = cursor + 1
                depth = 1
                nested = False
                openings = [cursor]
                while end < len(segment) and depth:
                    if segment[end] == '\\' and end + 1 < len(segment):
                        end += 2
                        continue
                    if segment[end] == '[':
                        require(end == 0 or segment[end - 1] != '!',
                                'Active link/image inside a label unsupported; split into separate simple links before preparation', 3)
                        depth += 1
                        nested = True
                        openings.append(end)
                    elif segment[end] == ']':
                        depth -= 1
                        opening = openings.pop()
                        if depth:
                            suffix = segment[end + 1:end + 2]
                            require(suffix not in ('(', '[') and
                                    reference_key(segment[opening + 1:end]) not in reference_labels,
                                    'Active link/image inside a label unsupported; split into separate simple links before preparation', 3)
                    end += 1
                require(depth == 0, 'Unsupported unbalanced/multiline label; use a single-line label or escape brackets', 3)
                label = segment[cursor:end]
                tail = segment[end:]
                if tail.startswith('('):
                    link = re.match(r'\(([^\s()]+)([ \t]+"[^"\n]*")?\)', tail)
                    require(link is not None, 'Unsupported Markdown destination; encode spaces/parentheses in URL', 3)
                    output.append(label + '(' + rebase(link[1]) + (link[2] or '') + ')')
                    cursor = end + link.end()
                elif tail.startswith(':'):
                    require(not nested and re.fullmatch(r' {0,3}', segment[:cursor]) is not None,
                            'Reference definition in unsupported container/label; move to a plain single line', 3)
                    definition = re.fullmatch(r'(:[ \t]*)([^\s<>]+)([ \t]+"[^"\r\n]*")?([ \t]*(?:\r?\n)?)', tail)
                    require(definition is not None, 'Unsupported reference definition; use a single-line encoded URL', 3)
                    output.append(label + definition[1] + rebase(definition[2]) + (definition[3] or '') + definition[4])
                    cursor = len(segment)
                else:
                    # Plain/simple reference labels need no change. Nested non-inline
                    # structures could conceal an active inner link: fail visibly.
                    require(not nested, 'Nested reference/plain bracket structure unsupported; simplify before preparation', 3)
                    output.append(label)
                    cursor = end
            return ''.join(output)

        def inline(content):
            output = []
            start = cursor = 0
            while cursor < len(content):
                if content[cursor] == '\\' and cursor + 1 < len(content):
                    cursor += 2
                    continue
                if content[cursor] != '`':
                    cursor += 1
                    continue
                end = cursor
                while end < len(content) and content[end] == '`':
                    end += 1
                delimiter = content[cursor:end]
                closing = re.search(r'(?<!`)' + re.escape(delimiter) + r'(?!`)', content[end:])
                require(closing is not None, 'Unmatched/multiline inline code unsupported; escape backticks or use a fenced block', 3)
                stop = end + closing.end()
                output.extend((prose(content[start:cursor]), content[cursor:stop]))
                start = cursor = stop
            output.append(prose(content[start:]))
            return ''.join(output)

        fence = None
        result = []
        for line in text.splitlines(keepends=True):
            if fence:
                closing = re.match(r'^ {0,3}(`{3,}|~{3,})[ \t]*(?:\r?\n)?$', line)
                if closing and closing[1][0] == fence[0] and len(closing[1]) >= len(fence):
                    fence = None
                result.append(line)
            else:
                # Peel only explicit quote markers. Nested/list containers we cannot
                # distinguish from literal code must be refused rather than rewritten.
                quote_prefix = ''
                content = line
                while True:
                    marker = re.match(r'^ {0,3}>[ \t]?', content)
                    if not marker:
                        break
                    quote_prefix += marker[0]
                    content = content[marker.end():]
                require(not content.strip() or not re.match(r'^(?: {4}| {0,3}\t)', content),
                        'Indented code/container unsupported; convert to a top-level fenced code block before preparation', 3)
                opening = re.match(r'^ {0,3}(`{3,}|~{3,})', content)
                if opening:
                    require(not quote_prefix, 'Container code fence unsupported; use a top-level fenced block', 3)
                    fence = opening[1]
                    result.append(line)
                else:
                    result.append(quote_prefix + inline(content))
        require(fence is None, 'Unclosed code fence in candidate', 3)
        return ''.join(result).encode('utf-8')

    def prepare_publication(self, s, arg):
        self.verify_candidate(s)
        base = self.batch_dir(s['batch_id'])
        destinations = {'stage': f'阶段单元/阶段-{self.stage_number(s):03d}.md', 'handover': '接手单元.md'}
        peers = {(base / (role + '.md')).resolve(): (self.home / target).resolve()
                 for role, target in destinations.items()}
        rendered = {role: self.render_publication((base / (role + '.md')).read_bytes().decode('utf-8'),
                     base / (role + '.md'), self.home / target, peers) for role, target in destinations.items()}
        plan = {'source_objects': s['candidates']['objects'], 'outputs': {role: {
            'path': f'.候选/{s["batch_id"]}/.待发布/{role}.md', 'target': destinations[role],
            'sha256': digest(data)} for role, data in rendered.items()}}
        if s.get('publication_plan') == plan:
            return {'status': s['status'], 'batch_id': s['batch_id'], 'candidates': s['candidates'], 'unchanged': True}
        self.revision(s, arg)
        for role, data in rendered.items():
            self.write(self.home / plan['outputs'][role]['path'], data)
        self.invalidate(s)
        s['publication_plan'] = plan
        s['candidates'] = self.snapshot(s)
        s['status'] = 'reviewing'
        self.save_state(s)
        return {'status': s['status'], 'batch_id': s['batch_id'], 'candidates': s['candidates']}

    def revision(self, s, arg):
        if s['reviews'] and arg.get('revision_mode', 'autonomous') == 'autonomous':
            require(s['revision_rounds'] < 2, 'Two autonomous revision rounds used; ask user', 3)
            s['revision_rounds'] += 1
        if arg.get('revision_mode') == 'user':
            instruction = read_json(arg['instruction_file']) if arg.get('instruction_file') else {}
            require(instruction.get('message_ref') and instruction.get('user_text'), 'User revision instruction required')
            self.record_hash(instruction, self.batch_dir(s['batch_id']) / ('instruction-' + uuid.uuid4().hex + '.json'))

    def evidence(self, s, value, kind):
        require(value.get('thread_id') == self.thread and value.get('batch_id') == s['batch_id'],
                'Evidence thread/batch mismatch', 3)
        require(value.get('content_hash') == self.verify_candidate(s)['content_hash'], 'Evidence is stale', 3)
        require(isinstance(value.get('message_ref'), str) and value['message_ref'].strip(),
                'Real source message_ref is required')
        # No inference from prose containing the word "pass". The caller attests provenance.
        if kind == 'approval':
            require(value.get('decision') == 'approve' and value.get('scope') == 'both',
                    'Explicit approval of both candidates is required')
            require(isinstance(value.get('user_text'), str) and value['user_text'].strip(), 'Original user approval text required')
        else:
            require(value.get('role') in ('stage', 'handover'), 'Invalid review role')
            require(value.get('verdict') in ('pass', 'revise', 'insufficient'), 'Invalid review verdict')
            require(isinstance(value.get('reviewer_id'), str) and value['reviewer_id'].strip(), 'Reviewer ID required')
            require(isinstance(value.get('summary'), str) and value['summary'].strip(), 'Review summary required')

    def record_hash(self, value, path):
        data = encode(value)
        self.write(path, data)
        return {'path': path.relative_to(self.home).as_posix(), 'sha256': digest(data)}

    def check_record(self, record):
        path = self.safe(self.home / record['path'])
        require(path.is_file() and digest(path.read_bytes()) == record['sha256'], 'Evidence file missing/changed', 3)
        return read_json(path)

    def ready(self, s):
        snap = self.verify_candidate(s)
        require(set(s['reviews']) == {'stage', 'handover'}, 'Two reviews required', 3)
        ids = []
        for role, rec in s['reviews'].items():
            review = self.check_record(rec)
            self.evidence(s, review, 'review')
            require(review['role'] == role and review['verdict'] == 'pass', 'Both reviews must pass', 3)
            ids.append(review['reviewer_id'])
        require(ids[0] != ids[1], 'Two distinct reviewers required', 3)
        require(s['approval'] is not None, 'User approval required', 3)
        self.evidence(s, self.check_record(s['approval']), 'approval')
        return snap

    def status(self, s):
        issues = self.transaction_issues()
        pending = list(issues)
        result = {'status': s['status'] if s else 'idle', 'state': s, 'pending_publications': pending}
        result['transaction_issues'] = issues
        if self.safe(self.interruption_path).exists():
            interruption = read_json(self.interruption_path)
            result['interruption'] = interruption
            if interruption.get('status') == 'awaiting_user':
                result['status'] = 'interrupted'
        if self.thread in pending:
            result['status'] = 'journal_conflict' if issues[self.thread].startswith('Journal conflict:') else 'publishing'
        if result.get('interruption', {}).get('status') == 'awaiting_user':
            result['status'] = 'interrupted'
        result['pending_compactions'] = self.pending_compactions()
        if result['pending_compactions']:
            result['status'] = 'compaction_pending'
        if s and s.get('candidates'):
            try:
                self.verify_candidate(s)
                result['candidate_integrity'] = True
            except (StoreError, OSError):
                result['candidate_integrity'] = False
        return result

    def execute(self, operation, **arg):
        # Read-only status does not create an archive for an unused project.
        if operation == 'status' and not self.root.exists():
            return self.status(None)
        with self.locked():
            s = self.load()
            pending = self.pending()
            if operation == 'status':
                return self.status(s)
            if operation == 'ack-interruption':
                self.batch_dir(arg['batch'])
                require(s is not None and s['batch_id'] == arg['batch'], 'Not the active batch', 3)
                require(self.interruption_active(s), 'Batch already closed', 3)
                self.reconcile_compactions()
                interruption = read_json(self.safe(self.interruption_path))
                instruction = read_json(arg['instruction_file'])
                require(interruption.get('batch_id') == s['batch_id'] and interruption.get('status') == 'awaiting_user',
                        'No matching interruption to acknowledge', 3)
                require(instruction.get('decision') == 'resume_after_compaction' and instruction.get('message_ref')
                        and instruction.get('user_text'), 'Explicit post-compaction instruction required', 3)
                reminder_path = self.safe(self.home / '.状态/reminder.json')
                if reminder_path.exists():
                    reminder = read_json(reminder_path)
                    if reminder.get('cycle_uncertain'):
                        reminder.update(cycle_uncertain=False, skip_cycle=None, used=None, window=None,
                                        health_at=None, notice=None, prepare_issued=False, organize_issued=False)
                        self.write(reminder_path, encode(reminder))
                interruption.update(status='acknowledged', instruction=instruction)
                self.write(self.interruption_path, encode(interruption))
                return {'status': s['status'], 'batch_id': s['batch_id']}
            # Reconcile metadata only; the resulting awaiting_user marker below still
            # forbids business writes/recovery until an explicit acknowledgement.
            self.reconcile_compactions()
            if self.safe(self.interruption_path).exists():
                require(read_json(self.interruption_path).get('status') != 'awaiting_user',
                        'Compaction interrupted work; wait for explicit user instruction', 3)
            if operation == 'recover':
                return self.recover(arg['batch'], arg.get('mode', 'complete'))
            require(not pending, 'Pending publication must be recovered first: ' + ','.join(pending), 3)
            batch = arg.get('batch')
            if operation == 'begin':
                require(s is None or s['status'] in ('published', 'cancelled'), 'Finish or cancel current batch first', 3)
                coverage = read_json(arg['coverage_file'])
                require(all(k in coverage for k in ('start', 'end', 'previous_compaction', 'original_visibility')),
                        'Coverage requires start/end/previous_compaction/original_visibility')
                require(all(isinstance(coverage[k], str) and coverage[k].strip() for k in ('start', 'end', 'original_visibility')),
                        'Coverage locators and visibility must be nonempty strings')
                if arg.get('protect_compaction'):
                    receipt_path = self.safe(self.home / '.状态/runtime-receipt.json')
                    require(receipt_path.exists(), 'Public Hook has not been observed; review it and retry this chat', 3)
                    receipt = read_json(receipt_path)
                    require(receipt.get('thread_id') == self.thread and receipt.get('project') == str(self.project)
                            and receipt.get('runtime_sha256') == runtime_digest(),
                            'Public Hook receipt is absent or belongs to a different runtime', 3)
                    reminder_path = self.safe(self.home / '.状态/reminder.json')
                    require(not reminder_path.exists() or not read_json(reminder_path).get('cycle_uncertain'),
                            'Resolve unknown compaction before starting a protected batch', 3)
                mode = coverage.get('mode', 'continuous')
                require(mode in ('continuous', 'first_visible', 'bootstrap'), 'Unknown coverage mode')
                if mode == 'bootstrap':
                    require(coverage.get('history_sources') and coverage.get('known_gaps') and
                            coverage.get('scope_confirmation', {}).get('message_ref') and
                            coverage['scope_confirmation'].get('user_text'),
                            'Bootstrap coverage needs history sources, gaps and actual user scope confirmation', 2)
                assignment = self.assign_stage(s, coverage)
                parent = arg.get('forked_from')
                if parent:
                    require(str(uuid.UUID(parent)) == parent and parent != self.thread, 'Invalid fork parent')
                if s:
                    require(parent is None or parent == s['forked_from'], 'Fork identity cannot be changed', 3)
                else:
                    s = {'schema_version': '0.1', 'revision': 0, 'project_root': str(self.project),
                         'thread_id': self.thread, 'forked_from': parent, 'published_anchor': None,
                         'publications': [], 'phase_number': 0}
                batch = 'b-' + uuid.uuid4().hex
                base = self.batch_dir(batch)
                self.write(base / 'coverage.json', encode(coverage))
                self.write(base / 'materials.json', encode({'items': []}))
                s.update(batch_id=batch, coverage=coverage, revision_rounds=0, interruption=None,
                         base_anchor=s['published_anchor'], protect_compaction=bool(arg.get('protect_compaction')),
                         stage_assignment=assignment)
                self.invalidate(s)
                self.save_state(s)
                return {'status': s['status'], 'batch_id': batch, 'candidate_directory': str(base),
                        'stage_assignment': assignment}
            if operation == 'publish' and s:
                previous = next((p for p in s['publications'] if p['batch_id'] == batch), None)
                if previous:
                    return {'status': 'published', 'batch_id': batch, 'publication': previous, 'already_published': True}
            self.active(s, batch)
            base = self.batch_dir(batch)
            if operation == 'prepare-publication':
                return self.prepare_publication(s, arg)
            if operation == 'cancel':
                require(isinstance(arg.get('reason'), str) and arg['reason'].strip(), 'Cancellation reason required')
                s.update(status='cancelled', approval=None, interruption=arg['reason'])
            elif operation == 'save-material':
                source = Path(arg['source']).resolve(strict=True)
                require(source.is_file(), 'Material source must be a file')
                require(isinstance(arg.get('purpose'), str) and arg['purpose'].strip(), 'Material purpose required')
                limit = 5 * 1024 * 1024
                if source.stat().st_size > limit:
                    ref = arg.get('large_approval_file')
                    require(ref is not None, 'Material exceeds 5 MiB trial limit; ask user and supply --large-approval-file', 3)
                    approved = read_json(ref)
                    require(approved.get('decision') == 'copy_material' and approved.get('source') == str(source)
                            and approved.get('message_ref') and approved.get('user_text'), 'Specific large-file approval required')
                self.revision(s, arg)
                name = 'm-' + uuid.uuid4().hex + source.suffix[:16]
                saved = self.safe(self.home / '材料' / name)
                size, sha = self.copy_material(source, saved, bool(arg.get('large_approval_file')))
                items = self.materials(s)
                entry = {'original_path': str(source), 'saved_path': saved.relative_to(self.home).as_posix(),
                         'purpose': arg['purpose'], 'bytes': size, 'sha256': sha,
                         'large_copy_approval': read_json(arg['large_approval_file']) if arg.get('large_approval_file') else None}
                items['items'].append(entry)
                self.write(base / 'materials.json', encode(items))
                self.invalidate(s)
            elif operation == 'save-candidate':
                stage = Path(arg['stage']).read_bytes()
                handover = Path(arg['handover']).read_bytes()
                require(stage.strip() and handover.strip(), 'Both candidates must have content')
                try:
                    stage.decode('utf-8'); handover.decode('utf-8')
                except UnicodeError as exc:
                    raise StoreError('Candidates must be UTF-8', 2) from exc
                previous = s.get('candidates')
                # Identical resave is idempotent, including preserving valid reviews.
                identical = previous and previous['objects']['stage']['sha256'] == digest(stage) and previous['objects']['handover']['sha256'] == digest(handover)
                if identical:
                    try:
                        self.verify_candidate(s)
                        return {'status': s['status'], 'batch_id': batch, 'candidates': previous, 'unchanged': True}
                    except StoreError:
                        pass
                self.revision(s, arg)
                self.write(base / 'stage.md', stage)
                self.write(base / 'handover.md', handover)
                self.invalidate(s)
                s['candidates'] = self.snapshot(s)
                s['status'] = 'reviewing'
            elif operation == 'record-review':
                review = read_json(arg['review_file'])
                self.evidence(s, review, 'review')
                role = review['role']
                other = s['reviews'].get('handover' if role == 'stage' else 'stage')
                if other:
                    require(self.check_record(other)['reviewer_id'] != review['reviewer_id'], 'Reviewers must be different', 3)
                rec = self.record_hash(review, base / ('review-' + role + '-' + uuid.uuid4().hex + '.json'))
                s['reviews'][role] = rec
                s['approval'] = None
                verdicts = [self.check_record(r)['verdict'] for r in s['reviews'].values()]
                s['status'] = 'needs_revision' if any(v != 'pass' for v in verdicts) else ('awaiting_approval' if len(verdicts) == 2 else 'reviewing')
            elif operation == 'record-approval':
                require(s['status'] == 'awaiting_approval', 'Both reviews must pass before approval', 3)
                approval = read_json(arg['approval_file'])
                self.evidence(s, approval, 'approval')
                s['approval'] = self.record_hash(approval, base / ('approval-' + uuid.uuid4().hex + '.json'))
            elif operation == 'publish':
                return self.publish(s)
            else:
                raise StoreError('Unknown operation', 2)
            self.save_state(s)
            return {'status': s['status'], 'batch_id': batch, 'candidates': s['candidates']}

    def file_hash(self, path):
        path = self.safe(path)
        return file_digest(path) if path.exists() else None

    def publish(self, s):
        snap = self.ready(s)
        batch = s['batch_id']
        base = self.batch_dir(batch)
        # Detect user edits to existing formal files before overwriting them.
        for old in s['publications'][-1:]:
            for rel, expected in old['formal_hashes'].items():
                require(self.file_hash(self.home / rel) == expected, 'Formal file changed; reconcile before publishing', 3)
        # The shared index is owned by the project, never by one chat's stale snapshot.
        if self.safe(self.project_baseline).exists():
            baseline = read_json(self.project_baseline)
            require(baseline.get('schema_version') == '0.1' and
                    self.file_hash(self.root / '项目目录.md') == baseline.get('index_sha256'),
                    'Project index changed; reconcile before publishing', 3)
        else:
            require(not (self.root / '项目目录.md').exists(), 'Project index has no committed baseline; reconcile first', 3)
        if not s['publications']:
            require(not (self.home / '对话目录.md').exists(), 'Chat index has no committed baseline; reconcile first', 3)
        else:
            require('对话目录.md' in s['publications'][-1]['formal_hashes'],
                    'Legacy chat index has no committed baseline; reconcile first', 3)
        number = self.stage_number(s)
        stage_rel = f'阶段单元/阶段-{number:03d}.md'
        stage_target = self.home / stage_rel
        assignment = s.get('stage_assignment')
        replacement = assignment and assignment['replaces_batch']
        if replacement:
            require(s['publications'][-1]['batch_id'] == replacement and
                    s['publications'][-1]['phase_number'] == number and stage_target.is_file(),
                    'Same-stage replacement baseline mismatch', 3)
        else:
            require(not stage_target.exists(), 'Phase target already exists', 3)
        if s.get('publication_plan'):
            stage = (self.home / s['publication_plan']['outputs']['stage']['path']).read_bytes()
            handover = (self.home / s['publication_plan']['outputs']['handover']['path']).read_bytes()
        else:
            # Legacy plain-text storage tests remain supported; linked documents need a reviewed plan.
            stage = (base / 'stage.md').read_bytes()
            handover = (base / 'handover.md').read_bytes()
            require(not any(re.search(rb'\]\(|(?m:^ {0,3}\[[^\]]+\]:)|\b(?:href|src)\s*=', data)
                            for data in (stage, handover)), 'Linked documents require prepare-publication before review/approval', 3)
        publication = {'batch_id': batch, 'phase_number': number, 'content_hash': snap['content_hash'],
                       'anchor': s['coverage']['end'], 'formal_hashes': {
                           stage_rel: digest(stage), '接手单元.md': digest(handover)}}
        if assignment:
            publication.update(stage_key=assignment['stage_key'], stage_start=s['coverage']['start'],
                               previous_compaction=s['coverage']['previous_compaction'],
                               replaces_batch=replacement, observed_compaction=assignment['observed_compaction'])
        final = json.loads(json.dumps(s))
        final.update(status='published', phase_number=number, published_anchor=s['coverage']['end'],
                     updated_at=now(), revision=s['revision'] + 1)
        final['publications'].append(publication)
        lines = ['# 对话目录', '', f'聊天ID：{self.thread}', '', '[当前接手](接手单元.md)', '']
        for n in sorted({p['phase_number'] for p in final['publications']}):
            lines.append(f'- [阶段{n:03d}](阶段单元/阶段-{n:03d}.md)')
        chat_index = ('\n'.join(lines) + '\n').encode('utf-8')
        publication['formal_hashes']['对话目录.md'] = digest(chat_index)
        project_lines = ['# 项目目录', '']
        threads = {self.thread}
        for p in self.root.iterdir():
            if p.name.startswith('.') or not p.is_dir() or p.name == self.thread:
                continue
            path = self.safe(p / '.状态/state.json')
            if path.exists() and read_json(path).get('publications'):
                threads.add(p.name)
        for thread in sorted(threads):
            project_lines.append(f'- [{thread}]({thread}/对话目录.md)')
        project_index = ('\n'.join(project_lines) + '\n').encode('utf-8')
        targets = [(stage_target, stage), (self.home / '接手单元.md', handover),
                   (self.home / '对话目录.md', chat_index),
                   (self.root / '项目目录.md', project_index),
                   (self.project_baseline, encode({'schema_version': '0.1', 'index_sha256': digest(project_index),
                                                  'thread_id': self.thread, 'batch_id': batch})),
                   (self.state_path, encode(final))]
        operations = []
        for index, (target, data) in enumerate(targets):
            snapshot = base / '.发布快照' / str(index)
            backup = self.home / '.恢复备份' / batch / str(index)
            before = self.file_hash(target)
            if before is not None:
                self.write(backup, target.read_bytes())
            self.write(snapshot, data)
            operations.append({'target': target.relative_to(self.root).as_posix(),
                'before': before, 'after': digest(data), 'snapshot': snapshot.relative_to(self.root).as_posix(),
                'backup': backup.relative_to(self.root).as_posix() if before is not None else None})
        journal = {'schema_version': '0.2', 'thread_id': self.thread, 'batch_id': batch,
                   'transaction_id': uuid.uuid4().hex,
                   'status': 'pending', 'created_at': now(), 'operations': operations}
        self.write(self.journal_path, encode(journal))
        return self.apply_journal(journal, 'complete')

    def recover(self, batch, mode):
        self.batch_dir(batch)
        require(self.journal_path.exists(), 'No publication journal', 3)
        journal = read_json(self.journal_path)
        self.validate_journal(journal)
        require(journal['batch_id'] == batch and journal['thread_id'] == self.thread, 'Journal identity mismatch', 3)
        if journal['status'] != 'pending':
            self.verify_terminal(journal)
            return {'status': journal['status'], 'batch_id': batch, 'already_recovered': True}
        require(mode in ('complete', 'rollback'), 'Unknown recovery mode')
        return self.apply_journal(journal, mode)

    def validate_journal(self, journal):
        try:
            self._validate_journal(journal)
        except (KeyError, TypeError, ValueError) as exc:
            raise StoreError('Malformed publication journal: ' + str(exc), 3) from exc

    def _validate_journal(self, journal):
        batch = journal['batch_id']
        self.batch_dir(batch)
        require(journal.get('schema_version') == '0.2' and
                journal.get('status') in ('pending', 'committed', 'rolled_back') and
                isinstance(journal.get('transaction_id'), str) and
                re.fullmatch(r'[0-9a-f]{32}', journal['transaction_id']) and
                isinstance(journal.get('created_at'), str) and
                journal['thread_id'] == self.thread and isinstance(journal.get('operations'), list) and
                len(journal['operations']) == 6, 'Invalid publication journal schema/status/identity', 3)
        targets = [op['target'] for op in journal['operations']]
        require(re.fullmatch(re.escape(self.thread) + r'/阶段单元/阶段-[0-9]{3,}\.md', targets[0]) and
                targets[1:] == [self.thread + '/接手单元.md', self.thread + '/对话目录.md',
                                '项目目录.md', '.状态/project-baseline.json',
                                self.thread + '/.状态/state.json'], 'Invalid journal target layout', 3)
        for index, op in enumerate(journal['operations']):
            require(op['snapshot'] == f'{self.thread}/.候选/{batch}/.发布快照/{index}' and
                    op['backup'] == (f'{self.thread}/.恢复备份/{batch}/{index}' if op['before'] is not None else None),
                    'Invalid journal source layout', 3)
            require(isinstance(op['after'], str) and re.fullmatch(r'[0-9a-f]{64}', op['after']) and
                    (op['before'] is None or isinstance(op['before'], str) and re.fullmatch(r'[0-9a-f]{64}', op['before'])),
                    'Invalid journal content hash', 3)

    def receipt_path(self, journal):
        return self.safe(self.home / '.状态/receipts' / (journal['transaction_id'] + '.json'))

    def journal_digest(self, journal):
        return digest(encode({k: journal[k] for k in ('schema_version', 'thread_id', 'batch_id',
                                                     'transaction_id', 'created_at', 'operations')}))

    def verify_terminal(self, journal):
        self.validate_journal(journal)
        path = self.receipt_path(journal)
        require(path.is_file(), 'Terminal journal has no verified completion receipt', 3)
        receipt = read_json(path)
        require(receipt.get('journal_sha256') == self.journal_digest(journal) and
                receipt.get('outcome') == journal['status'], 'Journal and completion receipt disagree', 3)
        s = self.load()
        require(s is not None, 'Terminal journal has no archive state', 3)
        publication = next((p for p in s['publications'] if p['batch_id'] == journal['batch_id']), None)
        if journal['status'] == 'committed':
            require(publication is not None and publication == receipt.get('publication') and
                    s['phase_number'] >= publication['phase_number'], 'Terminal journal contradicts publication record', 3)
            # The latest approved revision of each stage owns its formal bytes.
            # Earlier publications remain bound to their receipts and recovery backups.
            op = journal['operations'][0]
            latest = next(p for p in reversed(s['publications']) if p['phase_number'] == publication['phase_number'])
            require(self.file_hash(self.root / op['target']) == latest['formal_hashes'][op['target'].split('/', 1)[1]],
                    'Committed stage missing/changed', 3)
            if s['publications'][-1]['batch_id'] == publication['batch_id']:
                require(s['published_anchor'] == publication['anchor'], 'Committed anchor mismatch', 3)
        else:
            require(publication is None, 'Rollback journal contradicts publication record', 3)

    def apply_journal(self, journal, mode):
        self.validate_journal(journal)
        receipt_path = self.receipt_path(journal)
        if receipt_path.exists():
            # A crash after receipt but before terminal flag must finish the same direction only.
            receipt = read_json(receipt_path)
            outcome = 'committed' if mode == 'complete' else 'rolled_back'
            require(receipt.get('journal_sha256') == self.journal_digest(journal) and
                    receipt.get('outcome') == outcome, 'Transaction already completed in another direction', 3)
        # Preflight all files before making further changes, including snapshot/backup corruption.
        for op in journal['operations']:
            target = self.safe(self.root / op['target'])
            require(self.file_hash(target) in (op['before'], op['after']), 'Recovery target edited externally; needs user reconciliation', 3)
            source = self.root / (op['snapshot'] if mode == 'complete' else (op['backup'] or op['snapshot']))
            expected = op['after'] if mode == 'complete' or op['backup'] is None else op['before']
            require(self.file_hash(source) == expected, 'Recovery snapshot/backup changed; needs user reconciliation', 3)
        ops = journal['operations'] if mode == 'complete' else list(reversed(journal['operations']))
        for op in ops:
            target = self.safe(self.root / op['target'])
            expected = op['after'] if mode == 'complete' else op['before']
            if self.file_hash(target) == expected:
                continue
            if expected is None:
                self.safe(target).unlink()
            else:
                source = self.safe(self.root / (op['snapshot'] if mode == 'complete' else op['backup']))
                self.write(target, source.read_bytes())
        outcome = 'committed' if mode == 'complete' else 'rolled_back'
        s = self.load()
        receipt = {'journal_sha256': self.journal_digest(journal), 'outcome': outcome,
                   'publication': next((p for p in s['publications'] if p['batch_id'] == journal['batch_id']), None),
                   'finished_at': now()}
        self.write(receipt_path, encode(receipt))
        journal['status'] = outcome
        journal['finished_at'] = now()
        self.write(self.journal_path, encode(journal))
        return {'status': 'published' if mode == 'complete' else 'awaiting_approval',
                'batch_id': journal['batch_id'], 'recovery': mode}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise StoreError(message, 2)


def main():
    op = thread = batch = None
    try:
        parser = Parser(description=__doc__)
        parser.add_argument('--project', required=True)
        parser.add_argument('--thread', required=True)
        subs = parser.add_subparsers(dest='operation', required=True)
        subs.add_parser('status')
        begin = subs.add_parser('begin')
        begin.add_argument('--coverage-file', required=True)
        begin.add_argument('--forked-from')
        begin.add_argument('--protect-compaction', action='store_true')
        for name in ['save-material', 'save-candidate', 'prepare-publication', 'record-review', 'record-approval', 'publish', 'recover', 'cancel', 'ack-interruption']:
            p = subs.add_parser(name)
            p.add_argument('--batch', required=True)
            if name == 'save-material':
                p.add_argument('--source', required=True); p.add_argument('--purpose', required=True)
                p.add_argument('--large-approval-file')
                p.add_argument('--revision-mode', choices=['autonomous', 'user'], default='autonomous')
                p.add_argument('--instruction-file')
            elif name == 'save-candidate':
                p.add_argument('--stage', required=True); p.add_argument('--handover', required=True)
                p.add_argument('--revision-mode', choices=['autonomous', 'user'], default='autonomous')
                p.add_argument('--instruction-file')
            elif name == 'record-review':
                p.add_argument('--review-file', required=True)
            elif name == 'prepare-publication':
                p.add_argument('--revision-mode', choices=['autonomous', 'user'], default='autonomous')
                p.add_argument('--instruction-file')
            elif name == 'record-approval':
                p.add_argument('--approval-file', required=True)
            elif name == 'recover':
                p.add_argument('--mode', choices=['complete', 'rollback'], default='complete')
            elif name == 'cancel':
                p.add_argument('--reason', required=True)
            elif name == 'ack-interruption':
                p.add_argument('--instruction-file', required=True)
        values = vars(parser.parse_args())
        op = values.pop('operation'); project = values.pop('project'); thread = values.pop('thread')
        batch = values.get('batch')
        result = Store(project, thread).execute(op, **values)
        print(json.dumps({'ok': True, 'operation': op, 'thread_id': thread, 'batch_id': result.get('batch_id', batch),
                          'status': result.get('status'), 'error': None, **result}, ensure_ascii=False))
        return 0
    except (StoreError, OSError, ValueError, KeyError, TypeError) as exc:
        code = exc.code if isinstance(exc, StoreError) else 4
        print(json.dumps({'ok': False, 'operation': op, 'thread_id': thread, 'batch_id': batch,
                          'status': None, 'error': str(exc)}, ensure_ascii=False))
        return code


if __name__ == '__main__':
    sys.exit(main())
