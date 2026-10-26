"""Tests of demo/export_scene.py (no GPU needed).

blocks needs no model cache; the chess tests run only when PVHV_MODELS_CACHE points
to a cache that contains the chess glTF."""
import json
import os
import sys
import types

import numpy as np
import pytest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
DEMO = os.path.dirname(HERE)
sys.path.insert(0, DEMO)

import export_scene  # noqa: E402  (inserts <repo>/renderer into sys.path)
from pvhv import peel  # noqa: E402
from pvhv.camera import gl_mat  # noqa: E402
from pvhv.scene import load_scene  # noqa: E402

CHESS_GLTF = os.path.join(os.environ.get("PVHV_MODELS_CACHE", "/nonexistent"), "chess", "chess_set_1k.gltf")
needs_chess = pytest.mark.skipif(not os.path.isfile(CHESS_GLTF),
                                 reason="PVHV_MODELS_CACHE does not contain the chess model")


def export(out, scenes):
    assert export_scene.main(["--out", str(out), "--scenes", ",".join(scenes)]) == 0
    return out


@pytest.fixture(scope="module")
def blocks_out(tmp_path_factory):
    return export(tmp_path_factory.mktemp("blocks"), ["blocks"])


@pytest.fixture(scope="module")
def chess_out(tmp_path_factory):
    if not os.path.isfile(CHESS_GLTF):
        pytest.skip("PVHV_MODELS_CACHE does not contain the chess model")
    return export(tmp_path_factory.mktemp("chess"), ["chess"])


def read_scene(out, name):
    with open(out / name / "scene.json") as fh:
        data = json.load(fh)
    geo = (out / name / "geometry.bin").read_bytes()
    return data, geo


def batch_arrays(data, geo, b):
    o, n = b["offsets"], b["vertex_count"]
    pos = np.frombuffer(geo, "<f4", 3 * n, o["positions"]).reshape(n, 3)
    nrm = np.frombuffer(geo, "<f4", 3 * n, o["normals"]).reshape(n, 3)
    uv = np.frombuffer(geo, "<f4", 2 * n, o["uvs"]).reshape(n, 2)
    idx = np.frombuffer(geo, "<u2" if b["index_type"] == "uint16" else "<u4", b["index_count"], o["indices"])
    return pos, nrm, uv, idx


def check_geometry(out, name):
    data, geo = read_scene(out, name)
    scene = export_scene.canonical_batches(load_scene(name, width=640, height=360))
    assert len(data["batches"]) == len(scene.batches)
    assert len(geo) == data["geometry_bytes"]
    for b, src in zip(data["batches"], scene.batches):
        pos, nrm, uv, idx = batch_arrays(data, geo, b)
        assert b["index_type"] == ("uint16" if b["vertex_count"] <= 65535 else "uint32")
        assert 0 < b["vertex_count"] <= len(src.positions) and b["index_count"] == len(src.positions)
        # expanding the index list reproduces the renderer's arrays bit-exactly
        assert pos[idx].tobytes() == np.ascontiguousarray(src.positions, np.float32).tobytes()
        assert nrm[idx].tobytes() == np.ascontiguousarray(src.normals, np.float32).tobytes()
        assert uv[idx].tobytes() == np.ascontiguousarray(src.uvs, np.float32).tobytes()
        assert np.all(src.items == b["item"])
    return data, scene


def check_uniforms(data, scene):
    cam = scene.camera
    f32 = lambda a: np.asarray(a, np.float32)
    # probe pattern exactly as SceneRenderer._pattern (peel.py) builds it
    fake = types.SimpleNamespace(_patterns={})
    pattern = peel.SceneRenderer._pattern(fake, scene.samples)
    assert np.array_equal(f32(data["probes"]), pattern[:scene.samples].reshape(-1))
    assert data["nSamples"] == scene.samples
    assert np.array_equal(f32(data["view0"]), np.frombuffer(gl_mat(cam.view_matrix()), np.float32))
    assert np.array_equal(f32(data["view_tE"]), np.frombuffer(gl_mat(cam.view_matrix((data["E"], 0.0))), np.float32))
    assert np.array_equal(f32(data["proj_src"]), np.frombuffer(gl_mat(cam.projection_matrix(*cam.source_size)), np.float32))
    assert np.array_equal(f32(data["proj_ref"]), np.frombuffer(gl_mat(cam.projection_matrix(*cam.novel_size)), np.float32))
    for b, src in zip(data["batches"], scene.batches):
        assert np.array_equal(f32(b["model"]), np.frombuffer(gl_mat(src.model), np.float32))
        nm = np.linalg.inv(np.asarray(src.model, np.float64)[:3, :3]).T          # peel.py SceneRenderer.__init__
        assert np.array_equal(f32(b["normal_matrix"]), np.ascontiguousarray(nm.T, np.float32).reshape(-1))
        assert np.array_equal(f32(b["base_color"]), f32(src.base_color))
    for key, val in (("eps", peel.EPS), ("delta", peel.DELTA), ("margin", peel.MARGIN), ("E", scene.E),
                     ("near", cam.near), ("far", cam.far)):
        assert data[key] == float(np.float32(val)), key
    assert (data["W"], data["H"], data["Wg"], data["Hg"], data["ox"], data["oy"]) == (
        cam.W, cam.H, cam.Wg, cam.Hg, cam.ox, cam.oy)
    assert data["F"] == float(cam.F)
    right, up, back = cam.basis()
    assert data["basis"] == {"right": list(right), "up": list(up), "back": list(back)}


def test_blocks_geometry_roundtrip(blocks_out):
    check_geometry(blocks_out, "blocks")


def test_blocks_uniforms_match_renderer(blocks_out):
    data, scene = check_geometry(blocks_out, "blocks")
    check_uniforms(data, scene)


def test_export_is_deterministic(blocks_out, tmp_path):
    again = export(tmp_path / "again", ["blocks"])
    a = sorted(p.relative_to(blocks_out) for p in blocks_out.rglob("*") if p.is_file())
    b = sorted(p.relative_to(again) for p in again.rglob("*") if p.is_file())
    assert a == b
    for rel in a:
        assert (blocks_out / rel).read_bytes() == (again / rel).read_bytes(), rel


def test_manifest_lists_every_file(blocks_out):
    manifest = json.loads((blocks_out / "manifest.json").read_text())
    files = sorted(str(p.relative_to(blocks_out)) for p in blocks_out.rglob("*") if p.is_file())
    assert sorted(f["path"] for f in manifest["files"]) + ["manifest.json"] == sorted(files)
    assert manifest["label"] == export_scene.LABEL
    assert [s["id"] for s in manifest["scenes"]] == ["blocks"]


def test_check_mode(tmp_path, capsys):
    out = export(tmp_path / "out", ["blocks"])
    assert export_scene.main(["--out", str(out), "--scenes", "blocks", "--check"]) == 0
    target = out / "blocks" / "scene.json"
    original = target.read_bytes()
    target.write_bytes(original.replace(b'"version": 1', b'"version": 2'))
    assert export_scene.main(["--out", str(out), "--scenes", "blocks", "--check"]) == 1
    assert "differs: blocks/scene.json" in capsys.readouterr().out
    target.write_bytes(original)
    (out / "stray.txt").write_text("x")
    assert export_scene.main(["--out", str(out), "--scenes", "blocks", "--check"]) == 1
    (out / "stray.txt").unlink()
    assert export_scene.main(["--out", str(out), "--scenes", "blocks", "--check"]) == 0


def _tree(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_unrelated_file_survives_reexport(tmp_path):
    out = export(tmp_path / "out", ["blocks"])
    (out / "notes").mkdir()
    (out / "notes" / "keep.txt").write_text("not an export output")
    (out / "keep.bin").write_bytes(b"x")
    export(out, ["blocks"])
    assert (out / "notes" / "keep.txt").read_text() == "not an export output"
    assert (out / "keep.bin").read_bytes() == b"x"


def test_nonempty_dir_without_manifest_is_refused(tmp_path, capsys):
    out = tmp_path / "docs"
    out.mkdir()
    (out / "index.html").write_text("<p>page</p>")
    before = _tree(out)
    assert export_scene.main(["--out", str(out), "--scenes", "blocks"]) == 2
    assert "no manifest.json" in capsys.readouterr().err
    assert _tree(out) == before
    assert not any(p.name.startswith(".export-") for p in out.iterdir())


def test_file_dropped_from_manifest_is_removed(tmp_path):
    out = export(tmp_path / "out", ["blocks"])
    manifest = json.loads((out / "manifest.json").read_text())
    (out / "old" / "deep").mkdir(parents=True)
    (out / "old" / "deep" / "stale.bin").write_bytes(b"x")
    (out / "old" / "other.txt").write_text("unrelated")
    manifest["files"].append({"path": "old/deep/stale.bin", "bytes": 1, "sha256": "0" * 64})
    (out / "manifest.json").write_text(json.dumps(manifest))
    export(out, ["blocks"])
    assert not (out / "old" / "deep").exists()                    # emptied by the deletion
    assert (out / "old" / "other.txt").read_text() == "unrelated"  # not listed: kept, so old/ stays


@pytest.mark.parametrize("bad", ["../escape.txt", "/etc/passwd", "a/../../x", "./blocks/scene.json", ""])
def test_unsafe_old_manifest_path_is_refused(tmp_path, bad):
    out = export(tmp_path / "out", ["blocks"])
    (tmp_path / "escape.txt").write_text("outside")
    manifest = json.loads((out / "manifest.json").read_text())
    manifest["files"].append({"path": bad, "bytes": 1, "sha256": "0" * 64})
    (out / "manifest.json").write_text(json.dumps(manifest))
    before = _tree(out)
    assert export_scene.main(["--out", str(out), "--scenes", "blocks"]) == 2
    assert _tree(out) == before
    assert (tmp_path / "escape.txt").read_text() == "outside"


def test_reindex_rejects_mixed_items():
    scene = load_scene("blocks", width=640, height=360)
    b = scene.batches[0]
    b.items = b.items.copy()
    b.items[0] += 1
    with pytest.raises(ValueError, match="items"):
        export_scene.reindex(b)


@needs_chess
def test_chess_geometry_and_uniforms(chess_out):
    data, scene = check_geometry(chess_out, "chess")
    check_uniforms(data, scene)
    assert data["items"] == 33


@needs_chess
def test_chess_textures_and_size(chess_out):
    from PIL import Image

    data, _ = read_scene(chess_out, "chess")
    scene = export_scene.canonical_batches(load_scene("chess", width=640, height=360))
    for b, src in zip(data["batches"], scene.batches):
        assert b["texture"] is not None
        with Image.open(chess_out / "chess" / b["texture"]) as im:
            rgb = np.asarray(im.convert("RGB"), np.uint8)
        assert np.array_equal(rgb, scene.textures[src.texture][..., :3])
    total = sum(p.stat().st_size for p in chess_out.rglob("*") if p.is_file())
    print(f"chess export: {total} bytes")
    assert total <= 6_000_000


def test_symlinked_old_output_is_refused_and_target_survives(tmp_path, capsys):
    out = export(tmp_path / "out", ["blocks"])
    (out / "notes").mkdir()
    (out / "notes" / "keep.bin").write_bytes(b"precious")
    (out / "chess").mkdir()
    os.symlink(out / "notes" / "keep.bin", out / "chess" / "geometry.bin")
    manifest = json.loads((out / "manifest.json").read_text())
    manifest["files"].append({"path": "chess/geometry.bin", "bytes": 8, "sha256": "0" * 64})
    (out / "manifest.json").write_text(json.dumps(manifest))
    before = _tree(out)
    assert export_scene.main(["--out", str(out), "--scenes", "blocks"]) == 2
    assert "symbolic link" in capsys.readouterr().err
    assert (out / "notes" / "keep.bin").read_bytes() == b"precious"
    assert os.path.islink(out / "chess" / "geometry.bin")
    assert _tree(out) == before


def test_symlinked_output_dir_is_refused_and_external_file_unchanged(tmp_path, capsys):
    out = export(tmp_path / "out", ["blocks"])
    external = tmp_path / "external"
    external.mkdir()
    (external / "scene.json").write_text("external data")
    (out / "blocks" / "scene.json").unlink()
    (out / "blocks" / "geometry.bin").unlink()
    (out / "blocks").rmdir()
    os.symlink(external, out / "blocks")
    assert export_scene.main(["--out", str(out), "--scenes", "blocks"]) == 2
    assert "symbolic link" in capsys.readouterr().err
    assert (external / "scene.json").read_text() == "external data"
    assert sorted(p.name for p in external.iterdir()) == ["scene.json"]


def test_symlinked_out_dir_is_refused(tmp_path):
    real = export(tmp_path / "real", ["blocks"])
    link = tmp_path / "link"
    os.symlink(real, link)
    before = _tree(real)
    assert export_scene.main(["--out", str(link), "--scenes", "blocks"]) == 2
    assert _tree(real) == before


def test_unreadable_output_subdir_is_refused_and_external_file_unchanged(tmp_path, capsys):
    if os.geteuid() == 0:
        pytest.skip("root ignores directory read permissions")
    out = export(tmp_path / "out", ["blocks"])
    external = tmp_path / "external.bin"
    external.write_bytes(b"external data")
    unreadable = out / "notes"
    unreadable.mkdir()
    link = unreadable / "outside.bin"
    link.symlink_to(external)
    before = _tree(out)
    mode = unreadable.stat().st_mode
    try:
        unreadable.chmod(0o300)
        assert export_scene.main(["--out", str(out), "--scenes", "blocks"]) == 2
        assert "cannot scan" in capsys.readouterr().err
        assert external.read_bytes() == b"external data"
    finally:
        unreadable.chmod(mode)
    assert link.is_symlink()
    assert _tree(out) == before


def test_normal_reexport_still_works(tmp_path):
    out = export(tmp_path / "out", ["blocks"])
    first = _tree(out)
    assert export_scene.main(["--out", str(out), "--scenes", "blocks"]) == 0
    assert _tree(out) == first
    assert export_scene.main(["--out", str(out), "--scenes", "blocks", "--check"]) == 0
