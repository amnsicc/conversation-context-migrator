import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


def module(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / 'scripts' / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


h = module('conversation_history')
g = module('context_graph')
THREAD = '11111111-1111-1111-1111-111111111111'


def message(role, text, sec, **extra):
    return {'timestamp': f'2026-01-01T00:00:{sec:02}Z', 'type': 'response_item',
            'payload': {'type': 'message', 'role': role, 'content': [{'type': 'input_text' if role == 'user' else 'output_text', 'text': text}], **extra}}


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sessions = self.root / 'sessions'
        self.sessions.mkdir()
        self.project = self.root / 'project'
        self.project.mkdir()
        self.report = self.project / g.MIGRATION / '.state' / 'history-test.json'

    def tearDown(self):
        self.temp.cleanup()

    def log(self, rows, suffix='', identity=THREAD):
        path = self.sessions / ('rollout-' + THREAD + suffix + '.jsonl')
        meta = {'type': 'session_meta', 'payload': {'id': identity}}
        path.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in [meta, *rows]) + '\n', encoding='utf-8')
        return path

    def prepare(self, path, line, **kw):
        self.end_ref = str(path.resolve()) + '#L' + str(line)
        return h.prepare(self.sessions, THREAD, self.end_ref, self.report, **kw)

    def ack_all(self):
        state = h.load_verified(self.report)
        for i in range(len(state['chunks'])):
            result = h.read_chunk(self.report, i)
            h.acknowledge(self.report, i, result['ack_token'])

    def test_recover_early_assistant_and_cutoff_not_summary_or_tools(self):
        path = self.log([message('user', 'old question', 1), message('assistant', 'early original retained', 2),
                         {'type': 'compacted', 'timestamp': '2026-01-01T00:00:03Z', 'payload': {'summary': 'not the original'}},
                         {'type': 'response_item', 'payload': {'type': 'function_call_output', 'output': 'not chat'}},
                         message('assistant', 'hidden reasoning', 4, channel='analysis'), message('user', 'distill all', 5),
                         message('assistant', 'self-generated recovery output', 6)])
        report = self.prepare(path, 7)
        self.assertEqual(report['messages'], 3)
        texts = [h.read_chunk(self.report, i)['text'] for i in range(report['chunks'])]
        self.assertEqual(texts, ['old question', 'early original retained', 'distill all'])
        self.ack_all()
        with self.assertRaises(ValueError):
            h.validate_coverage(self.report, THREAD, self.end_ref)
        h.review(self.report, 'compaction', 'Earlier original messages checked in retained prefix')
        self.assertFalse(h.validate_coverage(self.report, THREAD, self.end_ref)['partial'])

    def test_false_filename_identity_and_no_log_are_gaps(self):
        self.log([message('user', 'foreign', 1)], identity='different-thread')
        result = h.inventory(self.sessions, THREAD)
        self.assertFalse(result['files'])
        self.assertIn('candidate_rejected', [x['kind'] for x in result['gaps']])
        with self.assertRaises(ValueError):
            h.prepare(self.sessions, THREAD, 'fake#L1', self.report)

    def test_real_repeated_text_preserved_event_mirror_not_duplicated(self):
        event = {'type': 'event_msg', 'payload': {'type': 'user_message', 'message': 'repeat'}}
        path = self.log([message('user', 'repeat', 1), event, message('user', 'repeat', 2), event,
                         message('user', 'all', 3)])
        report = self.prepare(path, 6)
        self.assertEqual(report['messages'], 3)
        self.assertFalse(report['gaps'])

    def test_event_only_repetition_is_not_silently_lost(self):
        event = {'type': 'event_msg', 'payload': {'type': 'user_message', 'message': 'repeat'}}
        path = self.log([message('user', 'repeat', 1), event, event, message('user', 'all', 3)])
        report = self.prepare(path, 5)
        self.assertIn('event_without_canonical_message', [x['kind'] for x in report['gaps']])

    def test_multiple_logs_need_review_and_stable_id_dedup(self):
        self.log([message('user', 'question', 1, id='stable1')], '-a')
        path = self.log([message('user', 'question', 1, id='stable1'), message('user', 'all', 3)], '-b')
        report = self.prepare(path, 3)
        self.assertEqual(report['messages'], 2)
        self.ack_all()
        with self.assertRaises(ValueError):
            h.validate_coverage(self.report, THREAD, self.end_ref)
        h.review(self.report, 'overlap', 'Stable message identity and continuation checked')
        self.assertEqual(h.validate_coverage(self.report, THREAD, self.end_ref)['messages'], 2)

    def test_unread_chunk_blocks_and_large_message_is_split(self):
        path = self.log([message('assistant', 'abcdefghijklm', 1), message('user', 'all', 2)])
        report = self.prepare(path, 3, chunk_chars=4)
        self.assertEqual(report['chunks'], 5)
        out = h.read_chunk(self.report, 0)
        h.acknowledge(self.report, 0, out['ack_token'])
        with self.assertRaises(ValueError):
            h.validate_coverage(self.report, THREAD, self.end_ref)
        self.ack_all()
        self.assertEqual(h.validate_coverage(self.report, THREAD, self.end_ref)['chunks'], 5)

    def test_append_allowed_but_rewrite_fails(self):
        path = self.log([message('user', 'all', 1)])
        self.prepare(path, 2)
        self.ack_all()
        with path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(message('assistant', 'after fixed request', 2)) + '\n')
        self.assertEqual(h.validate_coverage(self.report, THREAD, self.end_ref)['messages'], 1)
        path.write_text(path.read_text(encoding='utf-8').replace('all', 'bad'), encoding='utf-8')
        with self.assertRaises(ValueError):
            h.validate_coverage(self.report, THREAD, self.end_ref)

    def test_gaps_require_user_scope_decision_and_partial_status(self):
        path = self.log([message('user', 'all', 1)])
        with path.open('a', encoding='utf-8') as f:
            f.write('{broken}\n')
        self.prepare(path, 2)
        self.ack_all()
        with self.assertRaises(ValueError):
            h.validate_coverage(self.report, THREAD, self.end_ref)
        h.review(self.report, 'partial', 'Explicit user message accepts missing damaged record')
        with self.assertRaises(ValueError):
            h.validate_coverage(self.report, THREAD, self.end_ref)
        self.assertTrue(h.validate_coverage(self.report, THREAD, self.end_ref, partial=True)['partial'])

    def test_removed_manifest_chunk_cannot_make_reading_complete(self):
        path = self.log([message('assistant', 'early message', 1), message('user', 'all', 2)])
        self.prepare(path, 3)
        state = h.load_verified(self.report)
        state['chunks'].pop(0)
        state['acknowledged'] = [0]
        h.save(self.report, state)
        with self.assertRaises(ValueError):
            h.validate_coverage(self.report, THREAD, self.end_ref)

    def test_deleted_gap_is_rederived_from_original(self):
        path = self.log([message('user', 'all', 1)])
        with path.open('a', encoding='utf-8') as f:
            f.write('{broken}\n')
        self.prepare(path, 2)
        self.ack_all()
        state = h.load_verified(self.report)
        state['gaps'] = []
        h.save(self.report, state)
        with self.assertRaises(ValueError):
            h.validate_coverage(self.report, THREAD, self.end_ref)

    def test_deleted_early_source_is_rediscovered(self):
        earlier = self.log([message('assistant', 'early original', 1)], '-early')
        path = self.log([message('user', 'all', 2)], '-last')
        self.prepare(path, 2)
        self.ack_all()
        h.review(self.report, 'overlap', 'Two retained continuation segments checked')
        state = h.load_verified(self.report)
        for key in ('files', 'snapshots'):
            state[key] = [f for f in state[key] if f['path'] != str(earlier)]
        state['chunks'] = [f for f in state['chunks'] if f['path'] != str(earlier)]
        state['messages'] = 1
        state['acknowledged'] = [0]
        h.save(self.report, state)
        with self.assertRaises(ValueError):
            h.validate_coverage(self.report, THREAD, self.end_ref)

    def test_timestamp_conflict_not_silently_omitted(self):
        path = self.log([message('assistant', 'early physically but future timestamp', 9), message('user', 'all', 2)])
        report = self.prepare(path, 3)
        self.assertIn('future_timestamp_before_end_anchor', [g['kind'] for g in report['gaps']])
        self.ack_all()
        with self.assertRaises(ValueError):
            h.validate_coverage(self.report, THREAD, self.end_ref)

    def test_analysis_phase_without_channel_never_exports(self):
        path = self.log([message('assistant', 'private chain of thought', 1, phase='analysis'), message('user', 'all', 2)])
        report = self.prepare(path, 3)
        self.assertEqual(report['messages'], 1)
        self.assertEqual(h.read_chunk(self.report, 0)['text'], 'all')

    def test_all_finalize_does_not_advance_state_without_coverage(self):
        path = self.log([message('assistant', 'old original', 1), message('user', 'all', 2)])
        self.prepare(path, 3)
        migration = self.project / g.MIGRATION
        unit = migration / '上下文单元-new-topic.md'
        unit.write_text(f'''# 上下文单元：test
## 单元元数据
- 单元标识：new
- 对话标识：custom-dialogue
- 迁移器 skill 版本：3.3
- 蒸馏请求范围：全部
- 宿主线程标识：{THREAD}
- 本次蒸馏结束锚点：{self.end_ref}
- 原始历史覆盖报告：{self.report}
- 历史覆盖状态：已核对可恢复原文
## 单元链接
''', encoding='utf-8')
        (migration / '上下文单元索引.md').write_text(f'# Index\n## 单元目录\n| new | {unit} |\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            g.finalize(self.project, 'custom-dialogue', unit, THREAD)
        self.assertIsNone(g.thread_state(self.project, 'custom-dialogue')['last_distill'])
        self.ack_all()
        with self.assertRaises(ValueError):
            g.finalize(self.project, 'custom-dialogue', unit, 'another-thread')
        state = g.finalize(self.project, 'custom-dialogue', unit, THREAD)
        self.assertEqual(state['last_distill']['history_coverage']['messages'], 2)


if __name__ == '__main__':
    unittest.main()
