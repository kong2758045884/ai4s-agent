"""Temporary workspaces for subprocess tests on Windows.

The sandbox runner exits before the test leaves the context, but Windows can
briefly retain its former working-directory handle. Retry cleanup only for a
directory this helper created; persistent locks remain test failures.
"""

from __future__ import annotations

import os
import tempfile
import time
from contextlib import contextmanager
from collections.abc import Iterator


@contextmanager
def sandbox_temporary_directory() -> Iterator[str]:
    folder = tempfile.TemporaryDirectory()
    try:
        yield folder.name
    finally:
        for attempt in range(10):
            try:
                folder.cleanup()
                break
            except PermissionError:
                if os.name != "nt" or attempt == 9:
                    raise
                time.sleep(0.05 * (attempt + 1))
