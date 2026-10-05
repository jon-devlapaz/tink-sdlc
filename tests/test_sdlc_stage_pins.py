import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'assets'
STAGES = {
    '01-plan': 'planning-skillset',
    '02-design': 'design-skillset',
    '03-build': 'build-skillset',
    '04-test': 'testing-skillset',
    '05-deploy': 'deployment-skillset',
    '06-maintain': 'maintenance-skillset',
}
PINS = ASSETS / '.tink/skillsets'


# Chosen with Jev (fit of each discipline as an always-on rule per stage); stage-specific
# conditional principles stay on the shelf as routed skills, not always-on rules.
CURATED_REQUIRED = {
    'planning-skillset': {'principle-build-the-lever'},
    'design-skillset': {'principle-build-the-lever', 'architect', 'principle-foundational-thinking'},
    'build-skillset': {'principle-build-the-lever', 'unslop'},
    'testing-skillset': {'principle-build-the-lever', 'principle-prove-it-works'},
    'deployment-skillset': {'principle-prove-it-works'},
    'maintenance-skillset': {'principle-prove-it-works'},
}


class StagePinTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def run_init(self, *args, ok=True):
        result = subprocess.run(['python3', str(ROOT / 'scripts/init.py'), str(self.root), *args], capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return result

    def contents(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def test_exactly_six_canonical_pins_ship(self):
        names = sorted(p.name for p in PINS.glob('*'))
        self.assertEqual(names, sorted(f'{n}.json' for n in STAGES.values()))

    def test_every_shipped_pin_validates(self):
        for name in STAGES.values():
            pin = json.loads((PINS / f'{name}.json').read_text())
            with self.subTest(pin=name):
                self.assertRegex(pin['source'], r'^https://\S+\.git$')
                self.assertRegex(pin['revision'], r'^[0-9a-f]{40}$')
                self.assertIsInstance(pin['sourceRoot'], str)
                self.assertTrue(pin['sourceRoot'].strip())
                members = pin['members']
                self.assertTrue(members)
                self.assertEqual(len(members), len(set(members)))
                self.assertTrue(all(isinstance(m, str) and m.strip() for m in members))
                required = pin['required']
                self.assertTrue(required)
                self.assertEqual(len(required), len(set(required)))
                self.assertLessEqual(set(required), set(members))
                self.assertEqual(set(required), CURATED_REQUIRED[name])
                self.assertNotIn('seed-me', members)
                self.assertLessEqual(set(pin), {'source', 'revision', 'sourceRoot', 'members', 'required'})

    def test_stage_contracts_list_the_required_skills_with_a_rule_and_the_hooks(self):
        for directory, name in STAGES.items():
            with self.subTest(stage=directory):
                text = (ASSETS / 'stages' / directory / 'CONTEXT.md').read_text()
                section = re.search(r'^Stage skills \(.*?\):\n((?:- .*\n)+)', text, re.M)
                self.assertIsNotNone(section, 'no "Stage skills" list')
                entries = re.findall(r'^- `([a-z0-9-]+)`: (.+)$', section.group(1), re.M)
                self.assertEqual({skill for skill, _ in entries}, CURATED_REQUIRED[name])
                for skill, rule in entries:
                    self.assertGreaterEqual(len(rule), 30, skill)
                    self.assertNotIn('Apply when', rule, f'{skill}: a hand-written rule, not the trigger description')
                self.assertIn('read that skill in full before acting on it', text)
                self.assertIn('name each skill that changed a decision', text)

    def test_manifest_includes_pins(self):
        files = json.loads((ASSETS / 'manifest.json').read_text())['files']
        for name in STAGES.values():
            self.assertIn(f'.tink/skillsets/{name}.json', files)
        self.assertEqual(json.loads((ASSETS / 'manifest.json').read_text())['version'], '1.18.3')

    def test_no_gitignore_hides_pins(self):
        self.assertFalse(list(ASSETS.rglob('.gitignore')))

    def test_check_preview_lists_pins_without_writing(self):
        out = self.run_init('--check').stdout
        for name in STAGES.values():
            self.assertIn(f'.tink/skillsets/{name}.json', out)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_init_installs_pins_create_only_and_is_idempotent(self):
        (self.root / '.tink').mkdir()
        (self.root / '.tink/skills.toml').write_text('keep')
        self.run_init()
        for name in STAGES.values():
            self.assertEqual((self.root / f'.tink/skillsets/{name}.json').read_bytes(), (PINS / f'{name}.json').read_bytes())
        self.assertEqual((self.root / '.tink/skills.toml').read_text(), 'keep')
        before = self.contents()
        self.run_init()
        self.assertEqual(before, self.contents())

    def test_init_creates_tink_dir_when_absent(self):
        self.run_init()
        self.assertTrue((self.root / '.tink/skillsets').is_dir())

    def test_existing_different_pin_preserved_and_install_refused(self):
        (self.root / '.tink/skillsets').mkdir(parents=True)
        mine = self.root / '.tink/skillsets/planning-skillset.json'
        mine.write_text('{"mine": true}')
        before = self.contents()
        self.run_init(ok=False)
        self.assertEqual(before, self.contents())

    def test_modified_installed_pin_exempt_on_repeat(self):
        self.run_init()
        pin = self.root / '.tink/skillsets/build-skillset.json'
        pin.write_text('{"edited": true}')
        self.assertIn('Project-owned, changed locally', self.run_init().stdout)
        self.assertEqual(pin.read_text(), '{"edited": true}')

    def test_stage_contexts_document_skills(self):
        for stage, name in STAGES.items():
            text = (ASSETS / f'stages/{stage}/CONTEXT.md').read_text()
            with self.subTest(stage=stage):
                self.assertEqual(text.count('## Skills'), 1)
                self.assertIn(name, text)
                self.assertIn(f'tink library fetch .tink/skillsets/{name}.json', text)
                if stage == '04-test':
                    self.assertIn('Continue verification in the stage-3 build session', text)
                    self.assertIn('sdlc.py skills tink -- mount <skill> --json --payload', text)
                    self.assertNotIn('sdlc.py stage <slug> 4', text)
                    self.assertNotIn('NEW session', text)
                else:
                    self.assertRegex(text, rf'tink use {name} --snapshot runs/<slug>/{stage}\b')
                    self.assertIn('NEW session', text)
                self.assertIn('tink-route --receipt runs/<slug>/skills.jsonl "<what you need>"', text)
                self.assertNotIn('tink-route --skillset', text)
                self.assertIn('searches the whole library', text)
                self.assertNotIn('rules block above', text)

    def test_no_removed_route_flow_in_shipped_files(self):
        banned = ['--install', '--ephemeral', '--prune', 'tink-route --use', '--stage-only', '--strict', 'ephemeral.json']
        files = [p for p in ASSETS.rglob('*') if p.is_file()]
        files += [p for p in ROOT.glob('references/**/*') if p.is_file()]
        files += [ROOT / 'SKILL.md', ROOT / 'README.md']
        for path in files:
            if '__pycache__' in path.parts:
                continue
            text = path.read_text(errors='ignore')
            for needle in banned:
                with self.subTest(path=str(path.relative_to(ROOT)), needle=needle):
                    self.assertNotIn(needle, text)

    def test_maintain_marked_optional(self):
        text = (ASSETS / 'stages/06-maintain/CONTEXT.md').read_text()
        self.assertRegex(text.split('## Skills')[1].lower(), 'optional')

    def test_sdlc_doc_stage_skills(self):
        text = (ASSETS / '_system/SDLC.md').read_text()
        self.assertEqual(text.count('## Stage skills'), 1)
        for needle in ['seed-me', 'tink skill add jon-devlapaz/tink-skills --skill seed-me',
                       'tink library fetch', 'tink use', 'tink-route --receipt', '--anywhere', 'Hint:', '.tink/skillsets/',
                       'required', 'AGENTS.md', 'seed contract', 'optional']:
            self.assertIn(needle, text)

    def test_router_names_the_ad_hoc_skill_command_once_inside_the_markers(self):
        needle = 'tink-route --receipt runs/<slug>/skills.jsonl "<what you need>"'
        self.run_init()
        self.run_init()
        text = (self.root / 'AGENTS.md').read_text()
        self.assertEqual(text.count(needle), 1)
        self.assertLess(text.index('<!-- AI-Native SDLC Router -->'), text.index(needle))
        self.assertLess(text.index(needle), text.index('<!-- End AI-Native SDLC Router -->'))
        line = next(l for l in text.splitlines() if needle in l)
        self.assertIn('exit 1', line)

    def test_router_bullet_once_and_idempotent(self):
        bullet = "- Stage skills: see the Skills section of the current stage's CONTEXT.md."
        self.run_init()
        text = (self.root / 'AGENTS.md').read_text()
        self.assertEqual(text.count(bullet), 1)
        self.assertLess(text.index(bullet), text.index('<!-- End AI-Native SDLC Router -->'))
        self.run_init()
        self.assertEqual((self.root / 'AGENTS.md').read_text().count(bullet), 1)

    def test_package_manifest_matches(self):
        result = subprocess.run(['python3', str(ROOT / 'scripts/package.py'), '--check'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
