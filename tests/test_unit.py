"""Unit tests for kleach's pure functions (no filesystem / no side effects)."""
import hashlib
import json
from pathlib import Path

from kleach import core
from kleach.config import DEFAULTS


def test_mentions_respects_path_boundary():
    assert core.mentions("/a/app/x", "/a/app")
    assert core.mentions('"/a/app"', "/a/app")
    assert core.mentions("/a/app", "/a/app")
    # must NOT match a longer sibling segment (trailing)
    assert not core.mentions("/a/app2", "/a/app")
    assert not core.mentions("/a/app-x", "/a/app")
    assert not core.mentions("/a/app.bak", "/a/app")


def test_mentions_requires_leading_boundary():
    # unrelated path that merely ends with the project path must NOT match
    assert not core.mentions("/mnt/backup/home/user/dev/app", "/home/user/dev/app")
    assert not core.mentions("xapp/a/app", "pp/a/app")
    # but a path separator (incl. file://) is a valid leading boundary
    assert core.mentions("file:///a/app", "/a/app")
    assert core.mentions("cwd=/a/app", "/a/app")


def test_mentions_empty_needle():
    assert core.mentions("anything", "") is False


def test_path_keys_encodings_and_hashes():
    names, hashes = core.path_keys("/home/u/dev/proj")
    assert "-home-u-dev-proj" in names
    assert "file:///home/u/dev/proj" in names
    assert all(len(n) >= 12 for n in names)
    assert hashlib.sha1(b"/home/u/dev/proj").hexdigest() in hashes
    assert hashlib.md5(b"/home/u/dev/proj").hexdigest() in hashes


def test_name_keys_pairs_carry_their_separator():
    pairs = dict(core.name_keys("/home/u/dev/proj"))
    assert pairs["-home-u-dev-proj"] == "-"
    assert pairs["%2Fhome%2Fu%2Fdev%2Fproj"] == "%2F"
    assert pairs["file:///home/u/dev/proj"] == "/"


def test_state_matcher_exact_and_subfolder_longest_wins():
    m = core.state_matcher(["/home/u/dev/proj", "/home/u/dev/proj/frontend"])
    assert m("-home-u-dev-proj") == "/home/u/dev/proj"
    # a subfolder that is itself a project -> most specific key wins
    assert m("-home-u-dev-proj-frontend") == "/home/u/dev/proj/frontend"
    # deeper state under that subfolder -> nearest project via boundary prefix
    assert m("-home-u-dev-proj-frontend-dist") == "/home/u/dev/proj/frontend"
    # the %2F encoding boundary-matches too
    assert m("%2Fhome%2Fu%2Fdev%2Fproj%2Fbuild") == "/home/u/dev/proj"


def test_state_matcher_sibling_boundary_and_dash_ambiguity():
    m = core.state_matcher(["/home/u/dev/proj"])
    # sibling sharing only a name-stem prefix: next char is not the separator -> no match
    assert m("-home-u-dev-proj2") is None
    # inherent dash lossiness: proj-backup is indistinguishable from proj/backup
    assert m("-home-u-dev-proj-backup") == "/home/u/dev/proj"
    # too short to be any key
    assert m("short") is None


def test_to_bytes():
    assert core.to_bytes("1.5GB") == int(1.5 * 1024 ** 3)
    assert core.to_bytes("512MB") == 512 * 1024 ** 2
    assert core.to_bytes("2.0 GiB") == 2 * 1024 ** 3
    assert core.to_bytes("") == 0
    assert core.to_bytes("garbage") == 0


def test_reclaimed_parsing():
    assert core.reclaimed("Total reclaimed space: 2.0GB") == 2 * 1024 ** 3
    assert core.reclaimed("nothing here") == 0


def test_archive_base():
    assert core.archive_base("foo.tar.gz") == "foo"
    assert core.archive_base("foo.zip") == "foo"
    assert core.archive_base("foo") is None
    assert core.archive_base(".zip") is None


def test_distinct_projects_collapses_nested():
    assert core.distinct_projects({"/a", "/a/b"}) == {"/a"}
    assert core.distinct_projects({"/a", "/b"}) == {"/a", "/b"}
    assert core.distinct_projects({"/a/app", "/a/app2"}) == {"/a/app", "/a/app2"}


def test_tier_of_longest_prefix_wins():
    reg = {"projects": {"/a": {"tier": "long"}, "/a/b": {"tier": "short"}}}
    assert core.tier_of(Path("/a/x"), reg) == "long"
    assert core.tier_of(Path("/a/b/x"), reg) == "short"
    assert core.tier_of(Path("/other"), reg) == "unregistered"


def test_sweepable_registered_tiers():
    reg = {"projects": {"/a": {"tier": "long"}, "/b": {"tier": "short"}}}
    assert core.sweepable("/a", reg, 0)[0] is False
    assert core.sweepable("/b", reg, 0)[0] is True


def test_strip_projects_json_array_boundary():
    text = json.dumps(["/a/app", "/a/app2", "/a/other"])
    data = json.loads(core.strip_projects(text, ["/a/app"]))
    assert "/a/app" not in data        # removed
    assert "/a/app2" in data           # boundary: sibling kept
    assert "/a/other" in data


def test_strip_projects_json_map():
    text = json.dumps({"/a/app": 1, "/a/app2": 2})
    data = json.loads(core.strip_projects(text, ["/a/app"]))
    assert "/a/app" not in data
    assert "/a/app2" in data


def test_strip_projects_no_change_returns_none():
    assert core.strip_projects('["/x"]', ["/a/app"]) is None


def test_strip_projects_text_lines_boundary():
    text = "open /a/app here\nkeep /a/app2 here\n"
    new = core.strip_projects(text, ["/a/app"])
    assert "/a/app2" in new          # sibling line kept (boundary)
    assert "open /a/app here" not in new


def test_is_env_dir_detects_marker_regardless_of_name(tmp_path):
    venv = tmp_path / "myenv"             # a venv named outside the known set
    venv.mkdir()
    assert core.is_env_dir(venv, ["pyvenv.cfg"]) is False
    (venv / "pyvenv.cfg").write_text("home = /usr\n")
    assert core.is_env_dir(venv, ["pyvenv.cfg"]) is True
    assert core.is_env_dir(venv, []) is False        # no markers configured -> never matches


def test_sweep_ok_cache_vs_config():
    c = DEFAULTS
    assert core.sweep_ok(Path("/x/Cache/f"), c) is True
    assert core.sweep_ok(Path("/x/Sessions/s.json"), c) is True
    assert core.sweep_ok(Path("/x/functions/helper.fish"), c) is False
    assert core.sweep_ok(Path("/x/app/settings.toml"), c) is False
