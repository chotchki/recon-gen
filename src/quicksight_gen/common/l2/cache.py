"""In-memory ``L2Instance`` cache + atomic file-write primitive (X.4.a.6).

Studio's source-of-truth contract (per ``SPEC_studio.md``): the L2 YAML on
disk is the hard truth; the in-memory ``L2Instance`` is a cache of the
file, never a parallel source. This module ships the two primitives that
contract rests on:

- ``L2InstanceCache`` — Studio-owned read cache. Loads once at startup
  via ``common/l2/loader.py::load_instance``; holds the parsed model so
  per-request YAML re-parse isn't on the hot path. Read-only here in
  X.4.a.6 (``get()``); the ``save()`` method that writes back to disk
  lands at X.4.d.3 once the editor's serializer (``serialize_l2``) is
  in place. ``replace(new_instance)`` is provided now so the X.4.d.1
  mutators have a typed seam to update the cache without touching disk.

- ``save_yaml_atomic(text, path)`` — atomic file write: temp file in
  the same directory, fsync the contents, then rename onto the target.
  POSIX rename(2) inside one filesystem is atomic, so a crash mid-write
  leaves either the old YAML or the new YAML on disk — never a torn
  half-written file. The temp lives in the target's parent dir
  specifically so the rename can't cross filesystems.

Severability: this module is Studio-side. Dashboards (``quicksight-gen
dashboards``) does NOT instantiate ``L2InstanceCache`` — it reads the L2
once at startup and keeps the parsed instance in its own scope. The
cache only exists when Studio mounts.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

from .loader import load_instance
from .primitives import L2Instance


def save_yaml_atomic(text: str, path: Path) -> None:
    """Atomically write ``text`` to ``path``.

    Writes to a temp file in ``path.parent`` (so the final rename stays
    within one filesystem and is therefore atomic per POSIX
    ``rename(2)``), fsyncs the file contents, then renames onto the
    target. A crash between fsync and rename leaves the old file
    intact; a crash during write leaves a stray ``.<name>.*.tmp`` that
    the next save overwrites.

    Caller's responsibility: ``path.parent`` must already exist.
    """
    path = Path(path)
    fd, tmp_str = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent),
    )
    tmp = Path(tmp_str)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        # Path.replace == os.rename on POSIX; atomic within one fs.
        tmp.replace(path)
    except BaseException:
        # Clean up the temp on any failure (write error, KeyboardInterrupt,
        # the rename itself raising). Suppress the cleanup error so the
        # original exception propagates.
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


class L2InstanceCache:
    """Studio-owned in-memory cache of one ``L2Instance``.

    Constructed once at Studio startup via ``L2InstanceCache.from_path``;
    bound to a single L2 YAML path for its lifetime. Studio request
    handlers call ``get()`` to read the current model; once X.4.d lands
    its mutators, they call ``replace(new_instance)`` to swap the
    cached value, and X.4.d.3's serializer-aware ``save()`` will pair
    ``replace`` with ``save_yaml_atomic`` so disk + cache stay in sync.

    Read-only-from-disk for now (X.4.a.6 ships ``get()`` + ``replace``);
    no reload-on-file-change watcher (per the SPEC's "Studio writes;
    nobody else writes" rule).
    """

    __slots__ = ("_path", "_instance")

    def __init__(self, path: Path, instance: L2Instance) -> None:
        self._path = path
        self._instance = instance

    @classmethod
    def from_path(cls, path: Path | str) -> L2InstanceCache:
        """Load the YAML at ``path`` and wrap the result in a cache."""
        p = Path(path)
        return cls(p, load_instance(p))

    @property
    def path(self) -> Path:
        return self._path

    def get(self) -> L2Instance:
        return self._instance

    def replace(self, new_instance: L2Instance) -> None:
        """Swap the cached instance without writing to disk.

        X.4.d's mutators (``mutate_l2`` / ``rename_identifier``)
        produce a new ``L2Instance`` from the old one; ``replace``
        installs it. Pairing this with a disk write happens in
        ``save()`` below (which composes ``serialize_l2`` +
        ``save_yaml_atomic`` + ``replace``).
        """
        self._instance = new_instance

    def save(self, new_instance: L2Instance) -> None:
        """Persist + swap: serialize ``new_instance`` to YAML, atomically
        write it to ``self.path``, then ``replace`` the cached instance.

        The X.4.e PUT handler composes
        ``mutate_l2 → validate → save`` — every successful mutation
        lands on disk + in cache atomically (within one event loop
        tick). A crash mid-write leaves the prior YAML intact (per
        ``save_yaml_atomic``'s POSIX rename guarantee).

        Caller is responsible for validating ``new_instance`` BEFORE
        calling ``save`` — a structural-break delete that re-raises
        ``L2ValidationError`` should never reach disk.
        """
        from .serializer import serialize_l2

        save_yaml_atomic(serialize_l2(new_instance), self._path)
        self._instance = new_instance
