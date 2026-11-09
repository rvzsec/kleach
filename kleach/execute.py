"""Executors: filesystem removal, global caches, package-manager cleaners, docker prune."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .config import CLEANERS
from .core import Item, expand, path_size, reclaimed


def _safe_rm(path: Path, preserve: set) -> None:
    if path.name in preserve:
        return
    if path.is_symlink():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        if preserve and any(p.name in preserve for p in path.rglob("*")):
            for ch in list(path.iterdir()):
                _safe_rm(ch, preserve)
            try:
                path.rmdir()
            except OSError:
                pass
        else:
            shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def rm(item: Item, dry: bool, preserve=frozenset()) -> bool:
    if dry:
        return True
    try:
        _safe_rm(item.path, set(preserve))
        return True
    except OSError as e:
        print(f"  ! {item.path}: {e}", file=sys.stderr)
        return False


def clear_contents(d: Path, dry: bool) -> int:
    if not d.is_dir():
        return 0
    freed = 0
    for ch in d.iterdir():
        freed += path_size(ch)
        if not dry:
            try:
                if ch.is_dir() and not ch.is_symlink():
                    shutil.rmtree(ch, ignore_errors=True)
                else:
                    ch.unlink(missing_ok=True)
            except OSError:
                pass
    return freed


def run_global(c, dry: bool) -> int:
    if not dry:
        for cmd in c["global_cache_cmds"]:
            if shutil.which(cmd[0]):
                subprocess.run(cmd, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, check=False)
    return sum(clear_contents(expand(g), dry) for g in c["global_cache_dirs"])


def _detect(spec) -> bool:
    if "cmd" in spec:
        return bool(shutil.which(spec["cmd"]))
    if "path" in spec:
        return expand(spec["path"]).exists()
    return True


def run_cleaners(c, scope, dry):
    """Run spec-driven package-manager cleaners for one scope. Returns (freed, notes)."""
    freed, notes = 0, []
    sudo = scope == "system" and os.geteuid() != 0
    for spec in CLEANERS:
        if spec.get("scope") != scope or not _detect(spec):
            continue
        for d in spec.get("dir", []):
            freed += clear_contents(expand(d), dry)
            if dry:
                notes.append(f"[{spec['id']}] clear {d}")
        for cmd in spec.get("run", []):
            full = (["sudo"] + cmd) if sudo else cmd
            if dry:
                notes.append(f"[{spec['id']}] run: {' '.join(full)}")
            else:
                try:
                    out = subprocess.run(full, capture_output=True, text=True,
                                         timeout=300).stdout
                    freed += reclaimed(out)
                except (OSError, subprocess.TimeoutExpired):
                    pass
    return freed, notes


def docker_prune(c, hours: float, dry: bool):
    """System-wide: prune exited containers + unused images + build cache older
    than the window. Returns (freed_bytes, [note...])."""
    if not c.get("docker_prune") or not shutil.which("docker"):
        return 0, []
    until = f"{int(hours)}h"

    def dk(args):
        try:
            return subprocess.run(["docker", *args], capture_output=True,
                                  text=True, timeout=300).stdout
        except (OSError, subprocess.TimeoutExpired):
            return ""

    if dry:
        exited = [x for x in dk(["ps", "-a", "--filter", "status=exited", "-q"]).split() if x]
        dangling = [x for x in dk(["images", "-f", "dangling=true", "-q"]).split() if x]
        notes = [f"{len(exited)} exited containers, {len(dangling)} dangling images,"
                 f" + unused images/build cache older than {until}"]
        if c.get("docker_prune_volumes"):
            notes.append("+ unused volumes")
        return 0, notes

    freed = (reclaimed(dk(["container", "prune", "-f", "--filter", f"until={until}"]))
             + reclaimed(dk(["image", "prune", "-a", "-f", "--filter", f"until={until}"]))
             + reclaimed(dk(["builder", "prune", "-f", "--filter", f"until={until}"])))
    notes = ["pruned exited containers, unused images, build cache"]
    if c.get("docker_prune_volumes"):
        freed += reclaimed(dk(["volume", "prune", "-f"]))
        notes[0] += " + volumes"
    return freed, notes
