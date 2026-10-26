"""glTF loader reports base-colour textures that cannot be decoded (CPU only)."""
import os
import shutil

import pytest

from pvhv.scene import REPO_ROOT, load_scene

CHESS = os.path.join(REPO_ROOT, "models", "cache", "chess")


@pytest.fixture
def chess_copy(tmp_path, monkeypatch):
    if not os.path.isfile(os.path.join(CHESS, "chess_set_1k.gltf")):
        pytest.skip("models/cache/chess is absent (run models/fetch_models.py --only chess)")
    cache = tmp_path / "cache"
    shutil.copytree(CHESS, cache / "chess")
    monkeypatch.setenv("PVHV_MODELS_CACHE", str(cache))
    return cache / "chess"


def test_intact_chess_has_no_unresolved_textures(chess_copy):
    scene = load_scene("chess")
    assert scene.unresolved_textures == []
    assert len(scene.textures) == 3


def test_undecodable_gltf_texture_is_reported(chess_copy):
    (chess_copy / "textures" / "chess_set_board_diff_1k.jpg").write_bytes(b"not a jpeg" * 100)
    scene = load_scene("chess")
    assert scene.unresolved_textures == ["textures/chess_set_board_diff_1k.jpg"]
