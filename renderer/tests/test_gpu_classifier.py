"""GPU inPVHV classifier vs the numpy Algorithm 1 reference (plan test 11)."""
import numpy as np
import pytest

pytestmark = pytest.mark.gpu


@pytest.mark.parametrize("search", ["sampled", "exhaustive"])
def test_gpu_classifier_matches_cpu_algorithm1(blocks, blocks_renderer, search):
    from pvhv.pvhv_cpu import algorithm1

    cam = blocks.camera
    c = blocks_renderer.classify_layer1(search=search)
    mask = c["mask"]
    n = int(np.count_nonzero(mask))
    assert n > 10000, "blocks layer 1 must classify a meaningful number of fragments"
    cpu = algorithm1(c["prev_depth"], c["prev_item"], c["depth"], mask, blocks.E, cam.F, cam.near, cam.far,
                     delta=0.002, margin=1.5, n_samples=blocks.samples, search=search)
    gpu = c["in_pvhv"] & mask
    mismatch = int(np.count_nonzero((cpu != gpu) & mask))
    kept = int(np.count_nonzero(gpu))
    print(f"\n{search}: {n} classified, GPU keeps {kept}, CPU keeps {int(np.count_nonzero(cpu & mask))}, "
          f"mismatch {mismatch} ({mismatch / n:.2e})")
    assert 0 < kept < n, "classification must be non-trivial"
    assert mismatch <= 1e-4 * n
