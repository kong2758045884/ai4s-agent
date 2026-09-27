"""Package only current Python/UI code and additive migration, never local data."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

from package_server_release import copy_tree


def package(project, release):
    root = Path(project).resolve()
    if not release.startswith('ai4s-opt-') or Path(release).name != release or '/' in release or '\\' in release:
        raise ValueError('Invalid release name')
    work = root / 'runtime/deploy' / release
    stage = work / 'payload'
    stage.mkdir(parents=True, exist_ok=False)
    copy_tree(root / 'ui/dist', stage / 'ui/dist')
    copy_tree(root / 'ai4s-tool/ai4s_tool', stage / 'tool/ai4s_tool')
    for name in ['server.py', 'pyproject.toml', 'uv.lock']:
        shutil.copy2(root / 'ai4s-tool' / name, stage / 'tool' / name)
    (stage / 'tool/scripts').mkdir()
    shutil.copy2(root / 'ai4s-tool/scripts/migrate_strategic_optimization.py', stage / 'tool/scripts')
    shutil.copy2(root / 'ai4s-tool/scripts/manage_strategic_roles.py', stage / 'tool/scripts')
    (stage / 'scripts').mkdir()
    for name in ['deploy_strategic_optimization.py', 'deploy_canonical_frontend.py', 'ensure_spa_cache.py']:
        shutil.copy2(root / 'scripts' / name, stage / 'scripts')
    (stage / 'release-config.json').write_text(json.dumps({'applyDataBatches': False, 'verifyPrivateAssessments': True}), encoding='utf-8')
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    manifest = {'release': release, 'commit': revision, 'databaseIncluded': False, 'files': {}}
    for file in sorted(stage.rglob('*')):
        if file.is_file():
            assert file.suffix not in {'.db', '.sqlite', '.pem', '.key'} and not file.name.startswith('.env')
            manifest['files'][file.relative_to(stage).as_posix()] = hashlib.sha256(file.read_bytes()).hexdigest()
    (stage / 'release-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    archive = work / 'release.tar.gz'
    with tarfile.open(archive, 'w:gz', compresslevel=3) as tar:
        for file in stage.iterdir():
            tar.add(file, arcname=file.name)
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    (work / 'archive.sha256').write_text(checksum, encoding='utf-8')
    print(json.dumps({'archive': str(archive), 'bytes': archive.stat().st_size, 'files': len(manifest['files']), 'sha256': checksum}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('release'); parser.add_argument('--project', default='.')
    args = parser.parse_args(); package(args.project, args.release)
