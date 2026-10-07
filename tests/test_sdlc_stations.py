"""Stations: per-stage trigger tables decide which skills load; the model never chooses.

End-to-end and offline. A fake router (STATIONS_ROUTE_BIN) stands in for tink-route and logs
every call, so tests can prove stage open and pull make none. Temp git repos only.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'assets'
TOOL = ASSETS / '_system/scripts/stations.py'
INIT = ROOT / 'scripts/init.py'
PYTHON = shutil.which('python3') or sys.executable
GIT = shutil.which('git')
IDENT = {'GIT_AUTHOR_NAME': 'Launcher', 'GIT_AUTHOR_EMAIL': 'l@local',
         'GIT_COMMITTER_NAME': 'Launcher', 'GIT_COMMITTER_EMAIL': 'l@local'}
STAGE_DIRS = ['01-plan', '02-design', '03-build', '04-test', '05-deploy', '06-maintain']

FAKE = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
need = sys.argv[-1]
table = json.load(open(os.environ["FAKE_TABLE"]))
w = table.get(need)
if isinstance(w, list):  # scripted votes
    path = os.environ["FAKE_STATE"]
    st = json.load(open(path)) if os.path.exists(path) else {}
    i = st.get(need, 0); st[need] = i + 1; json.dump(st, open(path, "w")); w = w[i % len(w)]
print(json.dumps({"status": "routed" if w else "no_skill", "winner": w, "probability": 0.97 if w else 0.0,
                  "runner_up": None, "contract_version": 1}))
sys.exit(0 if w else 1)
"""

TRIGGERS = {
    'version': 1,
    'stage': '04-test',
    'max_pulls': 2,
    'max_src_files': 5,
    'triggers': [
        {'id': 'form-input', 'why': 'the diff adds form fields', 'need': 'users will type garbage into every field',
         'expect': 'break-ui', 'when': {'paths': r'\.html$', 'added': '<input'}},
        {'id': 'ui-pr', 'why': 'two UI files changed', 'need': 'the pull request has UI changes',
         'expect': 'visual-pr', 'when': {'paths': r'\.(html|css)$', 'min_files': 2}},
        {'id': 'lessons', 'why': 'always capture lessons', 'need': 'capture what we learned from the run',
         'expect': 'reflect', 'when': {'always': True}},
        {'id': 'ios', 'why': 'swift project', 'need': 'write the SwiftUI view model',
         'expect': 'write-swift', 'when': {'profile': ['ios']}},
    ],
}
TABLE = {'users will type garbage into every field': 'break-ui', 'the pull request has UI changes': 'visual-pr',
         'capture what we learned from the run': 'reflect', 'write the SwiftUI view model': 'write-swift'}


def sha16(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def git(repo, *args, env=None):
    return subprocess.run([GIT, '-C', str(repo), '-c', 'user.email=t@t', '-c', 'user.name=t', *args], check=True,
                          capture_output=True, text=True, env=env)


def clean_env(base, extra=None):
    """PATH with only python3 and git, so a real tink-route can never be reached."""
    tools = Path(base) / 'tools'
    if not tools.exists():
        tools.mkdir()
        (tools / 'python3').symlink_to(PYTHON)
        (tools / 'git').symlink_to(GIT)
    env = {'PATH': f'{tools}:/usr/bin:/bin', 'HOME': str(Path(base) / 'home'), 'GIT_CONFIG_GLOBAL': '/dev/null',
           'GIT_CONFIG_NOSYSTEM': '1', **(extra or {})}
    Path(env['HOME']).mkdir(exist_ok=True)
    return env


class ToolBase(unittest.TestCase):
    """A library of four skills, a fake router and one TRIGGERS.json."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.t = Path(self.temp.name).resolve()
        self.lib = self.t / 'lib'
        for n in ('break-ui', 'visual-pr', 'reflect', 'write-swift'):
            (self.lib / n).mkdir(parents=True)
            (self.lib / n / 'SKILL.md').write_text(f'---\nname: {n}\n---\n')
        self.fake = self.t / 'fake-route'
        self.fake.write_text(FAKE)
        self.fake.chmod(0o755)
        self.table = self.t / 'table.json'
        self.log = self.t / 'route.log'
        self.triggers = self.t / 'TRIGGERS.json'
        self.write_triggers(TRIGGERS)
        self.env = clean_env(self.t, {'STATIONS_ROUTE_BIN': str(self.fake), 'FAKE_TABLE': str(self.table),
                                      'FAKE_STATE': str(self.t / 'state.json'), 'FAKE_LOG': str(self.log)})
        self.set_table(TABLE)

    def write_triggers(self, doc):
        self.triggers.write_text(json.dumps(doc, indent=2) + '\n')

    def edited(self, fn):
        doc = json.loads(json.dumps(TRIGGERS))
        fn(doc)
        self.write_triggers(doc)
        return doc

    def set_table(self, d):
        self.table.write_text(json.dumps(d))
        state = self.t / 'state.json'
        if state.exists():
            state.unlink()

    def tool(self, *args):
        return subprocess.run([PYTHON, str(TOOL), *args], capture_output=True, text=True, env=self.env, cwd=str(self.t))

    def lock(self, *extra):
        return self.tool('lock', str(self.triggers), '--library', str(self.lib), *extra)

    def lock_file(self):
        return self.t / 'TRIGGERS.lock.json'

    def router_calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []


class ValidateTests(ToolBase):
    def validate(self):
        return self.tool('validate', str(self.triggers), '--library', str(self.lib))

    def test_good_file_passes(self):
        r = self.validate()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('ok', r.stdout)

    def test_unknown_key_missing_why_and_missing_skill_are_all_named(self):
        def change(d):
            d['bogus'] = 1
            del d['triggers'][3]['why']
            d['triggers'][3]['expect'] = 'no-such-skill'
            d['triggers'][0]['colour'] = 'red'
        self.edited(change)
        r = self.validate()
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown top-level key 'bogus'", r.stderr)
        self.assertIn("trigger 'ios': why is required", r.stderr)
        self.assertIn("trigger 'form-input': unknown key 'colour'", r.stderr)

    def test_missing_need_expect_and_when_are_named(self):
        def change(d):
            for key in ('need', 'expect', 'when'):
                del d['triggers'][1][key]
        self.edited(change)
        r = self.validate()
        self.assertEqual(r.returncode, 2)
        for key in ('need', 'expect', 'when'):
            self.assertIn(f"trigger 'ui-pr': {key} is required", r.stderr)

    def test_unknown_when_key_and_bad_regex_rejected(self):
        def change(d):
            d['triggers'][1]['when'] = {'paths': r'\.(html|css)$', 'min_fles': 2}
            d['triggers'][0]['when']['added'] = '<inp(ut'
        self.edited(change)
        r = self.validate()
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown when-key 'min_fles'", r.stderr)
        self.assertIn('not a regex', r.stderr)

    def test_duplicate_ids_rejected(self):
        self.edited(lambda d: d['triggers'][1].update(id='form-input'))
        r = self.validate()
        self.assertEqual(r.returncode, 2)
        self.assertIn("trigger 'form-input': duplicate id", r.stderr)

    def test_expect_must_exist_in_library(self):
        self.edited(lambda d: d['triggers'][2].update(expect='ghost'))
        r = self.validate()
        self.assertEqual(r.returncode, 2)
        self.assertIn("'ghost' is not in the library", r.stderr)

    def test_expect_checked_against_a_pool_lock_offline(self):
        pool = self.t / 'pool.lock.json'
        pool.write_text(json.dumps({'version': 1, 'skills': {n: {} for n in ('break-ui', 'visual-pr', 'reflect')}}))
        r = self.tool('validate', str(self.triggers), '--pool-lock', str(pool))
        self.assertEqual(r.returncode, 2)
        self.assertIn("'write-swift' is not in the pool lock", r.stderr)

    def test_invalid_json_is_an_error_not_a_traceback(self):
        self.triggers.write_text('{"version": 1,')
        r = self.validate()
        self.assertEqual(r.returncode, 2)
        self.assertNotIn('Traceback', r.stderr)


class LockCheckTests(ToolBase):
    def test_lock_writes_all_entries_and_check_passes(self):
        r = self.lock()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lk = json.loads(self.lock_file().read_text())
        self.assertEqual(set(lk['entries']), {'form-input', 'ui-pr', 'lessons', 'ios'})
        self.assertTrue(all(e['stable'] and e['matches_expect'] for e in lk['entries'].values()))
        self.assertEqual(lk['entries']['lessons']['need_sha'], sha16('capture what we learned from the run'))
        self.assertEqual((lk['votes_per_need'], lk['min_probability']), (3, 0.85))
        self.assertEqual(len(self.router_calls()), 12)  # 3 votes for each of 4 needs
        self.assertTrue(all(json.loads(c)[:3] == ['--pick', '--json', '--anywhere'] for c in self.router_calls()))
        before = self.lock_file().read_bytes()
        r = self.tool('check', str(self.triggers), '--library', str(self.lib))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.lock_file().read_bytes(), before)

    def test_only_the_need_text_is_sent(self):
        self.lock()
        sent = {json.loads(c)[-1] for c in self.router_calls()}
        self.assertEqual(sent, set(TABLE))

    def test_drift_when_router_disagrees_with_file_writes_nothing(self):
        self.set_table({**TABLE, 'capture what we learned from the run': 'write-swift'})
        r = self.lock()
        self.assertEqual(r.returncode, 1)
        self.assertIn('DRIFT', r.stdout)
        self.assertFalse(self.lock_file().exists())

    def test_unstable_votes_refuse_to_lock(self):
        self.set_table({**TABLE, 'the pull request has UI changes': ['visual-pr', None, 'visual-pr']})
        r = self.lock()
        self.assertEqual(r.returncode, 1)
        self.assertIn('UNSTABLE', r.stdout)
        self.assertFalse(self.lock_file().exists())

    def test_check_catches_changed_need_and_writes_nothing(self):
        self.lock()
        before = self.lock_file().read_bytes()
        self.edited(lambda d: d['triggers'][2].update(need='capture what we learned'))
        self.set_table({**TABLE, 'capture what we learned': 'reflect'})
        r = self.tool('check', str(self.triggers), '--library', str(self.lib))
        self.assertEqual(r.returncode, 1)
        self.assertIn('lessons: lock file is stale', r.stdout)
        self.assertEqual(self.lock_file().read_bytes(), before)

    def test_check_catches_later_drift(self):
        self.lock()
        self.set_table({**TABLE, 'write the SwiftUI view model': 'reflect'})
        r = self.tool('check', str(self.triggers), '--library', str(self.lib))
        self.assertEqual(r.returncode, 1)
        self.assertIn('DRIFT', r.stdout)

    def test_check_without_lock_fails(self):
        r = self.tool('check', str(self.triggers), '--library', str(self.lib))
        self.assertEqual(r.returncode, 1)
        self.assertIn('no lock file', r.stdout)
        self.assertFalse(self.lock_file().exists())

    def test_pool_hash_is_recorded_and_checked(self):
        pool = self.t / 'pool.lock.json'
        pool.write_text('{"version": 1, "skills": {}}\n')
        self.lock('--pool-lock', str(pool))
        self.assertEqual(json.loads(self.lock_file().read_text())['pool'], sha16(pool.read_text()))
        pool.write_text('{"version": 1, "skills": {"x": {}}}\n')
        r = self.tool('check', str(self.triggers), '--library', str(self.lib), '--pool-lock', str(pool))
        self.assertEqual(r.returncode, 1)
        self.assertIn('different pool', r.stdout)

    def test_router_error_is_exit_2(self):
        bad = self.t / 'bad-route'
        bad.write_text('#!/bin/sh\necho \'{"status": "error", "reason": "no_api_key"}\'\nexit 2\n')
        bad.chmod(0o755)
        self.env['STATIONS_ROUTE_BIN'] = str(bad)
        r = self.lock()
        self.assertEqual(r.returncode, 2)
        self.assertIn('no_api_key', r.stderr)


class PullTests(ToolBase):
    def setUp(self):
        super().setUp()
        self.repo = self.t / 'repo'
        self.repo.mkdir()
        git(self.repo, 'init', '-q', '-b', 'main')
        (self.repo / 'README.md').write_text('x\n')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-qm', 'base')
        self.assertEqual(self.lock().returncode, 0)
        self.log.unlink()

    def commit(self, files, delete=()):
        for n, c in files.items():
            p = self.repo / n
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(c)
        for n in delete:
            (self.repo / n).unlink()
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-qm', 'c')

    def pull(self, *extra, base=('--base', 'main~1'), head=('--head', 'main')):
        out = self.t / 'pulls.json'
        if out.exists():
            out.unlink()
        r = self.tool('pull', str(self.triggers), *base, *head, '--repo', str(self.repo), '--out', str(out), *extra)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.router_calls(), [], 'pull must never call the router')
        return json.loads(out.read_text()), r.stdout

    def status(self, j, tid):
        return next(r for r in j['sheet'] if r['id'] == tid)['status']

    def test_fires_on_diff_signals_and_always_and_honors_cap(self):
        self.commit({'a.html': '<input name=x>\n', 'b.css': 'p{}\n'})
        j, out = self.pull()
        self.assertEqual(j['pulls'], ['break-ui', 'visual-pr'])  # cap of 2 reached; 'lessons' skipped
        self.assertIn('cap of 2', self.status(j, 'lessons'))
        self.assertIn('3 of 4 triggers fired', out)
        self.assertIn("added /<input/ in a.html", next(r for r in j['sheet'] if r['id'] == 'form-input')['evidence'])

    def test_default_cap_is_five(self):
        def change(d):
            del d['max_pulls']
            d['triggers'] = [{'id': f'a{i}', 'why': 'w', 'need': 'capture what we learned from the run',
                              'expect': 'reflect', 'when': {'always': True}} for i in range(7)]
        self.edited(change)
        entry = json.loads(self.lock_file().read_text())['entries']['lessons']
        lock = json.loads(self.lock_file().read_text())
        lock['entries'] = {f'a{i}': entry for i in range(7)}
        self.lock_file().write_text(json.dumps(lock))
        self.commit({'notes.txt': 'hi\n'})
        j, _ = self.pull()
        self.assertEqual(len(j['pulls']), 5)
        self.assertIn('cap of 5', self.status(j, 'a6'))

    def test_nothing_fires_on_unrelated_diff_except_always(self):
        self.commit({'notes.txt': 'hi\n'})
        j, _ = self.pull()
        self.assertEqual(j['pulls'], ['reflect'])
        self.assertEqual(self.status(j, 'form-input'), 'not fired')

    def test_profile_trigger_needs_the_tag(self):
        self.commit({'notes.txt': 'hi\n'})
        self.assertNotIn('write-swift', self.pull()[0]['pulls'])
        j, out = self.pull('--tags', 'ios,web-ui')
        self.assertIn('write-swift', j['pulls'])
        self.assertEqual(j['tags'], ['ios', 'web-ui'])
        self.assertIn('tags: ios, web-ui', out)

    def test_deleted_min_and_dirs_min(self):
        def change(d):
            d['max_src_files'] = 40
            d['triggers'] = [
                {'id': 'removal', 'why': 'w', 'need': 'users will type garbage into every field', 'expect': 'break-ui',
                 'when': {'deleted_min': 3}},
                {'id': 'wide', 'why': 'w', 'need': 'the pull request has UI changes', 'expect': 'visual-pr',
                 'when': {'dirs_min': 3}}]
        self.edited(change)
        lock = json.loads(self.lock_file().read_text())
        lock['entries'] = {'removal': lock['entries']['form-input'], 'wide': lock['entries']['ui-pr']}
        self.lock_file().write_text(json.dumps(lock))
        self.commit({'a/1.py': 'x\n', 'b/2.py': 'x\n', 'old1.py': 'x\n', 'old2.py': 'x\n', 'old3.py': 'x\n'})
        j, _ = self.pull()
        self.assertEqual(j['pulls'], [])  # two folders, nothing deleted
        self.commit({'c/3.py': 'x\n', 'b/2.py': 'y\n', 'a/1.py': 'y\n'}, delete=['old1.py', 'old2.py', 'old3.py'])
        j, _ = self.pull()
        self.assertEqual(j['pulls'], ['break-ui', 'visual-pr'])
        self.assertIn('3 source files deleted', next(r for r in j['sheet'] if r['id'] == 'removal')['evidence'])

    def test_bulk_diff_skips_diff_triggers_but_keeps_tags_and_always(self):
        self.commit({f'f{i}.html': '<input>\n' for i in range(8)})
        j, _ = self.pull('--tags', 'ios')
        self.assertEqual(j['pulls'], ['reflect', 'write-swift'])
        self.assertIn('more than 5 source files', self.status(j, 'form-input'))

    def test_default_bulk_guard_is_forty_files(self):
        self.edited(lambda d: d.pop('max_src_files'))
        self.commit({f'f{i}.html': '<input>\n' for i in range(40)})
        self.assertIn('break-ui', self.pull()[0]['pulls'])
        self.commit({f'g{i}.html': '<input>\n' for i in range(41)})
        j, _ = self.pull()
        self.assertNotIn('break-ui', j['pulls'])
        self.assertIn('more than 40 source files', self.status(j, 'form-input'))

    def test_ignored_paths_do_not_count(self):
        self.commit({'runs/x/a.html': '<input>\n', 'docs/b.css': 'p{}\n', 'c.html.lock': '<input>\n'})
        self.assertEqual(self.pull()[0]['pulls'], ['reflect'])

    def test_unlocked_trigger_is_skipped_with_reason(self):
        self.lock_file().unlink()
        self.commit({'a.html': '<input>\n'})
        j, _ = self.pull()
        self.assertEqual(j['pulls'], [])
        self.assertIn('not in lock file', self.status(j, 'form-input'))

    def test_changed_need_text_invalidates_lock_entry(self):
        self.edited(lambda d: d['triggers'][0].update(need='users will type garbage into every box'))
        self.commit({'a.html': '<input>\n'})
        self.assertIn('need text changed', self.status(self.pull()[0], 'form-input'))

    def test_unstable_lock_entry_is_skipped(self):
        lock = json.loads(self.lock_file().read_text())
        lock['entries']['lessons']['stable'] = False
        self.lock_file().write_text(json.dumps(lock))
        self.commit({'notes.txt': 'hi\n'})
        j, _ = self.pull()
        self.assertEqual(j['pulls'], [])
        self.assertIn('unstable or disagrees', self.status(j, 'lessons'))

    def test_default_base_is_the_merge_base_with_main(self):
        git(self.repo, 'checkout', '-q', '-b', 'feature')
        self.commit({'a.html': '<input>\n'})
        self.commit({'notes.txt': 'more\n'})
        j, out = self.pull(base=(), head=())
        self.assertIn('break-ui', j['pulls'])
        self.assertEqual(j['base'], 'main')
        self.assertIn('merge-base with main', out)

    def test_no_default_branch_skips_diff_triggers_and_keeps_the_rest(self):
        git(self.repo, 'branch', '-m', 'main', 'trunk')
        self.commit({'a.html': '<input>\n'})
        j, out = self.pull('--tags', 'ios', base=(), head=())
        self.assertEqual(j['pulls'], ['reflect', 'write-swift'])
        self.assertEqual(sum('diff triggers skipped' in line for line in out.splitlines()), 1)
        self.assertEqual(self.status(j, 'form-input'), 'skipped: no diff base')
        self.assertIsNone(j['base'])


class PoolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.t = Path(self.temp.name).resolve()
        self.env = clean_env(self.t)
        src = self.t / 'src'
        for name, desc in (('alpha', 'Only use when invoked by name.'), ('beta', 'Beta things.')):
            (src / 'skills' / name).mkdir(parents=True)
            (src / 'skills' / name / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: "{desc}"\n---\nbody\n')
        (src / 'other' / 'gamma').mkdir(parents=True)
        (src / 'other' / 'gamma' / 'SKILL.md').write_text('---\nname: gamma\ndescription: g\n---\n')
        git(src, 'init', '-q', '-b', 'main')
        git(src, 'remote', 'add', 'origin', f'file://{src}')
        git(src, 'add', '.')
        git(src, 'commit', '-qm', 'x')
        self.src = src
        self.lock = self.t / 'pool.lock.json'

    def tool(self, *args):
        return subprocess.run([PYTHON, str(TOOL), 'pool', *args], capture_output=True, text=True, env=self.env)

    def pin(self):
        r = self.tool('pin', '--sources', f'x={self.src}#skills', '--out', str(self.lock))
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(self.lock.read_text())

    def test_pin_build_roundtrip_with_overlay(self):
        pinned = self.pin()
        self.assertEqual(set(pinned['skills']), {'alpha', 'beta'})  # #skills restricts the source
        self.assertEqual(pinned['skills']['alpha']['path'], 'skills/alpha')
        overlay = self.t / 'ov.json'
        overlay.write_text(json.dumps({'alpha': {'description': 'Do the alpha thing.'}}))
        r = self.tool('build', '--lock', str(self.lock), '--dest', str(self.t / 'lib'), '--overlay', str(overlay))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('description: "Do the alpha thing."', (self.t / 'lib/alpha/SKILL.md').read_text())
        self.assertIn('description: "Beta things."', (self.t / 'lib/beta/SKILL.md').read_text())
        self.assertIn('built 2 skills (1 with routing descriptions)', r.stdout)

    def test_non_empty_destination_refused(self):
        self.pin()
        dest = self.t / 'lib'
        dest.mkdir()
        (dest / 'keep.txt').write_text('mine')
        r = self.tool('build', '--lock', str(self.lock), '--dest', str(dest))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('is not empty', r.stderr)
        self.assertEqual([p.name for p in dest.iterdir()], ['keep.txt'])

    def test_digest_mismatch_refused(self):
        d = self.pin()
        d['skills']['alpha']['digest'] = '0' * 64
        self.lock.write_text(json.dumps(d))
        r = self.tool('build', '--lock', str(self.lock), '--dest', str(self.t / 'lib2'))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('digest mismatch', r.stderr)

    def test_overlay_must_name_pinned_skills(self):
        self.pin()
        overlay = self.t / 'ov2.json'
        overlay.write_text(json.dumps({'ghost': {'description': 'x'}}))
        r = self.tool('build', '--lock', str(self.lock), '--dest', str(self.t / 'lib3'), '--overlay', str(overlay))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('not in the lock', r.stderr)
        self.assertFalse((self.t / 'lib3').exists())

    def test_never_touches_the_home_library(self):
        self.pin()
        home_lib = Path(self.env['HOME']) / '.tink-library'
        r = self.tool('build', '--lock', str(self.lock), '--dest', str(home_lib / 'skills'))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('.tink-library', r.stderr)
        self.assertFalse(home_lib.exists())
        r = self.tool('build', '--lock', str(self.lock), '--dest', str(self.t / 'elsewhere'))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(home_lib.exists())


class ScaffoldBase(unittest.TestCase):
    """An installed scaffold in a git repo on `main`, a fake router that logs calls, and no tink-route on PATH."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'work' / 'proj'
        self.root.mkdir(parents=True)
        self.route_log = self.base / 'route.log'
        fake = self.base / 'fake-route'
        fake.write_text(FAKE)
        fake.chmod(0o755)
        (self.base / 'table.json').write_text('{}')
        self.env = clean_env(self.base, {'STATIONS_ROUTE_BIN': str(fake), 'FAKE_LOG': str(self.route_log),
                                         'FAKE_TABLE': str(self.base / 'table.json'),
                                         'FAKE_STATE': str(self.base / 'state.json'), **IDENT})
        self.assertIsNone(shutil.which('tink-route', path=self.env['PATH']), 'tink-route must be off PATH')
        self.assertIsNone(shutil.which('tink', path=self.env['PATH']))
        self.run_cmd([PYTHON, str(INIT), str(self.root)])
        self.git('init', '-q', '-b', 'main')
        self.git('add', '-A')
        self.git('commit', '-qm', 'baseline')

    def run_cmd(self, argv, ok=True, cwd='/'):
        result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, env=self.env)
        if ok is not None:
            self.assertEqual(result.returncode == 0, ok, ' '.join(map(str, argv)) + '\n' + result.stdout + result.stderr)
        return result

    def git(self, *args, cwd=None):
        return self.run_cmd([GIT, '-C', str(cwd or self.root), *args])

    def sdlc(self, *args, ok=True, root=None):
        return self.run_cmd([PYTHON, str((root or self.root) / '_system/scripts/sdlc.py'), *args], ok=ok)

    def new_run(self, run, *tags, profile='light'):
        self.sdlc('new', run, '--profile', profile, *[x for t in tags for x in ('--tag', t)])
        (self.root / 'runs' / run / 'checklist.json').write_text(json.dumps(
            {'schema': 1, 'items': [{'id': 'a', 'description': 'd', 'verify': 'v'}]}))
        for s in ([3] if profile == 'light' else [1, 2, 3]):
            self.sdlc('decide', run, str(s), 'approved', '--reviewer', 'h', '--source', 's', '--reason', 'r')

    def sheet(self, run, n, root=None):
        return json.loads(((root or self.root) / f'runs/{run}/skills/stage-{n}-pulls.json').read_text())

    def router_calls(self):
        return self.route_log.read_text().splitlines() if self.route_log.exists() else []


class StageOpenTests(ScaffoldBase):
    def test_stage_open_prints_records_and_names_pulls_without_any_router_call(self):
        self.new_run('r', 'ios')
        result = self.sdlc('stage', 'r', '3')
        self.assertIn('03-build: 1 of 4 triggers fired; 1 skill(s) pulled: write-swift', result.stdout)
        self.assertIn('PULL  write-swift', result.stdout)
        self.assertIn('pull sheet: runs/r/skills/stage-3-pulls.json', result.stdout)
        prompt = result.stdout.splitlines()[-1]
        self.assertIn('Pulled skills for this stage (runs/r/skills/stage-3-pulls.json): write-swift', prompt)
        sheet = self.sheet('r', 3, root=self.root.parent / 'proj-r')  # committed and carried into the worktree
        self.assertEqual((sheet['stage'], sheet['pulls'], sheet['tags']), ('03-build', ['write-swift'], ['ios']))
        self.assertEqual(self.router_calls(), [])
        self.assertEqual(self.git('status', '--porcelain', '--', 'runs/r').stdout, '')

    def test_every_launcher_stage_writes_its_sheet(self):
        self.new_run('f', 'existing-codebase', 'web-ui', 'has-verification-skill', profile='full')
        expected = {1: ['blast-radius'], 2: ['pick-ui-library'], 6: ['reflect', 'maintain-verification-skill']}
        for n, pulls in expected.items():
            with self.subTest(stage=n):
                result = self.sdlc('stage', 'f', str(n), '--here')
                self.assertEqual(self.sheet('f', n)['pulls'], pulls)
                self.assertIn(', '.join(pulls), result.stdout.splitlines()[-1])
        self.assertEqual(self.router_calls(), [])

    def test_stage_five_sheet_sees_the_branch_diff(self):
        self.new_run('r')
        self.sdlc('stage', 'r', '3', '--here')
        self.git('checkout', '-q', '-b', 'r')
        for i in range(2):
            (self.root / f'page{i}.html').write_text('<p>x</p>\n')
        self.git('add', '-A')
        self.git('commit', '-qm', 'ui')
        (self.root / '_system/verification.json').write_text(json.dumps(
            {'require_tink': False, 'checks': [{'argv': ['python3', '-c', 'pass'], 'timeout_seconds': 20}]}))
        self.git('add', '-A')
        self.git('commit', '-qm', 'checks')
        self.sdlc('mark', 'r', 'a', 'passed', '--evidence', 'seen')
        self.sdlc('verify', 'r')
        self.git('add', '-A')
        self.git('commit', '-qm', 'evidence')
        result = self.sdlc('stage', 'r', '5', '--here')
        self.assertIn('merge-base with main', result.stdout)
        self.assertEqual(self.sheet('r', 5)['pulls'], ['visual-pr'])
        self.assertEqual(self.router_calls(), [])

    def test_no_pulls_means_no_prompt_sentence(self):
        self.new_run('r')
        result = self.sdlc('stage', 'r', '3', '--here')
        self.assertIn('0 of 4 triggers fired; 0 skill(s) pulled', result.stdout)
        self.assertNotIn('Pulled skills', result.stdout)
        self.assertEqual(self.sheet('r', 3)['pulls'], [])

    def test_a_broken_trigger_file_prints_one_line_and_never_blocks(self):
        self.new_run('r')
        (self.root / 'stages/03-build/TRIGGERS.json').write_text('{"version": 1,')
        result = self.sdlc('stage', 'r', '3', '--here')
        lines = [line for line in result.stdout.splitlines() if line.startswith('pulls: skipped')]
        self.assertEqual(len(lines), 1, result.stdout)
        self.assertIn('Launch prompt', result.stdout)
        self.assertFalse((self.root / 'runs/r/skills').exists())

    def test_missing_tool_never_blocks(self):
        self.new_run('r')
        (self.root / '_system/scripts/stations.py').unlink()
        result = self.sdlc('stage', 'r', '3', '--here')
        self.assertIn('pulls: skipped (stations.py is missing', result.stdout)
        self.assertIn('Launch prompt', result.stdout)

    def test_check_prints_the_sheet_and_writes_nothing(self):
        self.new_run('r', 'ios')
        result = self.sdlc('stage', 'r', '3', '--check')
        self.assertIn('1 skill(s) pulled: write-swift', result.stdout)
        self.assertFalse((self.root / 'runs/r/skills').exists())
        self.assertEqual(self.router_calls(), [])

    def test_reopening_with_an_unchanged_sheet_needs_no_commit(self):
        self.new_run('r', 'ios')
        self.sdlc('stage', 'r', '1', '--here')
        self.git('add', '-A')
        self.git('commit', '-qm', 'sheet')
        result = self.sdlc('stage', 'r', '3', '--worktree', str(self.base / 'wt'))
        self.assertIn('Checkout:', result.stdout)
        result = self.sdlc('stage', 'r', '1', '--worktree', str(self.base / 'wt2'))
        self.assertEqual(self.git('status', '--porcelain').stdout, '')


class Stage4PullTests(ScaffoldBase):
    def test_pull_writes_the_stage_four_sheet_from_the_branch_diff(self):
        self.new_run('r', 'web-ui')
        self.sdlc('stage', 'r', '3')
        worktree = self.root.parent / 'proj-r'
        (worktree / 'src').mkdir()
        (worktree / 'src/form.html').write_text('<form>\n<input name="email">\n</form>\n')
        self.git('add', '-A', cwd=worktree)
        self.git('commit', '-qm', 'form', cwd=worktree)
        result = self.sdlc('pull', 'r', '4', root=worktree)
        self.assertIn('04-test: 2 of 4 triggers fired; 2 skill(s) pulled: break-ui, create-verification-skill', result.stdout)
        sheet = self.sheet('r', 4, root=worktree)
        self.assertEqual(sheet['pulls'], ['break-ui', 'create-verification-skill'])
        self.assertIn('src/form.html', next(r for r in sheet['sheet'] if r['id'] == 'form-input')['evidence'])
        self.assertEqual(self.router_calls(), [])

    def test_pull_works_for_every_stage_and_rejects_unknown_stages(self):
        self.new_run('r')
        for n in range(1, 7):
            self.sdlc('pull', 'r', str(n))
            self.assertTrue((self.root / f'runs/r/skills/stage-{n}-pulls.json').is_file())
        self.assertIn('stage must be one of', self.sdlc('pull', 'r', '7', ok=False).stderr)
        self.assertIn('Run is missing', self.sdlc('pull', 'ghost', '4', ok=False).stderr)

    def test_stage_four_context_tells_the_agent_to_pull(self):
        text = (ASSETS / 'stages/04-test/CONTEXT.md').read_text()
        self.assertIn('python3 _system/scripts/sdlc.py pull <slug> 4', text)
        self.assertIn('runs/<slug>/skills/stage-4-pulls.json', text)


class OldPickRemovedTests(unittest.TestCase):
    PATTERN = re.compile(r'stage-(\d+|<n>|\{n\})-pick|stage_pick|PICK_LIMIT|PICK_TIMEOUT')

    def test_whole_document_pick_is_gone(self):
        roots = [ASSETS, ROOT / 'scripts', ROOT / 'tests', ROOT / 'docs', ROOT / 'README.md', ROOT / 'SKILL.md']
        found = []
        for top in roots:
            for path in ([top] if top.is_file() else sorted(p for p in top.rglob('*') if p.is_file())):
                if path == Path(__file__).resolve() or '__pycache__' in path.parts or path.suffix in ('.gz', '.pyc'):
                    continue
                for number, line in enumerate(path.read_text(errors='ignore').splitlines(), 1):
                    if self.PATTERN.search(line):
                        found.append(f'{path.relative_to(ROOT)}:{number}')
        self.assertEqual(found, [])

    def test_contexts_name_the_pull_sheet(self):
        for stage in STAGE_DIRS:
            with self.subTest(stage=stage):
                text = (ASSETS / f'stages/{stage}/CONTEXT.md').read_text()
                self.assertIn('runs/<slug>/skills/stage-', text)
                self.assertIn('pulls.json', text)
                self.assertNotIn('stage-open pick', text)


class TagTests(ScaffoldBase):
    def test_tags_are_stored_sorted_and_unique(self):
        self.sdlc('new', 'r', '--tag', 'web-ui', '--tag', 'ios', '--tag', 'ios')
        self.assertEqual(json.loads((self.root / 'runs/r/run.json').read_text())['tags'], ['ios', 'web-ui'])
        self.sdlc('new', 'plain')
        self.assertEqual(json.loads((self.root / 'runs/plain/run.json').read_text())['tags'], [])

    def test_unknown_tag_is_refused_with_the_known_list(self):
        result = self.sdlc('new', 'r', '--tag', 'iso', ok=False)
        self.assertIn('unknown tag iso', result.stderr)
        self.assertIn('known tags: agent-loop, existing-codebase, has-verification-skill, ios, ui-motion, web-ui',
                      result.stderr)
        self.assertFalse((self.root / 'runs/r').exists())

    def test_known_tags_follow_the_trigger_files(self):
        path = self.root / 'stages/03-build/TRIGGERS.json'
        doc = json.loads(path.read_text())
        doc['triggers'][0]['when']['profile'] = ['swift-app']
        path.write_text(json.dumps(doc))
        self.sdlc('new', 'r', '--tag', 'swift-app')
        self.assertIn('known tags:', self.sdlc('new', 'q', '--tag', 'nope', ok=False).stderr)

    def test_tags_reach_the_pull(self):
        self.new_run('with', 'existing-codebase')
        self.new_run('without')
        self.assertEqual(self.sheet_for('with'), ['blast-radius'])
        self.assertEqual(self.sheet_for('without'), [])

    def sheet_for(self, run):
        self.sdlc('pull', run, '1')
        sheet = self.sheet(run, 1)
        return sheet['pulls']


class WalkW8Tests(ScaffoldBase):
    def w8(self, ok=True):
        result = self.sdlc('walk', '--json', ok=ok)
        return next(c for c in json.loads(result.stdout)['checks'] if c['id'] == 'W8')

    def test_shipped_locks_pass_offline(self):
        c = self.w8()
        self.assertEqual(c['status'], 'pass', c)
        self.assertEqual(c['name'], 'triggers-locked')
        self.assertIn('17 triggers in 6 stage(s)', c['title'])
        self.assertEqual(self.router_calls(), [])

    def test_changed_need_fails(self):
        path = self.root / 'stages/05-deploy/TRIGGERS.json'
        doc = json.loads(path.read_text())
        doc['triggers'][0]['need'] += ' quickly'
        path.write_text(json.dumps(doc))
        c = self.w8(ok=False)
        self.assertEqual(c['status'], 'fail')
        self.assertTrue(any("'ui-pr': need changed since lock" in item for item in c['items']), c)

    def test_changed_pool_fails(self):
        path = self.root / '.tink/pool.lock.json'
        path.write_text(path.read_text() + '\n')
        c = self.w8(ok=False)
        self.assertEqual(len([i for i in c['items'] if 'does not match .tink/pool.lock.json' in i]), 6, c)

    def test_expect_that_disagrees_with_the_lock_fails(self):
        path = self.root / 'stages/06-maintain/TRIGGERS.json'
        doc = json.loads(path.read_text())
        doc['triggers'][0]['expect'] = 'why'
        path.write_text(json.dumps(doc))
        c = self.w8(ok=False)
        self.assertTrue(any("lock says 'reflect', file expects 'why'" in item for item in c['items']), c)

    def test_unstable_or_missing_entry_fails(self):
        path = self.root / 'stages/01-plan/TRIGGERS.lock.json'
        doc = json.loads(path.read_text())
        doc['entries']['existing-codebase']['stable'] = False
        path.write_text(json.dumps(doc))
        lock4 = self.root / 'stages/04-test/TRIGGERS.lock.json'
        doc4 = json.loads(lock4.read_text())
        del doc4['entries']['perf-claim']
        lock4.write_text(json.dumps(doc4))
        items = self.w8(ok=False)['items']
        self.assertTrue(any('not stable' in i for i in items), items)
        self.assertTrue(any("'perf-claim': not in the lock file" in i for i in items), items)

    def test_no_trigger_files_passes_as_skipped(self):
        for stage in STAGE_DIRS:
            (self.root / f'stages/{stage}/TRIGGERS.json').unlink()
        c = self.w8(ok=None)  # W2 now reports the stage contracts' dead pointers to the tables; W8 itself passes
        self.assertEqual((c['status'], c['title']), ('pass', 'skipped (no stages/*/TRIGGERS.json)'))


def make_package(dest, version, drop=()):
    """Copy scripts/ and assets/, drop some payload files, regenerate the manifest at `version`."""
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc')
    shutil.copytree(str(ROOT / 'scripts'), str(dest / 'scripts'), ignore=ignore)
    shutil.copytree(str(ASSETS), str(dest / 'assets'), ignore=ignore)
    manifest = json.loads((dest / 'assets/manifest.json').read_text())
    manifest['version'] = version
    (dest / 'assets/manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    for name in drop:
        (dest / 'assets' / name).unlink()
    result = subprocess.run([PYTHON, str(dest / 'scripts/package.py'), '--version', version], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return dest


class ScaffoldTests(unittest.TestCase):
    SHIPPED = ['_system/scripts/stations.py', '.tink/pool.lock.json', '.tink/pool-overlay.json'] + \
        [f'stages/{s}/TRIGGERS{x}.json' for s in STAGE_DIRS for x in ('', '.lock')]
    OWNED = SHIPPED[1:]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.t = Path(self.temp.name).resolve()
        self.manifest = json.loads((ASSETS / 'manifest.json').read_text())

    def init(self, target, *args, package=ROOT):
        return subprocess.run([PYTHON, str(package / 'scripts/init.py'), str(target), *args], capture_output=True, text=True)

    def test_manifest_lists_the_tool_as_managed_and_the_tables_as_project_owned(self):
        self.assertEqual(self.manifest['version'], '1.21.0')
        for name in self.SHIPPED:
            self.assertIn(name, self.manifest['files'], name)
        owned = set(self.manifest['projectOwned'])
        self.assertTrue(set(self.OWNED) <= owned, set(self.OWNED) - owned)
        self.assertNotIn('_system/scripts/stations.py', owned)
        r = subprocess.run([PYTHON, str(ROOT / 'scripts/package.py'), '--check'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_init_installs_the_tool_tables_and_pool(self):
        target = self.t / 'proj'
        target.mkdir()
        r = self.init(target)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for name in self.SHIPPED:
            self.assertEqual((target / name).read_bytes(), (ASSETS / name).read_bytes(), name)
        self.assertTrue(os.access(target / '_system/scripts/stations.py', os.X_OK))

    def test_upgrade_creates_the_files_and_never_overwrites_project_owned_ones(self):
        old = make_package(self.t / 'old', '1.20.0', drop=self.SHIPPED)
        target = self.t / 'proj'
        target.mkdir()
        self.assertEqual(self.init(target, package=old).returncode, 0)
        self.assertFalse((target / 'stages/03-build/TRIGGERS.json').exists())
        r = self.init(target, '--upgrade')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for name in self.SHIPPED:
            self.assertTrue((target / name).is_file(), name)
        # a project edits its own card, then a later release changes the shipped card and the tool
        mine = '{"version": 1, "stage": "03-build", "triggers": []}\n'
        (target / 'stages/03-build/TRIGGERS.json').write_text(mine)
        newer = self.t / 'newer'
        shutil.copytree(str(ROOT / 'scripts'), str(newer / 'scripts'))
        shutil.copytree(str(ASSETS), str(newer / 'assets'), ignore=shutil.ignore_patterns('__pycache__'))
        for name in ('stages/03-build/TRIGGERS.json', '.tink/pool-overlay.json'):
            (newer / 'assets' / name).write_text('{"upstream": "changed"}\n')
        (newer / 'assets/_system/scripts/stations.py').write_text((ASSETS / '_system/scripts/stations.py').read_text() + '\n# newer\n')
        subprocess.run([PYTHON, str(newer / 'scripts/package.py'), '--version', '1.21.1'], check=True, capture_output=True)
        r = self.init(target, '--upgrade', package=newer)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((target / 'stages/03-build/TRIGGERS.json').read_text(), mine)
        self.assertIn('stages/03-build/TRIGGERS.json (changed locally; upstream also changed)', r.stdout)
        self.assertEqual((target / '.tink/pool-overlay.json').read_text(), '{"upstream": "changed"}\n')  # unmodified: updated
        self.assertTrue((target / '_system/scripts/stations.py').read_text().endswith('# newer\n'))

    def test_fresh_install_refuses_an_existing_trigger_file(self):
        target = self.t / 'proj'
        (target / '.tink').mkdir(parents=True)
        (target / '.tink/pool-overlay.json').write_text('{}')
        r = self.init(target)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual((target / '.tink/pool-overlay.json').read_text(), '{}')


class ShippedSetTests(unittest.TestCase):
    def tool(self, *args):
        return subprocess.run([PYTHON, str(TOOL), *args], capture_output=True, text=True, cwd=str(ROOT))

    def files(self):
        return [str(ASSETS / f'stages/{s}/TRIGGERS.json') for s in STAGE_DIRS]

    def test_seventeen_triggers_validate_against_the_pool(self):
        pool = ASSETS / '.tink/pool.lock.json'
        r = self.tool('validate', *self.files(), '--pool-lock', str(pool))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        count = sum(len(json.loads(Path(f).read_text())['triggers']) for f in self.files())
        self.assertEqual(count, 17)
        self.assertEqual(len(json.loads(pool.read_text())['skills']), 71)

    def test_locks_are_consistent_with_triggers_and_pool(self):
        pool = ASSETS / '.tink/pool.lock.json'
        r = self.tool('lint', *self.files(), '--pool-lock', str(pool))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for f in self.files():
            lock = json.loads(Path(f.replace('TRIGGERS.json', 'TRIGGERS.lock.json')).read_text())
            doc = json.loads(Path(f).read_text())
            self.assertEqual(lock['pool'], sha16(pool.read_text()))
            self.assertEqual(lock['stage'], doc['stage'])
            self.assertEqual(f'/{doc["stage"]}/', '/' + Path(f).parent.name + '/')
            for t in doc['triggers']:
                e = lock['entries'][t['id']]
                self.assertEqual((e['need_sha'], e['skill'], e['stable'], len(e['votes'])),
                                 (sha16(t['need']), t['expect'], True, 3), t['id'])
                self.assertGreaterEqual(e['min_probability'], 0.85)

    def test_overlay_names_pinned_skills_and_when_keys_are_closed(self):
        skills = json.loads((ASSETS / '.tink/pool.lock.json').read_text())['skills']
        overlay = json.loads((ASSETS / '.tink/pool-overlay.json').read_text())
        self.assertEqual(len(overlay), 9)
        for name, value in overlay.items():
            self.assertIn(name, skills)
            self.assertEqual(set(value), {'description'})
        whens = [t['when'] for f in self.files() for t in json.loads(Path(f).read_text())['triggers']]
        self.assertTrue(all(set(w) <= {'always', 'profile', 'paths', 'min_files', 'added', 'added_min',
                                       'deleted_min', 'dirs_min'} for w in whens))

    def test_tool_runs_on_python_39_syntax(self):
        source = TOOL.read_text()
        self.assertNotIn('tomllib', source)
        self.assertNotRegex(source, r'^\s*match\s+\S+:\s*$')
        compile(source, str(TOOL), 'exec')


if __name__ == '__main__':
    unittest.main()
