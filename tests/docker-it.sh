#!/usr/bin/env bash
# REAL docker integration test (no mocks): live daemon. Proves kleach removes a
# swept project's container + volume, and does NOT remove an image that a
# surviving container still uses. Skips cleanly if docker/image is unavailable.
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"; export PYTHONPATH="$REPO"
KL="python3 -m kleach"

docker info >/dev/null 2>&1 || { echo "SKIP: docker daemon not usable"; exit 0; }
if [ -z "$(docker images -q busybox 2>/dev/null)" ]; then
  docker pull -q busybox >/dev/null 2>&1 || { echo "SKIP: no busybox image and pull failed"; exit 0; }
fi

ID="kleachit$$"
IMG="${ID}-web"             # named after the compose project -> kleach may target it
CN="${ID}-app"             # swept project's container
CNK="${ID}-keeper"         # a DIFFERENT, surviving container using the same image
VOL="${ID}_data"
TMPPROJ="$(mktemp -d)"      # the project we sweep
OTHERDIR="$(mktemp -d)"     # unrelated dir (not a project) -> keeper survives
SB="$(mktemp -d)"; export HOME="$SB" XDG_CONFIG_HOME="$SB/.config"

cleanup() {
  docker rm -f "$CN" "$CNK" >/dev/null 2>&1
  docker volume rm "$VOL" >/dev/null 2>&1
  docker rmi -f "$IMG" >/dev/null 2>&1
  rm -rf "$TMPPROJ" "$OTHERDIR" "$SB" 2>/dev/null
}
trap cleanup EXIT

echo "FROM busybox" | docker build -q -t "$IMG" - >/dev/null
docker volume create --label com.docker.compose.project="$ID" "$VOL" >/dev/null
docker run -d --name "$CN" \
  --label com.docker.compose.project="$ID" \
  --label com.docker.compose.project.working_dir="$TMPPROJ" \
  -v "$VOL":/data "$IMG" sleep 600 >/dev/null
docker run -d --name "$CNK" \
  --label com.docker.compose.project.working_dir="$OTHERDIR" \
  "$IMG" sleep 600 >/dev/null

$KL drop "$TMPPROJ" >/dev/null

echo "clean --yes (scoped to swept project)"
$KL clean --yes -v --only docker --project "$TMPPROJ"

pass=0; fail=0
chk(){ if eval "$2"; then echo "ok  $1"; pass=$((pass+1)); else echo "XX  $1"; fail=$((fail+1)); fi; }
chk "swept container removed"        '[ -z "$(docker ps -a --filter name=$CN -q)" ]'
chk "swept volume removed"           '[ -z "$(docker volume ls --filter name=$VOL -q)" ]'
chk "shared image KEPT (in use)"     '[ -n "$(docker images -q $IMG)" ]'
chk "surviving container KEPT"       '[ -n "$(docker ps -a --filter name=$CNK -q)" ]'
echo "result: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
