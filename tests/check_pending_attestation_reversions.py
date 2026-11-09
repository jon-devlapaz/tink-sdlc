import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TEST = 'test_sdlc_pending_attestation.PendingAttestation.'
CASES = [
    ('nonzero', 'test_commands_run_and_log_before_nonzero_attestation_error',
     '        if incomplete:\n            raise ValueError(incomplete)',
     '        if False:\n            raise ValueError(incomplete)'),
    ('receipt-policy', 'test_receipt_retains_candidate_policy_lock_and_pending_ids',
     "**before, **proof, 'log': digest(log_path.read_bytes())}",
     "**before, **proof, 'policy': 'reverted', 'log': digest(log_path.read_bytes())}"),
    ('status', 'test_fresh_status_is_awaiting_attestation_and_never_passes_a_gate',
     "awaiting = record['result'] == 'awaiting-attestation' and proof_matches(path, record)",
     'awaiting = False'),
    ('freshness', 'test_freshness_retires_after_marks_and_input_changes_but_not_evidence_commits',
     "awaiting = record['result'] == 'awaiting-attestation' and proof_matches(path, record)",
     "awaiting = record['result'] == 'awaiting-attestation'"),
    ('human-only', 'test_mark_and_decide_still_refuse_agent_recorded_human_decisions',
     'if agent_record(vars(args)) or agent_record(require_run(path)):', 'if False:'),
    ('head-provenance', 'test_freshness_retires_after_marks_and_input_changes_but_not_evidence_commits',
     "return (record.get('candidate', {}).get('tree') == current['candidate']['tree']",
     "return (record.get('candidate') == current['candidate']"),
    ('later-marks', 'test_freshness_retires_after_marks_and_input_changes_but_not_evidence_commits',
     "if key != 'candidate'))", "if key not in ('candidate', 'manual_marks')))"),
    ('lock-freshness', 'test_freshness_retires_after_marks_and_input_changes_but_not_evidence_commits',
     "if key != 'candidate'))", "if key not in ('candidate', 'test_lock')))"),
    ('log-freshness', 'test_freshness_retires_after_marks_and_input_changes_but_not_evidence_commits',
     "and record['log'] == digest(log.read_bytes()))", 'and True)'),
]


def main():
    parser = argparse.ArgumentParser(description='Prove each CLI regression rejects a targeted protection reversion.')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.relative_to(ROOT)
    output.mkdir(parents=True, exist_ok=True)
    failures = []
    for name, test, old, new in CASES:
        with tempfile.TemporaryDirectory(prefix='reversion-', dir=output) as temporary:
            checkout = Path(temporary)
            for folder in ('assets', 'scripts', 'tests'):
                shutil.copytree(ROOT / folder, checkout / folder, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            script = checkout / 'assets/_system/scripts/sdlc.py'
            source = script.read_text()
            if source.count(old) != 1:
                raise RuntimeError(f'{name}: expected exactly one reversion site')
            script.write_text(source.replace(old, new))
            subprocess.run([sys.executable, 'scripts/package.py'], cwd=checkout, check=True)
            result = subprocess.run([sys.executable, '-m', 'unittest', TEST + test, '-v'], cwd=checkout,
                                    env={**os.environ, 'PYTHONPATH': 'tests', 'TMPDIR': str(checkout)},
                                    capture_output=True, text=True, timeout=120)
            log = result.stdout + result.stderr
            (output / f'{name}.log').write_text(log)
            rejected = result.returncode != 0 and 'AssertionError:' in log and 'FAILED (failures=' in log
            print(f'{name}: {"rejected" if rejected else "NOT PROVEN"}', flush=True)
            if not rejected:
                failures.append(name)
    if failures:
        sys.exit('Reversion evidence missing: ' + ', '.join(failures))


if __name__ == '__main__':
    main()
