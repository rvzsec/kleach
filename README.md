<div align="center">

<img src="https://www.zyenra.com/assets/img/kleach.png" alt="kleach logo" width="128" height="128">

# kleach

**Smart, aggressive cleanup for low-storage devices and projects you've abandoned.**

</div>

> ⚠️ **WARNING - kleach deletes permanently.** It is built to wipe *everything* a
> dead project left scattered across your system - including state that can never be
> regenerated (AI sessions, editor history, caches, Docker layers). There is no undo.
> Run `kleach scan` first, keep what matters with `kleach keep`, and only then
> `kleach clean --yes`.

---

## What it does

You work on a project for a week, wire up a dozen tools against it - AI assistants,
VS Code, GUI apps, Docker, a few GB of dependencies - then move on. Three months later
all of that is still piling up across `~/.cache`, `~/.config`, `~/.local`, Docker, and
inside the folder itself. Time to kleach it.

kleach finds every trace of an abandoned project and removes it, while **never touching
your source or your config**:

**Kept** - everything inside the project folder (source, `.git`, dotfiles, notes,
configs - whatever you left there), and your own config anywhere else (fish functions,
rc files, app settings).

**Nuked** - in-folder regenerable dirs (`node_modules` / `.venv` / `build` / `target`);
and every external trace that points at the folder: tool sessions, editor state, GUI app
data, caches, logs, and Docker containers/images/volumes - matched generically by the
project's path, a hash of it, or its path found *inside* a file. No tool is hardcoded.
Nothing you want to resume from is lost, because anything saved in the folder stays.

A project gets kleached once it's been idle past a threshold (default 90 days), or
whenever you kleach it by hand.

## Install

```sh
git clone https://github.com/rvzsec/kleach && cd kleach
pipx install .          # or: pip install --user .   (adds `kleach` to your PATH)
kleach setup            # writes an editable ~/.config/kleach/config.yaml
```

## Usage

| command | what it does |
|---|---|
| `kleach setup` | interactive first-run config → `~/.config/kleach/config.yaml` |
| `kleach scan` | show exactly what would be removed (no deletes) |
| `kleach clean` | dry-run plan; add `--yes` to actually delete |
| `kleach keep` | protect the current project forever |
| `kleach drop` | sweep the current project now, regardless of age |
| `kleach list` / `kleach forget` | view / remove protected projects |
| `kleach schedule on\|off` | weekly automatic sweep (systemd timer) |

Everything is tunable in `config.yaml` - scan roots, idle threshold, aggressiveness,
and what counts as cache vs config.

## Credits

Inspired by [BleachBit](https://www.bleachbit.org/) (system junk) and
[kondo](https://github.com/tbillington/kondo) (project artifacts) - kleach ties both
together and adds the per-project, cross-tool state tracking neither does.
