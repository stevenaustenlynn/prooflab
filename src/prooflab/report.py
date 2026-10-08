"""Deterministic Markdown over verified, manifest-bound records; no execution."""

from collections import Counter
import json
from pathlib import Path
import unicodedata

from .artifacts import open_payload
from .comparison import source_records
from .integrity import sha256_bytes
from .protocol import ExecutionState, IntegrityState
from .records import strict_loads
from .schema import validate_record
from .verify import verify_package

DISPLAY_LIMIT = 256


class ReportError(ValueError):
    """Fixed, path-free diagnostic with the authoritative verification status."""

    def __init__(self, state, message):
        super().__init__(message)
        self.state = state


def scalar(value):
    """Code spans disable automatic links; escapes cannot close spans or tables."""
    if value is None:
        return 'not recorded / unavailable'
    truncated = isinstance(value, str) and len(value) > DISPLAY_LIMIT
    if isinstance(value, str):
        value = value[:DISPLAY_LIMIT]
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
    safe = ''.join(f'\\u{ord(c):04x}' if c in '`|<>&' or unicodedata.category(c)[0] == 'C'
                   or c in '\u2028\u2029' else c for c in raw)
    return '`' + safe + '`' + (' [truncated after 256 code points]' if truncated else '')


def table(lines, headings, rows):
    lines.extend(['', '| ' + ' | '.join(headings) + ' |',
                  '| ' + ' | '.join('---' for _ in headings) + ' |'])
    lines.extend('| ' + ' | '.join(scalar(v) for v in row) + ' |' for row in rows)
    lines.append('')


def field(lines, label, value):
    if lines and lines[-1] and not lines[-1].startswith('- '):
        lines.append('')
    lines.append(f'- {label}: {scalar(value)}')


class _Records:
    """Rereads are bound to the exact manifest accepted by the verifier."""

    def __init__(self, root, digest):
        self.root = root
        raw = self.raw('manifest.json')
        if sha256_bytes(raw) != digest:
            raise ValueError('manifest changed')
        self.manifest = strict_loads(raw)
        validate_record(self.manifest, 'manifest')
        self.entries = {e['path']: e for e in self.manifest['files']}

    def raw(self, path):
        with open_payload(self.root, path) as stream:
            return stream.read()

    def read(self, path):
        raw = self.raw(path)
        entry = self.entries[path]
        if len(raw) != entry['size_bytes'] or sha256_bytes(raw) != entry['sha256']:
            raise ValueError('record changed')
        return raw

    def record(self, path, kind=None):
        value = strict_loads(self.read(path))
        if kind:
            validate_record(value, kind)
        return value


def _run(lines, records):
    run = records.record('run.json', 'run')
    protocol = records.record('protocol.json', 'protocol')
    cases = sorted((records.record(p, 'case') for p in run['case_records']),
                   key=lambda c: (c['case_id'], c['trial']))
    outputs = [o for c in cases for o in c.get('outputs', [])]
    measurements = [m for c in cases for m in c['measurements']]
    lines.extend(['## Summary', '', 'Operational state: ' + scalar(run['execution_state']) + '.', '',
                  '**Output and measurement attention (all retained trials):**', ''])
    for label, count in (
        ('Required-output absences', sum(o['reason'] == 'required_output_missing' for o in outputs)),
        ('Optional-output absences', sum(o['reason'] == 'optional_output_missing' for o in outputs)),
        ('Output capture errors', sum(o['state'] == 'error' and o['reason'] != 'required_output_missing' for o in outputs)),
        ('Measurement errors', sum(m['state'] == 'error' for m in measurements))):
        field(lines, label, count if label == 'Measurement errors' or
              ('outputs' in protocol and all('outputs' in c for c in cases)) else None)
    lines.extend(['', 'Command completion and VERIFIED integrity do not establish scientific success.', '',
                  '## Identity and scope', ''])
    for label, value in [('Run ID', run['id']), ('Experiment / protocol ID', protocol['id']),
                         ('Selected variant', run['variant']),
                         ('Exact protocol record SHA-256', records.entries['protocol.json']['sha256'])]:
        field(lines, label, value)
    execution = run.get('execution', {})
    for key in ('protocol_sha256', 'plan_sha256', 'inventory_sha256', 'snapshot_sha256'):
        field(lines, key, execution.get(key))
    lines.extend(['', '## Execution accounting', ''])
    field(lines, 'Planned cases', len(protocol['cases']))
    field(lines, 'Planned trials', sum(c['trials'] for c in protocol['cases']))
    field(lines, 'Retained trial records', len(cases))
    field(lines, 'Cases represented', len({c['case_id'] for c in cases}))
    counts = Counter(c['execution_state'] for c in cases)
    table(lines, ['Trial state', 'Count'], [(s.value, counts[s.value]) for s in ExecutionState])
    field(lines, 'Unfinished trial states (planned/running)', counts['planned'] + counts['running'])
    lines.append('')
    lines.append('Trial record accounting is complete. Execution receipts: ' +
                 ('recorded.' if execution else 'not recorded; legacy states are retained assertions.'))
    table(lines, ['Case', 'Trial', 'State', 'Exit code', 'Elapsed ns', 'Reason'],
          [(c['case_id'], c['trial'], c['execution_state'], c.get('execution', {}).get('exit_code'),
            c.get('execution', {}).get('elapsed_ns'), c.get('execution', {}).get('reason')) for c in cases])
    lines.extend(['Timing is a retained operational observation, not a statistically established performance result.', '',
                  '## Outputs and measurements', ''])
    if 'outputs' not in protocol:
        lines.append('Output declarations and coverage: not recorded (legacy package).')
    else:
        field(lines, 'Declared outputs per trial', len(protocol['outputs']))
        field(lines, 'Expected output observations', len(protocol['outputs']) * len(cases))
        field(lines, 'Retained output observations', len(outputs))
        table(lines, ['Output ID', 'Declared logical path', 'Required'],
              [(o['id'], o['path'], o['required']) for o in sorted(protocol['outputs'], key=lambda o: o['id'])])
    table(lines, ['Output state', 'Count'], sorted(Counter(o['state'] for o in outputs).items()))
    if outputs:
        table(lines, ['Case', 'Trial', 'Output', 'Path', 'Required', 'State', 'Reason', 'Artifact ID', 'SHA-256'],
              [(c['case_id'], c['trial'], o['id'], o['path'], o['required'], o['state'], o['reason'],
                o['artifact_id'], o['sha256']) for c in cases for o in sorted(c.get('outputs', []), key=lambda o: o['id'])])
    field(lines, 'Retained measurements', len(measurements))
    table(lines, ['Measurement state', 'Count'], sorted(Counter(m['state'] for m in measurements).items()))
    if 'measurements' not in protocol:
        lines.append('Measurement declarations, types, units and extraction provenance: not recorded for legacy measurements.')
    else:
        field(lines, 'Declared measurements per trial', len(protocol['measurements']))
        field(lines, 'Expected measurement observations', len(protocol['measurements']) * len(cases))
    table(lines, ['Case', 'Trial', 'Measurement', 'State', 'Type', 'Value', 'Unit', 'Reason', 'Source', 'Artifact ID'],
          [(c['case_id'], c['trial'], m['id'], m['state'], m.get('type'), m['value'], m.get('unit'),
            m.get('reason'), m.get('source'), m.get('source_artifact'))
           for c in cases for m in sorted(c['measurements'], key=lambda m: m['id'])])
    lines.extend(['Missing values remain unavailable; they are never zero.', '', '### Evidence references', ''])
    artifacts = {a['id']: a['path'] for a in run['artifacts']}
    table(lines, ['Artifact ID', 'Package-relative logical path', 'SHA-256'],
          [(identifier, path, records.entries[path]['sha256']) for identifier, path in sorted(artifacts.items())])
    lines.extend(['## Limits and provenance', ''])
    if execution:
        lines.append('No acceptance policy is recorded for this run. Reporting applies no acceptance policy; stored run acceptance labels are not recomputed.')
        field(lines, 'Working directory semantics', execution['cwd'])
        field(lines, 'Environment semantics', execution['environment'])
        # Provenance is only inventory-bound, not a closed validated fact schema.
        # Do not promote arbitrary provenance contents to verified system facts.
        lines.append('')
        lines.append('Provenance artifact is retained and inventory-bound. Its environment/Git assertions are not independently verified or reproduced here.')
        table(lines, ['Captured material role', 'Declared count'],
              sorted(Counter(m['role'] for m in protocol['execution']['materials'] if m['capture']).items()))
        omitted = [m for m in protocol['execution']['materials'] if not m['capture']]
        field(lines, 'Declared omitted materials', len(omitted))
        table(lines, ['Declared omitted material', 'Role', 'Reason'],
              [(m['id'], m['role'], m['reason']) for m in omitted])
    else:
        lines.append('Acceptance policy: not recorded. Legacy acceptance labels are not recomputed and are not scientific findings of this report. Execution and provenance identities: not recorded.')
    lines.extend(['', 'Captured declared materials bind source/input/evaluator bytes where recorded. External executable binaries, installed libraries, inherited environment values and undeclared inputs are outside captured identity. Execution is not hermetic or authenticated.',
                  'Verification does not prove historical execution, correctness, causality or reproducibility. Legacy numeric measurements are retained assertions; supported built-in measurements are recomputed from retained evidence.'])


def _comparison(lines, records):
    result = records.record('comparison.json', 'comparison')
    policy = records.record('policy.json')
    sources = {role: source_records(lambda p, role=role: records.read('sources/' + role + '/' + p))
               for role in ('baseline', 'candidate')}
    lines.extend(['## Summary', '', 'Compatibility: ' + scalar(result['compatibility']['state']) + '.', ''])
    if result['compatibility']['state'] == 'COMPATIBLE':
        lines.append('Scientific result under retained policy: **' + result['verdict'] + '**.')
    else:
        lines.append('Scientific result: unavailable / inapplicable because inputs are incompatible. No scientific verdict was evaluated.')
    lines.extend(['', '## Identity', ''])
    field(lines, 'Comparison ID', result['id'])
    field(lines, 'Retained creation timestamp', result['created_at'])
    for role in ('baseline', 'candidate'):
        field(lines, role + ' run ID', result[role]['run_id'])
        field(lines, role + ' source-manifest SHA-256', result[role]['manifest_sha256'])
    for key, label in [('id', 'Policy ID'), ('sha256', 'Original-policy SHA-256'),
                       ('normalized_sha256', 'Normalized-policy SHA-256')]:
        field(lines, label, result['policy'].get(key))
    field(lines, 'Policy description', policy['description'])
    lines.extend(['', '## Compatibility and population', ''])
    for role, source in sources.items():
        field(lines, role + ' expected cases', len(source['protocol']['cases']))
        field(lines, role + ' expected trials', sum(c['trials'] for c in source['protocol']['cases']))
    field(lines, 'Paired trials', result['paired_trials'])
    field(lines, 'Retained rule-pair rows (all outcomes)', len(result['pairs']))
    lines.append('')
    lines.append('Failed check IDs are the recorded incompatibility reasons; no free-text reasons are recorded.')
    table(lines, ['Compatibility check', 'Passed'],
          [(c['id'], c['passed']) for c in result['compatibility']['checks']])
    lines.extend(['## Rules and operands', '',
                  'Required and optional results are shown separately by obligation. Optional failures do not become required failures. Counts include unavailable and inapplicable pairs.', ''])
    outcomes = {r['rule_id']: r for r in result['rules']}
    definitions = {m['id']: m for m in sources['baseline']['protocol']['measurements']}
    for rule in policy['rules']:
        lines.extend(['### Rule ' + scalar(rule['id']), ''])
        for key in ('measurement', 'operator', 'required', 'description'):
            field(lines, key.capitalize(), rule[key])
        field(lines, 'Threshold (absent means inapplicable)', rule.get('threshold'))
        definition = definitions.get(rule['measurement'], {})
        field(lines, 'Baseline measurement type', definition.get('type'))
        field(lines, 'Baseline measurement unit', definition.get('unit'))
        candidate_definition = next((m for m in sources['candidate']['protocol']['measurements']
                                     if m['id'] == rule['measurement']), {})
        field(lines, 'Candidate measurement type', candidate_definition.get('type'))
        field(lines, 'Candidate measurement unit', candidate_definition.get('unit'))
        outcome = outcomes.get(rule['id'])
        if outcome is None:
            lines.append('')
            lines.append('Rule result and operands: unavailable / inapplicable (incompatible inputs).')
            continue
        field(lines, 'Rule result', outcome['state'])
        field(lines, 'Pair denominator (all outcomes)', sum(outcome['counts'].values()))
        table(lines, ['Pair result', 'Count'], sorted(outcome['counts'].items()))
        pairs = sorted((p for p in result['pairs'] if p['rule_id'] == rule['id']), key=lambda p: (p['case_id'], p['trial']))
        if not pairs:
            lines.append('No paired population; no operands available. Empty populations do not imply PASS.')
        table(lines, ['Case', 'Trial', 'Seed', 'Baseline state', 'Baseline value', 'Baseline reason',
                      'Candidate state', 'Candidate value', 'Candidate reason', 'Result', 'Reason'],
              [(p['case_id'], p['trial'], p['seed'], p['baseline']['state'], p['baseline']['value'], p['baseline']['reason'],
                p['candidate']['state'], p['candidate']['value'], p['candidate']['reason'], p['state'], p['reason']) for p in pairs])
    lines.extend(['## Verification scope and limits', '',
                  'This comparison package is VERIFIED: the verifier recomputed compatibility, pair outcomes, rule aggregation and the overall result from retained operands and policy.',
                  'Original source packages are not rechecked by report. Copied source manifests and record excerpts do not substitute for complete original packages, including their outputs and materials. Full source lineage requires separately verifying those complete packages against the source-manifest digests above.',
                  'No claim is made about baseline correctness, historical execution, causality, statistical significance or universal candidate superiority. Timing operands remain retained operational observations. Reporting explains the existing aggregation; it applies no new analysis or acceptance policy.'])


def render_report(directory):
    """Return complete Markdown only after verification and final change checks.

    Callers must keep the directory stable. Two checks are not an atomic snapshot.
    """
    root = Path(directory)
    verification = verify_package(root)
    if verification.state != IntegrityState.VERIFIED:
        raise ReportError(verification.state, 'input package did not pass verification; no trusted report emitted')
    try:
        records = _Records(root, verification.manifest_sha256)
        kind = records.manifest.get('package_type', 'run')
        lines = ['# ProofLab ' + kind + ' report', '',
                 'Current verification: **VERIFIED** — retained byte integrity and supported record relationships.', '',
                 'Manifest SHA-256: ' + scalar(verification.manifest_sha256), '']
        (_comparison if kind == 'comparison' else _run)(lines, records)
        lines.extend(['', '## Display and trust boundary', '',
                      'Strings display at most 256 Unicode code points each; truncation is labeled. Counts cover complete records. Scalars use JSON spelling in code spans; unsafe delimiters and controls use visible escapes. No source, stream or output-file contents are dumped.',
                      'Displayed user-authored strings may contain sensitive information. Escaping is not secret redaction.',
                      'Verification assumes a stable directory; reads are not an atomic snapshot. The local manifest sidecar is a consistency check, not an independent trust anchor. Report generation is not scientific acceptance.', ''])
        markdown = '\n'.join(lines)
        final = verify_package(root, verification.manifest_sha256)
        if final.state != IntegrityState.VERIFIED:
            raise ValueError('package changed')
        return markdown
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        raise ReportError(IntegrityState.INVALID, 'package changed or records became unreadable during reporting; no trusted report emitted') from exc


def write_report(directory, destination, markdown):
    """Exclusive external file creation; resolving parents also catches aliases."""
    root = Path(directory).resolve(strict=True)
    target = Path(destination)
    parent = target.parent.resolve(strict=True)
    resolved = parent / target.name
    if resolved.is_relative_to(root):
        raise ValueError('report destination is inside input package')
    # x rejects regular files, directories and dangling or live symlinks alike.
    with resolved.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(markdown)
