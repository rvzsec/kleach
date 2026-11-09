# AGENTS.md - driving kleach from an AI assistant

kleach is an aggressive, universal cleaner. It **permanently deletes** a project's
disposable traces (dependencies, build dirs, tool/IDE/AI sessions, caches, Docker)
while **never touching source or config**. There is no undo. `scan` previews;
`clean --yes` deletes.

## Golden rules for an agent

1. **Always `scan` first and show the user the list before `clean --yes`.** Deletion is permanent.
2. kleach keeps the project folder's source, `.git`, dotfiles, and notes, plus the
   user's own config (shell functions, rc files). It removes only disposable traces.
   It will **not** delete a whole project folder - if the user wants that, they do it themselves.
3. Never add `--system` (root/sudo cleaners) or enable the aggressive opt-ins
   (`system_deps`, `docker_prune`, `reclaim_clones`) unless the user asked for that scope.
4. Don't invent paths. Operate on the project the user names, or the current directory.

## Intent → commands

| User says | Do |
|---|---|
| "I'm done with this project, won't work on it again" | `cd <project>` → `kleach drop` → `kleach scan` (show user) → `kleach clean --yes` |
| "protect this project / keep it" | `kleach keep <project>` |
| "stop protecting it" | `kleach forget <project>` |
| "what would be cleaned?" | `kleach scan` (or `kleach scan --json` to parse) |
| "free up disk / clean stale stuff" | `kleach scan` → `kleach clean --yes` |
| "also clean distro package caches" | `kleach clean --yes --system` (uses sudo) |
| "set it to run automatically" | `kleach schedule on` (weekly) / `kleach schedule off` |
| "just the docker stuff" | `kleach scan --only docker` → `kleach clean --yes --only docker` |

`drop` marks a project sweepable **now** (ignores the idle age). Untouched projects
are also swept automatically once idle past the configured window (default 90 days),
so for "I abandoned this months ago" a plain `kleach clean --yes` already catches it;
`drop` is for "done as of today."

## Tiers

- `keep` → **long**: protected forever (artifacts + all traces kept).
- `drop` → **short**: swept on the next run.
- unregistered → swept once idle past the age window.

## Config (optional)

`kleach setup` writes `~/.config/kleach/config.yaml` (editable, commented). kleach also
runs on built-in defaults with no config. Keys an agent might set **only on request**:
`age_days`/`age_hours`, `system_deps` (stale deps anywhere), `docker_prune`,
`reclaim_clones` + `clone_roots` (remove re-cloneable git checkouts), `global_cache_dirs`.

## Example

> "I finished the prototype in ~/dev/spike, archive is pushed, I'm done."

```sh
cd ~/dev/spike
kleach drop
kleach scan            # show the user what will be removed
kleach clean --yes     # delete after they confirm
```

Result: `node_modules`/`.venv`/build dirs, its IDE/AI/CLI sessions and caches, and its
Docker resources are gone; the source and your git history stay.
