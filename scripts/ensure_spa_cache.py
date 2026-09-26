"""Prevent stale SPA documents while keeping hashed assets cacheable (server only)."""
from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess


def ensure_spa_cache():
    path = Path('/etc/ai4s/nginx-locations.conf')
    body = path.read_text()
    policy = 'add_header Cache-Control "no-cache, no-store, must-revalidate" always;'
    if 'location = /index.html {' in body and body.count(policy) >= 2:
        return
    old = 'location / {\n    try_files $uri $uri/ /index.html;\n}'
    assert old in body, 'Unexpected Nginx layout; review before changing'
    assert 'location = /index.html {' not in body, 'Existing index policy needs review'
    backup = path.with_name('nginx-locations.before-cache-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '.conf')
    shutil.copy2(path, backup)
    replacement = ('location = /index.html {\n    ' + policy + '\n    try_files $uri =404;\n}\n\n'
                   'location / {\n    ' + policy + '\n    try_files $uri $uri/ /index.html;\n}')
    path.write_text(body.replace(old, replacement))
    try:
        subprocess.run(['/www/server/nginx/sbin/nginx', '-t'], check=True)
        subprocess.run(['/www/server/nginx/sbin/nginx', '-s', 'reload'], check=True)
    except Exception:
        shutil.copy2(backup, path)
        raise


if __name__ == '__main__':
    ensure_spa_cache()
