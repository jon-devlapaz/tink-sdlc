"""Guard the operational docs against the 'review' -> 'parse' find/replace corruption.

The extraction commit replaced the word 'review' with 'parse' in several shipped
documents ('Stage 05: Parse and release', 'a model parse is not human approval').
These documents are instructions to agents, so the corruption changes their meaning.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# 'parse' is legitimate in code and in `git rev-parse`; the hyphen lookbehind keeps rev-parse out.
CORRUPTION = re.compile(r'(?<![\w-])parse[sd]?(?![\w-])', re.IGNORECASE)


def operational_docs():
    files = sorted((ROOT / 'assets' / 'stages').glob('*/CONTEXT.md'))
    files += sorted((ROOT / 'assets' / '_shared').glob('*.md'))
    files += [ROOT / 'assets' / '_system' / 'SDLC.md', ROOT / 'SKILL.md', ROOT / 'README.md',
              ROOT / 'references' / 'lifecycle-stages.md']
    return files


class WordingTests(unittest.TestCase):
    def test_no_parse_where_review_was_meant(self):
        offenders = []
        for path in operational_docs():
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if CORRUPTION.search(line):
                    offenders.append(f'{path.relative_to(ROOT)}:{number}: {line.strip()[:80]}')
        self.assertEqual(offenders, [], "the word 'parse' appears where 'review' was meant")

    def test_stage_five_is_review_and_release(self):
        first = (ROOT / 'assets' / 'stages' / '05-deploy' / 'CONTEXT.md').read_text().splitlines()[0]
        self.assertEqual(first, '# Stage 05: Review and release')

    def test_light_run_gate_vocabulary(self):
        sentence = 'Light runs have ONE definition gate, recorded as stage 3'
        for rel in ['_system/SDLC.md', 'stages/01-plan/CONTEXT.md', 'stages/02-design/CONTEXT.md', 'stages/03-build/CONTEXT.md']:
            self.assertIn(sentence, (ROOT / 'assets' / rel).read_text(), rel)
        build = (ROOT / 'assets/stages/03-build/CONTEXT.md').read_text()
        self.assertNotIn('Inspect code, write the plan,', build)
        self.assertIn('For full runs, write the plan', build)

    def test_stage_one_names_each_profiles_gate_and_template(self):
        # #54: a planner read the bare stage-1 Gate line as a stage-1 decision for a light run.
        text = ' '.join((ROOT / 'assets/stages/01-plan/CONTEXT.md').read_text().split())
        self.assertIn('(light runs: `decide <slug> 3`; full runs: `decide <slug> 1`)', text)
        self.assertIn('`_shared/brief-template.md` for light runs', text)

    def test_only_worktree_launches_commit_the_run(self):
        # #53: `stage --here` commits nothing, so the guide must not promise it does.
        text = ' '.join((ROOT / 'assets/_system/SDLC.md').read_text().split())
        self.assertIn('Only a worktree launch (stages 3 and 5 unless `--here`) commits `runs/<run>`', text)
        self.assertNotIn('It commits `runs/<run>`', text)

    def test_preapproval_dry_run_preserves_gate_authority(self):
        text = (ROOT / 'assets/_system/SDLC.md').read_text()
        self.assertLess(text.index('sdlc.py dry-run fix-example'), text.index('sdlc.py decide fix-example'))
        for phrase in ['expected reproduction failure', 'launch errors, timeouts',
                       'records no decision', 'does not enforce a new approval prerequisite',
                       'still requires current approval and passing checks',
                       'No check process survives its check', 'on normal exit or timeout']:
            self.assertIn(phrase, text)

    def test_stage_boundary_ownership_section(self):
        text = (ROOT / 'assets/_system/SDLC.md').read_text()
        self.assertIn('## Who does what at a stage boundary', text)
        section = text.split('## Who does what at a stage boundary')[1].split('\n## ')[0]
        self.assertLess(len(section.strip().splitlines()), 25)
        for word in ['launcher', 'tink use', 'NEW session', 'never approves', 'sdlc.py mark', 'REVIEW-findings.md', 'commit before verifying']:
            self.assertIn(word.lower(), section.lower(), word)

    def test_generated_rules_excluded_from_fingerprint_documented(self):
        text = (ROOT / 'assets/_system/SDLC.md').read_text()
        self.assertIn('excluded from the candidate fingerprint', text)

    def test_tinkery_ownership_and_handoff_contract(self):
        text = (ROOT / 'assets/_system/SDLC.md').read_text()
        self.assertIn('## Tinkery ownership and handoff', text)
        section = ' '.join(text.split('## Tinkery ownership and handoff', 1)[1].split('\n## ', 1)[0].split())
        for rule in [
            'SDLC owns run identity, implementation orchestration, technical review',
            'Tinkery owns the seed and its discovery decisions',
            'Tinkery owns the intended goal-to-result presentation',
            'The operator judges usefulness and authorizes merging',
            'Load only the explicitly supplied, operator-confirmed seed',
            'repository source and project files needed by the active stage',
            'Problem statement',
            'Proposed outcome (desired user-visible results and success criteria)',
            'Acceptance criteria',
            'Constraints and boundaries (non-negotiables, exclusions and rejected alternatives)',
            'Accepted decisions and assumed defaults',
            'never discover operator sessions',
            'human-readable only, not a normalized machine interface',
            'API 1 exposes run_id, the seed_contract binding and verification candidate provenance',
            'but not an explicit PR reference',
            'Use structured status for the seed binding',
            'A binding is evidence of intake, not approval, goal identity or release authority',
            'Never infer a goal-to-run connection from matching names',
            'each claimed completion condition to evidence for that result',
            'automated proof, manual attestation, independent review and operator acceptance',
            'Copying a handoff records no verdict and starts nothing',
            'Do not create a Taste verdict format or a competing acceptance ledger',
            'Missing, stale, failed, interrupted and conflicting evidence stay distinct',
        ]:
            self.assertIn(rule, section)
        self.assertNotIn('Until those fields are exposed', section)
        self.assertNotIn('not the seed_contract binding', section)

    def test_tinkery_guidance_is_reachable_from_active_contracts(self):
        for rel in ['SKILL.md', 'assets/stages/01-plan/CONTEXT.md',
                    'assets/_shared/REVIEW.md', 'assets/stages/05-deploy/CONTEXT.md']:
            text = (ROOT / rel).read_text()
            self.assertIn('Tinkery', text, rel)
            self.assertIn('_system/SDLC.md#tinkery-ownership-and-handoff', text, rel)

    def test_tinkery_readme_is_a_stable_pointer_not_a_snapshot(self):
        text = (ROOT / 'README.md').read_text()
        self.assertIn('assets/_system/SDLC.md#tinkery-ownership-and-handoff', text)
        self.assertNotIn('## Tinkery capability assessment', text)
        self.assertNotRegex(text, r'\b[0-9a-f]{40}\b')
        self.assertNotIn('needs-approval', text)

    def test_tinkery_source_links_follow_main(self):
        text = (ROOT / 'assets/_system/SDLC.md').read_text()
        links = re.findall(r'https://github.com/jon-devlapaz/tinkery/blob/[^)]+', text)
        self.assertTrue(links)
        for link in links:
            self.assertTrue(link.startswith('https://github.com/jon-devlapaz/tinkery/blob/main/'), link)

    def test_light_and_full_templates_preserve_seed_boundaries(self):
        for name, heading in [('brief', '## Constraints and exclusions'),
                              ('intent', '## 4. Constraints and boundaries'),
                              ('spec', '## 5. Constraints and boundaries')]:
            text = (ROOT / f'assets/_shared/{name}-template.md').read_text()
            self.assertIn(heading, text, name)
            section = text.split(heading, 1)[1].split('\n## ', 1)[0]
            self.assertIn('Must (non-negotiables):', section, name)
            self.assertIn('Must not (exclusions and rejected alternatives):', section, name)
        plan = (ROOT / 'assets/stages/01-plan/CONTEXT.md').read_text()
        self.assertIn('constraints and exclusions', plan)

    def test_review_guide_is_about_review(self):
        text = (ROOT / 'assets' / '_shared' / 'REVIEW.md').read_text()
        self.assertIn('Review logic and edge cases', text)
        self.assertIn('Automated review is advisory', text)


if __name__ == '__main__':
    unittest.main()
