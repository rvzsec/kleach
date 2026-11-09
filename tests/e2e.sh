#!/usr/bin/env bash
# Real end-to-end test: a throwaway sandbox HOME is filled with a simulated user
# workspace (projects + other tools' sessions/caches/deps + git clones + archives
# + shared files), then kleach really deletes inside the sandbox and we assert what
# survived vs went. No mocks. Docker is tested separately in tests/docker-it.sh.
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"; export PYTHONPATH="$REPO"
KL="python3 -m kleach"
SANDBOX="$(mktemp -d)"
export HOME="$SANDBOX"
export XDG_CONFIG_HOME="$SANDBOX/.config"
export XDG_STATE_HOME="$SANDBOX/.local/state"
OLD='200 days ago'

mk(){ mkdir -p "$(dirname "$1")"; printf '%s' "$2" > "$1"; }

# live project (recent) - must survive entirely
mk "$SANDBOX/dev/live/.git/HEAD" "ref"
mk "$SANDBOX/dev/live/src/app.js" "x"
mk "$SANDBOX/dev/live/node_modules/x/index.js" "x"
mk "$SANDBOX/.config/liveapp/Sessions/l.json" "cwd=$SANDBOX/dev/live"

# dead project (abandoned) - source/config survive, artifacts+state nuked
mk "$SANDBOX/dev/dead/.git/HEAD" "ref"
mk "$SANDBOX/dev/dead/src/main.py" "print()"
mk "$SANDBOX/dev/dead/README.md" "readme"
mk "$SANDBOX/dev/dead/handoff.md" "resume here"
mk "$SANDBOX/dev/dead/.env" "SECRET=1"
mk "$SANDBOX/dev/dead/node_modules/big/lib.js" "x"
mk "$SANDBOX/dev/dead/.venv/pyvenv.cfg" "x"

# kept project (abandoned BUT marked keep) - must survive entirely
mk "$SANDBOX/dev/kept/.git/HEAD" "ref"
mk "$SANDBOX/dev/kept/node_modules/dep/i.js" "x"

# system-wide stale deps, not inside any project (system_deps feature)
mk "$SANDBOX/scratch/oldlib/node_modules/a/i.js" "x"     # old -> nuked
mk "$SANDBOX/scratch/activelib/node_modules/b/i.js" "x"  # recent -> kept

# re-cloneable git clones (reclaim_clones feature)
if command -v git >/dev/null; then
  export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
  OLD_ISO="$(date -d "$OLD" '+%Y-%m-%d 00:00:00')"
  export GIT_AUTHOR_DATE="$OLD_ISO" GIT_COMMITTER_DATE="$OLD_ISO"
  git -c init.defaultBranch=main init -q --bare "$SANDBOX/remotes/tool.git"
  git clone -q "$SANDBOX/remotes/tool.git" "$SANDBOX/clones/toolA"
  ( cd "$SANDBOX/clones/toolA"; echo x > f; git add f; git commit -qm init
    git push -q -u origin HEAD )                            # clean+pushed -> NUKE
  git clone -q "$SANDBOX/remotes/tool.git" "$SANDBOX/clones/toolDirty"
  ( cd "$SANDBOX/clones/toolDirty"; echo y > uncommitted )  # dirty -> KEEP
  git clone -q "$SANDBOX/remotes/tool.git" "$SANDBOX/clones/toolUnpushed"
  ( cd "$SANDBOX/clones/toolUnpushed"; echo z > g; git add g; git commit -qm local )  # unpushed -> KEEP
  HAVE_GIT=1
else
  HAVE_GIT=0
fi

# redundant archive beside its extracted dir
mk "$SANDBOX/arch/foo/content.txt" "extracted"
mk "$SANDBOX/arch/foo.zip" "zipbytes"      # redundant with foo/ -> NUKE
mk "$SANDBOX/arch/bar.zip" "zipbytes"      # no bar/ dir -> KEEP
# shared multi-project file: dead (abandoned) + live (active) -> surgical edit
mk "$SANDBOX/.local/share/recentapp/recent.json" "[\"$SANDBOX/dev/dead\", \"$SANDBOX/dev/live\"]"

# deep_activity: old top level but a deeply-nested file is recent -> project is ACTIVE
mk "$SANDBOX/dev/deepactive/.git/HEAD" "ref"
mk "$SANDBOX/dev/deepactive/node_modules/x/i.js" "x"
mk "$SANDBOX/dev/deepactive/a/b/nested.txt" "recent work"   # stays recent (not backdated)

ENC="$(printf '%s' "$SANDBOX/dev/dead" | sed 's#/#-#g')"
SHA="$(printf '%s' "$SANDBOX/dev/dead" | sha1sum | awk '{print $1}')"

# external traces of dead project
mk "$SANDBOX/.local/share/toolz/$ENC/session.bin" "blob"          # name: dash-path
mk "$SANDBOX/.local/share/toolx/$SHA/data.bin" "blob"             # name: sha1 hash
mk "$SANDBOX/.local/share/appz/sessions/s1.json" "p=$SANDBOX/dev/dead"  # content + state seg
mk "$SANDBOX/.config/fish/functions/dead-helper.fish" "cd $SANDBOX/dev/dead" # content but CONFIG
mk "$SANDBOX/.config/someapp/settings.toml" "path=$SANDBOX/dev/dead"        # content but CONFIG
mk "$SANDBOX/.cache/junkapp/blob" "garbage"                        # global ~/.cache sweep

# backdate the dead project and all its traces
for p in "$SANDBOX/dev/dead" "$SANDBOX/dev/kept" "$SANDBOX/scratch/oldlib" \
         "$SANDBOX/clones" \
         "$SANDBOX/.local/share/toolz" "$SANDBOX/.local/share/toolx" \
         "$SANDBOX/.local/share/appz" "$SANDBOX/.config/fish" \
         "$SANDBOX/.config/someapp"; do
  find "$p" -exec touch -d "$OLD" {} + 2>/dev/null
done
# deepactive: backdate all but the deeply-nested recent file
touch -d "$OLD" "$SANDBOX/dev/deepactive" "$SANDBOX/dev/deepactive/a" "$SANDBOX/dev/deepactive/a/b" 2>/dev/null
find "$SANDBOX/dev/deepactive/.git" "$SANDBOX/dev/deepactive/node_modules" -exec touch -d "$OLD" {} + 2>/dev/null

mkdir -p "$XDG_CONFIG_HOME/kleach"
cat > "$XDG_CONFIG_HOME/kleach/config.json" <<EOF
{"scan_roots":["$SANDBOX/dev"],"age_days":90,"age_hours":0,"project_state":true,
 "content_scan":true,"docker_scan":false,"system_deps":true,"system_dep_roots":["~"],
 "global_cache_dirs":["~/.cache"],"docker_prune":false,"pkg_caches":false,
 "reclaim_clones":true,"clone_roots":["$SANDBOX/clones"],
 "dedupe_archives":true,"archive_roots":["$SANDBOX/arch"],"surgical_shared":true,
 "deep_activity":true}
EOF
$KL keep "$SANDBOX/dev/kept" >/dev/null
echo "# clean --yes"; $KL clean --yes | tail -1

pass=0; fail=0
keep(){ if [ -e "$1" ]; then echo "  ok   kept   ${1#$SANDBOX}"; pass=$((pass+1)); else echo "  FAIL lost   ${1#$SANDBOX}"; fail=$((fail+1)); fi; }
gone(){ if [ ! -e "$1" ]; then echo "  ok   nuked  ${1#$SANDBOX}"; pass=$((pass+1)); else echo "  FAIL left   ${1#$SANDBOX}"; fail=$((fail+1)); fi; }

echo "# assertions"
keep "$SANDBOX/dev/dead/src/main.py"
keep "$SANDBOX/dev/dead/README.md"
keep "$SANDBOX/dev/dead/handoff.md"
keep "$SANDBOX/dev/dead/.env"
keep "$SANDBOX/dev/dead/.git/HEAD"
keep "$SANDBOX/.config/fish/functions/dead-helper.fish"
keep "$SANDBOX/.config/someapp/settings.toml"
keep "$SANDBOX/dev/kept/node_modules/dep/i.js"
keep "$SANDBOX/dev/live/node_modules/x/index.js"
keep "$SANDBOX/.config/liveapp/Sessions/l.json"
keep "$SANDBOX/scratch/activelib/node_modules"
gone "$SANDBOX/scratch/oldlib/node_modules"
gone "$SANDBOX/dev/dead/node_modules"
gone "$SANDBOX/dev/dead/.venv"
gone "$SANDBOX/.local/share/toolz/$ENC"
gone "$SANDBOX/.local/share/toolx/$SHA"
gone "$SANDBOX/.local/share/appz/sessions/s1.json"
gone "$SANDBOX/.cache/junkapp"
if [ "${HAVE_GIT:-0}" = 1 ]; then
  gone "$SANDBOX/clones/toolA"          # clean + pushed + idle -> re-cloneable
  keep "$SANDBOX/clones/toolDirty"      # uncommitted work -> kept
  keep "$SANDBOX/clones/toolUnpushed"   # unpushed commit -> kept
fi
keep "$SANDBOX/dev/deepactive/node_modules"   # deep_activity: nested recent file keeps it alive
gone "$SANDBOX/arch/foo.zip"
keep "$SANDBOX/arch/bar.zip"
keep "$SANDBOX/arch/foo"

RJ="$SANDBOX/.local/share/recentapp/recent.json"
keep "$RJ"
if ! grep -q "$SANDBOX/dev/dead" "$RJ" 2>/dev/null; then echo "  ok   surgical stripped dead"; pass=$((pass+1)); else echo "  FAIL surgical kept dead"; fail=$((fail+1)); fi
if grep -q "$SANDBOX/dev/live" "$RJ" 2>/dev/null; then echo "  ok   surgical preserved live"; pass=$((pass+1)); else echo "  FAIL surgical dropped live"; fail=$((fail+1)); fi

# regression: content_scan disabled must not crash (was a tuple-unpack ValueError)
echo "{\"scan_roots\":[\"$SANDBOX/dev\"],\"content_scan\":false}" > "$XDG_CONFIG_HOME/kleach/config.json"
if $KL scan >/dev/null 2>&1; then echo "  ok   no crash with content_scan=false"; pass=$((pass+1)); else echo "  FAIL crash with content_scan=false"; fail=$((fail+1)); fi

echo "# result: $pass passed, $fail failed"
rm -rf "$SANDBOX"
[ "$fail" -eq 0 ]
