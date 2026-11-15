"""Install two explicit skills and merge a public Hook, with receipt-based rollback."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import tomllib
import uuid

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE/'skill/context-scroll/scripts'))
from store import StoreError, encode, lock_file, read_json, require
from hooks import EVENTS


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def files(folder):
    return {p.relative_to(folder).as_posix(): sha(p) for p in sorted(folder.rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts}


def safe_target(path):
    for ancestor in [path, *path.parents]:
        if ancestor.exists() or ancestor.is_symlink():
            st = ancestor.lstat()
            require(not ancestor.is_symlink() and not getattr(st, 'st_file_attributes', 0) & 0x400,
                    'Installation destination contains a link/reparse point', 3)


def atomic(path, data):
    safe_target(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
            temp = Path(f.name); f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if temp and temp.exists():
            temp.unlink()


def handler(router):
    argv = [sys.executable, '-B', str(router)]
    if os.name == 'nt':
        command = '& ' + ' '.join("'" + arg.replace("'", "''") + "'" for arg in argv)
    else:
        import shlex
        command = shlex.join(argv)
    return {'hooks': [{'type': 'command', 'command': command, 'commandWindows': command, 'timeout': 8}]}


def install(home, output):
    home = Path(home).absolute(); safe_target(home)
    home.mkdir(parents=True, exist_ok=True)
    output = Path(output).absolute(); output.mkdir(parents=True, exist_ok=False)
    registration = home/'hooks.json'
    with lock_file(home/'.context-scroll-install.lock'):
        targets = {name: home/'skills'/name for name in ('context-scroll', 'context-monitor')}
        require(all(not p.exists() for p in targets.values()), 'Target skill already exists; preserve it and use another home or explicitly reviewed update', 3)
        before = registration.read_bytes() if registration.exists() else None
        value = read_json(registration) if before is not None else {}
        groups = value.setdefault('hooks', {})
        require(isinstance(groups, dict), 'Existing hooks must be an object', 2)
        entry = handler(targets['context-scroll']/'scripts/router.py')
        for event in EVENTS:
            require(isinstance(groups.get(event, []), list), 'Existing Hook event must be a list', 2)
            groups.setdefault(event, []).append(entry)
        # Write a recovery journal before any destination is created.
        receipt = {'version': 'phase-7-candidate-20261115-001', 'home': str(home),
                   'skills': {}, 'registration': str(registration), 'status': 'installing',
                   'before_registration': before.decode('utf-8-sig') if before is not None else None,
                   'before_registration_hex': before.hex() if before is not None else None,
                   'entry': entry, 'events': EVENTS}
        atomic(output/'installation.json', encode(receipt))
        for name, target in targets.items():
            safe_target(target); target.parent.mkdir(parents=True, exist_ok=True)
            stage = target.parent/('.'+name+'-'+uuid.uuid4().hex)
            shutil.copytree(SOURCE/'skill'/name, stage, ignore=shutil.ignore_patterns('__pycache__'))
            receipt['skills'][name] = {'path': str(target), 'hashes': files(stage), 'staging': str(stage)}
            atomic(output/'installation.json', encode(receipt))
            os.replace(stage, target)
        require((registration.read_bytes() if registration.exists() else None) == before,
                'Hook registration changed during install; retain receipt for reconciliation', 3)
        receipt['after_registration_sha256'] = hashlib.sha256(encode(value)).hexdigest()
        atomic(output/'installation.json', encode(receipt))
        atomic(registration, encode(value))
        receipt.update(status='installed_pending_hook_review', hook_trust='user_review_required')
        atomic(output/'installation.json', encode(receipt))
        return receipt


def references_package(value, targets):
    """Conservatively identify retained references, including changed command fields."""
    if isinstance(value, dict):
        return any(references_package(item, targets) for item in value.values())
    if isinstance(value, list):
        return any(references_package(item, targets) for item in value)
    if not isinstance(value, str):
        return False
    text = value.replace('\\', '/').casefold()
    while '//' in text:
        text = text.replace('//', '/')
    for name, target in targets.items():
        absolute = str(target).replace('\\', '/').casefold()
        if absolute in text or 'skills/' + name + '/' in text or name + '/scripts/' in text:
            return True
    return False


def planned_registration(registration, receipt, targets):
    """Compute and validate the complete result before changing any file."""
    if sha(registration) == receipt.get('after_registration_sha256'):
        raw = receipt['before_registration_hex']
        after = bytes.fromhex(raw) if raw is not None else None
        retained = json.loads(after.decode('utf-8-sig')) if after is not None else {}
    else:
        retained = read_json(registration) if registration.exists() else {}
        groups = retained.get('hooks', {})
        require(isinstance(groups, dict), 'Hook registration shape changed; retain installation for reconciliation', 3)
        for event in receipt['events']:
            entries = groups.get(event, [])
            require(isinstance(entries, list), 'Hook event shape changed; retain installation for reconciliation', 3)
            require(entries.count(receipt['entry']) <= 1,
                    'Duplicate package Hook ownership is ambiguous; retain installation for reconciliation', 3)
            if receipt['entry'] in entries:
                entries.remove(receipt['entry'])
        after = encode(retained) if registration.exists() else None
    require(not references_package(retained, targets),
            'Retained Hook still references package files (modified or unowned handler); '
            'restore the registered entry or explicitly resolve the dependency before uninstall', 3)
    # Inline user hooks are a second known registration source. Preserve them and
    # refuse removal of their dependencies rather than editing another config.
    config = registration.with_name('config.toml')
    safe_target(config)
    if config.exists():
        with config.open('rb') as f:
            inline = tomllib.load(f).get('hooks', {})
        require(not references_package(inline, targets),
                'Inline user Hook still references package files; resolve it before uninstall', 3)
    return after


def uninstall(receipt_path):
    receipt_path = Path(receipt_path).resolve(); receipt = read_json(receipt_path)
    home = Path(receipt['home']); safe_target(home)
    registration = home/'hooks.json'
    require(Path(receipt['registration']) == registration, 'Receipt registration identity mismatch', 3)
    with lock_file(home/'.context-scroll-install.lock'):
        safe_target(registration)
        targets = {}
        moves = []
        for name, item in receipt['skills'].items():
            require(name in ('context-scroll', 'context-monitor') and Path(item['path']) == home/'skills'/name,
                    'Receipt skill destination mismatch', 3)
            p = Path(item['path']); safe_target(p)
            targets[name] = p
            retired = receipt_path.parent/('retired-'+name)
            safe_target(retired)
            if p.exists():
                require(files(p) == item['hashes'], 'Installed skill changed; retain it for reconciliation', 3)
                require(not retired.exists(), 'Retired backup already exists', 3)
                moves.append((p, retired))
            elif retired.exists():
                require(files(retired) == item['hashes'], 'Retired skill changed; retain recovery evidence', 3)
        before = registration.read_bytes() if registration.exists() else None
        after = planned_registration(registration, receipt, targets)
        if receipt.get('status') == 'uninstalled':
            require(not moves, 'Uninstalled receipt contradicts existing package files', 3)
            return receipt
        # This journal is written only AFTER the conflict-free preflight. On an
        # I/O failure it retains exact registration bytes and planned move targets.
        progress_path = receipt_path.parent/'uninstall-progress.json'
        progress = {'status': 'uninstalling', 'receipt': str(receipt_path),
                    'registration_before_hex': before.hex() if before is not None else None,
                    'registration_after_hex': after.hex() if after is not None else None,
                    'moves': [{'source': str(p), 'retired': str(r)} for p, r in moves]}
        if progress_path.exists():
            old_progress = read_json(progress_path)
            require(old_progress.get('receipt') == str(receipt_path), 'Uninstall progress identity conflict', 3)
            progress['previous_progress'] = old_progress
        atomic(progress_path, encode(progress))
        require((registration.read_bytes() if registration.exists() else None) == before,
                'Hook registration changed after uninstall preflight; retry after reconciliation', 3)
        if after != before:
            if after is None:
                registration.unlink()
            else:
                atomic(registration, after)
        for p, retired in moves:
            require(p.absolute() == (home/'skills'/p.name).absolute(), 'Unexpected move target', 3)
            shutil.move(str(p), str(retired))
        receipt['status'] = 'uninstalled'
        atomic(receipt_path, encode(receipt))
        progress['status'] = 'uninstalled'
        atomic(progress_path, encode(progress))
        return receipt


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action', required=True)
    i = sub.add_parser('install'); i.add_argument('--home', required=True); i.add_argument('--output', required=True)
    u = sub.add_parser('uninstall'); u.add_argument('--receipt', required=True)
    args = p.parse_args()
    try:
        value = install(args.home, args.output) if args.action == 'install' else uninstall(args.receipt)
        print(json.dumps({'ok': True, **value}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError, StoreError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        return exc.code if isinstance(exc, StoreError) else 4


if __name__ == '__main__':
    sys.exit(main())
