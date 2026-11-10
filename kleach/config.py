"""Paths, defaults, and config/registry loading for kleach."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

__version__ = "0.1.0"

HOME = Path.home()
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / "kleach"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", HOME / ".local/state")) / "kleach"
REGISTRY = CONFIG_DIR / "registry.json"
LOG = STATE_DIR / "kleach.log"

DEFAULTS: dict = {
    "scan_roots": ["~/dev", "~/projects", "~/code"],
    "age_days": 90,                       # idle this long -> stale/abandoned
    "age_hours": 0,                       # added to age_days for the window
    "deep_activity": False,               # idle = newest file ANYWHERE in the tree
    "artifact_dirs": [
        "node_modules", ".venv", "venv", "env", "__pycache__", ".pytest_cache",
        ".mypy_cache", ".ruff_cache", ".tox", "build", "dist", ".next", ".nuxt",
        ".svelte-kit", ".parcel-cache", ".turbo", ".gradle", "coverage", ".cache",
    ],
    "artifact_paths": ["target/debug", "target/release"],
    "env_markers": ["pyvenv.cfg"],        # a dir holding one of these is a rebuildable env
    "respect_git_tracked": True,          # never delete a dir that holds git-tracked source
    "orphan_stores": {                    # a regenerable store whose tool is not installed is dead
        "~/.local/share/pnpm/store": "pnpm",
        "~/.pnpm-store": "pnpm",
        "~/.cache/yarn": "yarn",
        "~/.cache/go-build": "go",
        "~/.cargo/registry": "cargo",
    },
    "system_deps": False,
    "system_dep_roots": ["~"],
    "reclaim_clones": False,
    "clone_roots": [],
    "dedupe_archives": False,
    "archive_roots": [],
    "surgical_shared": False,
    "state_search_roots": ["~/.config", "~/.local/share", "~/.local/state",
                           "~/.cache", "~/.var/app"],
    "scan_hidden_home": True,
    "state_search_depth": 5,
    "project_state": True,
    "content_scan": True,
    "content_scan_roots": ["~/.config", "~/.local/share", "~/.local/state", "~/.var/app"],
    "project_markers": [".git", "package.json", "Cargo.toml", "go.mod",
                        "pyproject.toml", "composer.json", "pom.xml",
                        "build.gradle", "pubspec.yaml", ".kleach"],
    "state_segments": ["cache", "caches", "gpucache", "code cache", "cacheddata",
        "cachestorage", "sessions", "session", "session storage", "local storage",
        "service worker", "serviceworker", "indexeddb", "blob_storage", "history",
        "logs", "log", "tmp", "temp", "snapshot", "snapshots", "crashpad",
        "shadercache", "dawncache", "shell", "state", "recent", "recentfolders"],
    "config_segments": ["functions", "conf.d", "keybindings", "snippets", "autostart"],
    "config_exts": [".fish", ".sh", ".bash", ".zsh", ".conf", ".cfg", ".ini",
        ".toml", ".yaml", ".yml", ".vim", ".lua", ".el", ".rc", ".desktop"],
    "preserve_names": [],
    "global_cache_dirs": ["~/.cache"],
    "global_cache_cmds": [],
    "pkg_caches": True,
    "docker_scan": True,
    "docker_prune": False,
    "docker_prune_volumes": False,
    "journal_vacuum": "",
}

# Spec-driven package-manager cleaners. Each runs only if `detect` passes. No tool
# is assumed present. scope "user" never needs root; "system" runs under sudo.
CLEANERS: list[dict] = [
    {"id": "npm",   "scope": "user", "cmd": "npm",   "run": [["npm", "cache", "clean", "--force"]]},
    {"id": "pnpm",  "scope": "user", "cmd": "pnpm",  "run": [["pnpm", "store", "prune"]]},
    {"id": "yarn",  "scope": "user", "cmd": "yarn",  "run": [["yarn", "cache", "clean"]]},
    {"id": "pip",   "scope": "user", "cmd": "pip",   "run": [["pip", "cache", "purge"]]},
    {"id": "uv",    "scope": "user", "cmd": "uv",    "run": [["uv", "cache", "clean"]]},
    {"id": "go",    "scope": "user", "cmd": "go",    "run": [["go", "clean", "-cache"]]},
    {"id": "cargo", "scope": "user", "path": "~/.cargo/registry",
     "dir": ["~/.cargo/registry/cache", "~/.cargo/registry/src"]},
    {"id": "aur-yay",  "scope": "user", "path": "~/.cache/yay",  "dir": ["~/.cache/yay"]},
    {"id": "aur-paru", "scope": "user", "path": "~/.cache/paru", "dir": ["~/.cache/paru"]},
    {"id": "pacman", "scope": "system", "cmd": "paccache",
     "run": [["paccache", "-rk1"], ["paccache", "-ruk0"]]},
    {"id": "apt",    "scope": "system", "cmd": "apt-get", "run": [["apt-get", "clean"]]},
    {"id": "dnf",    "scope": "system", "cmd": "dnf",     "run": [["dnf", "clean", "all"]]},
    {"id": "zypper", "scope": "system", "cmd": "zypper",  "run": [["zypper", "clean", "-a"]]},
    {"id": "apk",    "scope": "system", "cmd": "apk",     "run": [["apk", "cache", "clean"]]},
]

CONFIG_TEMPLATE = """\
# kleach configuration - edit and save.
# Preview with `kleach scan`; delete with `kleach clean --yes`. Deletes are permanent.

# ── Age window ──────────────────────────────────────────────────────────────
# Anything staler than days+hours is eligible for cleanup.
age_days: 90
age_hours: 0
deep_activity: false   # true = "idle" means newest file ANYWHERE in the tree
                       # (recursive, exact); false = shallow top-level check (fast)

# ── Per-project cleanup (keeps your source and config, removes dead traces) ──
scan_roots:            # where your projects live
  - ~/dev
project_state: true    # sweep sessions / caches / editor & GUI state of abandoned projects
content_scan: true     # also match tools that write the project path inside a file

# ── Safety: never delete version-controlled source ──────────────────────────
respect_git_tracked: true    # a dir holding git-tracked files is kept even if its
                             # name matches an artifact (a committed build/ stays; an
                             # ignored/untracked node_modules is still removed)

# ── Orphaned stores: a regenerable cache whose tool is uninstalled is dead ───
# which(tool) fails -> the store is swept. Everything here is re-downloadable, so a
# false positive (tool present via a wrapper like mvnw, or a minimal cron PATH) costs
# only a re-download, never data. Edit the map or set it to {} to disable.
orphan_stores:
  ~/.local/share/pnpm/store: pnpm
  ~/.pnpm-store: pnpm
  ~/.cache/yarn: yarn
  ~/.cache/go-build: go
  ~/.cargo/registry: cargo

# ── System-wide reclaim (independent of any project) ────────────────────────
system_deps: false           # remove node_modules/.venv/target… older than age, anywhere below
system_dep_roots:
  - ~
global_cache_dirs:           # cleared wholesale (XDG cache is disposable)
  - ~/.cache
pkg_caches: true             # run detected package-manager cache cleaners (npm/pip/pacman…)
docker_prune: false          # exited containers + unused/dangling images + build cache
docker_prune_volumes: false  # also unused volumes - DANGER: deletes data
# Distro package caches need root: run `kleach clean --system` (uses sudo).

# Re-cloneable git clones (clean tree, nothing unpushed, idle) under these roots
# are removable - you can re-clone them. Keeps anything with local/unpushed work.
reclaim_clones: false
clone_roots: []              # e.g. [/opt, /usr/local/src, ~/tools]

# Redundant archives: a foo.zip sitting beside an extracted foo/ is removable.
dedupe_archives: false
archive_roots: []            # e.g. [~/Downloads, /opt]

# Surgically remove a project's entry from files SHARED by many projects
# (text/JSON recent-lists etc.; binary DBs are never touched).
surgical_shared: false

# ── Advanced (uncomment to override built-in defaults) ──────────────────────
# preserve_names: []                       # never delete files/dirs with these names
# artifact_dirs: [node_modules, .venv, target, build, dist, __pycache__]
# env_markers: [pyvenv.cfg]                 # a dir holding one of these is a rebuildable env, any name
# state_search_depth: 5
# content_scan_roots: [~/.config, ~/.local/share, ~/.local/state, ~/.var/app]
"""


def _read_mapping(path: Path):
    try:
        text = path.read_text()
    except OSError:
        return None
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError:
            print(f"kleach: {path} needs PyYAML (pip install pyyaml); using defaults",
                  file=sys.stderr)
            return None
        return yaml.safe_load(text) or {}
    try:
        return json.loads(text)
    except ValueError:
        return None


def cfg() -> dict:
    c = dict(DEFAULTS)
    for name in ("config.yaml", "config.yml", "config.json"):
        p = CONFIG_DIR / name
        if p.exists():
            data = _read_mapping(p)
            if isinstance(data, dict):
                c.update(data)
            break
    return c


def registry() -> dict:
    try:
        data = json.loads(REGISTRY.read_text())
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("projects"), dict):
        return {"projects": {}}
    return data


def save_registry(reg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(reg, indent=2))
    os.replace(tmp, REGISTRY)      # atomic: never leaves a half-written registry
