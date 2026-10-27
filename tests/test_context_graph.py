import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("context_graph", Path(__file__).parents[1] / "scripts" / "context_graph.py")
g = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(g)


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sources = self.root / "sources"
        self.sources.mkdir()
        self.project = self.sources / "项目"
        self.project.mkdir()
        self.vault = self.root / "vault"
        (self.vault / ".obsidian").mkdir(parents=True)
        self.config = self.root / "config.json"
        g.write_json(self.config, {"source_roots": [str(self.sources)], "vault": str(self.vault), "output_folder": "上下文迁移"})

    def tearDown(self):
        self.temp.cleanup()

    def unit(self, uid, thread="A", rels=(), project=None, body="完整正文：需要保留的事实。"):
        project = project or self.project
        path = project / g.MIGRATION / f"上下文单元-{uid}-主题.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        text = f"""# 上下文单元：主题 {uid}

## 单元元数据
- 上下文单元协议版本：`2.0`
- 单元标识：`{uid}`
- 单元绝对路径：`{path}`
- 项目根目录：`{project}`
- 对话标识：`{thread}`
- 本次蒸馏结束锚点：消息-{uid}
- 主题标签：Java、测试

## 单元链接
| 类型 | 目标单元标识 | 目标绝对路径 | 关系说明 |
|---|---|---|---|
"""
        for kind, target in rels:
            target_id = g.parse_unit(target)["id"] if Path(target).exists() else "missing"
            text += f"| `{kind}` | `{target_id}` | `{target}` | 明确记录 |​\n".replace("\u200b", "")
        text += "\n## 核心摘要\n" + body + "\n"
        path.write_text(text, encoding="utf-8")
        index = project / g.MIGRATION / "上下文单元索引.md"
        if not index.exists():
            index.write_text("# 索引\n## 单元目录\n| 单元标识 | 绝对路径 |\n", encoding="utf-8")
        with index.open("a", encoding="utf-8") as stream:
            stream.write(f"| `{uid}` | `{path}` |\n")
        return path

    def manifest(self):
        return g.read_json(self.vault / "上下文迁移" / ".ccm" / "manifest.json")

    def test_pending_partial_reads_finalize_and_continue(self):
        a = self.unit("a1")
        g.record_read(self.project, "B", a, "L1-L5", "e1")
        g.record_read(self.project, "B", a, "L1-L5", "e1")
        state = g.record_read(self.project, "B", a, "章节二", "e2")
        self.assertEqual(len(state["reads"]), 2)
        g.sync(self.config)
        self.assertEqual(len(self.manifest()["units"]), 1)
        self.assertEqual(self.manifest()["edges"], [])
        b = self.unit("b1", "B", [("继承自", a)])
        state = g.finalize(self.project, "B", b)
        self.assertEqual({r["distilled_into"] for r in state["reads"]}, {"b1"})
        self.assertEqual(g.finalize(self.project, "B", b), state)
        b2 = self.unit("b2", "B", [("继续自", b)])
        g.finalize(self.project, "B", b2)
        g.sync(self.config)
        ids = {k: v["id"] for k, v in self.manifest()["units"].items()}
        self.assertEqual({(ids[e["from"]], ids[e["to"]]) for e in self.manifest()["edges"]}, {("a1", "b1"), ("b1", "b2")})

    def test_finalize_rejects_missing_source_and_index(self):
        a = self.unit("a")
        g.record_read(self.project, "B", a, "全文", "event")
        b = self.unit("b", "B")
        with self.assertRaises(ValueError):
            g.finalize(self.project, "B", b)
        self.assertIsNone(g.thread_state(self.project, "B")["reads"][0]["distilled_into"])
        b = self.unit("b", "B", [("继承自", a)])
        (self.project / g.MIGRATION / "上下文单元索引.md").write_text("b only mentioned in prose", encoding="utf-8")
        with self.assertRaises(ValueError):
            g.finalize(self.project, "B", b)

    def test_only_lineage_edges_full_content_and_source_unchanged(self):
        a = self.unit("a", body="原文 [[无关笔记]] 和 [本地](other.md)。\n第二段保留。")
        b = self.unit("b", rels=[("继承自", a)])
        c = self.unit("c", rels=[("纠正了", a)])
        hashes = {p: g.sha(p.read_bytes()) for p in (a, b, c)}
        result = g.sync(self.config, configure_graph=True)
        self.assertEqual(result["units"], 3)
        self.assertEqual(result["edges"], 1)
        texts = [(self.vault / u["note"]).read_text(encoding="utf-8") for u in self.manifest()["units"].values()]
        self.assertEqual(sum(len(re.findall(r"(?<!\\)\[\[", t)) for t in texts), 1)
        self.assertTrue(any("第二段保留。" in t for t in texts))
        self.assertTrue(any("obsidian://open?" in t and "纠正了" in t for t in texts))
        self.assertEqual(hashes, {p: g.sha(p.read_bytes()) for p in hashes})
        settings = g.read_json(self.vault / ".obsidian" / "graph.json")
        self.assertTrue(settings["showArrow"])
        self.assertFalse(settings["showTags"])

    def test_repeat_sync_does_not_rewrite_notes(self):
        self.unit("a")
        g.sync(self.config)
        info = next(iter(self.manifest()["units"].values()))
        note = self.vault / info["note"]
        before = note.stat().st_mtime_ns
        self.assertEqual(g.sync(self.config)["changed"], 0)
        self.assertEqual(note.stat().st_mtime_ns, before)

    def test_retry_after_partial_write(self):
        self.unit("a")
        self.unit("b")
        original = g.atomic_text
        calls = []
        def fail_second_note(path, text):
            if str(path).endswith(".md"):
                calls.append(path)
                if len(calls) == 2:
                    raise OSError("simulated disk failure")
            original(path, text)
        with patch.object(g, "atomic_text", side_effect=fail_second_note):
            with self.assertRaises(OSError):
                g.sync(self.config)
        self.assertEqual(g.sync(self.config)["units"], 2)
        self.assertEqual(len(self.manifest()["units"]), 2)

    def test_mirror_edit_protected(self):
        self.unit("a")
        g.sync(self.config)
        note = self.vault / next(iter(self.manifest()["units"].values()))["note"]
        note.write_text("用户手写内容", encoding="utf-8")
        with self.assertRaises(ValueError):
            g.sync(self.config)
        self.assertEqual(note.read_text(encoding="utf-8"), "用户手写内容")

    def test_dry_run_and_missing_source_preserve_output(self):
        a = self.unit("a")
        g.sync(self.config, dry_run=True)
        self.assertFalse((self.vault / "上下文迁移").exists())
        g.sync(self.config)
        before = self.manifest()
        a.unlink()
        with self.assertRaises(ValueError):
            g.sync(self.config)
        self.assertEqual(self.manifest(), before)

    def test_worktree_copy_is_one_node(self):
        a = self.unit("a")
        copy = self.project / ".worktrees" / "work" / g.MIGRATION / a.name
        copy.parent.mkdir(parents=True)
        copy.write_bytes(a.read_bytes())
        units, _, report = g.collect([self.sources])
        self.assertEqual(len(units), 1)
        self.assertEqual(next(iter(units.values()))["path"], str(a))
        self.assertTrue(any(x["kind"] == "duplicate_copy" for x in report["warnings"]))

    def test_same_id_different_content_not_silently_selected(self):
        a = self.unit("a")
        copy = a.with_name("上下文单元-a-冲突.md")
        copy.write_text(a.read_text(encoding="utf-8") + "different", encoding="utf-8")
        units, _, report = g.collect([self.sources])
        self.assertEqual(len(units), 0)
        self.assertEqual(report["warnings"][0]["kind"], "identity_conflict")
        self.unit("valid")
        with self.assertRaises(ValueError):
            g.sync(self.config)
        self.assertFalse((self.vault / "上下文迁移").exists())

    def test_legacy_thread_without_ledger_requires_continuation(self):
        old = self.unit("old", "legacy-thread")
        new = self.unit("new", "legacy-thread")
        with self.assertRaises(ValueError):
            g.finalize(self.project, "legacy-thread", new)
        new = self.unit("new", "legacy-thread", [("继续自", old)])
        state = g.finalize(self.project, "legacy-thread", new)
        self.assertEqual(state["last_distill"]["id"], "new")

    def test_missing_link_no_placeholder_node(self):
        self.unit("a", rels=[("继承自", self.project / g.MIGRATION / "上下文单元-missing.md")])
        result = g.sync(self.config)
        self.assertEqual(result["units"], 1)
        self.assertEqual(result["edges"], 0)
        self.assertEqual(result["warnings"][0]["kind"], "unresolved_target")

    def test_output_path_escape_rejected(self):
        config = g.read_json(self.config)
        config["output_folder"] = "../outside"
        g.write_json(self.config, config)
        with self.assertRaises(ValueError):
            g.sync(self.config)

    def test_graph_settings_wait_for_active_app(self):
        self.unit("a")
        config_file = self.vault / ".obsidian" / "graph.json"
        g.write_json(config_file, {"colorGroups": [], "showArrow": False})
        before = config_file.read_bytes()
        with patch.object(g, "vault_active", return_value=True):
            report = g.sync(self.config, configure_graph=True)
        self.assertEqual(report["graph_configuration"]["status"], "pending_app_close")
        self.assertEqual(config_file.read_bytes(), before)
        self.assertTrue((self.vault / "上下文迁移" / ".ccm" / "graph.pending.json").exists())
        with patch.object(g, "vault_active", return_value=False):
            report = g.sync(self.config, configure_graph=True)
        self.assertEqual(report["graph_configuration"]["status"], "applied")
        self.assertTrue(g.read_json(config_file)["colorGroups"])
        self.assertTrue(g.read_json(config_file)["showArrow"])
        self.assertFalse((self.vault / "上下文迁移" / ".ccm" / "graph.pending.json").exists())

    def test_markdown_link_destinations_titles_and_code(self):
        source = self.project / "source.md"
        uri = (self.project / "note (draft).md").as_uri()
        ordinary = (self.project / "note.md").as_uri()
        self.assertEqual(g.externalize_body('[原文件](<note (draft).md>)', source), f'[原文件](<{uri}>)\n')
        self.assertEqual(g.externalize_body('[原文件](note(draft).md)', source),
                         f'[原文件]({(self.project / "note(draft).md").as_uri()})\n')
        self.assertEqual(g.externalize_body('[原文件](note.md "原文标题")', source), f'[原文件]({ordinary} "原文标题")\n')
        self.assertEqual(g.externalize_body('[file][ref]\n[ref]: note.md "原文标题"', source),
                         f'[file][ref]\n[ref]: {ordinary} "原文标题"\n')
        text = '`[示例](relative.md)` and ``[[示例]] `code` ``\n```md\n[例](x.md)\n```\n'
        self.assertEqual(g.externalize_body(text, source), text)
        self.assertIn(ordinary, g.externalize_body('![](note.md)', source))

    def test_nested_lists_and_indented_code_are_distinct(self):
        source = self.project / "source.md"
        text = '- 父列表\n    - [[主题 B]]\n    - [本地](note.md)\n'
        rendered = g.externalize_body(text, source)
        self.assertIn(r'\[\[主题 B\]\]', rendered)
        self.assertIn((self.project / "note.md").as_uri(), rendered)
        self.assertIn(r'\[\[Ghost\]\]', g.externalize_body('普通段落\n    [[Ghost]]', source))
        code = '\n    [[code]] [示例](note.md)\n'
        self.assertEqual(g.externalize_body(code, source), code)
        nested_code = '- 父列表\n\n      [[code]] [示例](note.md)\n'
        self.assertEqual(g.externalize_body(nested_code, source), nested_code)


if __name__ == "__main__":
    unittest.main(verbosity=2)
