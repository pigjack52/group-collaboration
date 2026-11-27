"""FINAL group finalization: candidate preparation and human-confirmed publication."""
from datetime import datetime
import json
from pathlib import Path

from group_event_publish import (
    collect_known_events, commit_event_file, new_event_id, push_and_verify_event,
    sha256_bytes, sha256_file, utc_now,
)
from group_events import EventError, load_event, validate_event, write_event
from group_git import GitError, _git, inspect_status, repository_root
from group_int import GIT, ancestor, resolve_work
from group_int_finish import run_isolated_unittest
from group_status import StatusError, read_members


FINAL_DIR = '.group/final'
CANDIDATE_MD = '.group/final/candidate.md'
FINAL_REPORT_MD = '.group/final/final_report.md'
FINAL_JSON = '.group/final/final.json'


def _parse_dispositions(items):
    result = {}
    for item in items or []:
        if '=' not in item:
            raise EventError(f'Disposition must use CHANGE_ID=reason format: {item}')
        cid, reason = item.split('=', 1)
        cid, reason = cid.strip(), reason.strip()
        if not cid or not reason:
            raise EventError(f'Disposition requires nonblank change ID and reason: {item}')
        result[cid] = reason
    return result


def _canonical_commit(root, canonical):
    local_ref = 'refs/heads/' + canonical
    remote_ref = 'refs/remotes/origin/' + canonical
    local_sha, lcode = _git(root, *GIT, 'rev-parse', '--verify', local_ref + '^{commit}', allowed=(0, 128))
    remote_sha, rcode = _git(root, *GIT, 'rev-parse', '--verify', remote_ref + '^{commit}', allowed=(0, 128))
    if lcode != 0:
        raise GitError(f'Local canonical branch {canonical!r} is unavailable.')
    if rcode == 0 and local_sha != remote_sha:
        raise GitError(
            f'Local canonical {canonical} ({local_sha}) differs from origin/{canonical} ({remote_sha}); '
            'synchronize canonical before FINAL.'
        )
    return local_sha


def _render_report(title, *, assignment_text, result_summary, canonical_branch, canonical_commit,
                   incorporated, pending, dispositions, test_outcome, reviews, blockers,
                   limitations, contributors, confirmation=None, evidence_mode=None):
    lines = [
        f'# {title}',
        '',
    ]
    if evidence_mode == 'simulation':
        lines += [
            '> **EVIDENCE MODE: SIMULATION** — Generated for automated acceptance testing; '
            'not a real human group submission.',
            '',
        ]
    lines += [
        '## Assignment',
        assignment_text.strip() or '(Assignment file empty)',
        '',
        '## Result',
        result_summary.strip(),
        '',
        '## Canonical Commit',
        f'- Branch: `{canonical_branch}`',
        f'- Commit: `{canonical_commit}`',
        '',
        '## Important Changes',
    ]
    if incorporated:
        for ch in incorporated:
            lines.append(f'- `{ch["change_id"]}` ({ch["person_id"]}, `{ch["commit"][:12]}`): {ch["summary"]}')
    else:
        lines.append('- No published changes recorded in canonical history.')
    if pending:
        lines.append('')
        lines.append('### Pending / Unincorporated Changes')
        for ch in pending:
            disp = dispositions.get(ch['change_id'])
            note = f'Disposition: {disp}' if disp else 'UNRESOLVED (no disposition)'
            lines.append(f'- `{ch["change_id"]}` ({ch["person_id"]}): {ch["summary"]} — {note}')
    lines += [
        '',
        '## Validation',
        f'- Final isolated test run on `{canonical_commit}`: `{test_outcome["automated_result"]}` '
        f'({test_outcome["summary"]})',
        f'- Reviews recorded: {len(reviews)}',
        f'- Blockers recorded: {len(blockers)}',
        '',
        '## Known Limitations',
    ]
    if limitations:
        for item in limitations:
            lines.append(f'- {item}')
    else:
        lines.append('- None declared.')
    lines += [
        '',
        '## Contributors',
    ]
    for c in contributors:
        lines.append(f'- `{c["person_id"]}` ({c["display_name"]})')
    if confirmation:
        lines += [
            '',
            '## Human Confirmation',
            f'- Confirmed by: `{confirmation["confirmed_by"]}`',
            f'- Confirmed at: `{confirmation["confirmed_at"]}`',
            f'- Statement: {confirmation["statement"]}',
            f'- Evidence mode: `{confirmation.get("evidence_mode", "human")}`',
        ]
    lines.append('')
    return '\n'.join(lines)


def _resolved_blocker_ids(known):
    resolved = set()
    for event in known.values():
        if event['event_type'] != 'finding':
            continue
        targets = event.get('resolves_events')
        if not isinstance(targets, list):
            continue
        related = set(event.get('related_events') or [])
        for target_id in targets:
            if isinstance(target_id, str) and target_id in related:
                resolved.add(target_id)
    return resolved


def prepare_final(directory, load_config, args):
    """Synchronize-check canonical, run isolated final tests, and generate candidate.md + final_candidate."""
    report = dict(
        operation='final_prepare', status='blocked', blockers=[],
        unresolved_concerns=[], person_id=args.person_id,
    )
    try:
        if not args.result_summary or not args.result_summary.strip():
            raise EventError('A nonblank --result-summary is required.')
        root = repository_root(directory)
        config = load_config(root / '.group/group.json')
        canonical = config['canonical_branch']
        members = read_members(root)
        member_ids = {m['person_id'] for m in members}
        if args.person_id not in member_ids:
            raise EventError(f'Unknown member person_id: {args.person_id}')

        state = inspect_status(root, canonical, allow_missing_upstream=True)
        if state.branch != canonical:
            raise GitError(f'FINAL requires checked-out canonical branch {canonical!r} (currently {state.branch!r}).')
        if not state.clean:
            raise GitError('Working tree must be clean before FINAL --prepare.')

        canon_sha = _canonical_commit(root, canonical)
        dispositions = _parse_dispositions(args.disposition)
        limitations = list(args.limitation or [])

        known = collect_known_events(root, config['group_id'])
        changes = [e for e in known.values() if e['event_type'] == 'change_published']
        reviews = [e for e in known.values() if e['event_type'] == 'review']
        resolved_ids = _resolved_blocker_ids(known)
        all_blockers = [e for e in known.values() if e['event_type'] == 'blocker']
        open_blockers = [b for b in all_blockers if b['event_id'] not in resolved_ids]

        incorporated, pending = [], []
        for ch in sorted(changes, key=lambda e: e['change_id']):
            try:
                work_sha = resolve_work(root, ch['commit'], len(canon_sha))
                is_inc = ancestor(root, work_sha, canon_sha)
            except (EventError, GitError):
                is_inc = False
            if is_inc:
                incorporated.append(ch)
            else:
                pending.append(ch)
                if ch['change_id'] not in dispositions:
                    report['unresolved_concerns'].append(
                        f'Unincorporated change without disposition: {ch["change_id"]}'
                    )

        for blk in open_blockers:
            report['unresolved_concerns'].append(
                f'Blocker recorded ({blk["event_id"]}): {blk["summary"]}'
            )

        test_outcome = run_isolated_unittest(root, canon_sha)
        if test_outcome['automated_result'] != 'pass':
            report['unresolved_concerns'].append(
                f'Final canonical tests did not pass: {test_outcome["automated_result"]} ({test_outcome["summary"]})'
            )

        assignment_path = root / config['assignment_file']
        assignment_text = (assignment_path.read_text(encoding='utf-8-sig')
                           if assignment_path.is_file() else config['assignment_file'])
        evidence_mode = 'simulation' if getattr(args, 'simulation', False) else 'human'
        md_content = _render_report(
            'Group Final Report (Candidate)',
            assignment_text=assignment_text,
            result_summary=args.result_summary,
            canonical_branch=canonical,
            canonical_commit=canon_sha,
            incorporated=incorporated,
            pending=pending,
            dispositions=dispositions,
            test_outcome=test_outcome,
            reviews=reviews,
            blockers=open_blockers,
            limitations=limitations,
            contributors=members,
            evidence_mode=evidence_mode,
        )
        candidate_path = root / CANDIDATE_MD
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_bytes = md_content.encode('utf-8')
        candidate_path.write_bytes(candidate_bytes)
        report_sha256 = sha256_bytes(candidate_bytes)

        now, created_at = utc_now()
        event = validate_event(dict(
            protocol_version='0.1',
            event_id=new_event_id(args.person_id, now),
            event_type='final_candidate',
            group_id=config['group_id'],
            person_id=args.person_id,
            created_at=created_at,
            related_events=[],
            canonical_commit=canon_sha,
            candidate_report_path=CANDIDATE_MD,
            tests_evidence=[f'{canon_sha}: {test_outcome["automated_result"]} ({test_outcome["summary"]})'],
            known_issues=limitations + report['unresolved_concerns'],
            report_sha256=report_sha256,
            unresolved_concerns=report['unresolved_concerns'],
            known_change_ids=sorted(c['change_id'] for c in changes),
            open_blocker_ids=sorted(b['event_id'] for b in open_blockers),
            resolved_blocker_ids=sorted(b['event_id'] for b in all_blockers if b['event_id'] in resolved_ids),
            evidence_mode=evidence_mode,
        ))
        event_path = write_event(root, event)
        report.update(
            status='candidate_ready_for_human_review',
            candidate_id=event['event_id'],
            candidate_event_path=str(event_path.relative_to(root).as_posix()),
            candidate_report_path=CANDIDATE_MD,
            canonical_branch=canonical,
            canonical_commit=canon_sha,
            report_sha256=report_sha256,
            test_outcome=test_outcome,
            incorporated_changes=[c['change_id'] for c in incorporated],
            pending_changes=[c['change_id'] for c in pending],
            open_blockers=[b['event_id'] for b in open_blockers],
            resolved_blockers=event['resolved_blocker_ids'],
            evidence_mode=evidence_mode,
        )
    except (EventError, GitError, OSError, StatusError, ValueError) as exc:
        report['blockers'].append(str(exc))
    return report


def _validate_confirmation(path, candidate_event, current_sha256):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as exc:
        raise EventError(f'Cannot read confirmation file: {exc}') from exc
    if not isinstance(data, dict):
        raise EventError('Confirmation file must be a JSON object.')
    for field in ('candidate_id', 'canonical_commit', 'report_sha256',
                  'confirmed_by', 'confirmed_at', 'statement'):
        if not isinstance(data.get(field), str) or not data[field].strip():
            raise EventError(f'Confirmation missing or invalid field: {field}')
    if data['candidate_id'] != candidate_event['event_id']:
        raise EventError('Confirmation candidate_id does not match the prepared candidate.')
    if data['canonical_commit'] != candidate_event['canonical_commit']:
        raise EventError('Confirmation canonical_commit does not match the prepared candidate.')
    if data['report_sha256'].lower() != current_sha256.lower():
        raise EventError('Confirmation report_sha256 does not match candidate.md (candidate changed or stale).')
    confirmed_dt = datetime.fromisoformat(data['confirmed_at'].replace('Z', '+00:00'))
    if confirmed_dt.tzinfo is None:
        raise EventError('Confirmation confirmed_at must include a timezone.')
    created_dt = datetime.fromisoformat(candidate_event['created_at'].replace('Z', '+00:00'))
    if confirmed_dt < created_dt:
        raise EventError('Confirmation confirmed_at cannot be earlier than candidate created_at.')
    return data


def publish_final(directory, load_config, args):
    """Publish final_report.md, final.json, and final_result after candidate-bound human confirmation."""
    report = dict(
        operation='final_publish', status='blocked', verified=False,
        person_id=args.person_id, candidate_id=args.candidate_id, blockers=[],
    )
    try:
        if not args.confirm_human or not args.confirmation_file:
            raise EventError('FINAL --publish requires --confirm-human and a candidate-bound --confirmation-file.')
        root = repository_root(directory)
        config = load_config(root / '.group/group.json')
        canonical = config['canonical_branch']
        members = read_members(root)
        member_ids = {m['person_id'] for m in members}
        if args.person_id not in member_ids:
            raise EventError(f'Unknown member person_id: {args.person_id}')

        state = inspect_status(root, canonical, allow_missing_upstream=True)
        if state.branch != canonical:
            raise GitError(f'FINAL --publish requires checked-out canonical branch {canonical!r}.')
        canon_sha = _canonical_commit(root, canonical)

        cand_event_path = root / '.group/events' / f'{args.candidate_id}.json'
        if not cand_event_path.is_file():
            raise EventError(f'Candidate event not found locally: {cand_event_path}')
        candidate_event = load_event(cand_event_path)
        if candidate_event['event_type'] != 'final_candidate':
            raise EventError('Referenced event is not a final_candidate.')
        if candidate_event['canonical_commit'] != canon_sha:
            raise GitError('Canonical commit changed after candidate preparation; re-run final --prepare.')
        candidate_md_path = root / CANDIDATE_MD
        if not candidate_md_path.is_file():
            raise EventError(f'Candidate report missing: {CANDIDATE_MD}')
        current_sha256 = sha256_file(candidate_md_path)
        if candidate_event.get('report_sha256') and candidate_event['report_sha256'] != current_sha256:
            raise EventError('candidate.md changed after candidate preparation; re-run final --prepare.')

        # Re-scan group events to detect new proposals or open blockers introduced after --prepare.
        known_now = collect_known_events(root, config['group_id'])
        if 'known_change_ids' in candidate_event:
            prev_changes = set(candidate_event['known_change_ids'])
            new_pending = []
            for ch in known_now.values():
                if ch['event_type'] == 'change_published' and ch['change_id'] not in prev_changes:
                    try:
                        wsha = resolve_work(root, ch['commit'], len(canon_sha))
                        is_inc = ancestor(root, wsha, canon_sha)
                    except (EventError, GitError):
                        is_inc = False
                    if not is_inc:
                        new_pending.append(ch['change_id'])
            if new_pending:
                raise EventError(
                    'New unincorporated change(s) appeared after candidate preparation; '
                    're-run final --prepare: ' + ', '.join(sorted(new_pending))
                )
        if 'open_blocker_ids' in candidate_event:
            prev_blockers = set(candidate_event['open_blocker_ids'])
            resolved_now = _resolved_blocker_ids(known_now)
            new_blockers = sorted(
                e['event_id'] for e in known_now.values()
                if e['event_type'] == 'blocker'
                and e['event_id'] not in resolved_now
                and e['event_id'] not in prev_blockers
            )
            if new_blockers:
                raise EventError(
                    'New open blocker(s) appeared after candidate preparation; '
                    're-run final --prepare: ' + ', '.join(new_blockers)
                )

        confirmation = _validate_confirmation(args.confirmation_file, candidate_event, current_sha256)
        if confirmation['confirmed_by'] not in member_ids:
            raise EventError(f'Confirmation confirmed_by is not a registered member: {confirmation["confirmed_by"]}')

        unresolved = list(candidate_event.get('unresolved_concerns') or [])
        limitations = list(args.limitation or [])
        if unresolved and not args.allow_known_limitations:
            raise EventError(
                'Unresolved concerns remain in candidate; resolve them or explicitly pass '
                '--allow-known-limitations with --limitation: ' + '; '.join(unresolved)
            )
        all_limitations = limitations + [u for u in unresolved if u not in limitations]
        final_status = 'completed_with_known_limitations' if all_limitations else 'completed'
        evidence_mode = confirmation.get('evidence_mode', candidate_event.get('evidence_mode', 'human'))

        final_md_text = candidate_md_path.read_text(encoding='utf-8').replace(
            '# Group Final Report (Candidate)', '# Group Final Report', 1
        )
        final_md_text += (
            '\n## Human Confirmation\n'
            f'- Candidate ID: `{candidate_event["event_id"]}`\n'
            f'- Confirmed by: `{confirmation["confirmed_by"]}`\n'
            f'- Confirmed at: `{confirmation["confirmed_at"]}`\n'
            f'- Statement: {confirmation["statement"]}\n'
            f'- Evidence mode: `{evidence_mode}`\n'
        )
        final_report_path = root / FINAL_REPORT_MD
        final_report_path.write_text(final_md_text, encoding='utf-8', newline='\n')

        now, created_at = utc_now()
        final_envelope = dict(
            protocol_version='0.1',
            group_id=config['group_id'],
            canonical_branch=canonical,
            canonical_commit=canon_sha,
            report_file=FINAL_REPORT_MD,
            initiated_by=args.person_id,
            created_at=created_at,
            status=final_status,
            known_limitations=all_limitations,
            confirmed_by=confirmation['confirmed_by'],
            confirmed_at=confirmation['confirmed_at'],
            evidence_mode=evidence_mode,
        )
        final_json_path = root / FINAL_JSON
        final_json_path.write_text(json.dumps(final_envelope, indent=2, ensure_ascii=True) + '\n',
                                   encoding='utf-8', newline='\n')

        event = validate_event(dict(
            protocol_version='0.1',
            event_id=new_event_id(args.person_id, now),
            event_type='final_result',
            group_id=config['group_id'],
            person_id=args.person_id,
            created_at=created_at,
            related_events=[candidate_event['event_id']],
            canonical_commit=canon_sha,
            final_report_path=FINAL_REPORT_MD,
            test_evidence=candidate_event['tests_evidence'],
            known_limitations=all_limitations,
            initiated_by=args.person_id,
            final_status=final_status,
            evidence_mode=evidence_mode,
        ))

        extra_files = [
            f'.group/events/{candidate_event["event_id"]}.json',
            FINAL_REPORT_MD,
            FINAL_JSON,
        ]
        # Remove temporary candidate.md? Keep candidate.md out of final commit or leave untracked.
        commit_rep = commit_event_file(
            root, load_config, args.person_id, event,
            message=f'Publish group final result for {canon_sha[:12]} ({final_status})',
            allow_canonical=True,
            extra_files=extra_files,
            check_related=True,
        )
        report.update(
            status='local_final_committed',
            final_status=final_status,
            canonical_branch=canonical,
            canonical_commit=canon_sha,
            final_report_path=FINAL_REPORT_MD,
            final_json_path=FINAL_JSON,
            event_id=event['event_id'],
            event_commit=commit_rep['commit'],
            evidence_mode=evidence_mode,
        )
        if args.remote:
            if not args.expected_url:
                raise GitError('Pushing final result requires --remote and --expected-url.')
            push_rep = push_and_verify_event(
                root, branch=commit_rep['branch'], commit=commit_rep['commit'],
                parent=commit_rep['parent'], event_id=event['event_id'],
                event_path=commit_rep['event_path'], event_blob=commit_rep['event_blob'],
                remote=args.remote, expected_url=args.expected_url,
                expected_remote_head=args.expected_remote_head,
            )
            report['push'] = push_rep
            report['verified'] = push_rep['verified']
            report['status'] = push_rep['result']
    except (EventError, GitError, OSError, StatusError, ValueError) as exc:
        report['blockers'].append(str(exc))
    return report
