"""Local API adapter for the user's Hyper-Extract scan engine.

Loads existing graph/index files with network disabled. Only an explicit POST
starts the source scanner; startup and GET endpoints never run paid scans.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI, Request


def create_app(source: Path, snapshots: Path, config: Path | None = None):
    from dotenv import load_dotenv
    load_dotenv(source / '.env', override=False)
    sys.path.insert(0, str(source))
    if config is not None:
        from hyperextract.utils import client
        client.DEFAULT_CONFIG_FILE = config
    with patch('socket.socket.connect', side_effect=RuntimeError('Network disabled during graph initialization')):
        from hyperextract.utils.template_engine import Template
        spec = importlib.util.spec_from_file_location('ai4s_hyper_scan', source / 'daily-hier' / 'scan.py')
        scanner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(scanner)
        graphs = {}
        for name, folder in [('zn', 'ZN'), ('gw', 'GW')]:
            directory = snapshots / folder
            graph = Template.create(str(directory / 'ai4s_hierarchy.yaml'), 'zh')
            graph.load(directory)
            graphs[name] = graph
        scanner.init(graphs)
    app = FastAPI(title='AI4S Hyper-Extract scan adapter')

    @app.get('/api/meta')
    def metadata():
        return {'features': {'scan': True, 'search': False, 'chat': False},
                'graphs': {key: {'nodes': len(value.nodes), 'edges': len(value.edges)} for key, value in graphs.items()},
                'jobs': len(scanner.JOBS), 'automaticPaidCalls': False}

    @app.post('/api/scan')
    async def start_scan(request: Request):
        return await scanner.api_scan_start(request)

    @app.get('/api/scan')
    async def scan_status(request: Request):
        return await scanner.api_scan_status(request)

    return app


if __name__ == '__main__':
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--snapshots', type=Path, required=True)
    parser.add_argument('--port', type=int, default=1606)
    parser.add_argument('--config', type=Path)
    args = parser.parse_args()
    uvicorn.run(create_app(args.source.resolve(), args.snapshots.resolve(), args.config), host='127.0.0.1', port=args.port)
