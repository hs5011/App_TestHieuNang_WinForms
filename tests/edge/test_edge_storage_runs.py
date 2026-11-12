"""8. s07_run.delete_runs: chỉ xoá thư mục con trực tiếp của runs/ (thử '..', đường dẫn tuyệt đối, junction/symlink).
9. storage: id dự án tiếng Việt / ký tự đặc biệt, project.json hỏng."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from perftool import storage
from perftool.models import Project, RunInfo


@pytest.fixture
def s07(monkeypatch):
    from perftool.ui import state
    from perftool.ui.steps import s07_run
    saved = []
    monkeypatch.setattr(state, "save", lambda p: saved.append(p))
    s07_run._saved = saved
    return s07_run


def _mkrun(root: Path, run_id: str, script_rel: str | None = None) -> RunInfo:
    d = root / run_id
    d.mkdir(parents=True, exist_ok=True)
    f = d / "script.js"
    f.write_text("//")
    (d / "raw.csv").write_text("x")
    return RunInfo(run_id=run_id, tool="k6", status="failed", script_file=str(script_rel or f))


def _setup(tmp_path):
    p = Project(id="del-edge")
    runs = storage.sub_dir(p.id, "runs")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "important.txt").write_text("keep")
    return p, runs, outside


def test_delete_runs_only_direct_children(tmp_path, s07):
    p, runs, outside = _setup(tmp_path)
    good = _mkrun(runs, "r1")
    keep = _mkrun(runs, "r2")
    p.runs = [good, keep]
    s07.delete_runs(p, [good], del_files=True)
    assert not (runs / "r1").exists() and (runs / "r2").exists()
    assert [r.run_id for r in p.runs] == ["r2"] and s07._saved


def test_delete_runs_without_files_keeps_disk(tmp_path, s07):
    p, runs, _ = _setup(tmp_path)
    r = _mkrun(runs, "r1")
    p.runs = [r]
    s07.delete_runs(p, [r], del_files=False)
    assert (runs / "r1").exists() and p.runs == []


@pytest.mark.parametrize("make_path", [
    lambda runs, out: str(runs / ".." / ".." / ".." / "outside" / "script.js"),     # '..' thoát ra ngoài
    lambda runs, out: str(out / "script.js"),                                     # đường dẫn tuyệt đối ngoài
    lambda runs, out: str(runs / "script.js"),                                    # file ngay trong runs/ -> d = runs
    lambda runs, out: str(runs / "r1" / "sub" / "script.js"),                     # cháu, không phải con trực tiếp
    lambda runs, out: "",                                                         # rỗng -> thư mục hiện hành
    lambda runs, out: str(runs.parent / "script.js"),                             # thư mục dự án
    lambda runs, out: str(runs / "r1" / ".." / ".." / "script.js"),
])
def test_delete_runs_rejects_paths_outside(tmp_path, s07, make_path, monkeypatch):
    p, runs, outside = _setup(tmp_path)
    _mkrun(runs, "r1")
    (runs / "r1" / "sub").mkdir()
    monkeypatch.chdir(outside)                           # nếu lỡ xoá "thư mục hiện hành" thì sẽ thấy ngay
    bad = RunInfo(run_id="..", tool="k6", status="failed", script_file=make_path(runs, outside))
    p.runs = [bad]
    s07.delete_runs(p, [bad], del_files=True)
    assert (outside / "important.txt").exists()
    assert runs.exists() and runs.parent.exists() and (runs / "r1" / "script.js").exists()
    assert p.runs == []


def _junction(link: Path, target: Path) -> bool:
    try:
        if sys.platform == "win32":
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
        else:
            os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, AttributeError):
        return False


def test_delete_runs_junction_run_dir_pointing_outside(tmp_path, s07):
    p, runs, outside = _setup(tmp_path)
    (outside / "script.js").write_text("//")
    if not _junction(runs / "evil", outside):
        pytest.skip("không tạo được junction/symlink")
    bad = RunInfo(run_id="evil", tool="k6", status="failed", script_file=str(runs / "evil" / "script.js"))
    p.runs = [bad]
    s07.delete_runs(p, [bad], del_files=True)
    assert (outside / "important.txt").exists()


def test_delete_runs_junction_inside_run_dir_not_followed(tmp_path, s07):
    p, runs, outside = _setup(tmp_path)
    r = _mkrun(runs, "r1")
    if not _junction(runs / "r1" / "link", outside):
        pytest.skip("không tạo được junction/symlink")
    p.runs = [r]
    s07.delete_runs(p, [r], del_files=True)
    assert (outside / "important.txt").exists()          # rmtree không đi theo junction ra ngoài
    assert not (runs / "r1").exists()


def test_delete_runs_other_project_run_dir(tmp_path, s07):
    p, runs, _ = _setup(tmp_path)
    other_runs = storage.sub_dir("du-an-khac", "runs")
    foreign = _mkrun(other_runs, "r9")
    p.runs = [foreign]
    s07.delete_runs(p, [foreign], del_files=True)
    assert (other_runs / "r9" / "script.js").exists()


# ------------------------------------------------------------------ storage
@pytest.mark.parametrize("name,prefix", [
    ("Hệ thống Quản lý Chính quyền số – Đợt 2", "he-thong-quan-ly-chinh-quyen-s"),
    ("../../etc/passwd", "etc-passwd"), ("CON", "con"), ("!!!", "project"), ("", "project"),
    ("a" * 500, "a" * 30), ("Đ/Ư\\Ơ:*?<>|", "d-u-o"),
])
def test_create_project_slug_is_safe(name, prefix):
    p = storage.create_project(name)
    assert p.id.split("-", 2)[2] == prefix
    assert (storage.PROJECTS_DIR / p.id / "project.json").exists()
    assert storage.PROJECTS_DIR.resolve() in (storage.PROJECTS_DIR / p.id).resolve().parents
    assert storage.load_project(p.id).info.name == name


def test_vietnamese_project_id_roundtrip():
    p = Project(id="dự-án-thử")
    p.info.name = "Dự án thử ✓"
    storage.save_project(p)
    assert storage.load_project("dự-án-thử").info.name == "Dự án thử ✓"
    assert [x.id for x in storage.list_projects()] == ["dự-án-thử"]


def test_corrupted_project_json(tmp_path):
    good = Project(id="good")
    storage.save_project(good)
    for pid, content in (("broken", "{not json"), ("empty", ""), ("wrong", json.dumps({"foo": 1})),
                         ("bom", "﻿" + good.model_dump_json())):
        d = storage.PROJECTS_DIR / pid
        d.mkdir(parents=True)
        (d / "project.json").write_text(content, encoding="utf-8")
    with pytest.raises(Exception):
        storage.load_project("broken")
    ids = {p.id for p in storage.list_projects()}
    assert "good" in ids and "broken" not in ids and "empty" not in ids and "wrong" not in ids


def test_save_project_atomic_no_tmp_left():
    p = Project(id="atomic")
    storage.save_project(p)
    storage.save_project(p)
    files = sorted(x.name for x in (storage.PROJECTS_DIR / "atomic").iterdir())
    assert files == ["project.json"]


def test_delete_project_missing_is_noop():
    storage.delete_project("khong-ton-tai")
