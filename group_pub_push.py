"""Push one reviewed work commit and independently observe the remote tip."""
import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from group_git import GitError, _git, repository_root
from group_pub_commit import check_state


OBJECT_ID = re.compile(r'[0-9a-f]{40}|[0-9a-f]{64}')
TRANSPORT = ('-c', 'http.followRedirects=false')
NETWORK_ENV = {'GIT_ALLOW_PROTOCOL': 'file:https:ssh', 'GIT_TERMINAL_PROMPT': '0'}

# Git supplies the receive-pack advertisement to this hook before sending an update.
GUARD = r"""#!/bin/sh
reject () {
    printf '%s\n' rejected > "$GROUPCTL_GUARD_RESULT"
    exit 1
}
[ "$2" = "$GROUPCTL_PUSH_URL" ] || reject
IFS= read -r update || reject
[ "$update" = "$GROUPCTL_PUSH_UPDATE" ] || reject
if IFS= read -r extra; then
    reject
fi
printf '%s\n' approved > "$GROUPCTL_GUARD_RESULT"
"""


def check_destination(root, args):
    url = args.expected_url
    parsed = urlsplit(url)
    network = parsed.scheme in ('https', 'ssh') and parsed.hostname
    if (not url.strip() or url != url.strip() or any(ord(char) < 32 for char in url)
            or (network and (parsed.password is not None or parsed.query or parsed.fragment
                             or (parsed.scheme == 'https' and parsed.username is not None)))
            or (not network and (not Path(url).is_absolute() or '::' in url
                                 or url.replace('\\', '/').startswith('//')))):
        raise GitError('Expected URL must be HTTPS without credentials, ssh://, or an absolute local path.')
    remotes, _ = _git(root, 'remote')
    if args.remote not in remotes.splitlines() or args.remote.startswith('-'):
        raise GitError('The specified Git remote is not configured.')
    for options in ((), ('--push',)):
        urls, _ = _git(root, 'remote', 'get-url', '--all', *options, args.remote)
        if urls != url:
            raise GitError('The remote must resolve to one reviewed URL for both fetch and push.')
    check_literal_url(root, url)


def check_literal_url(root, url):
    remotes, _ = _git(root, 'remote')
    # Confirm the literal endpoint cannot be reinterpreted as a name or rewritten.
    resolved, _ = _git(root, 'ls-remote', '--get-url', '--', url)
    rewrites, _ = _git(root, 'config', '--get-regexp', r'^url\..*\.pushinsteadof$',
                       allowed=(0, 1))
    if (url in remotes.splitlines() or resolved != url
            or any(url.startswith(value) for _, _, value in
                   (line.partition(' ') for line in rewrites.splitlines()))):
        raise GitError('The reviewed URL must be usable directly without a further Git URL rewrite.')


def remote_head(root, url, ref, width):
    try:
        output, code = _git(root, *TRANSPORT, '-c', 'protocol.version=2',
                            'ls-remote', '--symref', '--exit-code',
                            '--', url, ref, allowed=(0, 2), env_overrides=NETWORK_ENV)
    except GitError as exc:
        raise GitError('Live remote branch query failed.') from exc
    if code == 2 and not output:
        return None
    oid, separator, advertised_ref = output.partition('\t')
    if (not separator or advertised_ref != ref or len(oid) != width
            or not OBJECT_ID.fullmatch(oid) or oid == '0' * width):
        raise GitError('Live remote query did not return one exact direct branch ref.')
    return oid


def check_target(root, load_config, args, *, own_lock=False):
    facts = check_state(root, load_config, args, own_lock=own_lock)
    if args.expected_branch.casefold() in ('main', facts['canonical_branch'].casefold()):
        raise GitError('Canonical branch is prohibited for this PUB operation.')
    committed, _ = _git(root, '--no-replace-objects', 'cat-file', 'blob',
                        f'{facts["head"]}:.group/group.json')
    config = json.loads(committed.removeprefix('\ufeff'))
    if 'repository' in config and config['repository'] != args.expected_url:
        raise GitError('The reviewed URL differs from the group repository URL.')
    kind, _ = _git(root, '--no-replace-objects', 'cat-file', '-t', args.expected_head)
    if kind != 'commit':
        raise GitError('Reviewed HEAD must identify a commit object.')
    grafts, _ = _git(root, 'rev-parse', '--git-path', 'info/grafts')
    if 'GIT_GRAFT_FILE' in os.environ or (root / grafts).exists():
        raise GitError('Git history grafts are unsupported for this PUB operation.')
    shallow, _ = _git(root, 'rev-parse', '--is-shallow-repository')
    if shallow == 'true':
        raise GitError('A shallow repository requires a separate PUB workflow.')
    check_destination(root, args)


def check_local(root, load_config, args):
    check_target(root, load_config, args)
    hooks, _ = _git(root, 'rev-parse', '--git-path', 'hooks')
    pre_push = root / hooks / 'pre-push'
    if pre_push.is_file() and os.access(pre_push, os.X_OK):
        raise GitError('An active pre-push hook requires a separate push workflow.')


def send_update(root, args, ref, old, report):
    head = args.expected_head
    with TemporaryDirectory(prefix='groupctl-push-') as directory:
        temporary = Path(directory)
        transport = temporary / 'transport.git'
        transport.mkdir()
        object_format = 'sha1' if len(head) == 40 else 'sha256'
        _git(transport, 'init', '--bare', '--template=', f'--object-format={object_format}')
        objects, _ = _git(root, 'rev-parse', '--path-format=absolute', '--git-path', 'objects')
        # Git may match a literal URL to an existing remote and update tracking refs.
        # Keep all such writes in the temporary repository, borrowing objects read-only.
        if '\n' in objects or '\r' in objects:
            raise GitError('Git object directory contains a line break.')
        (transport / 'objects/info/alternates').write_bytes((objects + '\n').encode('utf-8'))
        check_literal_url(transport, args.expected_url)
        hooks = temporary / 'hooks'
        hooks.mkdir()
        hook = hooks / 'pre-push'
        hook.write_bytes(GUARD.encode('ascii'))
        hook.chmod(0o755)
        guard_result = hooks / 'result'
        env = dict(NETWORK_ENV, GROUPCTL_PUSH_URL=args.expected_url,
                   GROUPCTL_PUSH_UPDATE=f'{head} {head} {ref} {old or "0" * len(head)}',
                   GROUPCTL_GUARD_RESULT=guard_result.as_posix())
        report['result'] = 'verification_failed'
        try:
            output, code = _git(
                transport, '--no-replace-objects', *TRANSPORT,
                '-c', f'core.hooksPath={hooks.as_posix()}', '-c', 'push.pushOption=',
                'push', '--porcelain', '--no-force', '--no-mirror', '--no-tags', '--no-follow-tags',
                '--no-signed', '--recurse-submodules=no', '--', args.expected_url,
                f'{head}:{ref}', allowed=(0, 1, 128), env_overrides=env,
            )
        except GitError as exc:
            raise GitError('Git push outcome is uncertain. Query the remote before retrying.') from exc
        report['push_exit'] = code
        decision = guard_result.read_text(encoding='ascii').strip() if guard_result.exists() else None
        if decision == 'rejected' or any(
            line.startswith('!\t') and line.split('\t', 2)[-1].startswith(
                ('[rejected]', '[remote rejected]')) for line in output.splitlines()
        ):
            report['result'] = 'push_rejected'
            if decision == 'rejected':
                raise GitError('Push advertisement differed from the reviewed URL or ref update.')
            raise GitError('Git rejected the requested branch update.')
        if code or decision != 'approved':
            raise GitError('Git push outcome is uncertain. Query the remote before retrying.')


def push_work(directory, load_config, args):
    head = args.expected_head
    report = dict(operation='pub_push_work', result='not_executed', verified=False,
                  publication_scope='work_commit_only', person_id=args.person_id, remote=args.remote,
                  branch=args.expected_branch, commit=head,
                  expected_remote_head=args.expected_remote_head)
    try:
        if not args.confirm_reviewed:
            raise GitError('Explicit review and push authorization are required.')
        if not OBJECT_ID.fullmatch(head):
            raise GitError('Expected HEAD must be a full lowercase Git object ID.')
        expected_old = args.expected_remote_head
        if expected_old != 'absent' and (not OBJECT_ID.fullmatch(expected_old)
                                        or len(expected_old) != len(head)
                                        or expected_old == '0' * len(head)):
            raise GitError('Expected remote HEAD must be a full object ID or absent.')
        local_env, _ = _git(directory, 'rev-parse', '--local-env-vars')
        if set(local_env.splitlines()).intersection(os.environ) or 'GIT_NAMESPACE' in os.environ:
            raise GitError('Repository-local Git environment overrides are unsupported.')
        root = repository_root(directory)
        ref = 'refs/heads/' + args.expected_branch
        _git(root, 'check-ref-format', ref)
        check_local(root, load_config, args)
        report['remote_url'] = args.expected_url
        old = remote_head(root, args.expected_url, ref, len(head))
        report['remote_head_before'] = old
        if old == head:
            report.update(result='already_present', verified=True, remote_head_verified=old)
            return report
        if old != (None if expected_old == 'absent' else expected_old):
            raise GitError('Live remote tip differs from the explicitly reviewed remote tip.')
        if old is not None:
            try:
                _, ancestry = _git(root, '--no-replace-objects', 'merge-base', '--is-ancestor',
                                   old, head, allowed=(0, 1))
            except GitError as exc:
                raise GitError('Remote tip history is unavailable locally; review it before retrying.') from exc
            if ancestry:
                raise GitError('Remote tip is not an ancestor of the reviewed commit.')
        check_local(root, load_config, args)
        send_update(root, args, ref, old, report)
        check_destination(root, args)
        observed = remote_head(root, args.expected_url, ref, len(head))
        report['remote_head_after'] = observed
        if observed != head:
            raise GitError('Post-push remote tip differs from the reviewed commit.')
        report.update(result='verified', verified=True, remote_head_verified=observed)
    except (GitError, OSError, ValueError) as exc:
        report['error'] = str(exc)
    return report
