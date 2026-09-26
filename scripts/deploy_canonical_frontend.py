"""Atomically publish a frontend and retire legacy SPA entry scripts on this host."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path('/www/wwwroot/lab')
BEGIN = '# BEGIN AI4S canonical entries'
END = '# END AI4S canonical entries'
POLICY = 'add_header Cache-Control "no-cache, no-store, must-revalidate" always;'


def entry(path):
    match = re.search(r'<script\b[^>]*\bsrc="([^"]+)"', path.read_text())
    assert match and re.fullmatch(r'/assets/[\w.-]+\.js', match[1]), str(path)
    return match[1]


def deploy(dist):
    dist = Path(dist).resolve()
    assert dist.is_relative_to(ROOT/'releases') and dist.name == 'dist'
    current = ROOT/'ui/dist'
    assert current.is_symlink()
    previous = current.resolve()
    new_entry = entry(dist/'index.html')
    old_entries = {entry(p) for p in (ROOT/'releases').glob('*/ui/**/index.html')}
    old_entries.update(entry(p) for p in (ROOT/'ui').glob('dist.before-*/index.html'))
    old_entries.discard(new_entry)
    config = Path('/etc/ai4s/nginx-locations.conf')
    original = config.read_text()
    body = re.sub(re.escape(BEGIN)+r'.*?'+re.escape(END)+r'\n?', '', original, flags=re.S)
    bridge = '(()=>{const u=new URL(location.href);const map=u.pathname==="/workspace/strategic-map"||u.searchParams.get("view")==="strategic-map";u.pathname=map?"/workspace/strategic-map":"/app";if(map)u.searchParams.delete("view");u.searchParams.delete("appBuild");location.replace(u.pathname+u.search+u.hash);})();'
    block = f'''{BEGIN}
location = / {{
    {POLICY}
    if ($arg_view = strategic-map) {{ return 302 /workspace/strategic-map$is_args$args; }}
    return 302 /app$is_args$args;
}}
location = /app {{
    {POLICY}
    if ($arg_view = strategic-map) {{ return 302 /workspace/strategic-map$is_args$args; }}
    try_files /index.html =404;
}}
'''
    for retired in sorted(old_entries):
        block += f'''location = {retired} {{
    default_type application/javascript;
    {POLICY}
    return 200 '{bridge}';
}}
'''
    block += END+'\n'
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
    backup = config.with_name('nginx-locations.before-canonical-'+stamp+'.conf')
    shutil.copy2(config, backup)
    # Preserve other lazy chunks for an already-open conversation, but old entry
    # bundles are served the recovery bridge instead of the obsolete application.
    for asset in (previous/'assets').glob('*'):
        destination = dist/'assets'/asset.name
        if asset.is_file() and not destination.exists():shutil.copy2(asset, destination)
    temporary = ROOT/'ui'/('dist.next-'+stamp)
    try:
        config.write_text(block+body)
        subprocess.run(['/www/server/nginx/sbin/nginx','-t'], check=True)
        temporary.symlink_to(dist, target_is_directory=True)
        os.replace(temporary,current)
        subprocess.run(['/www/server/nginx/sbin/nginx','-s','reload'], check=True)
    except Exception:
        config.write_text(original)
        if temporary.is_symlink():temporary.unlink()
        temporary.symlink_to(previous,target_is_directory=True);os.replace(temporary,current)
        subprocess.run(['/www/server/nginx/sbin/nginx','-s','reload'],check=False)
        raise
    result={'previous':str(previous),'current':str(dist),'entry':new_entry,
            'retiredEntries':sorted(old_entries),'nginxBackup':str(backup)}
    (dist.parent/'deployment.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result))


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('dist')
    deploy(parser.parse_args().dist)
