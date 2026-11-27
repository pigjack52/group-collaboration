"""Event contract and real CLI checks; all writes use temporary repositories."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from group_events import (COMMON, PAYLOADS, ENUMS, EventError, validate_event,
                          load_event, write_event, read_event, list_events)

CLI = Path(__file__).resolve().parents[1] / 'groupctl.py'


def sample(kind='finding'):
    event = dict(protocol_version='0.1', event_id='E-20261113T000000Z-P01-ab12',
                 event_type=kind, group_id='G-01', person_id='P01',
                 created_at='2026-11-13T00:00:00Z', related_events=[])
    for field, shape in PAYLOADS[kind].items():
        event[field] = {'text': 'Concrete standalone description or identifier',
                        'strings': [], 'bool': False, 'count': 0,
                        'verification': dict(build='pass', tests='pass', semantic_review='pass')}[shape]
    if kind in ENUMS:
        field, choices = ENUMS[kind]
        event[field] = sorted(choices)[0]
    return event


class EventTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / 'input.json'

    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-B', str(CLI), 'events', *args],
                              cwd=self.root, capture_output=True, text=True, encoding='utf-8')

    def source_event(self, event):
        self.source.write_text(json.dumps(event), encoding='utf-8')

    def test_all_types_round_trip_with_extensions(self):
        for kind in PAYLOADS:
            with self.subTest(kind=kind):
                event = sample(kind)
                event['event_id'] = 'E-' + kind
                event['optional_context'] = {'reason': 'Useful without chat history'}
                write_event(self.root, event)
                self.assertEqual(read_event(self.root, event['event_id']), event)
        self.assertEqual(len(list_events(self.root)), 10)

    def test_each_missing_common_field(self):
        for field in COMMON:
            with self.subTest(field=field):
                event = sample()
                del event[field]
                with self.assertRaisesRegex(EventError, field):
                    validate_event(event)

    def test_unknown_type_and_version(self):
        for field, value in [('event_type', 'unknown'), ('protocol_version', '1.0')]:
            event = sample()
            event[field] = value
            with self.assertRaisesRegex(EventError, field):
                validate_event(event)

    def test_each_missing_payload_field(self):
        for kind, fields in PAYLOADS.items():
            for field in fields:
                with self.subTest(kind=kind, field=field):
                    event = sample(kind)
                    del event[field]
                    with self.assertRaisesRegex(EventError, field):
                        validate_event(event)

    def test_each_invalid_payload_type(self):
        for kind, fields in PAYLOADS.items():
            for field in fields:
                with self.subTest(kind=kind, field=field):
                    event = sample(kind)
                    event[field] = None
                    with self.assertRaisesRegex(EventError, field):
                        validate_event(event)

    def test_common_field_types(self):
        for field, value in [('protocol_version', 0.1), ('group_id', ''),
                             ('person_id', None), ('related_events', 'E-1'),
                             ('related_events', [3]), ('created_at', 'yesterday'),
                             ('created_at', '2026-02-30T00:00:00Z'),
                             ('created_at', '2026-11-13T00:00:00')]:
            with self.subTest(field=field, value=value):
                event = sample()
                event[field] = value
                with self.assertRaises(EventError):
                    validate_event(event)

    def test_safe_ids(self):
        for identifier in ['../escape', '..', '/absolute', 'x/y', 'x\\y',
                           'CON', 'nul.txt', 'x:', 'end.', '']:
            with self.subTest(identifier=identifier):
                event = sample()
                event['event_id'] = identifier
                with self.assertRaises(EventError):
                    write_event(self.root, event)
                with self.assertRaises(EventError):
                    read_event(self.root, identifier)
        self.assertFalse((self.root / '.group').exists())

    def test_enumerations(self):
        for kind, (field, choices) in ENUMS.items():
            for choice in choices:
                event = sample(kind)
                event[field] = choice
                validate_event(event)
            event[field] = 'unsupported'
            with self.assertRaisesRegex(EventError, field):
                validate_event(event)

    def test_counts_and_boolean_are_strict(self):
        for field, value in [('passed', True), ('failed', -1), ('skipped', 1.5)]:
            event = sample('test_result')
            event[field] = value
            with self.assertRaisesRegex(EventError, field):
                validate_event(event)
        event = sample('question')
        event['blocking'] = 1
        with self.assertRaisesRegex(EventError, 'blocking'):
            validate_event(event)

    def test_verification(self):
        for field in ('build', 'tests', 'semantic_review'):
            event = sample('integration_result')
            del event['verification'][field]
            with self.assertRaisesRegex(EventError, field):
                validate_event(event)

    def test_duplicate_never_overwrites(self):
        event = sample()
        path = write_event(self.root, event)
        original = path.read_bytes()
        event['summary'] = 'Changed content'
        with self.assertRaisesRegex(EventError, 'already exists'):
            write_event(self.root, event)
        self.assertEqual(path.read_bytes(), original)

    def test_invalid_json_diagnostics(self):
        self.source.write_text('{"x":\n}', encoding='utf-8')
        result = self.run_cli('create', str(self.source))
        self.assertEqual(result.returncode, 1)
        self.assertIn('Invalid JSON', result.stderr)
        self.assertIn('line 2, column 1', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertFalse((self.root / '.group').exists())

    def test_non_objects_duplicate_keys_and_constants(self):
        for content in ['[]', 'null', '{"x":1,"x":2}', '{"x":NaN}']:
            self.source.write_text(content, encoding='utf-8')
            with self.assertRaises(EventError):
                load_event(self.source)

    def test_utf8_bom_and_bad_encoding(self):
        self.source.write_text(json.dumps(sample()), encoding='utf-8-sig')
        self.assertEqual(load_event(self.source), sample())
        self.source.write_bytes(b'\xff')
        with self.assertRaisesRegex(EventError, 'UTF-8'):
            load_event(self.source)

    def test_missing_and_unreadable_source(self):
        for path in (self.source, self.root):
            with self.assertRaisesRegex(EventError, 'Cannot read event'):
                load_event(path)

    def test_filename_mismatch_and_malformed_repository_event(self):
        event = sample()
        path = write_event(self.root, event)
        event['event_id'] = 'different'
        path.write_text(json.dumps(event), encoding='utf-8')
        with self.assertRaisesRegex(EventError, 'filename'):
            list_events(self.root)
        path.write_text('{', encoding='utf-8')
        with self.assertRaisesRegex(EventError, 'Invalid JSON'):
            list_events(self.root)

    def test_list_empty_and_sorted(self):
        self.assertEqual(list_events(self.root), [])
        for identifier in ('Z', 'A'):
            write_event(self.root, dict(sample(), event_id=identifier))
        self.assertEqual([e['event_id'] for e in list_events(self.root)], ['A', 'Z'])

    def test_cli_create_validate_show_list(self):
        event = sample()
        self.source_event(event)
        for action in ('validate', 'create'):
            result = self.run_cli(action, str(self.source))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), event)
            if action == 'validate':
                self.assertFalse((self.root / '.group').exists())
        result = self.run_cli('show', event['event_id'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), event)
        result = self.run_cli('list')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [event])
        result = self.run_cli('create', str(self.source))
        self.assertEqual(result.returncode, 1)
        self.assertIn('refusing to overwrite', result.stderr)

    def test_cli_help_and_invalid_arguments(self):
        for args in [('--help',), ('create', '--help'), ('validate', '--help'),
                     ('show', '--help'), ('list', '--help')]:
            self.assertEqual(self.run_cli(*args).returncode, 0)
        for args in [(), ('create',), ('show',), ('list', '--unknown')]:
            self.assertEqual(self.run_cli(*args).returncode, 2)

    def test_cli_validation_errors(self):
        for event in [dict(sample(), event_type='bad'),
                      dict(sample(), evidence='chat above'), {}]:
            self.source_event(event)
            result = self.run_cli('create', str(self.source))
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, '')
            self.assertNotIn('Traceback', result.stderr)
        self.assertFalse((self.root / '.group').exists())


if __name__ == '__main__':
    unittest.main()
