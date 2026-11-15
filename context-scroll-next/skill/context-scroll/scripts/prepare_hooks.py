"""Create an inactive project hook preview, never install or trust it automatically."""
import argparse
import json
import os
from pathlib import Path
import shlex
import sys
import uuid
from hooks import EVENTS


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project', type=Path, required=True)
    p.add_argument('--thread', required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if not args.project.is_absolute() or not args.project.is_dir() or str(uuid.UUID(args.thread)) != args.thread:
        p.error('Existing absolute project and canonical UUID required')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    config_path = output / 'context-scroll-hook.json'
    config = {'schema_version': '0.1', 'enabled': False, 'manual_resend': True,
              'project': str(args.project.resolve()), 'threads': [args.thread], 'audit': False}
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    script = Path(__file__).with_name('hooks.py').resolve()
    argv = [sys.executable, '-B', str(script), '--config', str(config_path)]
    command = ("& " + ' '.join("'" + arg.replace("'", "''") + "'" for arg in argv)
               if os.name == 'nt' else shlex.join(argv))
    handlers = {event: [{'hooks': [{'type': 'command', 'command': command,
                 'commandWindows': command, 'timeout': 5}]}] for event in EVENTS}
    (output / 'hooks-preview.json').write_text(json.dumps({'hooks': handlers}, indent=2), encoding='utf-8')
    print(json.dumps({'status': 'inactive_preview_only', 'output': str(output),
                      'installed': False, 'trusted': False, 'enabled': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
