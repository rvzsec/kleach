"""Planners: each returns the Items (and docker refs / shared edits) to act on."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .core import (
    DockerRef,
    Item,
    SharedEdit,
    archive_base,
    distinct_projects,
    expand,
    git,
    mentions,
    path_keys,
    path_size,
    project_last_active,
    search_roots,
    sweep_ok,
    sweepable,
    under_long,
)


def plan_projects(c, reg, cutoff, projects) -> list:
    items: list = []
    names, rels = set(c["artifact_dirs"]), c["artifact_paths"]
    plist = sorted(projects, key=len, reverse=True)

    def owner(path):
        s = str(path)
        for p in plist:
            if s == p or s.startswith(p.rstrip("/") + "/"):
                return p
        return str(path.parent)

    for root in [expand(r) for r in c["scan_roots"] if expand(r).exists()]:
        for dp, dn, _fn in os.walk(root):
            d = Path(dp)
            for name in list(dn):
                if name in names:
                    art = d / name
                    ok, t = sweepable(owner(art), reg, cutoff)
                    if ok:
                        items.append(Item(art, path_size(art), f"artifact in {t} project"))
                    dn.remove(name)
            for rel in rels:
                cand = d / rel
                if cand.is_dir():
                    ok, t = sweepable(owner(cand), reg, cutoff)
                    if ok:
                        items.append(Item(cand, path_size(cand), f"artifact in {t} project"))
    return items


def plan_system_deps(c, reg, cutoff, projects) -> list:
    if not c.get("system_deps"):
        return []
    names, rels = set(c["artifact_dirs"]), c["artifact_paths"]
    plist = sorted(projects, key=len, reverse=True)

    def active(path):
        s = str(path)
        for p in plist:
            if s == p or s.startswith(p.rstrip("/") + "/"):
                return project_last_active(p) >= cutoff
        return False

    items: list = []
    for root in [expand(r) for r in c.get("system_dep_roots", ["~"]) if expand(r).exists()]:
        for dp, dn, _fn in os.walk(root):
            d = Path(dp)
            for name in list(dn):
                full = d / name
                if under_long(full, reg) or active(full):
                    if name in names:
                        dn.remove(name)
                    continue
                if name in names:
                    try:
                        if full.stat().st_mtime < cutoff:
                            items.append(Item(full, path_size(full), "stale rebuildable deps"))
                    except OSError:
                        pass
                    dn.remove(name)
            for rel in rels:
                cand = d / rel
                if cand.is_dir() and not under_long(cand, reg) and not active(cand):
                    try:
                        if cand.stat().st_mtime < cutoff:
                            items.append(Item(cand, path_size(cand), "stale rebuildable deps"))
                    except OSError:
                        pass
    return items


def _git_reclaimable(d: Path):
    """Return the remote URL if this repo can be re-cloned losing nothing, else None."""
    if not shutil.which("git"):
        return None
    r = git(d, "remote")
    if not r or r.returncode != 0 or not r.stdout.strip():
        return None
    remote = r.stdout.split()[0]
    url = git(d, "remote", "get-url", remote)
    url = url.stdout.strip() if url and url.returncode == 0 else remote
    st = git(d, "status", "--porcelain")
    if st is None or st.returncode != 0 or st.stdout.strip():
        return None
    unpushed = git(d, "log", "--branches", "--not", "--remotes", "--oneline")
    if unpushed is None or unpushed.stdout.strip():
        return None
    stash = git(d, "stash", "list")
    if stash and stash.stdout.strip():
        return None
    return url


def plan_reclaim_clones(c, reg, cutoff) -> list:
    if not c.get("reclaim_clones"):
        return []
    items: list = []
    for root in [expand(r) for r in c.get("clone_roots", []) if expand(r).exists()]:
        for dp, dn, _fn in os.walk(root):
            d = Path(dp)
            if ".git" in dn:
                dn[:] = []
                if under_long(d, reg) or project_last_active(str(d)) >= cutoff:
                    continue
                url = _git_reclaimable(d)
                if url:
                    items.append(Item(d, path_size(d), f"re-cloneable from {url}"))
    return items


def plan_redundant_archives(c, reg) -> list:
    if not c.get("dedupe_archives"):
        return []
    items: list = []
    for root in [expand(r) for r in c.get("archive_roots", []) if expand(r).exists()]:
        for dp, dn, fn in os.walk(root):
            d = Path(dp)
            if under_long(d, reg):
                dn[:] = []
                continue
            dirs = set(dn)
            for f in fn:
                base = archive_base(f)
                if base and base in dirs:
                    sib = d / base
                    if sib.is_dir() and any(sib.iterdir()):
                        arch = d / f
                        items.append(Item(arch, path_size(arch),
                                          f"archive redundant with {base}/"))
    return items


def plan_generic_state(c, reg, cutoff, projects) -> list:
    preserve = set(c.get("preserve_names", []))
    exact: dict = {}
    hashes: list = []
    for proj in projects:
        nkeys, hkeys = path_keys(proj)
        for k in nkeys:
            exact.setdefault(k, proj)
        for h in hkeys:
            hashes.append((h, proj))
    items, seen = [], set()
    for base in search_roots(c):
        base_depth = len(base.parts)
        for dp, dn, fn in os.walk(base):
            d = Path(dp)
            if len(d.parts) - base_depth >= c["state_search_depth"]:
                dn[:] = []
            for name in list(dn) + fn:
                if name in preserve:
                    continue
                proj = exact.get(name)
                if proj is None:
                    for h, pr in hashes:
                        if h in name:
                            proj = pr
                            break
                if proj is None:
                    continue
                node = d / name
                if str(node) in seen:
                    continue
                ok, t = sweepable(proj, reg, cutoff)
                if not ok:
                    if name in dn:
                        dn.remove(name)
                    continue
                if sweep_ok(node, c):
                    seen.add(str(node))
                    items.append(Item(node, path_size(node), f"state ({t}) for {proj}"))
                    if name in dn:
                        dn.remove(name)
    return items


def plan_content_state(c, reg, cutoff, projects):
    if not c.get("content_scan", True) or not shutil.which("grep"):
        return [], []
    roots = [str(expand(r)) for r in c.get("content_scan_roots", c["state_search_roots"])
             if expand(r).is_dir()]
    plist = sorted(projects, key=len, reverse=True)
    if not roots or not plist:
        return [], []
    preserve = set(c.get("preserve_names", []))
    pats: list = []
    for p in plist:
        pats += ["-e", p, "-e", "file://" + p]
    try:
        res = subprocess.run(["grep", "-rlaF", *pats, *roots],
                             capture_output=True, text=True, timeout=900)
    except (OSError, subprocess.TimeoutExpired):
        return [], []
    items, surgical = [], []
    for line in res.stdout.split("\n"):
        if not line:
            continue
        f = Path(line)
        if f.name in preserve:
            continue
        try:
            if f.is_symlink() or not f.is_file():
                continue
            st = f.lstat()
            text = f.read_bytes().decode("latin-1")   # lossless byte->char, keeps offsets
        except OSError:
            continue
        hits = {p for p in plist if mentions(text, p) or mentions(text, "file://" + p)}
        distinct = distinct_projects(hits)
        if len(distinct) == 1:
            proj = next(iter(distinct))
            ok, t = sweepable(proj, reg, cutoff)
            if ok and sweep_ok(f, c):
                items.append(Item(f, st.st_size, f"contents reference {proj} ({t})"))
        elif c.get("surgical_shared") and sweep_ok(f, c):
            try:
                text.encode("latin-1").decode("utf-8")   # only edit valid-utf8 text;
            except UnicodeDecodeError:                   # leave binary DBs untouched
                continue
            sweep_here = [p for p in distinct if sweepable(p, reg, cutoff)[0]]
            if sweep_here:
                surgical.append(SharedEdit(f, sweep_here))
    return items, surgical


def plan_docker(c, reg, cutoff, projects) -> list:
    if not c.get("docker_scan", True) or not shutil.which("docker"):
        return []
    plist = sorted(projects, key=len, reverse=True)

    def match(wd):
        for p in plist:
            if wd == p or wd.startswith(p.rstrip("/") + "/"):
                return p
        return None

    def q(args):
        try:
            return subprocess.run(["docker", *args], capture_output=True,
                                  text=True, timeout=60).stdout
        except (OSError, subprocess.TimeoutExpired):
            return ""

    refs, sweep_proj = [], {}
    for line in q(["ps", "-a", "--format",
                   '{{.ID}}\t{{.Label "com.docker.compose.project"}}'
                   '\t{{.Label "com.docker.compose.project.working_dir"}}']).splitlines():
        cid, cproj, wd = (line.split("\t") + ["", "", ""])[:3]
        if not cid or not wd:
            continue
        p = match(wd)
        if not p:
            continue
        ok, t = sweepable(p, reg, cutoff)
        if not ok:
            continue
        size = 0
        try:
            size = int(q(["inspect", "-s", "--format", "{{.SizeRw}}", cid]).strip() or 0)
        except ValueError:
            pass
        refs.append(DockerRef("container", cid, size, f"docker container ({t}) for {p}", p))
        if cproj:
            sweep_proj[cproj] = p
    if not sweep_proj:
        return refs
    for line in q(["volume", "ls", "--format",
                   '{{.Name}}\t{{.Label "com.docker.compose.project"}}']).splitlines():
        name, vproj = (line.split("\t") + ["", ""])[:2]
        if vproj in sweep_proj:
            refs.append(DockerRef("volume", name, 0, f"docker volume for {vproj}", sweep_proj[vproj]))
    for line in q(["network", "ls", "--format",
                   '{{.Name}}\t{{.Label "com.docker.compose.project"}}']).splitlines():
        name, nproj = (line.split("\t") + ["", ""])[:2]
        if nproj in sweep_proj and name not in ("bridge", "host", "none"):
            refs.append(DockerRef("network", name, 0, f"docker network for {nproj}", sweep_proj[nproj]))
    swept_cids = {r.ref for r in refs if r.kind == "container"}
    for line in q(["images", "--format", "{{.ID}}\t{{.Repository}}"]).splitlines():
        iid, repo = (line.split("\t") + ["", ""])[:2]
        for sp, pp in sweep_proj.items():
            if repo == sp or repo.startswith(sp + "-") or repo.startswith(sp + "_"):
                users = [x for x in q(["ps", "-a", "--filter", f"ancestor={iid}", "-q"]).split() if x]
                if any(u not in swept_cids for u in users):
                    break      # still used by a container we're keeping -> don't touch it
                refs.append(DockerRef("image", iid, 0, f"docker image {repo}", pp))
                break
    return refs


def plan_junk_files(c, reg) -> list:
    items: list = []
    for root in [expand(r) for r in c["scan_roots"] if expand(r).exists()]:
        for g in ("*.swp", "*.swo", "*~", "*.pyc"):
            for f in root.rglob(g):
                if f.is_file() and not under_long(f, reg):
                    items.append(Item(f, path_size(f), "scattered junk file"))
    return items


def dedupe(items: list) -> list:
    items.sort(key=lambda i: str(i.path))
    kept: list = []
    for i in items:
        s = str(i.path)
        if any(s == str(k.path) or s.startswith(str(k.path).rstrip("/") + "/") for k in kept):
            continue
        kept.append(i)
    return kept
