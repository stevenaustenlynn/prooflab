"""Deterministic comparison of retained data. Never imports experiment code."""

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from pathlib import Path
import uuid

from .artifacts import RESERVED, check_aliases, open_payload
from .comparison_policy import parse_policy, validate_policy
from .execution_evidence import MATERIAL, SNAPSHOT
from .integrity import build_manifest, sha256_bytes
from .materials import checked_root
from .measurements import VALUE_TYPES
from .records import canonical_json, strict_loads
from .schema import SchemaError, UnsupportedSchema, _validate, validate_record
from .verify import verify_package


class ComparisonError(ValueError):
    """Invalid/unsupported input; diagnostics contain no host locations."""


def require(condition, message):
    if not condition:
        raise SchemaError(message)


def same(a, b):
    return canonical_json(a) == canonical_json(b)


def read_bytes(root, relative):
    with open_payload(root, relative) as stream:
        return stream.read()


def source_records(read):
    """Read the small source subset, checking exact bytes against its manifest.

    This is NOT full source verification: outputs and material bytes are absent.
    The caller verifies complete sources before creating a comparison.
    """
    raw_manifest = read('manifest.json')
    manifest = strict_loads(raw_manifest)
    validate_record(manifest, 'manifest')
    require(manifest.get('package_type', 'run') == 'run', 'source must be a run package')
    require(canonical_json(manifest) == raw_manifest, 'source manifest encoding')
    paths = [e['path'] for e in manifest['files']]
    check_aliases(paths + sorted(RESERVED))
    require(paths == sorted(paths), 'source manifest order')
    entries = {e['path']: e for e in manifest['files']}
    retained = {'manifest.json': raw_manifest}

    def record(relative, role, kind=None):
        require(relative in entries and entries[relative]['role'] == role, 'source record manifest reference')
        raw = read(relative)
        entry = entries[relative]
        require(len(raw) == entry['size_bytes'] and sha256_bytes(raw) == entry['sha256'],
                'source record differs from source manifest')
        retained[relative] = raw
        value = strict_loads(raw)
        if kind:
            validate_record(value, kind)
        return value

    run = record('run.json', 'run', 'run')
    protocol = record('protocol.json', 'protocol', 'protocol')
    if 'execution' not in run or 'execution' not in protocol or not protocol.get('measurements'):
        raise UnsupportedSchema('comparison requires execution identity and declared typed measurements')
    require(run['protocol_id'] == protocol['id'] and run['variant'] in protocol['variants'],
            'source protocol/variant reference')
    require(run['execution']['protocol_sha256'] == sha256_bytes(canonical_json(protocol)),
            'source protocol identity')
    artifacts = {a['id']: a['path'] for a in run['artifacts']}
    require(len(artifacts) == len(run['artifacts']), 'source duplicate artifact ID')
    check_aliases(['protocol.json', 'run.json', *run['case_records'], *artifacts.values()])
    snapshot_path = artifacts[run['execution']['snapshot_artifact']]
    snapshot = record(snapshot_path, 'artifact')
    _validate(snapshot, SNAPSHOT, 'source.snapshot')
    require(sha256_bytes(retained[snapshot_path]) == run['execution']['snapshot_sha256'],
            'source snapshot identity')
    materials = [{k: m[k] for k in MATERIAL['properties']} for m in snapshot['materials']]
    require(sha256_bytes(canonical_json(materials)) == snapshot['inventory_sha256'] ==
            run['execution']['inventory_sha256'], 'source material inventory identity')
    require(len({m['id'] for m in materials}) == len(materials), 'source duplicate material ID')
    check_aliases([m['path'] for m in materials])
    require([{k: m[k] for k in ('id', 'path', 'role')} for m in materials] ==
            [{k: m[k] for k in ('id', 'path', 'role')} for m in protocol['execution']['materials']
             if m['capture']], 'source material declarations')
    planned = {c['id']: c['trials'] for c in protocol['cases']}
    require(len(planned) == len(protocol['cases']), 'source duplicate case ID')
    cases = {}
    ids = set()
    definitions = {m['id']: m for m in protocol['measurements']}
    bindings = {b['case_id']: b for b in protocol['execution']['bindings']}
    for relative in run['case_records']:
        case = record(relative, 'case', 'case')
        key = (case['case_id'], case['trial'])
        require(key not in cases and case['id'] not in ids, 'source duplicate trial/record ID')
        require(case['run_id'] == run['id'] and key[0] in planned and key[1] <= planned[key[0]],
                'source unplanned trial or run reference')
        require('execution' in case, 'source missing execution receipt')
        for field in ('protocol_sha256', 'plan_sha256', 'inventory_sha256', 'snapshot_sha256'):
            require(case['execution'][field] == run['execution'][field], 'source receipt identity')
        seed = bindings[key[0]]['seed']
        require(case['execution']['seed'] == (None if seed is None else seed + key[1] - 1),
                'source trial seed semantics')
        seen = set()
        for m in case['measurements']:
            require(m['id'] not in seen and m['id'] in definitions, 'source measurement ID')
            seen.add(m['id'])
            definition = definitions[m['id']]
            require(all(k in m for k in definition) and same({k: m[k] for k in definition}, definition),
                    'source measurement definition')
            if m['state'] == 'observed':
                require(type(m['value']) is VALUE_TYPES[m['type']], 'source measurement value type')
            else:
                require(m['value'] is None, 'unobserved source measurement has value')
        cases[key] = case
        ids.add(case['id'])
    require(len(cases) == sum(planned.values()), 'source missing expected trial')
    return {'run': run, 'protocol': protocol, 'materials': materials, 'cases': cases,
            'identity': {'run_id': run['id'], 'manifest_sha256': sha256_bytes(raw_manifest)},
            'retained': retained}


def compatibility(baseline, candidate, policy):
    checks = []

    def check(identifier, a, b):
        checks.append({'id': identifier, 'passed': same(a, b)})

    a, b = baseline['protocol'], candidate['protocol']
    check('schema_versions', [baseline['run']['schema'], a['schema']],
          [candidate['run']['schema'], b['schema']])
    check('experiment_id', a['id'], b['id'])
    check('protocol_identity', sha256_bytes(canonical_json(a)), sha256_bytes(canonical_json(b)))
    check('case_identities', sorted(c['id'] for c in a['cases']), sorted(c['id'] for c in b['cases']))
    # JSON has no tuple type; render identity keys explicitly.
    check('trial_population', [list(k) for k in sorted(baseline['cases'])],
          [list(k) for k in sorted(candidate['cases'])])
    check('trial_seed_semantics', a['execution']['bindings'], b['execution']['bindings'])
    for role in ('input', 'evaluator'):
        check(role + '_identities', [m for m in baseline['materials'] if m['role'] == role],
              [m for m in candidate['materials'] if m['role'] == role])
    definitions = [{m['id']: m for m in p['measurements']} for p in (a, b)]
    for identifier in sorted({r['measurement'] for r in policy['rules']}):
        left, right = (d.get(identifier) for d in definitions)
        if left is None and right is None:
            raise ComparisonError('policy: unknown measurement ' + identifier)
        check('measurement.' + identifier + '.definition', left, right)
        check('measurement.' + identifier + '.type', left and left['type'], right and right['type'])
        check('measurement.' + identifier + '.unit', left and left['unit'], right and right['unit'])
    return checks


def operand(case, identifier):
    m = next((m for m in case['measurements'] if m['id'] == identifier), None)
    return ({'state': 'missing', 'value': None, 'reason': 'expected_measurement_absent'} if m is None
            else {k: m[k] for k in ('state', 'value', 'reason')})


def evaluate_pair(rule, baseline, candidate):
    states = (baseline['state'], candidate['state'])
    if states == ('not_applicable', 'not_applicable'):
        return 'NOT_APPLICABLE', 'both_not_applicable'
    if states != ('observed', 'observed'):
        return 'INCONCLUSIVE', 'operand_unavailable'
    a, b = baseline['value'], candidate['value']
    require(type(a) is type(b), 'operand types differ')
    op = rule['operator']
    if op == 'equal':
        passed = b == a
    else:
        require(type(a) in (int, float), 'numeric operator requires numeric measurement')
        if op == 'candidate_gte_baseline':
            passed = b >= a
        elif op == 'candidate_lte_baseline':
            passed = b <= a
        else:
            try:
                delta = b - a
                if type(delta) is float and not math.isfinite(delta):
                    return 'INCONCLUSIVE', 'nonfinite_delta'
                if op == 'delta_gte':
                    passed = delta >= rule['threshold']
                elif op == 'delta_lte':
                    passed = delta <= rule['threshold']
                else:
                    passed = abs(delta) <= rule['threshold']
            except OverflowError:
                return 'INCONCLUSIVE', 'nonfinite_delta'
    return ('PASS' if passed else 'FAIL'), 'evaluated'


def aggregate(states):
    if 'FAIL' in states:
        return 'FAIL'
    if 'INCONCLUSIVE' in states:
        return 'INCONCLUSIVE'
    if 'PASS' in states:
        return 'PASS'
    return 'NOT_APPLICABLE'


def derive(baseline, candidate, policy):
    validate_policy(policy)
    checks = compatibility(baseline, candidate, policy)
    compatible = all(c['passed'] for c in checks)
    pairs, rules = [], []
    if compatible:
        definitions = {m['id']: m for m in baseline['protocol']['measurements']}
        for rule in policy['rules']:
            definition = definitions[rule['measurement']]
            if rule['operator'] != 'equal' and definition['type'] not in ('integer', 'float'):
                raise ComparisonError('policy: numeric operator requires numeric measurement')
            outcomes = []
            for key in sorted(baseline['cases']):
                left = operand(baseline['cases'][key], rule['measurement'])
                right = operand(candidate['cases'][key], rule['measurement'])
                state, reason = evaluate_pair(rule, left, right)
                outcomes.append(state)
                pairs.append({'rule_id': rule['id'], 'case_id': key[0], 'trial': key[1],
                              'seed': baseline['cases'][key]['execution']['seed'],
                              'baseline': left, 'candidate': right, 'state': state, 'reason': reason})
            rules.append({'rule_id': rule['id'], 'required': rule['required'], 'state': aggregate(outcomes),
                          'counts': {s: outcomes.count(s) for s in ('PASS', 'FAIL', 'INCONCLUSIVE', 'NOT_APPLICABLE')}})
        required = [r['state'] for r in rules if r['required']]
        overall = ('FAIL' if 'FAIL' in required else
                   'PASS' if required and all(s == 'PASS' for s in required) else 'INCONCLUSIVE')
    else:
        overall = 'INCOMPATIBLE'
    return {'baseline': baseline['identity'], 'candidate': candidate['identity'],
            'compatibility': {'state': 'COMPATIBLE' if compatible else 'INCOMPATIBLE', 'checks': checks},
            'paired_trials': len(baseline['cases']) if compatible else 0,
            'pairs': pairs, 'rules': rules, 'verdict': overall}


def check_comparison(read, entries):
    record = strict_loads(read('comparison.json'))
    validate_record(record, 'comparison')
    policy_raw = read('policy.toml')
    policy = parse_policy(policy_raw)
    require(read('policy.json') == canonical_json(policy), 'normalized policy differs from original policy')
    sources = [source_records(lambda p, role=role: read('sources/' + role + '/' + p))
               for role in ('baseline', 'candidate')]
    expected = {'schema': 'prooflab.comparison/v1', 'id': record['id'], 'created_at': record['created_at'],
                'policy': policy_identity(policy_raw, policy), **derive(*sources, policy)}
    require(same(record, expected), 'comparison recomputation mismatch')
    try:
        require(datetime.fromisoformat(record['created_at']).utcoffset() is not None, 'comparison timestamp timezone')
    except ValueError:
        raise SchemaError('comparison timestamp invalid') from None
    roles = {'comparison.json': 'comparison', 'policy.toml': 'artifact', 'policy.json': 'artifact'}
    for role, source in zip(('baseline', 'candidate'), sources):
        roles.update({'sources/' + role + '/' + p: 'artifact' for p in source['retained']})
    require({p: e['role'] for p, e in entries.items()} == roles, 'comparison file/role accounting mismatch')


def policy_identity(raw, policy):
    return {'id': policy['id'], 'sha256': sha256_bytes(raw),
            'normalized_sha256': sha256_bytes(canonical_json(policy))}


@dataclass(frozen=True)
class ComparisonResult:
    record: dict
    directory: Path
    sealed: bool
    exit_code: int


def compare_runs(baseline, candidate, policy_path, *, output_root=None):
    roots = [checked_root(p) for p in (baseline, candidate)]
    verifications = []
    for role, root in zip(('baseline', 'candidate'), roots):
        verification = verify_package(root)
        if verification.state != 'verified':
            raise ComparisonError(role + ' package: ' + verification.state.value.upper())
        verifications.append(verification)
    sources = []
    for role, root, verification in zip(('baseline', 'candidate'), roots, verifications):
        try:
            source = source_records(lambda p: read_bytes(root, p))
        except (ValueError, KeyError, OSError) as exc:
            raise ComparisonError(role + ': unsupported or changed source records') from exc
        require(source['identity']['manifest_sha256'] == verification.manifest_sha256,
                role + ': source changed after verification')
        sources.append(source)
    policy_path = Path(policy_path).absolute()
    raw_policy = read_bytes(checked_root(policy_path.parent), policy_path.name)
    policy = parse_policy(raw_policy)
    identity = policy_identity(raw_policy, policy)
    derived = derive(*sources, policy)
    # Destination choice is operational only; it never changes scientific semantics.
    parent = checked_root(Path.cwd() if output_root is None else output_root)
    destination = parent / '.prooflab' / 'comparisons'
    require(not any(destination.is_relative_to(root) for root in roots), 'output directory is inside a source package')
    for component in ('.prooflab', 'comparisons'):
        parent = parent / component
        parent.mkdir(exist_ok=True)
        checked_root(parent)
    identifier = 'comparison-' + uuid.uuid4().hex
    package = parent / identifier
    package.mkdir()
    record = {'schema': 'prooflab.comparison/v1', 'id': identifier,
              'created_at': datetime.now(timezone.utc).isoformat(timespec='microseconds'),
              'policy': identity, **derived}
    try:
        payloads = {'comparison.json': canonical_json(record), 'policy.toml': raw_policy,
                    'policy.json': canonical_json(policy)}
        for role, source in zip(('baseline', 'candidate'), sources):
            payloads.update({'sources/' + role + '/' + p: data for p, data in source['retained'].items()})
        for relative, data in payloads.items():
            target = package / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as stream:
                stream.write(data)
        roles = {p: 'comparison' if p == 'comparison.json' else 'artifact' for p in payloads}
        manifest, sidecar = build_manifest(package, roles, package_type='comparison')
        (package / 'manifest.json').write_bytes(manifest)
        (package / 'manifest.sha256').write_bytes(sidecar)
        sealed = verify_package(package).state == 'verified'
    except (ValueError, OSError, KeyboardInterrupt):
        sealed = False
    return ComparisonResult(record, package, sealed,
                            {'PASS': 0, 'FAIL': 1, 'INCONCLUSIVE': 3, 'INCOMPATIBLE': 2}[record['verdict']]
                            if sealed else 4)
