"""Keeps the latest uploaded files so a user can refresh ONE of the three uploads (pure Python, no Django).

   <root>/master/   the registration master workbook
   <root>/final/    the final-list workbook
   <root>/coursera/ all Coursera CSV files (every account)

An import is built in a temporary 'staging' folder: files uploaded now + the stored files for everything not uploaded.
Only after the database import succeeds are the newly uploaded slots copied into the store (all-or-nothing).
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SLOTS = ("master", "final", "coursera")
SLOT_LABEL = {"master": "Step 1 - Master registration sheet", "final": "Step 2 - Final list", "coursera": "Step 3 - Coursera export"}


class MissingUpload(ValueError):
    def __init__(self, slot):
        self.slot = slot
        super().__init__(f"{SLOT_LABEL[slot]} has not been uploaded yet. Please upload it (the first import needs all three).")


class StagedImport:
    def __init__(self, store, stage, provided):
        self.store, self.stage, self.provided = store, Path(stage), set(provided)

    @property
    def paths(self):
        out = []
        for slot in SLOTS:
            out += sorted(str(p) for p in (self.stage / slot).iterdir() if p.is_file())
        return out

    def commit(self):
        """Replace the stored copy of every slot that was uploaded now."""
        for slot in self.provided:
            target = self.store.root / slot
            old = None
            if target.exists():
                old = self.store.root / f".old-{slot}-{int(time.time() * 1000)}"
                os.replace(target, old)
            shutil.move(str(self.stage / slot), str(target))
            if old:
                shutil.rmtree(old, ignore_errors=True)


class DataStore:
    def __init__(self, root):
        self.root = Path(root)

    def files(self, slot):
        d = self.root / slot
        return sorted(p for p in d.iterdir() if p.is_file()) if d.is_dir() else []

    def info(self):
        """{slot: [{'name', 'saved_at'}]} for display."""
        out = {}
        for slot in SLOTS:
            out[slot] = [{"name": p.name, "saved_at": datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)} for p in self.files(slot)]
        return out

    def cleanup_stale(self, older_than_seconds=86400):
        if not self.root.is_dir():
            return
        for p in self.root.iterdir():
            if p.name.startswith((".staging-", ".old-")) and time.time() - p.stat().st_mtime > older_than_seconds:
                shutil.rmtree(p, ignore_errors=True)

    @contextmanager
    def staging(self, master=None, final=None, coursera=None):
        """master / final: a path or None.  coursera: list of paths or None.  Missing slots fall back to the stored files."""
        self.root.mkdir(parents=True, exist_ok=True)
        self.cleanup_stale()
        stage = Path(tempfile.mkdtemp(prefix=".staging-", dir=self.root))
        provided = set()
        try:
            for slot, given in (("master", master), ("final", final), ("coursera", coursera)):
                d = stage / slot
                d.mkdir()
                if isinstance(given, (str, os.PathLike)):
                    given = [given]
                srcs = [Path(p) for p in (given or [])]
                if srcs:
                    provided.add(slot)
                else:
                    srcs = self.files(slot)
                if not srcs:
                    raise MissingUpload(slot)
                for p in srcs:
                    dest = d / p.name
                    if dest.exists():
                        raise ValueError(f"Two files with the same name '{p.name}' were uploaded in {SLOT_LABEL[slot]}.")
                    shutil.copy2(p, dest)
            yield StagedImport(self, stage, provided)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
