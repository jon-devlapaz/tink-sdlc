import hashlib
import importlib.util
import json
import os
from pathlib import Path
import contextlib
import io
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PIN = '.tink/skillsets/build-skillset.json'
NEW_EDITS = {
    '_shared/REVIEW.md': 'upstream review text\n',
    'stages/03-build/CONTEXT.md': 'upstream build context\n',
    '_shared/extra.md': 'brand new\n',
    '_shared/brief-template.md': None,
    PIN: '{"upstream": "changed pin"}\n',
    '.tink/skillsets/extra-skillset.json': '{"new": "pin"}\n',
}


def make_package(dest, version, edits=None, init_edit=None):
    """Copy scripts/ and assets/, apply edits, and regenerate the manifest with the copied package.py."""
    dest = Path(dest)
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc')
    shutil.copytree(ROOT / 'scripts', dest / 'scripts', ignore=ignore)
    shutil.copytree(ROOT / 'assets', dest / 'assets', ignore=ignore)
    # Synthetic old releases start from their own baseline, not the current release version.
    manifest_path = dest / 'assets/manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['version'] = version
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    for name, content in (edits or {}).items():
        path = dest / 'assets' / name
        if content is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    if init_edit:
        init = dest / 'scripts/init.py'
        init.write_text(init_edit(init.read_text()))
    result = subprocess.run(['python3', str(dest / 'scripts/package.py'), '--version', version], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return dest


def old_router(source):
    marker = "- Stage skills: see the Skills section of the current stage's CONTEXT.md.\n"
    assert marker in source
    return source.replace(marker, '- Old router line.\n')


def tree(root, skip=()):
    out = {}
    for p in sorted(Path(root).rglob('*')):
        rel = str(p.relative_to(root))
        if any(rel == s or rel.startswith(s + '/') for s in skip):
            continue
        if p.is_file() and not p.is_symlink():
            out[rel] = (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mode & 0o777)
        elif p.is_dir():
            out[rel + '/'] = None
    return out


class UpgradeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pkgs = tempfile.TemporaryDirectory()
        base = Path(cls.pkgs.name)
        cls.old = make_package(base / 'old', '1.4.0')
        cls.new = make_package(base / 'new', '1.5.0', NEW_EDITS)
        cls.oldr = make_package(base / 'oldr', '1.4.0', init_edit=old_router)
        cls.newctx = make_package(base / 'newctx', '1.5.0', {'_shared/REVIEW.md': 'only review\n'})

    @classmethod
    def tearDownClass(cls):
        cls.pkgs.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def run_pkg(self, pkg, *args, ok=True, root=None):
        result = subprocess.run(['python3', str(pkg / 'scripts/init.py'), str(root or self.root), *args], capture_output=True, text=True)
        if ok is not None:
            self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return result

    def install_old(self, pkg=None):
        self.run_pkg(pkg or self.old)

    def receipt(self):
        return json.loads((self.root / '_system/scaffold.json').read_text())

    def new_manifest(self):
        return json.loads((self.new / 'assets/manifest.json').read_text())

    def test_manifest_lists_project_owned(self):
        owned = self.new_manifest()['projectOwned']
        self.assertEqual(owned, sorted(owned))
        self.assertIn('_system/verification.json', owned)
        self.assertIn(PIN, owned)
        self.assertIn('.tink/skillsets/extra-skillset.json', owned)
        real = json.loads((ROOT / 'assets/manifest.json').read_text())
        self.assertEqual(real['version'], '1.18.4')
        self.assertEqual(len(real['projectOwned']), 7)

    def test_clean_upgrade(self):
        self.install_old()
        out = self.run_pkg(self.new, '--upgrade').stdout.splitlines()
        self.assertEqual(out[0], 'Upgrade 1.4.0 -> 1.5.0')
        self.assertIn(f'Update: {PIN}, _shared/REVIEW.md, stages/03-build/CONTEXT.md', out)
        self.assertIn('Create: .tink/skillsets/extra-skillset.json, _shared/extra.md', out)
        self.assertIn('Remove: _shared/brief-template.md', out)
        self.assertTrue(out[-1].startswith('Upgraded to 1.5.0.'))
        self.assertIn('sdlc.py walk', out[-1])
        self.assertFalse(any(line.startswith(('Restore', 'Kept', 'Blocked', 'In-flight')) for line in out))
        self.assertEqual((self.root / '_shared/REVIEW.md').read_text(), 'upstream review text\n')
        self.assertEqual((self.root / '_shared/extra.md').read_text(), 'brand new\n')
        self.assertFalse((self.root / '_shared/brief-template.md').exists())
        self.assertEqual((self.root / PIN).read_text(), '{"upstream": "changed pin"}\n')
        self.assertEqual(self.receipt(), self.new_manifest())
        self.assertTrue(os.access(self.root / '_system/scripts/sdlc.py', os.X_OK))
        self.assertFalse((self.root / '.sdlc-init-lock').exists())

    def test_customized_pin_kept_and_nonblocking(self):
        self.install_old()
        (self.root / PIN).write_text('tuned locally\n')
        out = self.run_pkg(self.new, '--upgrade').stdout
        self.assertEqual((self.root / PIN).read_text(), 'tuned locally\n')
        self.assertIn(f'Kept (project-owned): {PIN} (changed locally; upstream also changed)', out)
        self.assertNotIn('Blocked', out)
        self.assertEqual(self.receipt(), self.new_manifest())

    def test_customized_pin_upstream_unchanged_is_silent(self):
        self.install_old()
        other = '.tink/skillsets/design-skillset.json'
        (self.root / other).write_text('tuned\n')
        out = self.run_pkg(self.new, '--upgrade').stdout
        self.assertEqual((self.root / other).read_text(), 'tuned\n')
        self.assertNotIn(other, out)

    def test_project_owned_deleted_locally_kept_absent(self):
        self.install_old()
        (self.root / PIN).unlink()
        out = self.run_pkg(self.new, '--upgrade').stdout
        self.assertFalse((self.root / PIN).exists())
        self.assertIn(f'{PIN} (deleted locally)', out)

    def test_managed_missing_is_restored(self):
        self.install_old()
        (self.root / '_shared/spec-template.md').unlink()
        out = self.run_pkg(self.new, '--upgrade').stdout
        self.assertIn('Restore: _shared/spec-template.md', out)
        self.assertTrue((self.root / '_shared/spec-template.md').is_file())

    def test_removed_upstream_but_modified_is_kept(self):
        self.install_old()
        (self.root / '_shared/brief-template.md').write_text('mine\n')
        out = self.run_pkg(self.new, '--upgrade').stdout
        self.assertEqual((self.root / '_shared/brief-template.md').read_text(), 'mine\n')
        self.assertNotIn('Remove:', out)
        self.assertIn('_shared/brief-template.md (removed upstream, changed locally)', out)

    def test_new_upstream_file_colliding_with_local(self):
        self.install_old()
        (self.root / '_shared/extra.md').write_text('local extra\n')
        (self.root / '.tink/skillsets/extra-skillset.json').parent.mkdir(exist_ok=True)
        (self.root / '.tink/skillsets/extra-skillset.json').write_text('local pin\n')
        before = tree(self.root)
        result = self.run_pkg(self.new, '--upgrade', ok=False)
        self.assertEqual(before, tree(self.root))
        self.assertIn('Blocked (customized managed files): _shared/extra.md', result.stdout)
        self.assertNotIn('extra-skillset.json (', result.stdout.split('Blocked')[1])
        (self.root / '_shared/extra.md').unlink()
        out = self.run_pkg(self.new, '--upgrade').stdout
        self.assertEqual((self.root / '.tink/skillsets/extra-skillset.json').read_text(), 'local pin\n')

    def test_customized_managed_blocks_without_writes(self):
        self.install_old()
        (self.root / '_shared/REVIEW.md').write_text('mine\n')
        (self.root / '_shared/spec-template.md').write_text('mine too\n')  # upstream unchanged
        before = tree(self.root)
        result = self.run_pkg(self.new, '--upgrade', ok=False)
        self.assertEqual(before, tree(self.root))
        self.assertIn('Upgrade 1.4.0 -> 1.5.0', result.stdout)
        self.assertIn('Blocked (customized managed files): _shared/REVIEW.md, _shared/spec-template.md', result.stdout)
        self.assertIn(f'compare with: diff {self.root}/_shared/REVIEW.md {self.new.resolve()}/assets/_shared/REVIEW.md', result.stdout)
        self.assertNotIn('Upgraded to', result.stdout)
        self.assertFalse((self.root / '.sdlc-init-lock').exists())

    def test_blocked_check_also_fails(self):
        self.install_old()
        (self.root / '_shared/REVIEW.md').write_text('mine\n')
        before = tree(self.root)
        result = self.run_pkg(self.new, '--upgrade', '--check', ok=False)
        self.assertEqual(before, tree(self.root))
        self.assertIn('Blocked', result.stdout)

    def test_overwrite_customized_takes_package(self):
        self.install_old()
        (self.root / '_shared/REVIEW.md').write_text('mine\n')
        (self.root / '_shared/spec-template.md').write_text('mine too\n')
        out = self.run_pkg(self.new, '--upgrade', '--overwrite-customized').stdout
        self.assertEqual((self.root / '_shared/REVIEW.md').read_text(), 'upstream review text\n')
        self.assertEqual((self.root / '_shared/spec-template.md').read_bytes(), (self.new / 'assets/_shared/spec-template.md').read_bytes())
        self.assertIn('git', out)
        self.assertIn('_shared/spec-template.md', out)
        self.assertEqual(self.receipt(), self.new_manifest())

    def test_verification_json_never_overwritten(self):
        pkg = make_package(Path(self.temp.name) / 'pkg', '1.5.0', {'_system/verification.json': '{"checks": [{"argv": ["upstream"]}]}\n'})
        self.install_old()
        (self.root / '_system/verification.json').write_text('{"checks": [{"argv": ["mine"]}]}\n')
        for flags in (['--upgrade'], ['--upgrade', '--overwrite-customized']):
            self.run_pkg(pkg, *flags)
            self.assertEqual((self.root / '_system/verification.json').read_text(), '{"checks": [{"argv": ["mine"]}]}\n')
            self.assertEqual(self.receipt()['version'], '1.5.0')
            self.install_old_receipt_back()

    def install_old_receipt_back(self):
        shutil.copyfile(self.old / 'assets/manifest.json', self.root / '_system/scaffold.json')

    def test_downgrade_refused(self):
        self.run_pkg(self.new)
        before = tree(self.root)
        result = self.run_pkg(self.old, '--upgrade', ok=False)
        self.assertEqual(before, tree(self.root))
        self.assertIn('downgrade', result.stderr.lower())

    def test_equal_version_is_noop(self):
        self.install_old()
        before = tree(self.root)
        result = self.run_pkg(self.old, '--upgrade')
        self.assertEqual(before, tree(self.root))
        self.assertIn('Installed scaffold matches package', result.stdout)

    def test_missing_receipt_errors(self):
        result = self.run_pkg(self.new, '--upgrade', ok=False)
        self.assertIn('No installed scaffold found; run init.py <target> without --upgrade.', result.stderr)
        self.assertEqual(list(self.root.iterdir()), [])

    def seed_agents(self):
        (self.root / 'AGENTS.md').write_text('project head\n')
        (self.root / 'AGENTS.md').chmod(0o640)
        self.run_pkg(self.oldr)
        agents = self.root / 'AGENTS.md'
        text = agents.read_text()
        self.assertIn('Old router line.', text)
        agents.write_text(text + '\n<!-- tink:rules begin -->\ngenerated\n<!-- tink:rules end -->\n\nproject tail\n')
        agents.chmod(0o640)
        return agents

    def test_router_replaced_rest_preserved(self):
        agents = self.seed_agents()
        before = agents.read_text()
        start = before.index('<!-- AI-Native SDLC Router -->')
        end = before.index('<!-- End AI-Native SDLC Router -->') + len('<!-- End AI-Native SDLC Router -->')
        spec = importlib.util.spec_from_file_location('newinit', self.new / 'scripts/init.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        out = self.run_pkg(self.new, '--upgrade').stdout
        self.assertEqual(agents.read_text(), before[:start] + mod.ROUTER + before[end:])
        self.assertIn('AGENTS.md', out)
        self.assertEqual(agents.stat().st_mode & 0o777, 0o640)
        self.assertNotIn('.AGENTS.md.sdlc-install-tmp', ' '.join(p.name for p in self.root.iterdir()))

    def test_router_markers_missing_or_duplicated_refused(self):
        agents = self.seed_agents()
        good = agents.read_text()
        cases = {
            'no start': good.replace('<!-- AI-Native SDLC Router -->', ''),
            'no end': good.replace('<!-- End AI-Native SDLC Router -->', ''),
            'duplicate': good + '\n<!-- AI-Native SDLC Router -->\nx\n<!-- End AI-Native SDLC Router -->\n',
        }
        for label, text in cases.items():
            with self.subTest(label):
                agents.write_text(text)
                before = tree(self.root)
                result = self.run_pkg(self.new, '--upgrade', ok=False)
                self.assertEqual(before, tree(self.root))
                self.assertIn('router', result.stderr.lower())

    def test_runs_untouched_and_inflight_warning(self):
        self.install_old()
        (self.root / 'runs/r1/decisions').mkdir(parents=True)
        (self.root / 'runs/r1/decisions/a.json').write_text('{}')
        (self.root / 'runs/r2').mkdir()
        (self.root / 'runs/r2/brief.md').write_text('no decisions')
        (self.root / 'runs/r0/decisions').mkdir(parents=True)
        (self.root / 'runs/r0/decisions/b.json').write_text('{}')
        before = tree(self.root / 'runs')
        out = self.run_pkg(self.new, '--upgrade').stdout
        self.assertEqual(before, tree(self.root / 'runs'))
        self.assertIn('In-flight runs: approvals will read as stale until re-approved: r0, r1 (stage docs/templates changed)', out)

    def test_no_warning_without_context_change_or_without_decisions(self):
        self.install_old()
        (self.root / 'runs/r1/decisions').mkdir(parents=True)
        (self.root / 'runs/r1/decisions/a.json').write_text('{}')
        out = self.run_pkg(self.newctx, '--upgrade').stdout
        self.assertNotIn('In-flight', out)
        other = Path(self.temp.name) / 'other'
        other.mkdir()
        self.run_pkg(self.old, root=other)
        (other / 'runs/r2').mkdir(parents=True)
        out = self.run_pkg(self.new, '--upgrade', root=other).stdout
        self.assertNotIn('In-flight', out)

    def test_check_writes_nothing(self):
        self.install_old()
        before = tree(self.root)
        out = self.run_pkg(self.new, '--upgrade', '--check').stdout
        self.assertEqual(before, tree(self.root))
        self.assertIn('Update:', out)
        self.assertTrue(out.rstrip().endswith('Preview only; nothing changed.'))

    def test_fault_injection_converges(self):
        ref = Path(self.temp.name) / 'ref'
        ref.mkdir()
        self.run_pkg(self.old, root=ref)
        counter = {'n': 0}
        real_replace = os.replace

        def counting(*a, **k):
            counter['n'] += 1
            return real_replace(*a, **k)

        spec = importlib.util.spec_from_file_location('newinit_ref', self.new / 'scripts/init.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with patch('os.replace', counting):
            with contextlib.redirect_stdout(io.StringIO()):
                mod.install(ref, False, True, False)
        total = counter['n']
        self.assertGreaterEqual(total, 5)
        expected = tree(ref)
        for nth in range(1, total + 1):
            with self.subTest(nth=nth):
                target = Path(self.temp.name) / f'fault{nth}'
                target.mkdir()
                self.run_pkg(self.old, root=target)
                calls = {'n': 0}

                def flaky(*a, **k):
                    calls['n'] += 1
                    if calls['n'] == nth:
                        raise OSError('injected failure')
                    return real_replace(*a, **k)

                spec2 = importlib.util.spec_from_file_location(f'newinit_{nth}', self.new / 'scripts/init.py')
                mod2 = importlib.util.module_from_spec(spec2)
                spec2.loader.exec_module(mod2)
                with patch('os.replace', flaky):
                    with self.assertRaises(OSError):
                        with contextlib.redirect_stdout(io.StringIO()):
                            mod2.install(target, False, True, False)
                self.assertFalse((target / '.sdlc-init-lock').exists())
                self.assertFalse([p for p in target.rglob('*') if 'tmp' in p.name])
                if nth < total:
                    self.assertEqual(json.loads((target / '_system/scaffold.json').read_text())['version'], '1.4.0')
                self.run_pkg(self.new, '--upgrade', root=target)
                self.assertEqual(expected, tree(target))

    def test_second_upgrade_is_noop(self):
        self.install_old()
        self.run_pkg(self.new, '--upgrade')
        before = tree(self.root)
        result = self.run_pkg(self.new, '--upgrade')
        self.assertEqual(before, tree(self.root))
        self.assertIn('Installed scaffold matches package', result.stdout)

    def test_plain_init_on_older_install_prints_hint(self):
        self.install_old()
        before = tree(self.root)
        result = self.run_pkg(self.new, ok=False)
        self.assertEqual(before, tree(self.root))
        self.assertIn(f'Installed scaffold is 1.4.0; package is 1.5.0. Preview: init.py {self.root} --upgrade --check; apply: init.py {self.root} --upgrade.', result.stderr)

    def test_same_version_check_exempts_tuned_pin_but_not_managed(self):
        self.install_old()
        (self.root / PIN).write_text('tuned\n')
        for args in ([], ['--check']):
            out = self.run_pkg(self.old, *args).stdout
            self.assertIn(f'Project-owned, changed locally: {PIN}', out)
        (self.root / '_shared/REVIEW.md').write_text('drift\n')
        before = tree(self.root)
        result = self.run_pkg(self.old, ok=False)
        self.assertIn('_shared/REVIEW.md', result.stderr)
        self.assertEqual(before, tree(self.root))

    def test_overwrite_customized_requires_upgrade(self):
        result = self.run_pkg(self.new, '--overwrite-customized', ok=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn('--upgrade', result.stderr)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_busy_lock(self):
        self.install_old()
        (self.root / '.sdlc-init-lock').mkdir()
        before = tree(self.root)
        result = self.run_pkg(self.new, '--upgrade', ok=False)
        self.assertIn('busy', result.stderr)
        self.assertEqual(before, tree(self.root))

    def test_symlink_destination_refused(self):
        self.install_old()
        outside = Path(self.temp.name) / 'outside.md'
        outside.write_text('outside')
        (self.root / '_shared/REVIEW.md').unlink()
        (self.root / '_shared/REVIEW.md').symlink_to(outside)
        result = self.run_pkg(self.new, '--upgrade', ok=False)
        self.assertEqual(outside.read_text(), 'outside')
        self.assertIn('Symlink', result.stderr)
        self.assertEqual(self.receipt()['version'], '1.4.0')
        self.assertFalse((self.root / '_shared/extra.md').exists())


if __name__ == '__main__':
    unittest.main()
