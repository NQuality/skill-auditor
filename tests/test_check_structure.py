import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'check_structure.py'
spec = importlib.util.spec_from_file_location('check_structure', SCRIPT)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='skill-auditor-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def write(self, name, content=''):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        return path

    def skill(self, body='', description='Use for editing short messages.'):
        return self.write('SKILL.md', '---\nname: demo\ndescription: ' + description + '\n---\n' + body)

    def test_folded_and_literal_yaml_descriptions(self):
        for marker in ('>', '|'):
            with self.subTest(marker=marker):
                self.skill(description=marker + '\n  Use for editing\n  short messages.')
                result = checker.snapshot(self.root)
                self.assertIn('Use for editing', result['skill_md']['frontmatter']['description'])
                self.assertIn('short messages.', result['skill_md']['frontmatter']['description'])
                self.assertEqual([], result['warnings'])

    def test_empty_and_nonstring_metadata_is_reported(self):
        for value in ('""', "''", 'null', 'false', '123', '"  "'):
            with self.subTest(value=value):
                self.skill(description=value)
                result = checker.snapshot(self.root)
                self.assertTrue(any('description' in warning for warning in result['warnings']))
                self.assertNotIn('critical_checks', result)

    def test_invalid_yaml_still_scans_body(self):
        self.write('SKILL.md', '---\nname: [broken\n---\n[rules](references/missing.md)\n')
        result = checker.snapshot(self.root)
        self.assertIn('frontmatter_error', result['skill_md'])
        self.assertEqual(['references/missing.md'], result['ghost_references'])

    def test_missing_and_nonmapping_frontmatter(self):
        for text in ('# Instructions\n', '---\n- item\n---\n'):
            with self.subTest(text=text):
                self.write('SKILL.md', text)
                self.assertIn('frontmatter_error', checker.snapshot(self.root)['skill_md'])

    def test_markdown_links_report_missing_files(self):
        self.skill('[rules](references/missing.md)\n')
        self.assertEqual(['references/missing.md'], checker.snapshot(self.root)['ghost_references'])

    def test_anchors_queries_spaces_and_windows_separators(self):
        self.skill('[rules](<references/real file.md#section>)\n'
                   '[again](references/real%20file.md?view=1#section)\n'
                   '`references\\real file.md#section`\n')
        self.write('references/real file.md', '# Section\n')
        result = checker.snapshot(self.root)
        self.assertEqual([], result['ghost_references'])
        self.assertEqual([], result['unlinked_files'])

    def test_inline_root_paths_and_command_arguments(self):
        self.skill('`requirements.txt` and `scripts/run.py <path> --pretty`\n')
        self.write('requirements.txt')
        self.write('scripts/run.py')
        result = checker.snapshot(self.root)
        self.assertEqual([], result['ghost_references'])
        self.assertEqual([], result['unlinked_files'])

    def test_reference_style_link(self):
        self.skill('[rules][guide]\n\n[guide]: references/rules.md "Rules"\n')
        self.write('references/rules.md')
        self.assertEqual([], checker.snapshot(self.root)['unlinked_files'])

    def test_inline_commands_are_not_misreported_as_paths(self):
        self.skill('Run `python scripts/run.py` or `python.exe run.py`.\n')
        self.assertEqual([], checker.snapshot(self.root)['ghost_references'])

    def test_second_level_resources_and_cycles(self):
        self.skill('[rules](references/rules.md)\n')
        self.write('references/rules.md', '[helper](../scripts/helper.py)\n[entry](../SKILL.md)\n')
        self.write('scripts/helper.py', 'raise RuntimeError("MUST NOT EXECUTE")\n')
        result = checker.snapshot(self.root)
        self.assertEqual(['SKILL.md', 'references/rules.md'], result['scanned_documents'])
        self.assertEqual([], result['ghost_references'])
        self.assertEqual([], result['unlinked_files'])

    def test_skill_root_relative_references_from_nested_document(self):
        self.skill('[guide](references/guide.md)\n')
        self.write('references/guide.md', '[helper](scripts/helper.py)\n')
        self.write('scripts/helper.py')
        result = checker.snapshot(self.root)
        self.assertEqual([], result['ghost_references'])
        self.assertEqual([], result['unlinked_files'])

    def test_ambiguous_bases_are_not_silently_selected(self):
        self.skill('[guide](references/guide.md)\n')
        self.write('references/guide.md', '[rules](rules.md)\n')
        self.write('references/rules.md', 'Document-relative rule.\n')
        self.write('rules.md', 'Skill-relative rule.\n')
        result = checker.snapshot(self.root)
        self.assertEqual(['SKILL.md', 'references/guide.md'], result['scanned_documents'])
        self.assertEqual(1, len(result['ambiguous_references']))
        self.assertCountEqual(['references/rules.md', 'rules.md'],
                              result['ambiguous_references'][0]['candidates'])
        for mode, selected, rejected in (('skill', 'rules.md', 'references/rules.md'),
                                         ('document', 'references/rules.md', 'rules.md')):
            with self.subTest(mode=mode):
                result = checker.snapshot(self.root, path_base=mode)
                self.assertIn(selected, result['scanned_documents'])
                self.assertNotIn(rejected, result['scanned_documents'])
                self.assertEqual([], result['ambiguous_references'])

    def test_runtime_paths_and_injected_commands_are_deferred_without_execution(self):
        self.skill('`${SKILL_ROOT}/resources/live.md`\n'
                   '`%SKILL_ROOT%/resources/windows.md`\n'
                   '`resources/*.md`\n!`cat data.json`\n')
        with patch.object(Path, 'read_text', autospec=True, side_effect=Path.read_text) as reader:
            result = checker.snapshot(self.root)
            self.assertEqual(1, reader.call_count)
        self.assertEqual([], result['ghost_references'])
        self.assertEqual(4, len(result['deferred_references']))

    def test_unknown_metadata_is_retained_without_an_allowlist(self):
        self.write('SKILL.md', '---\ndescription: Summarize provided messages.\n'
                   'disable-model-invocation: true\ncontext: fork\n'
                   'future-capability: {revision: 2}\n---\nReturn the summary.\n')
        result = checker.snapshot(self.root)
        self.assertIn('future-capability', result['skill_md']['frontmatter_keys'])
        self.assertIsNone(result['skill_md']['frontmatter']['name'])
        self.assertEqual(1, len(result['warnings']))
        self.assertIn('未提供 name', result['warnings'][0])

    def test_custom_resource_directory_is_read_when_linked(self):
        self.skill('[guide](custom-knowledge/guide.md)\n')
        self.write('custom-knowledge/guide.md', 'Use the supplied reference.\n')
        result = checker.snapshot(self.root)
        self.assertIn('custom-knowledge/guide.md', result['scanned_documents'])
        self.assertEqual([], result['ghost_references'])

    def test_cli_supports_explicit_path_base_and_rejects_unknown_mode(self):
        self.skill('[guide](references/guide.md)\n')
        self.write('references/guide.md', '[rules](rules.md)\n')
        self.write('rules.md')
        for mode, expected in (('skill', 0), ('unknown', 2)):
            with self.subTest(mode=mode):
                completed = subprocess.run([sys.executable, str(SCRIPT), str(self.root),
                                            '--path-base', mode],
                                           capture_output=True, text=True, encoding='utf-8')
                self.assertEqual(expected, completed.returncode, completed.stderr)
                data = json.loads(completed.stdout)
                if expected == 0:
                    self.assertEqual('skill', data['path_base'])
                    self.assertEqual([], data['ghost_references'])
                else:
                    self.assertIn('error', data)

    def test_directory_reference_covers_resources(self):
        self.skill('Read `references/` as needed.\n')
        self.write('references/rules.md')
        self.assertEqual([], checker.snapshot(self.root)['unlinked_files'])

    def test_absolute_and_outside_paths_not_read(self):
        self.skill('[remote](https://example.test/rules.md)\n[up](../outside.md)\n'
                   '`C:\\outside\\rules.md`\n[unix](/outside/rules.md)\n')
        with patch.object(Path, 'read_text', autospec=True, side_effect=Path.read_text) as reader:
            result = checker.snapshot(self.root)
            self.assertEqual(1, reader.call_count)
        self.assertEqual(4, len(result['external_references']))
        self.assertEqual([], result['ghost_references'])

    def test_fenced_examples_are_not_actual_routes(self):
        self.skill('```markdown\n[rules](references/example-only.md)\n```\n'
                   '~~~sh\n`references/another-example.md`\n~~~\n')
        self.assertEqual([], checker.snapshot(self.root)['ghost_references'])

    def test_single_file_has_explicit_limited_coverage(self):
        entry = self.skill('[rules](references/rules.md)\n[missing](references/missing.md)\n')
        self.write('references/rules.md', '[helper](../scripts/helper.py)\n')
        self.write('scripts/helper.py')
        result = checker.snapshot(entry)
        self.assertEqual({}, result['directories'])
        self.assertEqual(['SKILL.md'], result['scanned_documents'])
        self.assertEqual(['references/missing.md'], result['ghost_references'])
        self.assertTrue(any('单文件' in warning for warning in result['warnings']))

    def test_simple_writing_skill_has_no_structural_penalty(self):
        self.skill('Rewrite the supplied message in plain language, preserving names and facts. '
                   'Return only the revised message; if no text is supplied ask for it.\n')
        result = checker.snapshot(self.root)
        self.assertEqual([], result['warnings'])
        self.assertEqual([], result['ghost_references'])
        self.assertNotIn('tier_hint', result)
        self.assertNotIn('missing_critical', result)

    def test_missing_entry_is_a_fact_not_a_quality_verdict(self):
        result = checker.snapshot(self.root)
        self.assertFalse(result['skill_md']['exists'])
        self.assertTrue(result['warnings'])
        self.assertNotIn('critical_checks', result)

    def test_utf8_bom_and_crlf(self):
        entry = self.root / 'SKILL.md'
        entry.write_bytes(b'\xef\xbb\xbf---\r\nname: demo\r\ndescription: Read messages.\r\n---\r\nBody\r\n')
        self.assertEqual([], checker.snapshot(self.root)['warnings'])

    def test_unreadable_linked_document_is_not_silent(self):
        self.skill('[rules](references/rules.md)\n')
        path = self.write('references/rules.md')
        path.write_bytes(b'\xff\xfe')
        result = checker.snapshot(self.root)
        self.assertTrue(any('无法读取引用文档' in warning for warning in result['warnings']))
        self.assertNotIn('references/rules.md', result['scanned_documents'])

    def test_cli_json_and_error_exit_codes(self):
        self.skill()
        for target, expected in ((self.root, 0), (self.root / 'absent', 2)):
            with self.subTest(target=target):
                completed = subprocess.run([sys.executable, str(SCRIPT), str(target), '--pretty'],
                                           capture_output=True, text=True, encoding='utf-8')
                self.assertEqual(expected, completed.returncode, completed.stderr)
                data = json.loads(completed.stdout)
                self.assertIn('schema_version' if expected == 0 else 'error', data)
        completed = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True,
                                   text=True, encoding='utf-8')
        self.assertEqual(2, completed.returncode)
        self.assertIn('error', json.loads(completed.stdout))


if __name__ == '__main__':
    unittest.main()
