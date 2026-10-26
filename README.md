# Potentially Visible Hidden-Volume Rendering for Multi-View Warping

Effective depth peeling (EDP) with potentially visible hidden volumes (PVHVs), an offline GPU renderer, figures, and an interactive WebGL2 demo.

[Live project page](https://janghunn.github.io/pvhv/)

## Provenance

This repository is an independent clean-room re-implementation written in 2026 from the published paper text. Code published by the Computer Graphics Lab at Sungkyunkwan University was not consulted. It is not the code used for the paper's experiments, and its figures and numbers are not the paper's results.

Applications of the method, such as depth-of-field rendering, are described in the [paper](https://doi.org/10.1145/3592108) and are not implemented in this re-implementation.

The paper is by Janghun Kim and Sungkil Lee (Sungkyunkwan University, Suwon, South Korea), ACM Transactions on Graphics 42(4), Proc. SIGGRAPH 2023. The [paper page](https://cg.skku.edu/pub/2023-kim-siggraph-pvhv), with the camera-ready PDF, slides and video, is hosted by the Computer Graphics Lab at Sungkyunkwan University.

## Layout

| Directory | Contents |
| --- | --- |
| [renderer/](renderer/README.md) | Headless EGL renderer, PVHV/EDP, depth-peeling baselines, warping and verification |
| [models/](models/README.md) | Model sources, licences and verified downloads into the ignored cache |
| [figures/](figures/README.md) | Re-implementation results and their model credits |
| [blender/](blender/README.md) | Cycles source-view render and hidden-layer teaser composition |
| [demo/](demo/README.md) | WebGL2 scene exporter, harness and cross-checks |
| [docs/](docs/index.html) | Static project page and shipped demo assets |

## Quick start

The renderer requires Python 3.11 and an NVIDIA driver providing EGL with OpenGL 4.3. Create its environment from `renderer/`, as described in the [renderer setup](renderer/README.md):

```sh
cd renderer
conda env create -f environment.yml
conda activate pvhv
cd ..
```

From the repository root, download and verify the textured models (about 5.5 GB including archives). The procedural blocks scene needs no download:

```sh
python3 models/fetch_models.py
python3 models/fetch_models.py --check
```

Verify the sampled novel views and regenerate or check the three renderer figures:

```sh
cd renderer
python -m pvhv info
python -m pvhv verify --scenes blocks,sanmiguel,bistro,chess --views 16
python -m pvhv figures --out ../figures
python -m pvhv figures --check ../figures
cd ..
```

The teaser also requires Blender 5.2.2 and an NVIDIA GPU with OptiX or CUDA. See the [Blender requirements and settings](blender/README.md). From the repository root:

```sh
blender/install_blender.sh
conda activate pvhv && blender/make_teaser.sh
conda activate pvhv && blender/make_teaser.sh --check
```

The page already contains the exported demo data. To regenerate and byte-check it with the renderer environment active, run from the repository root:

```sh
PVHV_MODELS_CACHE="$PWD/models/cache" python demo/export_scene.py --out docs/assets/demo
PVHV_MODELS_CACHE="$PWD/models/cache" python demo/export_scene.py --check
```

Preview the integrated page locally, then open `http://localhost:8000/`:

```sh
python3 -m http.server -d docs
```

Drag the demo view, or Tab to its canvas and use the arrow keys. WebGL2, floating-point render targets and four draw buffers are required; otherwise an accessible notice is shown. See [demo controls and checks](demo/README.md).

## Model licences and attribution

| Model | Licence | Attribution |
| --- | --- | --- |
| San Miguel 2.0, low-polygon real-time version | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/); see bundled-licence note below | San Miguel 2.0 © Guillermo M. Leal Llaguno (Evolución Visual); 2017 version by Morgan McGuire, Guedis Cardenas, Michael Mara and Nicholas Hull; from Morgan McGuire's Computer Graphics Archive (https://casual-effects.com/data); licensed CC BY 3.0 (https://creativecommons.org/licenses/by/3.0/) |
| Amazon Lumberyard Bistro, exterior | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | Amazon Lumberyard Bistro © 2017 Amazon Lumberyard; released in the NVIDIA ORCA collection (https://developer.nvidia.com/orca/amazon-lumberyard-bistro); via Morgan McGuire's Computer Graphics Archive; licensed CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/) |
| Chess Set by Riley Queen, Poly Haven (1k glTF) | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) | Chess Set by Riley Queen, Poly Haven (https://polyhaven.com/a/chess_set), CC0 1.0 (https://creativecommons.org/publicdomain/zero/1.0/) |

The McGuire archive lists CC BY 3.0 for San Miguel, while the `license.txt` bundled in `San_Miguel.zip` (labelled “San Miguel 2.1”) states: “This model is free for research and educational use with attribution.” These renders are for non-commercial research and educational purposes and carry that attribution. See [model licence notes](models/README.md).

The full model downloads remain in the ignored cache. The demo redistributes re-exported CC0 chess geometry and three unchanged diffuse JPEGs, with [asset credits](docs/assets/demo/ATTRIBUTION.md). The blocks scene is procedural code under this repository's MIT licence.

## Known limitations

1. In `renderer/pvhv/cli.py`, two `render` runs started at the same moment into the same empty `--out` directory can race. This is an accepted limitation; the outputs are regenerable.
2. The chess glTF batch order in `renderer/pvhv/scene.py` depends on `PYTHONHASHSEED`. Set `PYTHONHASHSEED=0` for reproducible renderer ordering. The demo exporter sorts its batches itself.
3. `CYCLES_DEVICE_INDEX` and `PVHV_EGL_DEVICE` may map to different physical GPUs. This affects speed only; see the [GPU mapping notes](blender/README.md).
4. The re-implementation's EDP is not equivalent to full depth peeling. The [renderer verification gates](renderer/README.md) measure losses on sampled views: pixels a BDP warp gets right and an EDP warp does not. Those measured losses are not a general error bound or the paper's results.

## Citation

```bibtex
@article{kim23:pvhv,
  title={{Potentially Visible Hidden-Volume Rendering for Multi-View Warping}},
  author={Janghun Kim and Sungkil Lee},
  journal={{ACM Trans. Graphics (Proc. SIGGRAPH)}},
  volume={42},
  number={4},
  pages={86:1--11},
  year={2023}
}
```

## Licence

MIT, © 2026 Janghun Kim; see [LICENSE](LICENSE). Models retain their own licences as listed above. The bundled Lato fonts use the [SIL Open Font License 1.1](docs/assets/fonts/OFL-Lato.txt).
