#!/usr/bin/env python3
"""stations: per-stage trigger tables, so Jev (tink-route) chooses the skills, not the model.

    stations.py validate TRIGGERS.json... [--library DIR | --pool-lock FILE]
    stations.py lock     TRIGGERS.json... [--library DIR] [--pool-lock FILE]   # ask Jev 3x per need, write TRIGGERS.lock.json
    stations.py check    TRIGGERS.json... [--library DIR] [--pool-lock FILE]   # same calls, write nothing, fail on drift
    stations.py lint     TRIGGERS.json... --pool-lock FILE                     # offline: locks match triggers and pool
    stations.py pull     TRIGGERS.json [--base REF] [--head REF] [--tags a,b] [--out FILE] [--repo DIR]
    stations.py pool pin   --sources NAME=PATH[#SUBDIR]... --out pool.lock.json
    stations.py pool build --lock pool.lock.json --dest DIR [--overlay pool-overlay.json]

Standard library only, Python 3.9+. The only router call is `lock`/`check`, and it only ever sends
the fixed `need` lines written in the trigger file, never code, diffs or seeds. `pull` and `lint`
make no router call and need no network. The router command is $STATIONS_ROUTE_BIN, else tink-route.
Exit codes: 0 ok, 1 drift, unstable or stale, 2 invalid file or tool error.
"""
import argparse
import collections
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

VERSION = 1
VOTES = 3            # Jev calls per need
MIN_PROB = 0.85      # every vote must reach this
ROUTE_TIMEOUT = 120
DEFAULT_MAX_PULLS = 5
DEFAULT_MAX_SRC_FILES = 40
DEFAULT_IGNORE = [r'(^|/)(\.agents|\.claude|\.tink|runs|node_modules|vendor|docs|dist|build)/', r'\.lock$',
                  r'lock\.json$', r'SKILL\.md$']
STAGE_KEYS = {'version', 'stage', 'max_pulls', 'max_src_files', 'ignore', 'triggers'}
TRIGGER_KEYS = {'id', 'why', 'need', 'expect', 'when'}
WHEN_KEYS = {'always', 'profile', 'paths', 'min_files', 'added', 'added_min', 'deleted_min', 'dirs_min'}
DIFF_KEYS = {'paths', 'min_files', 'added', 'added_min', 'deleted_min', 'dirs_min'}
INT_KEYS = ('min_files', 'added_min', 'deleted_min', 'dirs_min')
ID_RE = re.compile(r'^[a-z][a-z0-9-]*$')
SKIP_DIRS = {'automations'}


class Problem(Exception):
    pass


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def read_json_file(path, what='file'):
    path = Path(path)
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise Problem(f'{path}: cannot read {what}: {error}')


# --- trigger files -----------------------------------------------------------

def check_when(where, when, errs):
    if not isinstance(when, dict):
        errs.append(f'{where}: when must be an object')
        return
    for k in sorted(set(when) - WHEN_KEYS):
        errs.append(f"{where}: unknown when-key {k!r} (allowed: {', '.join(sorted(WHEN_KEYS))})")
    if not when:
        errs.append(f'{where}: when is empty; use "when": {{"always": true}} to fire every time')
    if 'always' in when and when['always'] is not True:
        errs.append(f'{where}: when.always must be true')
    if 'profile' in when and (not isinstance(when['profile'], list) or not when['profile']
                              or not all(isinstance(x, str) and ID_RE.match(x) for x in when['profile'])):
        errs.append(f'{where}: when.profile must be a nonempty list of lowercase-with-dashes tags')
    for k in ('paths', 'added'):
        if k in when:
            if not isinstance(when[k], str):
                errs.append(f'{where}: when.{k} must be a regex string')
                continue
            try:
                re.compile(when[k])
            except re.error as e:
                errs.append(f'{where}: when.{k} is not a regex: {e}')
    for k in INT_KEYS:
        if k in when and (not is_int(when[k]) or when[k] < 1):
            errs.append(f'{where}: when.{k} must be a positive integer')


def load(path):
    """Read and check one TRIGGERS.json; raise Problem naming every format error."""
    path = Path(path)
    doc = read_json_file(path, 'trigger file')
    if not isinstance(doc, dict):
        raise Problem(f'{path}: must be a JSON object')
    errs = []
    if doc.get('version') != VERSION or isinstance(doc.get('version'), bool):
        errs.append(f'version must be {VERSION}')
    for k in sorted(set(doc) - STAGE_KEYS):
        errs.append(f'unknown top-level key {k!r}')
    if not isinstance(doc.get('stage'), str) or not doc.get('stage'):
        errs.append('stage (a string like "04-test") is required')
    for k in ('max_pulls', 'max_src_files'):
        if k in doc and (not is_int(doc[k]) or doc[k] < 1):
            errs.append(f'{k} must be a positive integer')
    if 'ignore' in doc:
        if not isinstance(doc['ignore'], list) or not all(isinstance(x, str) for x in doc['ignore']):
            errs.append('ignore must be a list of regex strings')
        else:
            for x in doc['ignore']:
                try:
                    re.compile(x)
                except re.error as e:
                    errs.append(f'ignore {x!r} is not a regex: {e}')
    triggers = doc.get('triggers', [])
    if not isinstance(triggers, list):
        errs.append('triggers must be a list')
        triggers = []
    seen = set()
    for i, t in enumerate(triggers):
        where = f'trigger[{i}]'
        if not isinstance(t, dict):
            errs.append(f'{where}: must be an object')
            continue
        tid = t.get('id')
        if isinstance(tid, str):
            where = f'trigger {tid!r}'
            if not ID_RE.match(tid):
                errs.append(f'{where}: id must be lowercase-with-dashes')
            if tid in seen:
                errs.append(f'{where}: duplicate id')
            seen.add(tid)
        for k in sorted(set(t) - TRIGGER_KEYS):
            errs.append(f'{where}: unknown key {k!r}')
        for k in ('id', 'why', 'need', 'expect', 'when'):
            if k not in t:
                errs.append(f'{where}: {k} is required')
        for k in ('id', 'why', 'need', 'expect'):
            if k in t and (not isinstance(t[k], str) or not t[k].strip()):
                errs.append(f'{where}: {k} must be a nonempty string')
        if isinstance(t.get('need'), str) and not 8 <= len(t['need']) <= 200:
            errs.append(f'{where}: need must be 8 to 200 characters')
        if 'when' in t:
            check_when(where, t['when'], errs)
    if errs:
        raise Problem(f'{path}:\n  ' + '\n  '.join(errs))
    doc.setdefault('triggers', [])
    return doc


def lock_path(trigger_file):
    p = Path(trigger_file)
    return p.with_name(p.name[:-len('.json')] + '.lock.json' if p.name.endswith('.json') else p.name + '.lock.json')


def library_skills(lib):
    lib = Path(lib).expanduser()
    if not lib.is_dir():
        raise Problem(f'library {lib} is not a directory (pass --library DIR)')
    return {p.parent.name for p in lib.glob('*/SKILL.md')}


def pool_skills(pool_lock):
    doc = read_json_file(pool_lock, 'pool lock')
    if not isinstance(doc, dict) or not isinstance(doc.get('skills'), dict):
        raise Problem(f'{pool_lock}: pool lock needs a "skills" object')
    return set(doc['skills'])


def pool_sha(pool_lock):
    return sha(Path(pool_lock).read_text()) if pool_lock else None


def validate(path, have, source):
    doc = load(path)
    missing = [f"trigger {t['id']!r}: expect {t['expect']!r} is not in the {source}"
               for t in doc['triggers'] if t['expect'] not in have]
    if missing:
        raise Problem(f'{path}:\n  ' + '\n  '.join(missing))
    return doc


def known_tags(trigger_files):
    """Union of `profile` tags across readable trigger files (unreadable ones are skipped)."""
    tags = set()
    for f in trigger_files:
        try:
            doc = json.loads(Path(f).read_text())
            for t in doc.get('triggers', []):
                profile = t.get('when', {}).get('profile', [])
                tags.update(x for x in profile if isinstance(x, str))
        except (OSError, ValueError, AttributeError, TypeError):
            continue
    return tags


# --- lock / check (the only router calls) ------------------------------------

def route(need, lib):
    exe = os.environ.get('STATIONS_ROUTE_BIN') or 'tink-route'
    try:
        p = subprocess.run([exe, '--pick', '--json', '--anywhere', '--library', str(lib), '--', need],
                           capture_output=True, text=True, timeout=ROUTE_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Problem(f'{exe} did not run: {error}')
    try:
        j = json.loads(p.stdout)
    except ValueError:
        raise Problem(f'{exe} gave no JSON (exit {p.returncode}): {(p.stderr or p.stdout).strip()[:200]}')
    if not isinstance(j, dict):
        raise Problem(f'{exe} gave unexpected JSON (exit {p.returncode})')
    if p.returncode == 2:
        raise Problem(f"{exe} error: {j.get('reason') or j.get('status')}")
    return j


def ask(need, lib):
    votes = [route(need, lib) for _ in range(VOTES)]
    winners = [v.get('winner') for v in votes]
    top, n = collections.Counter(winners).most_common(1)[0]
    probs = [v.get('probability') or 0 for v in votes if v.get('winner') == top]
    stable = bool(top) and n == VOTES and min(probs) >= MIN_PROB
    return {'skill': top, 'votes': winners, 'min_probability': round(min(probs), 3) if probs else 0, 'stable': stable,
            'runner_up': votes[0].get('runner_up'), 'contract_version': votes[0].get('contract_version')}


def resolve(doc, lib):
    out = {}
    for t in doc['triggers']:
        r = ask(t['need'], lib)
        r['need_sha'] = sha(t['need'])
        r['matches_expect'] = r['skill'] == t['expect']
        out[t['id']] = r
    return out


def report(doc, res):
    bad = 0
    for t in doc['triggers']:
        r = res[t['id']]
        ok = r['stable'] and r['matches_expect']
        bad += not ok
        note = 'ok' if ok else ('UNSTABLE ' + str(r['votes']) if not r['stable']
                                else f"DRIFT: Jev says {r['skill']}, file expects {t['expect']}")
        print(f"  {'✓' if ok else '✗'} {t['id']:22} {str(r['skill']):52} p>={r['min_probability']:.2f}  {note}")
    return bad


def read_lock(trigger_file):
    """Entries of the lock next to a trigger file, or None when there is no lock."""
    lf = lock_path(trigger_file)
    if not lf.exists():
        return None
    doc = read_json_file(lf, 'lock file')
    if not isinstance(doc, dict) or not isinstance(doc.get('entries'), dict):
        raise Problem(f'{lf}: lock file needs an "entries" object')
    return doc


def cmd_lock(a, check):
    bad = 0
    for p in a.files:
        doc = validate(p, library_skills(a.library), 'library')
        res = resolve(doc, a.library)
        print(f"{p} ({doc['stage']})")
        file_bad = report(doc, res)
        bad += file_bad
        if check:
            prev = read_lock(p)
            old = prev['entries'] if prev else None
            if old is None:
                print('  ✗ no lock file; run `lock` first')
                bad += 1
            else:
                for tid, r in res.items():
                    if tid not in old or old[tid].get('skill') != r['skill'] or old[tid].get('need_sha') != r['need_sha']:
                        print(f'  ✗ {tid}: lock file is stale')
                        bad += 1
                for tid in sorted(set(old) - set(res)):
                    print(f'  ✗ {tid}: in lock but not in file')
                    bad += 1
                if a.pool_lock and prev.get('pool') != pool_sha(a.pool_lock):
                    print(f'  ✗ lock was made against a different pool ({prev.get("pool")}); run `lock` again')
                    bad += 1
        elif file_bad == 0 and bad == 0:
            lock_path(p).write_text(json.dumps({
                'version': VERSION, 'stage': doc['stage'], 'generated': datetime.date.today().isoformat(),
                'pool': pool_sha(a.pool_lock), 'votes_per_need': VOTES, 'min_probability': MIN_PROB,
                'entries': res}, indent=2, sort_keys=True) + '\n')
            print(f'  wrote {lock_path(p)}')
    return 1 if bad else 0


# --- lint (offline) ----------------------------------------------------------

def lint(trigger_file, pool_lock):
    """Offline consistency problems of one trigger file against its lock and the pool lock; [] when consistent."""
    try:
        doc = load(trigger_file)
    except Problem as error:
        return [str(error)]
    name = str(trigger_file)
    try:
        lock = read_lock(trigger_file)
    except Problem as error:
        return [str(error)]
    if lock is None:
        return [f'{name}: no lock file {lock_path(trigger_file).name}; run `stations.py lock`'] if doc['triggers'] else []
    problems = []
    entries = lock['entries']
    try:
        skills = pool_skills(pool_lock)
        current = pool_sha(pool_lock)
    except (Problem, OSError) as error:
        return [f'{name}: {error}']
    if lock.get('pool') != current:
        problems.append(f"{name}: lock pool {lock.get('pool')} does not match {pool_lock} ({current}); run `stations.py check`")
    for t in doc['triggers']:
        e = entries.get(t['id'])
        where = f"{name}: trigger {t['id']!r}"
        if not isinstance(e, dict):
            problems.append(f'{where}: not in the lock file')
            continue
        if e.get('need_sha') != sha(t['need']):
            problems.append(f'{where}: need changed since lock (stale)')
        if e.get('stable') is not True:
            problems.append(f'{where}: lock entry is not stable')
        if e.get('skill') != t['expect']:
            problems.append(f"{where}: lock says {e.get('skill')!r}, file expects {t['expect']!r}")
        if t['expect'] not in skills:
            problems.append(f"{where}: expect {t['expect']!r} is not in {pool_lock}")
    for tid in sorted(set(entries) - {t['id'] for t in doc['triggers']}):
        problems.append(f'{name}: {tid!r} is in the lock but not in the file')
    return problems


# --- pull (offline, deterministic) -------------------------------------------

def git(repo, *args):
    p = subprocess.run(['git', '-C', str(repo), *args], capture_output=True)
    if p.returncode:
        raise Problem(f"git {' '.join(args)}: {p.stderr.decode('utf-8', 'replace').strip()}")
    return p.stdout.decode('utf-8', 'replace')


def default_base(repo):
    """(merge-base commit, label) of HEAD and the default branch (origin/HEAD, else main, else master), or (None, reason)."""
    ref = subprocess.run(['git', '-C', str(repo), 'symbolic-ref', '-q', '--short', 'refs/remotes/origin/HEAD'],
                         capture_output=True, text=True)
    remote = ref.stdout.strip() if ref.returncode == 0 else ''
    branches = [remote] if remote else ['main', 'master']
    for branch in branches:
        base = subprocess.run(['git', '-C', str(repo), 'merge-base', 'HEAD', branch], capture_output=True, text=True)
        if base.returncode == 0 and base.stdout.strip():
            return base.stdout.strip(), branch
    return None, f"no merge-base of HEAD and {' or '.join(branches)}"


def changes(repo, base, head, ignore):
    ign = [re.compile(x) for x in ignore]
    status = git(repo, 'diff', '--name-status', '--no-renames', f'{base}..{head}').splitlines()
    files, deleted = {}, []
    for line in status:
        st, _, name = line.partition('\t')
        if not name or any(r.search(name) for r in ign):
            continue
        if st == 'D':
            deleted.append(name)
        else:
            files[name] = []
    patch = git(repo, 'diff', '-U0', '--no-renames', f'{base}..{head}')
    for chunk in patch.split('\ndiff --git '):
        m = re.search(r'^\+\+\+ b/(.+)$', chunk, re.M)
        if m and m.group(1) in files:
            files[m.group(1)] = [line[1:] for line in chunk.splitlines() if line.startswith('+') and not line.startswith('+++')]
    return files, deleted


def fired(t, files, deleted, tags):
    """Evidence list when the trigger fires, else None. Every `when` key must hold."""
    w, ev = t['when'], []
    if w.get('always'):
        return ['always']
    if 'profile' in w:
        hit = sorted(set(w['profile']) & tags)
        if not hit:
            return None
        ev.append('tag ' + ','.join(hit))
    names = sorted(files)
    if 'paths' in w:
        names = [n for n in names if re.search(w['paths'], n)]
        if len(names) < w.get('min_files', 1):
            return None
        ev += names[:3]
    if 'added' in w:
        hits = [n for n in names if sum(bool(re.search(w['added'], line)) for line in files[n]) >= w.get('added_min', 1)]
        if not hits:
            return None
        ev += [f"added /{w['added']}/ in {h}" for h in hits[:2]]
    if 'deleted_min' in w:
        if len(deleted) < w['deleted_min']:
            return None
        ev.append(f'{len(deleted)} source files deleted')
    if 'dirs_min' in w:
        dirs = {n.split('/')[0] for n in files if '/' in n}
        if len(dirs) < w['dirs_min']:
            return None
        ev.append(f'{len(dirs)} top-level folders')
    if not ({'paths', 'added', 'deleted_min', 'dirs_min', 'profile'} & set(w)):
        return None
    return ev or None


def pull(trigger_file, repo='.', base=None, head='HEAD', tags=(), auto_base=True):
    """Evaluate one trigger file. Returns (sheet dict, printable lines). Makes no router call.

    base None with auto_base: the merge-base of HEAD and the default branch; when that cannot be
    found, diff triggers are skipped (one line says why) and tag/always triggers still run."""
    doc = load(trigger_file)
    lock = read_lock(trigger_file)
    entries = lock['entries'] if lock else {}
    tags = {x for x in tags if x}
    lines, label = [], base
    if base is None and auto_base:
        base, label = default_base(repo)
        if base is None:
            lines.append(f'  diff triggers skipped: {label}')
            label = None
    if base is not None:
        files, deleted = changes(repo, base, head, doc.get('ignore', DEFAULT_IGNORE))
    else:
        files, deleted = {}, []
    cap, guard = doc.get('max_pulls', DEFAULT_MAX_PULLS), doc.get('max_src_files', DEFAULT_MAX_SRC_FILES)
    too_big = len(files) + len(deleted) > guard
    sheet, pulled = [], []
    for t in doc['triggers']:
        keys = set(t['when'])
        row = {'id': t['id'], 'skill': t['expect'], 'why': t['why']}
        diff_only = bool(keys & DIFF_KEYS) and not ({'always', 'profile'} & keys)
        if too_big and diff_only:
            row['status'] = f'skipped: diff touches more than {guard} source files'
            sheet.append(row)
            continue
        ev = fired(t, files, deleted, tags)
        if ev is None:
            row['status'] = 'skipped: no diff base' if base is None and keys & DIFF_KEYS else 'not fired'
        else:
            e = entries.get(t['id'])
            row['evidence'] = ev
            if not isinstance(e, dict):
                row['status'] = 'skipped: not in lock file (run `stations.py lock`)'
            elif e.get('need_sha') != sha(t['need']):
                row['status'] = 'skipped: need text changed since lock'
            elif not (e.get('stable') and e.get('skill') == t['expect']):
                row['status'] = 'skipped: lock is unstable or disagrees with expect'
            elif len(pulled) >= cap:
                row['status'] = f'skipped: cap of {cap} pulls reached'
            else:
                row['status'] = 'pulled'
                pulled.append(t['expect'])
        sheet.append(row)
    fired_count = sum('evidence' in r for r in sheet)
    for r in sheet:
        if r['status'] != 'not fired':
            shown = '; '.join(r['evidence']) if r['status'] == 'pulled' else r['status']
            lines.append(f"  {'PULL' if r['status'] == 'pulled' else 'skip'}  {r['skill']:40} {r['id']:20} {shown}")
    shown_base = f'{base[:12]} (merge-base with {label})' if label and label != base else base
    head_line = (f"{doc['stage']}: diff {shown_base}..{head} ({len(files)} changed, {len(deleted)} deleted source files)"
                 if base is not None else f"{doc['stage']}: no diff")
    lines.insert(0, head_line + f"; tags: {', '.join(sorted(tags)) or 'none'}")
    lines.append(f"{doc['stage']}: {fired_count} of {len(sheet)} triggers fired; {len(pulled)} skill(s) pulled"
                 + (': ' + ', '.join(pulled) if pulled else ''))
    # `base` is the ref name, not the commit, so re-pulling an unchanged run writes an identical sheet
    result = {'version': VERSION, 'stage': doc['stage'], 'base': label, 'head': head,
              'tags': sorted(tags), 'fired': fired_count, 'triggers': len(sheet), 'pulls': pulled, 'sheet': sheet}
    return result, lines


def cmd_pull(a):
    tags = [x for x in (a.tags or '').split(',') if x]
    result, lines = pull(a.file, a.repo, a.base, a.head, tags)
    print('\n'.join(lines))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(result, indent=2) + '\n')
    return 0


# --- pool: pin and rebuild the library the stations route over ----------------

def tree_digest(d):
    h = hashlib.sha256()
    for p in sorted(x for x in Path(d).rglob('*') if x.is_file()):
        h.update(str(p.relative_to(d)).encode() + b'\0' + p.read_bytes() + b'\0')
    return h.hexdigest()


def git_out(*args, cwd=None):
    p = subprocess.run(['git', *args], capture_output=True, text=True, cwd=cwd)
    if p.returncode:
        raise Problem(f"git {' '.join(args)}: {p.stderr.strip()}")
    return p.stdout.strip()


def pool_pin(a):
    skills = {}
    for spec in a.sources:
        name, _, rest = spec.partition('=')
        path, _, subdir = rest.partition('#')
        if not name or not path:
            raise Problem(f'--sources {spec!r}: expected NAME=PATH[#SUBDIR]')
        root = Path(path).expanduser()
        url, commit = git_out('remote', 'get-url', 'origin', cwd=root), git_out('rev-parse', 'HEAD', cwd=root)
        for f in sorted(root.rglob('SKILL.md')):
            rel = f.parent.relative_to(root)
            if '.git' in rel.parts or SKIP_DIRS & set(rel.parts):
                continue
            if subdir and not str(rel).startswith(subdir.rstrip('/') + '/'):
                continue
            if f.parent.name in skills:
                raise Problem(f'duplicate skill name {f.parent.name}')
            skills[f.parent.name] = {'source': url, 'commit': commit, 'path': str(rel), 'digest': tree_digest(f.parent)}
    Path(a.out).write_text(json.dumps({'version': 1, 'skills': skills}, indent=1, sort_keys=True) + '\n')
    print(f'pinned {len(skills)} skills from {len(a.sources)} sources -> {a.out}')
    return 0


def load_overlay(path, lock):
    overlay = read_json_file(path, 'overlay')
    if not isinstance(overlay, dict):
        raise Problem(f'{path}: overlay must be an object of skill name -> {{"description": ...}}')
    for n, v in overlay.items():
        if n not in lock:
            raise Problem(f'overlay names {n!r}, which is not in the lock')
        if not isinstance(v, dict) or set(v) != {'description'} or not isinstance(v['description'], str) or not v['description'].strip():
            raise Problem(f'overlay {n!r}: must be {{"description": "<nonempty text>"}}')
    return overlay


def pool_build(a):
    doc = read_json_file(a.lock, 'pool lock')
    lock = doc.get('skills') if isinstance(doc, dict) else None
    if not isinstance(lock, dict):
        raise Problem(f'{a.lock}: pool lock needs a "skills" object')
    dest = Path(a.dest).expanduser().absolute()
    home_lib = (Path('~').expanduser() / '.tink-library').absolute()
    if dest == home_lib or home_lib in dest.parents:
        raise Problem(f'{dest} is inside {home_lib}; build the pool somewhere else')
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise Problem(f'{dest} is not empty')
    overlay = load_overlay(a.overlay, lock) if a.overlay else {}
    by = collections.OrderedDict()
    for n, e in sorted(lock.items()):
        by.setdefault((e['source'], e['commit']), []).append(n)
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as t:
        for i, ((url, commit), names) in enumerate(by.items()):
            c = Path(t) / str(i)
            git_out('clone', '-q', '--filter=blob:none', '--no-checkout', url, str(c))
            git_out('checkout', '-q', commit, cwd=c)
            for n in names:
                src = c / lock[n]['path']
                if not src.is_dir() or tree_digest(src) != lock[n]['digest']:
                    raise Problem(f'{n}: digest mismatch at {commit[:9]}; refusing')
                shutil.copytree(str(src), str(dest / n))
                if n in overlay:
                    f = dest / n / 'SKILL.md'
                    txt = f.read_text()
                    new, k = re.subn(r'^description:.*?(?=\n[A-Za-z_-]+:|\n---)',
                                     lambda m: 'description: ' + json.dumps(overlay[n]['description']),
                                     txt, count=1, flags=re.S | re.M)
                    if not k:
                        raise Problem(f'{n}: no description to overlay')
                    f.write_text(new)
    print(f'built {len(lock)} skills ({len(overlay)} with routing descriptions) -> {dest}')
    return 0


# --- command line ------------------------------------------------------------

def default_library():
    return str(Path(os.environ.get('TINK_HOME', '~/.tink-library')).expanduser() / 'skills')


def main(argv=None):
    ap = argparse.ArgumentParser(prog='stations.py', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd')
    sub.required = True
    for name in ('validate', 'lock', 'check', 'lint'):
        s = sub.add_parser(name)
        s.add_argument('files', nargs='+')
        s.add_argument('--pool-lock', help='pool.lock.json the library was built from; its hash is recorded in the lock')
        if name != 'lint':
            s.add_argument('--library', help='skill library directory (default $TINK_HOME/skills or ~/.tink-library/skills)')
    s = sub.add_parser('pull')
    s.add_argument('file')
    s.add_argument('--base', help='diff base (default: merge-base of HEAD and origin/HEAD, else main, else master)')
    s.add_argument('--head', default='HEAD')
    s.add_argument('--tags', help='comma-separated run tags')
    s.add_argument('--out')
    s.add_argument('--repo', default='.')
    pool = sub.add_parser('pool').add_subparsers(dest='pool_cmd')
    pool.required = True
    p = pool.add_parser('pin')
    p.add_argument('--sources', nargs='+', required=True, help='NAME=PATH_TO_CLONE[#SUBDIR]')
    p.add_argument('--out', default='pool.lock.json')
    b = pool.add_parser('build')
    b.add_argument('--lock', default='pool.lock.json')
    b.add_argument('--dest', required=True)
    b.add_argument('--overlay')
    a = ap.parse_args(argv)
    try:
        if a.cmd == 'validate':
            if a.library or not a.pool_lock:
                have, source = library_skills(a.library or default_library()), 'library'
            else:
                have, source = pool_skills(a.pool_lock), 'pool lock'
            for f in a.files:
                validate(f, have, source)
                print(f'{f}: ok')
            return 0
        if a.cmd in ('lock', 'check'):
            a.library = a.library or default_library()
            return cmd_lock(a, a.cmd == 'check')
        if a.cmd == 'lint':
            if not a.pool_lock:
                raise Problem('lint needs --pool-lock FILE')
            problems = [x for f in a.files for x in lint(f, a.pool_lock)]
            for x in problems:
                print(f'  ✗ {x}')
            print(f'{len(a.files)} trigger file(s): ' + (f'{len(problems)} problem(s)' if problems else 'locks consistent'))
            return 1 if problems else 0
        if a.cmd == 'pull':
            return cmd_pull(a)
        return pool_pin(a) if a.pool_cmd == 'pin' else pool_build(a)
    except Problem as e:
        print(e, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
