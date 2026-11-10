"""Core model and pure helpers: sizing, path matching, project activity, items."""
from __future__ import annotations

import hashlib
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .config import HOME, LOG, STATE_DIR

# Per-run caches, reset by the CLI at the start of each collection.
_ACTIVE: dict[str, float] = {}
_OPTS: dict[str, bool] = {"deep": False}

_ARCHIVE_SUFFIXES = [".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst", ".tgz", ".tbz2",
                     ".txz", ".tar", ".zip", ".7z", ".rar", ".gz", ".bz2", ".xz", ".zst"]


# ---- small helpers ----------------------------------------------------------
def expand(p: str) -> Path:
    return Path(os.path.expanduser(p)).resolve()


def human(n: float) -> str:
    for u in ("B", "K", "M", "G", "T"):
        if n < 1024:
            return f"{n:.0f}{u}" if u == "B" else f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}P"


def dir_size(p: Path) -> int:
    total = 0
    for dp, _dn, fn in os.walk(p, followlinks=False):
        for f in fn:
            try:
                total += (Path(dp) / f).lstat().st_size
            except OSError:
                pass
    return total


def path_size(p: Path) -> int:
    try:
        st = p.lstat()
    except OSError:
        return 0
    return dir_size(p) if (p.is_dir() and not p.is_symlink()) else st.st_size


def log(msg: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as fh:
        fh.write(f"{datetime.now(timezone.utc).isoformat()} {msg}\n")


def to_bytes(s: str) -> int:
    """Parse a human size like '1.23GB' into bytes (best effort)."""
    s = s.strip().upper().replace("IB", "B")
    num = ""
    for ch in s:
        if ch.isdigit() or ch == ".":
            num += ch
        else:
            break
    try:
        n = float(num)
    except ValueError:
        return 0
    unit = s[len(num):].strip() or "B"
    mult = {"B": 1, "KB": 1024, "K": 1024, "MB": 1024**2, "M": 1024**2,
            "GB": 1024**3, "G": 1024**3, "TB": 1024**4}.get(unit, 1)
    return int(n * mult)


def reclaimed(out: str) -> int:
    for line in out.splitlines():
        if "reclaimed space" in line.lower():
            return to_bytes(line.split(":")[-1])
    return 0


def mentions(text: str, needle: str) -> bool:
    """True if needle occurs at BOTH path boundaries. So /a/app does not match
    /a/app2 or /a/app-x (trailing), nor an unrelated suffix like /mnt/bak/a/app
    (leading). A path separator counts as a boundary, so a file://-prefixed
    occurrence still matches."""
    if not needle:
        return False
    cont = "._-"
    n = len(needle)
    i = text.find(needle)
    while i != -1:
        prev = text[i - 1] if i > 0 else ""
        nxt = text[i + n:i + n + 1]
        lead = prev == "" or not (prev.isalnum() or prev in cont)
        trail = nxt == "" or not (nxt.isalnum() or nxt in cont)
        if lead and trail:
            return True
        i = text.find(needle, i + n)
    return False


def archive_base(name: str):
    low = name.lower()
    for suf in _ARCHIVE_SUFFIXES:
        if low.endswith(suf) and len(name) > len(suf):
            return name[:-len(suf)]
    return None


def name_keys(path_str: str):
    """Flattened single-name encodings a tool might use to key per-project state,
    each paired with the path separator that encoding uses. The separator lets a
    caller boundary-match a sub-path (key + sep + subpath) to its project while a
    sibling that merely shares a name prefix stays unmatched."""
    p = path_str.rstrip("/")
    bare = p.lstrip("/")
    pairs = [(p, "/"), (bare, "/"), (p.replace("/", "-"), "-"),
             ("-" + bare.replace("/", "-"), "-"), (p.replace("/", "%2F"), "%2F"),
             ("file://" + p, "/")]
    out: dict[str, str] = {}
    for key, sep in pairs:
        if len(key) >= 12:
            out.setdefault(key, sep)
    return list(out.items())


def path_keys(path_str: str):
    """Flattened name keys and path hashes a tool might use to key per-project state."""
    names = {k for k, _ in name_keys(path_str)}
    p = path_str.rstrip("/")
    hashes = set()
    for form in (p, p + "/", "file://" + p):
        b = form.encode()
        for algo in (hashlib.md5, hashlib.sha1, hashlib.sha256):
            hashes.add(algo(b).hexdigest())
    return names, hashes


def state_matcher(projects):
    """Resolve a flattened state name to its owning project by boundary-aware
    longest-prefix match. A name belongs to a project when it equals an encoded
    key, or begins with that key followed by the encoding's own separator; the
    longest (most specific) key wins. Purely path-based, no tool knowledge.

    Flattening that collapses '/' to '-' is lossy, so a dash-encoded name cannot
    be told apart from a literal-hyphen sibling (proj/sub vs proj-sub). That is
    inherent to the encoding, not special-cased: longest-match lets a more
    specific project reclaim its own key, and any residue is swept with the
    parent, consistent with the rest of kleach."""
    keyed = []
    for proj in projects:
        for key, sep in name_keys(proj):
            keyed.append((key, sep, proj))
    keyed.sort(key=lambda t: len(t[0]), reverse=True)   # most specific wins
    buckets: dict = {}
    for entry in keyed:
        buckets.setdefault(entry[0][:8], []).append(entry)   # keys are >= 12 chars

    def match(name):
        if len(name) < 12:
            return None
        for key, sep, proj in buckets.get(name[:8], ()):
            if name == key or (name.startswith(key) and name[len(key):].startswith(sep)):
                return proj
        return None
    return match


def distinct_projects(paths) -> set:
    out: list[str] = []
    for p in sorted(paths, key=len):
        if not any(p == o or p.startswith(o.rstrip("/") + "/") for o in out):
            out.append(p)
    return set(out)


def git(d, *args, timeout=15):
    try:
        return subprocess.run(["git", "-C", str(d), *args],
                              capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def git_has_tracked(path: Path) -> bool:
    """True if the directory contains files tracked by git (version-controlled
    source), so it must not be deleted even when its name matches a build/artifact
    pattern. False when the dir is untracked, git-ignored, or not inside a repo at
    all - which keeps node_modules and friends deletable while holding a committed
    build/. Path-based, no name or tool knowledge."""
    r = git(path, "ls-files")
    return bool(r and r.returncode == 0 and r.stdout.strip())


# ---- project model ----------------------------------------------------------
def tier_of(path: Path, reg) -> str:
    s = str(path)
    best = None
    for rp, meta in reg["projects"].items():
        if s == rp or s.startswith(rp.rstrip("/") + "/"):
            if best is None or len(rp) > len(best[0]):
                best = (rp, meta)
    return best[1]["tier"] if best else "unregistered"


def under_long(path: Path, reg) -> bool:
    s = str(path)
    return any(m.get("tier") == "long" and (s == rp or s.startswith(rp.rstrip("/") + "/"))
               for rp, m in reg["projects"].items())


def project_last_active(p: str) -> float:
    """Most recent activity: working-tree file mtimes (VCS internals ignored) and,
    for git repos, the last commit date. Cached per run."""
    if p in _ACTIVE:
        return _ACTIVE[p]
    newest = 0.0
    pth = Path(p)
    vcs = {".git", ".hg", ".svn"}
    try:
        newest = pth.stat().st_mtime
        if _OPTS.get("deep"):
            for dp, dn, fn in os.walk(pth):
                dn[:] = [x for x in dn if x not in vcs]
                for f in fn:
                    try:
                        newest = max(newest, (Path(dp) / f).lstat().st_mtime)
                    except OSError:
                        pass
        else:
            for ch in pth.iterdir():
                if ch.name in vcs:
                    continue
                try:
                    newest = max(newest, ch.stat().st_mtime)
                except OSError:
                    pass
    except OSError:
        newest = 0.0
    if (pth / ".git").exists():
        r = git(pth, "log", "-1", "--format=%ct")
        if r and r.returncode == 0 and r.stdout.strip():
            try:
                newest = max(newest, float(r.stdout.strip()))
            except ValueError:
                pass
    _ACTIVE[p] = newest
    return newest


def sweepable(proj: str, reg, cutoff: float):
    """(should_sweep, tier) for the project a trace belongs to."""
    t = tier_of(Path(proj), reg)
    if t == "long":
        return False, t
    if t == "short":
        return True, t
    return project_last_active(proj) < cutoff, t


def is_env_dir(path: Path, markers) -> bool:
    """True if the directory holds a defining marker file (e.g. pyvenv.cfg for a
    Python virtual environment), identifying it as a rebuildable environment
    regardless of what it is named. Path-based, no tool or name knowledge."""
    return any((path / m).is_file() for m in markers)


def discover_projects(c) -> set:
    markers = set(c["project_markers"])
    found = set()
    for r in c["scan_roots"]:
        root = expand(r)
        if not root.exists():
            continue
        for dp, dn, fn in os.walk(root):
            if markers & (set(dn) | set(fn)):
                found.add(str(Path(dp)))
                dn[:] = []
    return found


def sweep_ok(path: Path, c) -> bool:
    """True = disposable cache/state (sweep). False = user config (keep).
    Judges the path WITHIN home, so the home location can't pollute it."""
    sp = str(path)
    if sp.startswith(str(HOME / ".cache") + "/") or sp.startswith(str(HOME / ".local/state") + "/"):
        return True
    try:
        rel = path.relative_to(HOME).parts
    except ValueError:
        rel = path.parts
    parts = {p.lower() for p in rel}
    if parts & {s.lower() for s in c.get("state_segments", [])}:
        return True
    if parts & {s.lower() for s in c.get("config_segments", [])}:
        return False
    if path.suffix.lower() in {e.lower() for e in c.get("config_exts", [])}:
        return False
    return True


def search_roots(c) -> list[Path]:
    roots = set()
    for r in c["state_search_roots"]:
        p = expand(r)
        if p.is_dir():
            roots.add(p)
    if c.get("scan_hidden_home"):
        try:
            for child in HOME.iterdir():
                if child.name.startswith(".") and child.is_dir() and not child.is_symlink():
                    roots.add(child.resolve())
        except OSError:
            pass
    out: list[Path] = []
    for r in sorted(roots, key=lambda p: len(str(p))):
        if not any(str(r) == str(o) or str(r).startswith(str(o).rstrip("/") + "/") for o in out):
            out.append(r)
    return out


# ---- items ------------------------------------------------------------------
class Item:
    def __init__(self, path: Path, size: int, reason: str):
        self.path, self.size, self.reason = path, size, reason


class DockerRef:
    def __init__(self, kind: str, ref: str, size: int, reason: str, proj=None):
        self.kind, self.ref, self.size, self.reason = kind, ref, size, reason
        self.proj = proj      # owning project path (for --project scoping)

    def remove(self, dry: bool) -> bool:
        import subprocess as sp
        cmd = {"container": ["docker", "rm", "-f", self.ref],
               "volume": ["docker", "volume", "rm", self.ref],
               "network": ["docker", "network", "rm", self.ref],
               "image": ["docker", "rmi", "-f", self.ref]}[self.kind]
        if dry:
            return True
        sp.run(cmd, stdout=sp.DEVNULL, stderr=sp.DEVNULL, check=False)
        return True


def _refs(x, proj) -> bool:
    if isinstance(x, str):
        return mentions(x, proj)
    if isinstance(x, dict):
        return any(_refs(k, proj) or _refs(v, proj) for k, v in x.items())
    if isinstance(x, list):
        return any(_refs(v, proj) for v in x)
    return False


def strip_projects(text: str, projs):
    """Remove entries referencing any of projs from JSON (arrays/maps) or text
    lines. Returns new text, or None if nothing changed / can't be done safely."""
    import json as _json

    def hit(x):
        return any(_refs(x, p) for p in projs)

    try:
        obj = _json.loads(text)

        def filt(o):
            if isinstance(o, list):
                return [filt(v) for v in o if not hit(v)]
            if isinstance(o, dict):
                return {k: filt(v) for k, v in o.items() if not (hit(k) or hit(v))}
            return o
        new_obj = filt(obj)
        if new_obj == obj:
            return None                 # nothing to strip -> don't rewrite/reformat
        return _json.dumps(new_obj, indent=2)
    except (ValueError, TypeError):
        lines = text.splitlines(keepends=True)
        kept = [ln for ln in lines if not any(mentions(ln, p) for p in projs)]
        new = "".join(kept)
        return new if new != text else None


class SharedEdit:
    """Strip one or more projects' entries out of a file shared by many projects."""
    def __init__(self, path: Path, projs, size: int = 0):
        self.path, self.projs, self.size = path, list(projs), size
        self.ref = str(path)
        self.reason = f"strip {', '.join(projs)} from shared file"

    def remove(self, dry: bool) -> bool:
        try:
            text = self.path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            return False            # missing or binary -> never touch
        new = strip_projects(text, self.projs)
        if new is None:
            return False
        if not dry:
            try:
                self.path.write_text(new)
            except OSError:
                return False
        return True
