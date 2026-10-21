"""Record zero-call Rust-confirmed TEST sessions; never run in the operator checkout.

Usage after `cargo +stable build --locked` in a fresh pinned temporary clone:
    python3 tests/fixtures/tinkery-785a693/record.py /tmp/tink-sdlc-90-TEST-...

Only the TEST example is added to that disposable clone. The unmodified binary
records the exact public command responses. CI replays these; it needs no Rust.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from tinkery_fixture import DEFAULT, command, provisional

COMMIT = '785a6937f2d946fc78afebcdb6e4f811430056a1'
NAMES = ('ledger.json', 'seed-contract.md', 'seed-checks.json', 'seed-confirmation.json')
HEADINGS = ('You are confirming', 'Problem statement', 'Proposed outcome', 'Acceptance criteria',
            'Affected users and systems', 'Constraints and boundaries', 'Decisions (from the ledger)',
            'Assumed (not confirmed by you)', 'Knowledge map', 'Evidence and uncertainty',
            'Suggested first slice', 'Risks and verification', 'Open questions and deferrals',
            'Owners and next steps', 'Downstream handoff')


def run(argv, **kwargs):
    result = subprocess.run(list(map(str, argv)), capture_output=True, text=True, **kwargs)
    assert result.returncode == 0, result.stderr
    return result.stdout


def draft(template, extra):
    sections = {}
    if template is not None:
        for block in template.split('\n## ')[1:]:
            title, _, body = block.partition('\n')
            sections[title] = body.rstrip('\n')
    text = 'status: draft\n# TEST intake\nrevision: r1\n\n'
    for heading in HEADINGS:
        if heading == 'Acceptance criteria':
            text += '{{checks}}'
        elif heading == 'Decisions (from the ledger)':
            text += '{{decisions}}\n'
        elif heading != 'Assumed (not confirmed by you)':
            text += '## ' + heading + '\n' + sections.get(heading, 'TEST fixture only.') + '\n\n'
    return text + extra


def main(clone):
    clone = clone.resolve()
    assert clone.parent == Path('/tmp').resolve() and clone.name.startswith('tink-sdlc-90-TEST-')
    assert run(['git', '-C', clone, 'rev-parse', 'HEAD']).strip() == COMMIT
    example = clone / 'examples/test_sdlc_90_confirm.rs'
    assert not example.exists()
    shutil.copyfile(HERE / 'confirm.rs', example)
    run(['cargo', '+stable', 'build', '--locked', '--example', 'test_sdlc_90_confirm'], cwd=clone)
    binary = clone / 'target/debug/tinkery'
    fixture = HERE.parent / 'stacker-r1-seed-contract.md'
    template = fixture.read_text()
    stacker = json.loads((HERE.parent / 'stacker-records.json').read_text())
    record_sets = [DEFAULT, [command()], [provisional()], [provisional('false')],
                   [provisional('./future')], [command('test -f accepted')], [command(), DEFAULT[0]],
                   [command(), DEFAULT[0], dict(command(), expect='sound plays on the intended output device'), command()],
                   [command('test -d `pwd` && printf "%s" "literal | expect: not a field"')],
                   [command('cat proof | grep "exact output"', 'sub')],
                   [command(), provisional(), provisional('false'), {**DEFAULT[0], 'how_to_judge': 'Inspect'}]]
    requests = [{'records': records, 'extra': '', 'template': None} for records in record_sets]
    requests += [{'records': DEFAULT, 'extra': 'Revised TEST scope.\n', 'template': None},
                 {'records': stacker, 'extra': '', 'template': template}]
    output = {'provenance': {'repository': 'https://github.com/jon-devlapaz/tinkery.git', 'commit': COMMIT,
              'confirmation': 'ledger::confirm_seed via confirm.rs; synthetic TEST action, no model or human approval',
              'commands': ['PATH seed status --json DIR', 'PATH seed checks DIR'],
              'build': 'cargo +stable build --locked', 'provider_calls': 0}, 'cases': [], 'refusals': {}}
    with tempfile.TemporaryDirectory(prefix='TEST-record-', dir=clone) as temp:
        root = Path(temp)
        home = root / 'TEST-home'
        home.mkdir()
        env = {**os.environ, 'HOME': str(home)}

        def capture(directory):
            files = {name: (directory / name).read_text() if (directory / name).is_file() else None for name in NAMES}
            result = {'files': files}
            for name, args in [('status', ['seed', 'status', '--json']), ('checks', ['seed', 'checks'])]:
                reply = subprocess.run([str(binary), *args, str(directory)], env=env, capture_output=True, text=True)
                result[name] = {'returncode': reply.returncode, 'stdout': reply.stdout, 'stderr': reply.stderr}
            assert files == {name: (directory / name).read_text() if (directory / name).is_file() else None for name in NAMES}
            return result

        for index, request in enumerate(requests):
            directory = root / f'TEST-session-{index}'
            directory.mkdir()
            ledger = {'schema_version': 1, 'session_id': 'TEST-session', 'version': 1, 'revision': 0,
                      'created_at': '2026-10-21T00:00:00+00:00', 'updated_at': '2026-10-21T00:00:00+00:00',
                      'status': 'active', 'draft': {'goal': 'TEST goal', 'outcome': 'TEST outcome', 'options': ['A', 'B']},
                      'goal': 'TEST goal', 'origin': 'goal', 'current_question': None, 'frontier': [], 'assumed': [],
                      'operator': 'human', 'nodes': [{'id': 'goal', 'kind': 'decision', 'status': 'settled',
                      'prerequisites': [], 'evidence': [], 'owner': 'TEST operator', 'gate': 'choose', 'answer': 'TEST goal',
                      'authority': 'user', 'authority_source': 'TEST explicit approval', 'history': [], 'revision': 0}]}
            (directory / 'ledger.json').write_text(json.dumps(ledger))
            (directory / 'seed-checks.json').write_text(json.dumps(request['records']))
            (directory / 'TEST-draft.md').write_text(draft(request['template'], request['extra']))
            run([clone / 'target/debug/examples/test_sdlc_90_confirm', directory], env=env)
            case = capture(directory)
            assert json.loads(case['status']['stdout'])['handoff']['verified'] is True
            case['request'] = request
            output['cases'].append(case)

        base = next(case for case in output['cases'] if case['request']['records'] == [command()])
        for mutation in ('records', 'seed', 'decisions', 'v1', 'missing', 'missing-confirmation',
                         'simulated', 'rendered', 'emphasized-heading', 'comment-only'):
            directory = root / ('TEST-' + mutation)
            directory.mkdir()
            for name, text in base['files'].items():
                (directory / name).write_text(text)
            seed = directory / 'seed-contract.md'
            receipt = json.loads(base['files']['seed-confirmation.json'])
            if mutation == 'records':
                (directory / 'seed-checks.json').write_text(json.dumps([command('false')]))
            elif mutation == 'seed':
                seed.write_text(seed.read_text() + 'changed\n')
            elif mutation == 'decisions':
                ledger = json.loads(base['files']['ledger.json'])
                ledger['draft']['outcome'] = 'TEST changed decision context'
                (directory / 'ledger.json').write_text(json.dumps(ledger))
            elif mutation == 'v1':
                receipt['schema_version'] = 1
            elif mutation in ('missing', 'missing-confirmation'):
                (directory / ('seed-checks.json' if mutation == 'missing' else 'seed-confirmation.json')).unlink()
            elif mutation == 'simulated':
                ledger = json.loads(base['files']['ledger.json'])
                ledger['operator'] = 'simulated'
                ledger['nodes'][0]['authority'] = 'simulated'
                (directory / 'ledger.json').write_text(json.dumps(ledger))
                seed.write_text(seed.read_text().replace('status: confirmed for intake', 'status: simulated', 1))
            else:
                text = seed.read_text()
                if mutation == 'rendered':
                    text = text.replace('\ncheck: ', '\nCHECK: ')
                else:
                    rendered = text[text.index('## Acceptance criteria\n'):text.index('## Affected users and systems\n')]
                    replacement = (rendered + '## **Acceptance checks**\nTEST additional failing requirement\n'
                                   'cmd: false\nexpect: exit zero\ncwd: .\n\n' if mutation == 'emphasized-heading'
                                   else '<!--\n' + rendered + '## End example\n-->\n\n')
                    text = text.replace(rendered, replacement, 1)
                seed.write_text(text)
                receipt['seed_sha256'] = hashlib.sha256(seed.read_bytes()).hexdigest()
            if mutation != 'missing-confirmation':
                (directory / 'seed-confirmation.json').write_text(json.dumps(receipt))
            case = capture(directory)
            assert case['status']['returncode'] == 0
            assert json.loads(case['status']['stdout'])['handoff']['verified'] is False
            output['refusals'][mutation] = case
    (HERE / 'recorded.json').write_text(json.dumps(output, indent=2, ensure_ascii=False) + '\n')
    print(f"Recorded {len(output['cases'])} Rust-confirmed TEST sessions and {len(output['refusals'])} refusal cases at {COMMIT}")


if __name__ == '__main__':
    main(Path(sys.argv[1]))
