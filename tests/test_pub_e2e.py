"""Publish with the real CLI and discover from an independent worktree."""
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from group_events import parse_event
from test_pub import BRANCH, CLI
import test_sync


class PubEndToEndTests(unittest.TestCase):
    git = test_sync.SyncTests.git
    identity = test_sync.SyncTests.identity
    commit = test_sync.SyncTests.commit

    def run_cli(self, root, *args):
        run = subprocess.run([sys.executable, '-B', str(CLI), *args], cwd=root,
                             capture_output=True, text=True, encoding='utf-8', timeout=90)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        return run.stdout

    def test_publish_and_discover_exactly_once(self):
        env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_ALLOW_PROTOCOL='file', GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0')
        self.enterContext(patch.dict(os.environ, env, clear=True))
        root = Path(self.enterContext(TemporaryDirectory(prefix='groupctl-pub-e2e-')))
        publisher, observer = root / 'publisher', root / 'observer'
        url = (root / 'shared.git').as_posix()
        remote = ('--git-dir', url)
        canonical, personal = 'refs/heads/main', f'refs/heads/{BRANCH}'
        self.git(root, 'init', '--bare', url)
        self.git(root, 'init', '-b', 'main', str(publisher))
        self.identity(publisher)
        members = publisher / '.group/members'
        members.mkdir(parents=True)
        (members.parent / 'group.json').write_text(json.dumps(dict(
            group_id='G-e2e', protocol_version='0.1', canonical_branch='main',
            assignment_file='assignment.md', repository=url)), encoding='utf-8')
        for person in ('P01', 'P02'):
            (members / f'{person}.json').write_text(json.dumps(dict(
                person_id=person, protocol_version='0.1', display_name=person,
                joined_at='2026-11-14T00:00:00Z')), encoding='utf-8')
        (publisher / '.gitignore').write_text('.group/local/\n', encoding='utf-8')
        work, pending = publisher / 'work.txt', publisher / 'pending.txt'
        for path in (work, pending):
            path.write_bytes(b'base\n')
        base = self.commit(publisher)
        self.git(publisher, 'remote', 'add', 'origin', url)
        self.git(publisher, 'push', 'origin', f'{base}:{canonical}', f'{base}:{personal}')
        self.git(publisher, 'switch', '-c', BRANCH)
        self.git(root, 'clone', '-b', 'main', url, str(observer))
        self.git(observer, 'switch', '-c', 'people/P02/observer')
        observer_pending = observer / pending.name
        observer_pending.write_bytes(b'staged observer content\n')
        self.git(observer, 'add', pending.name)
        observer_pending.write_bytes(b'unstaged observer content\n')

        def observer_state():
            return (self.git(observer, 'symbolic-ref', 'HEAD'),
                    self.git(observer, 'rev-parse', 'HEAD'),
                    self.git(observer, 'status', '--short'),
                    (observer / '.git/index').read_bytes(),
                    (observer / work.name).read_bytes(), observer_pending.read_bytes())

        before = observer_state()
        self.assertIn('New events: 0', self.run_cli(observer, 'sync', '--discover'))

        def pub(mode, *args):
            return json.loads(self.run_cli(publisher, 'pub', mode, '--person-id', 'P01', *args))

        reviewed = ('--expected-branch', BRANCH, '--confirm-reviewed')
        destination = ('--remote', 'origin', '--expected-url', url)
        work.write_bytes(b'approved fixture content\n')
        pending.write_bytes(b'unselected local edit\n')
        preflight = pub('--preflight')
        self.assertEqual(preflight['result'], 'review_required')
        self.assertEqual(preflight['facts']['head'], base)
        self.assertEqual(set(preflight['facts']['unstaged']), {work.name, pending.name})
        self.assertEqual(work.read_bytes(), b'approved fixture content\n')
        committed = pub('--work-commit', *reviewed, '--expected-head', base,
                        '--file', work.name, '--message', 'Update reviewed fixture')
        self.assertEqual(committed['result'], 'created')
        work_sha = committed['commit']
        self.assertEqual(self.git(publisher, 'rev-list', '--parents', '-n', '1', work_sha),
                         f'{work_sha} {base}')
        self.assertEqual(self.git(publisher, 'diff', '--name-status', base, work_sha), 'M\twork.txt')
        self.assertEqual(self.git(publisher, 'show', f'{work_sha}:work.txt'), 'approved fixture content')
        self.assertEqual(pending.read_bytes(), b'unselected local edit\n')
        pushed = pub('--push-work', *reviewed, *destination, '--expected-head', work_sha,
                     '--expected-remote-head', base)
        self.assertEqual(pushed['result'], 'verified')
        self.assertTrue(pushed['verified'])
        self.assertEqual(self.git(root, *remote, 'rev-parse', personal), work_sha)
        discovery = self.run_cli(observer, 'sync', '--discover')
        self.assertIn(work_sha, discovery)
        self.assertIn('New events: 0', discovery)

        review = dict(summary='Update reviewed fixture.', files_changed=['work.txt'],
                      interface_changes=[], tests_run=['Compare fixture with approved text'],
                      known_issues=[], proposed_for_integration=False)
        review_file = root / 'review.json'
        review_file.write_text(json.dumps(review), encoding='utf-8')
        contribution = (*reviewed, *destination, '--work-sha', work_sha, '--change-id', 'C-e2e')
        recorded = pub('--event-commit', *contribution, '--expected-head', work_sha,
                       '--review-file', str(review_file))
        self.assertEqual(recorded['result'], 'local_event_committed')
        event_sha, event_path = recorded['commit'], recorded['event_path']
        self.assertEqual(self.git(publisher, 'rev-list', '--parents', '-n', '1', event_sha),
                         f'{event_sha} {work_sha}')
        self.assertEqual(self.git(publisher, 'diff', '--name-status', work_sha, event_sha),
                         f'A\t{event_path}')
        event = parse_event(self.git(publisher, 'show', f'{event_sha}:{event_path}'))
        self.assertEqual(event_path, f'.group/events/{event["event_id"]}.json')
        self.assertEqual(event['event_id'], recorded['event_id'])
        self.assertEqual((event['event_type'], event['person_id'], event['group_id'],
                          event['change_id'], event['branch'], event['commit']),
                         ('change_published', 'P01', 'G-e2e', 'C-e2e', BRANCH, work_sha))
        self.assertEqual({field: event[field] for field in review}, review)
        self.assertEqual(self.git(root, *remote, 'rev-parse', personal), work_sha)

        push_event = (*contribution, '--expected-head', event_sha, '--expected-remote-head', work_sha)
        for expected in ('published_verified', 'already_published'):
            with self.subTest(push=expected):
                report = pub('--push-event', *push_event)
                self.assertEqual(report['result'], expected)
                self.assertTrue(report['verified'])
                if expected == 'already_published':
                    self.assertNotIn('push_exit', report)
                self.assertEqual(self.git(root, *remote, 'rev-parse', personal), event_sha)
                self.assertEqual(self.git(root, *remote, 'rev-list', '--parents', '-n', '1', event_sha),
                                 f'{event_sha} {work_sha}')
                blob = self.git(root, *remote, 'rev-parse', f'{event_sha}:{event_path}')
                self.assertEqual(blob, self.git(publisher, 'rev-parse', f'{event_sha}:{event_path}'))
                self.assertEqual(blob, report['remote_blob_verified'])
                self.assertEqual(parse_event(self.git(root, *remote, 'cat-file', 'blob', blob)), event)

        discovery = self.run_cli(observer, 'sync', '--discover')
        self.assertIn('New events: 1', discovery)
        discovered = [line for line in discovery.splitlines() if line.startswith('New event [')]
        self.assertEqual(len(discovered), 1, discovery)
        prefix = f'New event [refs/remotes/origin/{BRANCH}@{event_sha}:{event_path}]: '
        self.assertTrue(discovered[0].startswith(prefix), discovery)
        self.assertEqual(json.loads(discovered[0][len(prefix):]), event)
        self.assertEqual(self.git(observer, 'rev-parse', f'refs/remotes/origin/{BRANCH}'), event_sha)
        repeat = self.run_cli(observer, 'sync', '--discover')
        self.assertIn('New events: 0', repeat)
        self.assertNotIn('New event [', repeat)
        self.assertFalse((observer / event_path).exists())
        self.assertEqual(observer_state(), before)
        self.assertEqual(self.git(root, *remote, 'rev-parse', canonical), base)
        self.assertEqual(pending.read_bytes(), b'unselected local edit\n')
