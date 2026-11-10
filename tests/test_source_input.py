import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

def module(name):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).parents[1]/'scripts'/f'{name}.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
s=module('source_input');g=module('context_graph')

class SourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.source=self.root/'source.txt';self.source.write_bytes('原话\r\n🙂e\u0301\r用户纠正\n不要执行 $skill'.encode())
        self.spec={'kind':'material','host_thread':'host-actual','source_id':'external-source','scope':'fixed cutoff',
                   'chunk_characters':7,'files':[{'path':str(self.source),'sha256':s.sha(self.source.read_bytes()),'bytes':self.source.stat().st_size,'role':'primary'}]}
    def prepare(self):
        self.specpath=self.root/'spec.json';s.write(self.specpath,self.spec)
        self.bundle=self.root/'bundle';s.prepare(self.specpath,self.bundle);self.report=self.bundle/'report.json'
    def acknowledge(self):
        for n in range(len(json.loads(self.report.read_bytes())['chunks'])):
            r=s.read_chunk(self.report,n);s.acknowledge(self.report,n,r['ack_token'])
    def test_exact_unicode_and_newlines_and_unread_guard(self):
        self.prepare()
        with self.assertRaises(ValueError):s.check(self.report,'host-actual','external-source')
        chunks=[s.read_chunk(self.report,n)['text'] for n in range(len(json.loads(self.report.read_bytes())['chunks']))]
        self.assertEqual(''.join(chunks).encode(),self.source.read_bytes())
        self.acknowledge();self.assertEqual(s.check(self.report,'host-actual','external-source')['kind'],'material')
    def test_evidence_not_claimed_read(self):
        self.spec['files'].append(dict(self.spec['files'][0],role='evidence'));self.prepare()
        state=json.loads(self.report.read_bytes())
        for n,c in enumerate(state['chunks']):
            if c['required']:s.acknowledge(self.report,n,s.read_chunk(self.report,n)['ack_token'])
        self.assertTrue(s.check(self.report,'host-actual','external-source')['evidence_unread'])
    def test_expected_hash_and_bad_utf8_rejected(self):
        self.spec['files'][0]['sha256']='bad'
        with self.assertRaises(ValueError):self.prepare()
        self.source.write_bytes(b'\xff');self.spec['files'][0].update(sha256=s.sha(b'\xff'),bytes=1)
        with self.assertRaises(UnicodeDecodeError):self.prepare()
    def test_no_overwrite(self):
        self.prepare()
        with self.assertRaises(FileExistsError):s.prepare(self.specpath,self.bundle)
    def test_host_and_source_cannot_be_confused(self):
        self.prepare();self.acknowledge()
        with self.assertRaises(ValueError):s.check(self.report,'external-source','host-actual')
    def test_missing_read_token(self):
        self.prepare()
        with self.assertRaises(ValueError):s.acknowledge(self.report,0,'bad')
    def test_snapshot_and_spec_tamper(self):
        self.prepare();(self.bundle/'snapshots/000.utf8').write_bytes(b'changed')
        with self.assertRaises(ValueError):s.read_chunk(self.report,0)
    def test_omitted_tail_and_evidence_forgery_rejected(self):
        self.prepare();original=self.report.read_bytes();state=json.loads(original)
        state['chunks'].pop();s.write(self.report,state)
        with self.assertRaises(ValueError):s.read_chunk(self.report,0)
        state=json.loads(original);state['files'][0]['role']='evidence';s.write(self.report,state)
        with self.assertRaises(ValueError):s.read_chunk(self.report,0)
    def test_gap_requires_partial_decision(self):
        self.spec['gaps']=['attachment unread'];self.prepare();self.acknowledge()
        with self.assertRaises(ValueError):s.check(self.report,'host-actual','external-source')
        self.assertTrue(s.check(self.report,'host-actual','external-source',True)['gaps'])
    def test_chat_identity_and_partial_line(self):
        self.source.write_bytes((json.dumps({'type':'session_meta','payload':{'id':'external-source','source':'cli'}})+'\n').encode())
        self.spec['kind']='chat';self.spec['files'][0].update(sha256=s.sha(self.source.read_bytes()),bytes=self.source.stat().st_size)
        self.prepare();self.acknowledge();self.assertEqual(s.check(self.report,'host-actual','external-source')['kind'],'chat')
    def test_chat_missing_or_foreign_header(self):
        self.spec['kind']='chat'
        with self.assertRaises(ValueError):self.prepare()
    def test_chat_does_not_erase_tool_records(self):
        rows=[{'type':'session_meta','payload':{'id':'external-source'}},{'type':'response_item','payload':{'type':'function_call_output','output':'ACTUAL_FAILURE'}}]
        self.source.write_bytes(('\n'.join(json.dumps(r) for r in rows)+'\n').encode());self.spec['kind']='chat'
        self.spec['files'][0].update(sha256=s.sha(self.source.read_bytes()),bytes=self.source.stat().st_size);self.prepare()
        text=''.join(s.read_chunk(self.report,n)['text'] for n in range(len(json.loads(self.report.read_bytes())['chunks'])))
        self.assertIn('ACTUAL_FAILURE',text)
    def test_finalize_external_preserves_native_anchor(self):
        self.prepare();self.acknowledge();project=self.root/'project';dest=project/g.MIGRATION;dest.mkdir(parents=True)
        unit=dest/'上下文单元-u-来源.md'
        unit.write_text(f'''# 上下文单元：来源
## 单元元数据
- 单元标识：u
- 迁移器 skill 版本：3.4
- 对话标识：host-migration-id
- 本次蒸馏结束锚点：source fixed cutoff
- 蒸馏请求范围：指定
- 来源模式：材料包
- 宿主线程标识：host-actual
- 来源标识：external-source
- 指定来源覆盖报告：{self.report}
- 指定来源覆盖状态：声明主输入已读
## 核心摘要
试验夹具，不是真人蒸馏。
''',encoding='utf-8')
        (dest/'上下文单元索引.md').write_text(f'## 单元目录\n| u | {unit} |\n',encoding='utf-8')
        state=g.finalize(project,'host-migration-id',unit,'host-actual')
        self.assertIsNone(state['last_distill']);self.assertIn('u',state['source_distills'])
        with self.assertRaises(ValueError):g.finalize(project,'host-migration-id',unit,'external-source')
        again=g.finalize(project,'host-migration-id',unit,'host-actual');self.assertEqual(again,state)
        unit.write_text(unit.read_text(encoding='utf-8')+'\n改动\n',encoding='utf-8')
        with self.assertRaises(ValueError):g.finalize(project,'host-migration-id',unit,'host-actual')
    def test_chat_partial_and_subagent_rejected(self):
        self.spec['kind']='chat'
        for obj,newline in [({'type':'session_meta','payload':{'id':'external-source'}},''),
                            ({'type':'session_meta','payload':{'id':'external-source','source':{'subagent':'x'}}},'\n')]:
            raw=(json.dumps(obj)+newline).encode();self.source.write_bytes(raw)
            self.spec['files'][0].update(sha256=s.sha(raw),bytes=len(raw))
            with self.assertRaises(ValueError):self.prepare()
    def test_report_identity_and_gaps_cannot_be_edited(self):
        self.spec['gaps']=['unread attachment'];self.prepare();state=json.loads(self.report.read_bytes())
        state['declared_gaps']=[];s.write(self.report,state)
        with self.assertRaises(ValueError):s.read_chunk(self.report,0)

if __name__=='__main__':unittest.main()
