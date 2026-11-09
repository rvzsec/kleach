"""Command-line interface: argument parsing, commands, and the collect pipeline."""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone

from . import core
from .config import CONFIG_DIR, CONFIG_TEMPLATE, DEFAULTS, HOME, __version__, cfg, registry, save_registry
from .core import discover_projects, expand, human, log
from .execute import clear_contents, docker_prune, rm, run_cleaners, run_global
from .planners import (
    dedupe,
    plan_content_state,
    plan_docker,
    plan_generic_state,
    plan_junk_files,
    plan_projects,
    plan_reclaim_clones,
    plan_redundant_archives,
    plan_system_deps,
)


# ---- collect ----------------------------------------------------------------
def _collect(a):
    c = cfg()
    reg = registry()
    core._ACTIVE.clear()
    core._OPTS["deep"] = bool(c.get("deep_activity", False))
    age_days = a.age if getattr(a, "age", None) is not None else c["age_days"]
    hours = age_days * 24 + c.get("age_hours", 0)
    cutoff = time.time() - hours * 3600
    projects = set(reg["projects"].keys()) | discover_projects(c)
    only = getattr(a, "only", None)
    pstate = c.get("project_state", True)
    proj_filter = [str(expand(x)) for x in (getattr(a, "project", None) or [])]
    if proj_filter:
        projects |= set(proj_filter)
    items, dockers, surgical = [], [], []
    if (not only or only == "projects") and pstate:
        items += plan_projects(c, reg, cutoff, projects)
    if not only or only == "deps":
        items += plan_system_deps(c, reg, cutoff, projects)
    if not only or only == "clones":
        items += plan_reclaim_clones(c, reg, cutoff)
    if not only or only == "archives":
        items += plan_redundant_archives(c, reg)
    if (not only or only == "state") and pstate:
        items += plan_generic_state(c, reg, cutoff, projects)
        ci, surgical = plan_content_state(c, reg, cutoff, projects)
        items += ci
    if not only or only == "junk":
        items += plan_junk_files(c, reg)
    if (not only or only == "docker") and pstate:
        dockers += plan_docker(c, reg, cutoff, projects)
    if proj_filter:
        def _m(reason, path=None):
            return any(p in reason or (path is not None and
                       (str(path) == p or str(path).startswith(p.rstrip("/") + "/")))
                       for p in proj_filter)
        items = [i for i in items if _m(i.reason, i.path)]
        dockers = [d for d in dockers if (d.proj in proj_filter) or _m(d.reason)]
        kept = []
        for s in surgical:
            inter = [p for p in s.projs if p in proj_filter]
            if inter:
                s.projs = inter
                s.reason = f"strip {', '.join(inter)} from shared file"
                kept.append(s)
        surgical = kept
    return c, reg, dedupe(items), dockers, hours, surgical


# ---- commands ---------------------------------------------------------------
def _register(paths, tier):
    reg = registry()
    for raw in (paths or ["."]):
        p = str(expand(raw))
        reg["projects"][p] = {"tier": tier, "added": datetime.now(timezone.utc).isoformat()}
        print(f"{tier.upper()}: {p}")
    save_registry(reg)


def cmd_keep(a):
    _register(a.path, "long")


def cmd_drop(a):
    _register(a.path, "short")


def cmd_forget(a):
    reg = registry()
    for raw in (a.path or ["."]):
        p = str(expand(raw))
        if reg["projects"].pop(p, None):
            print(f"forgot {p}")
        else:
            print(f"not registered: {p}")
    save_registry(reg)


def cmd_list(a):
    reg = registry()
    if not reg["projects"]:
        print("no projects registered (abandonment age still applies to all)")
        return
    for p, m in sorted(reg["projects"].items()):
        print(f"  {m['tier'].upper():5}  {p}")


def _impact(i):
    try:
        age = max((time.time() - i.path.lstat().st_mtime) / 86400, 1.0)
    except OSError:
        age = 1.0
    return i.size * age


def cmd_scan(a):
    c, _, items, dockers, hours, surgical = _collect(a)
    only = getattr(a, "only", None)
    scoped = bool(getattr(a, "project", None))
    items = sorted(items, key=lambda x: -_impact(x))        # impact = size x age
    globals_ = []
    if (not only or only == "global") and not scoped:
        for g in c["global_cache_dirs"]:
            gp = expand(g)
            sz = clear_contents(gp, dry=True) if gp.is_dir() else 0
            if sz:
                globals_.append((gp, sz))
    if getattr(a, "json", False):
        print(json.dumps({
            "items": [{"path": str(i.path), "size": i.size, "reason": i.reason} for i in items],
            "docker": [{"kind": d.kind, "ref": d.ref, "size": d.size, "reason": d.reason}
                       for d in dockers],
            "global": [{"path": str(p), "size": sz} for p, sz in globals_],
            "edits": [{"path": str(s.path), "projects": s.projs, "reason": s.reason}
                      for s in surgical],
            "total_bytes": sum(i.size for i in items) + sum(d.size for d in dockers)
            + sum(sz for _, sz in globals_),
        }, indent=2))
        return
    for i in items:
        print(f"  {human(i.size):>8}  {i.path}   ({i.reason})")
    for d in dockers:
        print(f"  {human(d.size):>8}  docker:{d.kind}/{d.ref}   ({d.reason})")
    for p, sz in globals_:
        print(f"  {human(sz):>8}  {p}   (global cache, cleared wholesale on clean)")
    for s in surgical:
        print(f"  {'(edit)':>8}  {s.path}   ({s.reason})")
    if (not only or only == "docker") and not scoped:
        for n in docker_prune(c, hours, dry=True)[1]:
            print(f"  {'(prune)':>8}  docker: {n}")
    if (not only or only == "pkg") and not scoped:
        if c.get("pkg_caches", True):
            for n in run_cleaners(c, "user", dry=True)[1]:
                print(f"  {'(pkg)':>8}  {n}")
        if getattr(a, "system", False):
            for n in run_cleaners(c, "system", dry=True)[1]:
                print(f"  {'(sys)':>8}  {n}")
    total = sum(i.size for i in items) + sum(d.size for d in dockers) + sum(sz for _, sz in globals_)
    extra = "" if scoped else " (+ package managers / docker prune on clean)"
    print(f"\n{len(items)+len(dockers)+len(globals_)} items (+{len(surgical)} edits), "
          f"~{human(total)} reclaimable{extra}")


def cmd_clean(a):
    c, _, items, dockers, hours, surgical = _collect(a)
    only = getattr(a, "only", None)
    dry = not a.yes
    tag = "[DRY-RUN] " if dry else ""
    preserve = c.get("preserve_names", [])
    freed = 0
    errors = 0
    for i in sorted(items, key=lambda x: -x.size):
        if rm(i, dry, preserve):
            freed += i.size
            if a.verbose or dry:
                print(f"  {tag}{human(i.size):>8}  {i.path}  ({i.reason})")
        else:
            errors += 1
    for d in dockers:
        d.remove(dry)
        freed += d.size
        if a.verbose or dry:
            print(f"  {tag}{human(d.size):>8}  docker:{d.kind}/{d.ref}  ({d.reason})")
    for s in surgical:
        if s.remove(dry) and (a.verbose or dry):
            print(f"  {tag}{'(edit)':>8}  {s.path}  ({s.reason})")
    scoped = bool(getattr(a, "project", None))
    gl = run_global(c, dry) if ((not only or only == "global") and not scoped) else 0
    dpf = 0
    if (not only or only == "docker") and not scoped:
        dpf, notes = docker_prune(c, hours, dry)
        if (a.verbose or dry) and notes:
            print(f"  {tag}docker prune: {'; '.join(notes)}")
    pf = 0
    if (not only or only == "pkg") and not scoped:
        if c.get("pkg_caches", True):
            uf, un = run_cleaners(c, "user", dry)
            pf += uf
            if (a.verbose or dry) and un:
                print(f"  {tag}pkg (user): {'; '.join(un)}")
        if getattr(a, "system", False):
            sf, sn = run_cleaners(c, "system", dry)
            pf += sf
            if (a.verbose or dry) and sn:
                print(f"  {tag}pkg (system): {'; '.join(sn)}")
    total = freed + gl + dpf + pf
    msg = (f"{tag}reclaimed ~{human(total)} (targeted {human(freed)}, global {human(gl)}, "
           f"docker {human(dpf)}, pkg {human(pf)}) from {len(items)+len(dockers)} items")
    print(msg)
    if not dry:
        log(msg)
    if errors:
        print(f"{errors} item(s) could not be removed", file=sys.stderr)
        return 1
    return 0


def cmd_schedule(a):
    unitdir = HOME / ".config/systemd/user"
    if a.action == "off":
        import subprocess
        subprocess.run(["systemctl", "--user", "disable", "--now", "kleach.timer"], check=False)
        for u in ("kleach.timer", "kleach.service"):
            (unitdir / u).unlink(missing_ok=True)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        print("auto-sweep disabled")
        return 0
    import subprocess
    exe = shutil_which_kleach()
    interval = getattr(a, "interval", None) or "weekly"
    unitdir.mkdir(parents=True, exist_ok=True)
    (unitdir / "kleach.service").write_text(
        f"[Unit]\nDescription=kleach sweep\n\n[Service]\nType=oneshot\n"
        f"ExecStart={exe} clean --yes\n")
    (unitdir / "kleach.timer").write_text(
        f"[Unit]\nDescription=kleach sweep ({interval})\n\n[Timer]\n"
        f"OnCalendar={interval}\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    subprocess.run(["systemctl", "--user", "enable", "--now", "kleach.timer"], check=False)
    print(f"sweep enabled, interval={interval} (systemd user timer)")
    return 0


def shutil_which_kleach() -> str:
    import shutil
    return shutil.which("kleach") or f"{sys.executable} -m kleach"


def cmd_setup(a):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        import yaml  # noqa: F401
        has_yaml = True
    except ImportError:
        has_yaml = False
    target = CONFIG_DIR / ("config.yaml" if has_yaml else "config.json")
    if target.exists() and not a.force:
        print(f"config exists: {target}\nedit it, or `kleach setup --force` to reset to defaults")
        return 0
    if has_yaml:
        target.write_text(CONFIG_TEMPLATE)
    else:
        base = {k: DEFAULTS[k] for k in ("scan_roots", "age_days", "age_hours",
                "project_state", "content_scan", "system_deps", "system_dep_roots",
                "global_cache_dirs", "docker_prune", "docker_prune_volumes")}
        target.write_text(json.dumps(base, indent=2))
        print("(install PyYAML for a commented config.yaml)")
    print(f"wrote {target}\nedit it, then:  kleach scan   (preview)   |   kleach clean --yes")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="kleach",
                                description="Smart, aggressive cleanup for low-storage "
                                            "devices and projects you've abandoned.")
    p.add_argument("--version", action="version", version=f"kleach {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    psu = sub.add_parser("setup", help="write an editable config file")
    psu.add_argument("--force", action="store_true", help="overwrite existing config")
    psu.set_defaults(func=cmd_setup)

    for name, fn, h in (("keep", cmd_keep, "protect project(s) forever"),
                        ("drop", cmd_drop, "mark project(s) sweepable now")):
        sp = sub.add_parser(name, help=h)
        sp.add_argument("path", nargs="*", help="one or more project folders (default: cwd)")
        sp.set_defaults(func=fn)

    pf = sub.add_parser("forget", help="unregister project(s)")
    pf.add_argument("path", nargs="*")
    pf.set_defaults(func=cmd_forget)

    sub.add_parser("list", help="show registered projects").set_defaults(func=cmd_list)

    for name, fn, h in (("scan", cmd_scan, "report what would be removed"),
                        ("clean", cmd_clean, "sweep (dry-run unless --yes)")):
        sp = sub.add_parser(name, help=h)
        sp.add_argument("--age", type=int, help="override idle-days threshold")
        sp.add_argument("--only", choices=["projects", "deps", "clones", "archives",
                                           "state", "junk", "docker", "global", "pkg"])
        sp.add_argument("--system", action="store_true",
                        help="also run root-scope cleaners (distro package caches, via sudo)")
        sp.add_argument("--project", action="append", metavar="PATH",
                        help="restrict to one project's traces (repeatable); "
                             "skips global/pkg/docker-prune")
        if name == "clean":
            sp.add_argument("--yes", action="store_true", help="actually delete")
            sp.add_argument("-v", "--verbose", action="store_true")
        else:
            sp.add_argument("--json", action="store_true", help="machine-readable output")
        sp.set_defaults(func=fn)

    ps = sub.add_parser("schedule", help="auto-sweep on/off")
    ps.add_argument("action", choices=["on", "off"])
    ps.add_argument("--interval", default="weekly",
                    help="systemd OnCalendar value: hourly|daily|weekly|monthly "
                         "or an expression like '*-*-* 03:00:00'")
    ps.set_defaults(func=cmd_schedule)
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    try:
        rc = a.func(a)
        return rc if isinstance(rc, int) else 0
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except BrokenPipeError:
        return 0
    except Exception as e:                       # top-level guard: no raw tracebacks
        print(f"kleach: error: {e}", file=sys.stderr)
        return 1
