"""Closed declarative policy; no expressions, code, or ambient configuration."""

import tomllib

from .records import canonical_json
from .schema import SchemaError, validate_record

OPERATORS = ('equal', 'candidate_gte_baseline', 'candidate_lte_baseline',
             'delta_gte', 'delta_lte', 'abs_delta_lte')
THRESHOLD_OPERATORS = ('delta_gte', 'delta_lte', 'abs_delta_lte')


def validate_policy(policy):
    validate_record(policy, 'comparison-policy')
    if len({r['id'] for r in policy['rules']}) != len(policy['rules']):
        raise SchemaError('policy: duplicate rule ID')
    for rule in policy['rules']:
        if ('threshold' in rule) != (rule['operator'] in THRESHOLD_OPERATORS):
            raise SchemaError('policy: threshold required only for delta operators')
        if rule['operator'] == 'abs_delta_lte' and rule['threshold'] < 0:
            raise SchemaError('policy: absolute tolerance must be nonnegative')
    canonical_json(policy)  # Also rejects TOML-only values and nonfinite numbers.


def parse_policy(raw):
    try:
        policy = tomllib.loads(raw.decode('utf-8', errors='strict'))
    except (UnicodeError, tomllib.TOMLDecodeError):
        raise SchemaError('policy: invalid UTF-8 TOML') from None
    policy.setdefault('description', '')
    if type(policy.get('rules')) is list:
        for rule in policy['rules']:
            if type(rule) is dict:
                rule.setdefault('required', True)
                rule.setdefault('description', '')
    validate_policy(policy)
    policy['rules'].sort(key=lambda rule: rule['id'])
    return policy
