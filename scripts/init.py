#!/usr/bin/env python3
"""Install a versioned SDLC scaffold, check an existing one, or upgrade it explicitly.

init.py <target>                     install, or check an install of the same version
init.py <target> --check             preview an install / validate an existing one
init.py <target> --upgrade [--check] adopt this package over an older install; --check previews
                                     and writes nothing. Managed files you customized block the
                                     upgrade unless --overwrite-customized is given (keep them in
                                     git first). Project-owned files (_system/verification.json,
                                     .tink/skillsets/*.json, .tink/pool.lock.json, .tink/pool-overlay.json,
                                     stages/*/TRIGGERS.json and their locks) are never overwritten. Only the router
                                     block of AGENTS.md is replaced. runs/ is never modified.
                                     The receipt _system/scaffold.json is written last, so an
                                     interrupted upgrade is safe to rerun.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

PACKAGE = Path(__file__).resolve().parents[1] / 'assets'
RECEIPT = '_system/scaffold.json'
CONFIG = '_system/verification.json'
ROUTER_START = '<!-- AI-Native SDLC Router -->'
ROUTER_END = '<!-- End AI-Native SDLC Router -->'
ROUTER = '''<!-- AI-Native SDLC Router -->
## SDLC Workspace
- Read `_system/SDLC.md` for setup, evidence boundaries, and recovery.
- Inspect `python3 _system/scripts/sdlc.py status` before creating a run.
- Read `stages/<stage-name>/CONTEXT.md` before processing a stage.
- Keep factory references in `_shared/` unchanged during feature runs.
- Use separate worktrees or clones for code-writing runs.
- Stage skills: see the Skills section of the current stage's CONTEXT.md.
- Need a specialised skill mid-task? `tink-route --receipt runs/<slug>/skills.jsonl "<what you need>"` prints it on stdout; exit 1 means none fits, so continue without one.
<!-- End AI-Native SDLC Router -->'''


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe(root, relative):
    part = Path(relative)
    if part.is_absolute() or '..' in part.parts:
        raise ValueError(f'Invalid package path: {relative}')
    target = root / part
    if target.resolve() != target.absolute():
        raise ValueError(f'Symlink destination refused: {relative}')
    for parent in target.parents:
        if parent == root:
            break
        if parent.exists() and not parent.is_dir():
            raise ValueError(f'Parent is not a directory: {parent}')
    return target


def package_files():
    manifest = json.loads((PACKAGE / 'manifest.json').read_text())
    files = {}
    for relative, expected in manifest['files'].items():
        data = safe(PACKAGE, relative).read_bytes()
        if sha(data) != expected:
            raise ValueError(f'Package integrity mismatch: {relative}')
        files[relative] = data
    return manifest, files


def parse_version(text):
    if not isinstance(text, str) or not re.fullmatch(r'\d+\.\d+\.\d+', text):
        raise ValueError(f'Cannot compare version {text!r}; expected numeric X.Y.Z.')
    return tuple(int(part) for part in text.split('.'))


def project_owned(*manifests):
    owned = {CONFIG}
    for manifest in manifests:
        owned.update(manifest.get('projectOwned', []))
    return owned


def disk_hash(path):
    if path.is_symlink():
        raise ValueError(f'Symlink destination refused: {path}')
    if path.exists() and not path.is_file():
        raise ValueError(f'Destination is not a file: {path}')
    return sha(path.read_bytes()) if path.is_file() else None


def atomic_write(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f'.{path.name}.sdlc-upgrade-tmp'
    try:
        tmp.write_bytes(data)
        tmp.chmod(mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def plan_upgrade(root, previous, manifest, files, original):
    old, new = previous['files'], manifest['files']
    owned = project_owned(previous, manifest)
    plan = {key: [] for key in ('update', 'create', 'restore', 'remove', 'kept', 'blocked')}
    for name in sorted(set(old) | set(new)):
        d = disk_hash(safe(root, name))
        o, n = old.get(name), new.get(name)
        if name in new:
            if d == n:
                continue
            if d is None:
                if name in owned and o is not None:
                    plan['kept'].append(f'{name} (deleted locally)')
                else:
                    plan['restore' if o is not None else 'create'].append(name)
            elif o is not None and d == o:
                plan['update'].append(name)
            elif name in owned:
                if n != o:
                    plan['kept'].append(f'{name} (changed locally; upstream also changed)')
            else:
                plan['blocked'].append(name)
        elif d is not None:
            if d == o:
                plan['remove'].append(name)
            else:
                plan['kept'].append(f'{name} (removed upstream, changed locally)')
    if original is None or original.count(ROUTER_START.encode()) != 1 or original.count(ROUTER_END.encode()) != 1 \
            or original.index(ROUTER_START.encode()) > original.index(ROUTER_END.encode()):
        raise ValueError('AGENTS.md must contain exactly one SDLC router block (start and end markers); no files changed. Repair the markers, then rerun the upgrade.')
    start = original.index(ROUTER_START.encode())
    end = original.index(ROUTER_END.encode()) + len(ROUTER_END)
    updated = original[:start] + ROUTER.encode() + original[end:]
    plan['agents'] = updated if updated != original else None
    return plan


def inflight_runs(root):
    runs = root / 'runs'
    if not runs.is_dir():
        return []
    return sorted(p.name for p in runs.iterdir() if p.is_dir() and not p.is_symlink()
                  and (p / 'decisions').is_dir() and any(p.glob('decisions/*.json')))


def print_plan(root, previous, manifest, plan, overwrite, check):
    print(f"Upgrade {previous['version']} -> {manifest['version']}")
    update = sorted(plan['update'] + (plan['blocked'] if overwrite else []))
    if plan['agents'] is not None:
        update = sorted(update + ['AGENTS.md (router)'])
    for label, names in (('Update', update), ('Create', plan['create']), ('Restore', plan['restore']),
                         ('Remove', plan['remove']), ('Kept (project-owned)', sorted(plan['kept'])),
                         ('Blocked (customized managed files)', [] if overwrite else plan['blocked'])):
        if names:
            print(f'{label}: ' + ', '.join(names))
    if overwrite and plan['blocked']:
        print('Overwriting customized files (git keeps the old copy): ' + ', '.join(plan['blocked']))
    if not overwrite:
        for name in plan['blocked']:
            print(f'  {name}: compare with: diff {root / name} {PACKAGE / name}')
    changing = plan['update'] + plan['create'] + plan['restore'] + plan['remove'] + (plan['blocked'] if overwrite else [])
    if any(re.fullmatch(r'stages/[^/]+/CONTEXT\.md', name) for name in changing):
        slugs = inflight_runs(root)
        if slugs:
            print('In-flight runs: approvals will read as stale until re-approved: ' + ', '.join(slugs) + ' (stage docs/templates changed)')
    if check:
        print('Preview only; nothing changed.')


def upgrade(root, previous, manifest, files, agents, check, overwrite):
    if parse_version(manifest['version']) < parse_version(previous['version']):
        raise ValueError(f"Downgrade refused: installed scaffold is {previous['version']}; package is {manifest['version']}.")
    safe(root, RECEIPT)
    original = agents.read_bytes() if agents.exists() else None
    plan = plan_upgrade(root, previous, manifest, files, original)
    print_plan(root, previous, manifest, plan, overwrite, check)
    if plan['blocked'] and not overwrite:
        raise ValueError('Upgrade blocked by customized managed files; no files changed. Review the diffs, or rerun with --overwrite-customized after committing your copies.')
    if check:
        return
    for name in plan['update'] + plan['create'] + plan['restore'] + plan['blocked']:
        target = safe(root, name)
        mode = 0o755 if name.startswith('_system/scripts/') else (target.stat().st_mode & 0o777 if target.is_file() else 0o644)
        atomic_write(target, files[name], mode)
    for name in plan['remove']:
        target = safe(root, name)
        target.unlink()
        for parent in target.parents:
            if parent == root:
                break
            try:
                parent.rmdir()
            except OSError:
                break
    if plan['agents'] is not None:
        if agents.read_bytes() != original:
            raise ValueError('AGENTS.md changed during upgrade.')
        atomic_write(agents, plan['agents'], agents.stat().st_mode & 0o777)
    atomic_write(safe(root, RECEIPT), (json.dumps(manifest, indent=2) + '\n').encode(), 0o644)
    print(f"Upgraded to {manifest['version']}. Verify with: python3 _system/scripts/sdlc.py walk")


def install(root, check=False, upgrade_mode=False, overwrite_customized=False, target_arg=None):
    target_arg = target_arg or str(root)
    root = root.resolve()
    if not root.is_dir():
        raise ValueError('Target must be an existing project directory.')
    lock = root / '.sdlc-init-lock'
    install_error = None
    try:
        lock.mkdir()
    except FileExistsError:
        raise ValueError('Initialization busy or interrupted; confirm no initializer remains before removing .sdlc-init-lock.')
    try:
        manifest, files = package_files()
        receipt = safe(root, RECEIPT)
        agents = safe(root, 'AGENTS.md')
        original = agents.read_bytes() if agents.exists() else None
        agents_mode = agents.stat().st_mode if original is not None else None
        tmp_agents = agents.parent / '.AGENTS.md.sdlc-install-tmp'
        text = original.decode() if original is not None else ''
        destinations = {name: safe(root, name) for name in files}
        if upgrade_mode and not receipt.exists():
            raise ValueError('No installed scaffold found; run init.py <target> without --upgrade.')
        if receipt.exists():
            previous = json.loads(receipt.read_text())
            if previous.get('version') != manifest['version']:
                if upgrade_mode:
                    upgrade(root, previous, manifest, files, agents, check, overwrite_customized)
                    return
                raise ValueError(f"Installed scaffold is {previous.get('version')}; package is {manifest['version']}. "
                                 f"Preview: init.py {target_arg} --upgrade --check; apply: init.py {target_arg} --upgrade.")
            if previous != manifest:
                raise ValueError('Same package version but different content; preserve the installation and review the difference.')
            owned = project_owned(manifest)
            differences = [name for name, path in destinations.items()
                           if name not in owned and (not path.is_file() or sha(path.read_bytes()) != manifest['files'][name])]
            if differences or ROUTER not in text:
                raise ValueError('Installed scaffold was customized or is incomplete; preserve it and review a migration: ' + ', '.join(differences or ['AGENTS.md router']))
            if not destinations[CONFIG].is_file():
                raise ValueError('Verification configuration is missing; restore it through a reviewed change.')
            config = json.loads(destinations[CONFIG].read_text())
            print('Installed scaffold matches package; no files changed.')
            changed = [name for name in sorted(owned & set(destinations))
                       if not destinations[name].is_file() or sha(destinations[name].read_bytes()) != manifest['files'][name]]
            if changed:
                print('Project-owned, changed locally: ' + ', '.join(changed))
            print('Verification: ' + ('configured; inspect checks before use.' if config.get('checks') else 'UNCONFIGURED; choose real project checks in ' + CONFIG))
            return
        conflicts = [name for name, path in destinations.items() if path.exists()]
        runs = safe(root, 'runs')
        if runs.exists() and (not runs.is_dir() or any(runs.iterdir())):
            conflicts.append('runs (existing work)')
        for top in ['stages', '_shared', '_system']:
            entry = safe(root, top)
            if entry.exists() and (not entry.is_dir() or any(entry.iterdir())):
                conflicts.append(f'{top} (existing content outside this package)')
        if tmp_agents.exists():
            conflicts.append(f'{tmp_agents.name} (stale installer temp file)')
        if conflicts or '<!-- AI-Native SDLC Router -->' in text:
            raise ValueError('Unmanaged or partial scaffold exists; no files changed. Review migration separately: ' + ', '.join(conflicts or ['AGENTS.md router']))
        print('Create: ' + ', '.join(sorted(files)))
        print('Append SDLC router to AGENTS.md; preserve existing instructions.')
        if check:
            print('Preview only; verification will remain UNCONFIGURED.')
            return
        fresh_dirs = {parent for target in (*destinations.values(), receipt) for parent in target.parents if parent != root and root in parent.parents and not parent.exists()}
        created = []
        agents_attempted = False
        router_written = False
        install_error = None
        try:
            for name, data in files.items():
                target = destinations[name]
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as stream:
                    created.append(target)
                    stream.write(data)
                if name.startswith('_system/scripts/'):
                    target.chmod(0o755)
            # Best-effort concurrent-edit check: an edit after this comparison can still be overwritten by replace() below.
            if (agents.read_bytes() if agents.exists() else None) != original:
                raise ValueError('AGENTS.md changed during initialization.')
            agents_attempted = True
            tmp_agents.write_bytes((text + ('\n\n' if text else '') + ROUTER + '\n').encode())
            if agents_mode is not None:
                tmp_agents.chmod(agents_mode & 0o777)
            tmp_agents.replace(agents)
            router_written = True
            with receipt.open('x') as stream:
                created.append(receipt)
                json.dump(manifest, stream, indent=2)
                stream.write('\n')
        except Exception as error:
            install_error = error
            cleanup_errors = []
            for target in reversed(created):
                try:
                    target.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    cleanup_errors.append(f'{target}: {cleanup_error}')
            if agents_attempted:
                try:
                    if router_written:
                        if original is None:
                            agents.unlink(missing_ok=True)
                        else:
                            agents.write_bytes(original)
                    tmp_agents.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    cleanup_errors.append(f'{agents}: {cleanup_error}')
            for fresh in sorted(fresh_dirs, reverse=True):
                try:
                    fresh.rmdir()
                except OSError:
                    pass
            if cleanup_errors:
                raise RuntimeError('Rollback incomplete: ' + '; '.join(cleanup_errors) + f'; original error: {error}') from error
            raise
        print('Scaffold installed. Verification is UNCONFIGURED and will fail until real checks are selected. Read _system/SDLC.md.')
    finally:
        try:
            lock.rmdir()
        except FileNotFoundError:
            pass
        except OSError as lock_error:
            if install_error is None:
                raise RuntimeError(f'Installer finished but .sdlc-init-lock cleanup failed: {lock_error}; remove it manually.') from lock_error
            raise RuntimeError(f'Installation failed: {install_error}; .sdlc-init-lock cleanup also failed: {lock_error}; remove it manually.') from install_error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', help='Explicit target project checkout')
    parser.add_argument('--check', action='store_true', help='Preview installation or validate an existing install without changing scaffold files; with --upgrade, preview the upgrade')
    parser.add_argument('--upgrade', action='store_true', help='Upgrade an existing install of an older package version')
    parser.add_argument('--overwrite-customized', action='store_true', help='With --upgrade, overwrite customized managed files with the package version')
    args = parser.parse_args()
    if args.overwrite_customized and not args.upgrade:
        parser.error('--overwrite-customized requires --upgrade')
    try:
        install(Path(args.target), args.check, args.upgrade, args.overwrite_customized, args.target)
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
