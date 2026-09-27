"""Retain verified gzip backups, removing only duplicate DBs in this release's journals."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

ROOT = Path('/www/wwwroot/lab/releases')


def checksum(stream):
    digest = hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def compact(name):
    release = (ROOT / name).resolve()
    assert release.parent == ROOT and release.name.startswith('ai4s-opt-')
    assert (release / 'activated.json').is_file() and json.loads((release / 'stage.json').read_text())['passed']
    # A preview must be stopped before checkpointing and archiving its copy.
    for cmd in Path('/proc').glob('[0-9]*/cmdline'):
        try:
            args = cmd.read_bytes().split(b'\0')
        except (FileNotFoundError, PermissionError):
            continue
        if any(args[i:i+2] == [b'--port', b'1608'] for i in range(len(args)-1)):
            raise RuntimeError('A preview is still running; no files compacted')
    results = []
    for directory in ('live-journal', 'preview'):
        parent = (release / directory).resolve()
        assert parent.parent == release
        for name in ('strategic_map.db', 'impact_triage.db'):
            source = parent / name
            if not source.is_file():
                continue
            assert not source.is_symlink() and source.resolve().parent == parent
            with sqlite3.connect(source) as conn:
                assert conn.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
                assert conn.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] == 0
            zipped = source.with_suffix('.db.gz')
            if not zipped.exists():
                temporary = parent / (name + '.gz.pending')
                with source.open('rb') as raw, gzip.open(temporary, 'wb', compresslevel=3) as target:
                    shutil.copyfileobj(raw, target)
                temporary.replace(zipped)
            with source.open('rb') as raw, gzip.open(zipped, 'rb') as archived:
                expected, actual = checksum(raw), checksum(archived)
                assert expected == actual, 'Existing compressed copy differs; original retained'
            record = {'file': str(source), 'archive': str(zipped), 'sha256': expected,
                      'originalBytes': source.stat().st_size, 'compressedBytes': zipped.stat().st_size}
            source.unlink()
            results.append(record)
    (release / 'backup-compaction.json').write_text(json.dumps(results, indent=2))
    print(json.dumps({'verifiedArchives': len(results), 'originalBytesFreed': sum(r['originalBytes'] for r in results)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('release')
    compact(parser.parse_args().release)
