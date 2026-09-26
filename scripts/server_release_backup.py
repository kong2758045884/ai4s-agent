"""Run over SSH before deployment. Credentials stay on the server."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
from urllib.parse import urlsplit


def env_file(path):
    result = {}
    for line in Path(path).read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            result[key.strip()] = value.strip().strip('\"\'')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('release')
    args = parser.parse_args()
    assert args.release.startswith('ai4s-') and '/' not in args.release
    root = Path('/www/wwwroot/lab')
    destination = root / 'release-backups' / args.release
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    result = {'backup': str(destination), 'sqlite': {}}
    for filename in ['strategic_map.db', 'autobots.db', 'mrag_sqlite.db']:
        source = root / 'reactor-tool' / filename
        target = destination / filename
        with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as src, sqlite3.connect(target) as dst:
            src.backup(dst)
            assert dst.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            counts = {r[0]: dst.execute('SELECT COUNT(*) FROM "'+r[0]+'"').fetchone()[0]
                      for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        result['sqlite'][filename] = {'counts': counts, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
        with target.open('rb') as src, gzip.open(str(target)+'.gz', 'wb', compresslevel=3) as dst:
            shutil.copyfileobj(src, dst)
    for name in ['ai4s-reactor-tool', 'ai4s-reactor-backend']:
        shutil.copy2('/etc/systemd/system/'+name+'.service', destination / (name+'.service'))
    shutil.copytree(root/'config', destination/'config')
    shutil.copy2('/etc/ai4s/backend.env', destination/'backend.env')
    shutil.copy2(root/'reactor-tool/.env', destination/'tool.env')
    shutil.copy2('/www/server/panel/vhost/nginx/ai4s.qfnu.org.cn.conf', destination/'nginx.conf')
    result['frontend'] = str((root/'ui/dist').resolve())
    values = env_file('/etc/ai4s/backend.env')
    url = urlsplit(values['SPRING_DATASOURCE_MYSQL_URL'].removeprefix('jdbc:'))
    database = url.path.lstrip('/')
    cli = ['/www/server/mysql/bin/mysql', '-h', url.hostname, '-P', str(url.port or 3306),
           '-u', values['SPRING_DATASOURCE_MYSQL_USERNAME'], '--default-character-set=utf8mb4', '-N', '-B', database]
    environ = {**os.environ, 'MYSQL_PWD': values['SPRING_DATASOURCE_MYSQL_PASSWORD']}
    schema = subprocess.check_output(cli + ['-e', 'SELECT TABLE_NAME,COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,COLUMN_DEFAULT FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() ORDER BY TABLE_NAME,ORDINAL_POSITION'], env=environ)
    (destination/'mysql-columns.tsv').write_bytes(schema)
    tables = subprocess.check_output(cli+['-e','SHOW TABLES'], env=environ, text=True).splitlines()
    result['mysql'] = {'database': database, 'tables': len(tables), 'counts': {}}
    for table in tables:
        count = subprocess.check_output(cli+['-e','SELECT COUNT(*) FROM `'+table+'`'],env=environ,text=True).strip()
        result['mysql']['counts'][table] = int(count)
    dump = ['/www/server/mysql/bin/mysqldump', '-h', url.hostname, '-P', str(url.port or 3306),
            '-u', values['SPRING_DATASOURCE_MYSQL_USERNAME'], '--single-transaction', '--skip-lock-tables',
            '--no-tablespaces', '--hex-blob', '--default-character-set=utf8mb4', database]
    with gzip.open(destination/'mysql.sql.gz', 'wb', compresslevel=3) as out:
        process = subprocess.Popen(dump, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environ)
        shutil.copyfileobj(process.stdout, out)
        error = process.stderr.read()
        if process.wait() != 0:
            raise RuntimeError('MySQL backup failed: '+error.decode(errors='replace'))
    result['mysql']['dumpBytes'] = (destination/'mysql.sql.gz').stat().st_size
    (destination/'manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
