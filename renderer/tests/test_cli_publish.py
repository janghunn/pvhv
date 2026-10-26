"""Publication of `render --out` re-checks the target at publication time (CPU only)."""
import os

from pvhv.cli import _publish_render


def _tmp_render(parent, files):
    tmp = parent / ".tmp-render-test"
    tmp.mkdir()
    for name, data in files.items():
        (tmp / name).write_bytes(data)
    return tmp


def test_publish_into_new_directory(tmp_path):
    tmp = _tmp_render(tmp_path, {"layer_000.png": b"new0", "counts.json": b"{}"})
    out = tmp_path / "out"
    assert _publish_render(str(tmp), str(out), force=False) == 0
    assert sorted(os.listdir(out)) == ["counts.json", "layer_000.png"]
    assert not tmp.exists()


def test_publish_refuses_a_directory_filled_meanwhile(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    first = {"layer_000.png": b"first0", "layer_001.png": b"first1", "counts.json": b"first"}
    for name, data in first.items():
        (out / name).write_bytes(data)
    tmp = _tmp_render(tmp_path, {"layer_000.png": b"second0", "counts.json": b"second"})
    assert _publish_render(str(tmp), str(out), force=False) == 2
    assert {f: (out / f).read_bytes() for f in os.listdir(out)} == first
    assert not tmp.exists()


def test_publish_with_force_replaces_layers_and_counts(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    for name, data in {"layer_000.png": b"old0", "layer_001.png": b"old1", "counts.json": b"old",
                       "notes.txt": b"keep"}.items():
        (out / name).write_bytes(data)
    tmp = _tmp_render(tmp_path, {"layer_000.png": b"new0", "counts.json": b"new"})
    assert _publish_render(str(tmp), str(out), force=True) == 0
    assert {f: (out / f).read_bytes() for f in os.listdir(out)} == {
        "layer_000.png": b"new0", "counts.json": b"new", "notes.txt": b"keep"}
    assert not tmp.exists()
