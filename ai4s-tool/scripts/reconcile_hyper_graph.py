"""Audit and copy graph JSON/index artifacts into a NEW isolated directory."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


def reconcile(source: Path, target: Path | None = None):
    manifest = {'source': str(source.resolve()), 'scopes': {}, 'files': {}, 'schema': 'hyper-fusion-v1'}
    for scope in ('ZN', 'GW'):
        folder = source / scope
        data = json.loads((folder / 'data.json').read_text(encoding='utf-8'))
        names = [n['name'] for n in data['nodes']]
        ids = set(names)
        dangling = [e for e in data['edges'] if e['source'] not in ids or e['target'] not in ids]
        if len(names) != len(ids) or dangling:
            raise ValueError(f'{scope}: duplicate IDs or dangling edges; source must be reviewed')
        manifest['scopes'][scope] = {'nodes': len(names), 'edges': len(data['edges']), 'dangling': 0}
        files = [folder / n for n in ('data.json', 'metadata.json', 'sources_nodes.json', 'sources_edges.json', 'ai4s_hierarchy.yaml')]
        files += sorted((folder / 'index').rglob('*'))
        for file in files:
            if file.is_file():
                manifest['files'][file.relative_to(source).as_posix()] = hashlib.sha256(file.read_bytes()).hexdigest()
    if target:
        target = target.resolve()
        if target.exists():
            raise FileExistsError('Target must be new; refusing to overwrite existing data')
        target.mkdir(parents=True)
        for relative, digest in manifest['files'].items():
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative, destination)
            if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                raise ValueError('Copy reconciliation failed')
        (target / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--target', type=Path)
    args = parser.parse_args()
    manifest = reconcile(args.source.resolve(), args.target)
    print(json.dumps({'scopes': manifest['scopes'], 'files': len(manifest['files']), 'copied': bool(args.target)}, ensure_ascii=False))
