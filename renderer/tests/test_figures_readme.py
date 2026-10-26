"""figures/README.md documents every committed figure (plan test 7b; CPU only)."""
import json
import os
import re

from pvhv.figures import CITATION, FIGURE_SCENES, LABEL

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIG_DIR = os.path.join(ROOT, "figures")
MAX_BYTES = 1_048_576


def _sections():
    with open(os.path.join(FIG_DIR, "README.md"), encoding="utf-8") as fh:
        text = fh.read()
    parts = re.split(r"^### (.+)$", text, flags=re.M)
    return {parts[i].strip(): parts[i + 1] for i in range(1, len(parts), 2)}


def _images():
    return sorted(f for f in os.listdir(FIG_DIR) if f.lower().endswith((".png", ".jpg", ".jpeg")))


def _attributions():
    with open(os.path.join(ROOT, "models", "manifest.json"), encoding="utf-8") as fh:
        return {m["id"]: m["attribution"] for m in json.load(fh)["models"]}


def test_required_figures_exist():
    stems = {os.path.splitext(f)[0] for f in _images()}
    assert {"keep_cull", "novel_view", "layers"} <= stems
    assert "teaser" not in FIGURE_SCENES and not any(f.startswith("teaser.") for f in os.listdir(FIG_DIR))


def test_every_image_has_a_complete_section():
    sections, attributions = _sections(), _attributions()
    for f in _images():
        assert f in sections, f"figures/README.md has no section '### {f}'"
        body = sections[f]
        stem = os.path.splitext(f)[0]
        assert LABEL in body, f"{f}: label line missing"
        assert CITATION in body, f"{f}: paper citation line missing"
        assert f"python -m pvhv figures --out ../figures --only {stem}" in body, f"{f}: regeneration command missing"
        assert stem in FIGURE_SCENES, f"{f}: not a figure produced by pvhv.figures"
        for scene in FIGURE_SCENES[stem]:
            assert f"`{scene}`" in body, f"{f}: scene {scene} not named"
            assert attributions[scene] in body, f"{f}: attribution of {scene} missing"


def test_every_section_refers_to_an_existing_file():
    images = set(_images())
    for name in _sections():
        assert name in images, f"section '### {name}' has no file in figures/"


def test_image_sizes():
    for f in _images():
        size = os.path.getsize(os.path.join(FIG_DIR, f))
        assert size <= MAX_BYTES, f"{f} is {size} bytes"
