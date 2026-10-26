# Figures

Qualitative figures rendered by the re-implementation in `renderer/` (headless OpenGL 4.3, NVIDIA GPU).
All figures regenerate deterministically from the committed code and scene configs; the fetched models are
verified by SHA-256 (`models/fetch_models.py`). Numbers drawn into panels (fractions of non-empty pixels or holes)
are measured by this re-implementation for the shown view; they are not results from the paper.

Regenerate all: `cd renderer && python -m pvhv figures --out ../figures`.
Check against the committed files: `cd renderer && python -m pvhv figures --check ../figures` (passes when, for every
figure, decoded RGB differs by more than 2/255 in at most 0.1 % of the pixels; byte identity is reported separately).
Scene cameras, E, near/far and |Ω| are in `renderer/pvhv/scenes/<scene>.json`. Model licences, including the note on
San Miguel's bundled licence text, are in [`../models/README.md`](../models/README.md).

Paper: Janghun Kim and Sungkil Lee. Potentially Visible Hidden-Volume Rendering for Multi-View Warping. ACM Transactions on Graphics 42(4) (Proc. SIGGRAPH 2023). https://doi.org/10.1145/3592108

### keep_cull.jpg

Rows San Miguel and Bistro. Columns: source view (h = 0); BDP hidden layer h = 1 with each fragment in its own colour if EDP keeps it (the same pixel, item and depth occur in some EDP layer) and tinted toward orange (60 %) if EDP culls it; EDP h = 1; UDP h = 1. Solid orange `#F2A65A` = empty pixel. Each title gives the fraction of non-empty pixels of the shown screen region (the central W×H of the guard-banded layer).

Panels of 480×270 px (downscaled from 960×540, Lanczos); JPEG quality 92, 4:4:4 chroma (the PNG exceeded 1 MiB).

Regenerate: `cd renderer && python -m pvhv figures --out ../figures --only keep_cull`

Scene(s): `sanmiguel`, `bistro`

Model attribution:

- `sanmiguel` (San Miguel 2.0 (low-polygon real-time version)), licence CC BY 3.0 (https://creativecommons.org/licenses/by/3.0/):
  San Miguel 2.0 © Guillermo M. Leal Llaguno (Evolución Visual); 2017 version by Morgan McGuire, Guedis Cardenas, Michael Mara and Nicholas Hull; from Morgan McGuire's Computer Graphics Archive (https://casual-effects.com/data); licensed CC BY 3.0 (https://creativecommons.org/licenses/by/3.0/)
- `bistro` (Amazon Lumberyard Bistro (exterior)), licence CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/):
  Amazon Lumberyard Bistro © 2017 Amazon Lumberyard; released in the NVIDIA ORCA collection (https://developer.nvidia.com/orca/amazon-lumberyard-bistro); via Morgan McGuire's Computer Graphics Archive; licensed CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)

Label: Re-implementation (2026), not the code used for the paper's experiments

Paper: Janghun Kim and Sungkil Lee. Potentially Visible Hidden-Volume Rendering for Multi-View Warping. ACM Transactions on Graphics 42(4) (Proc. SIGGRAPH 2023). https://doi.org/10.1145/3592108

### novel_view.jpg

Rows San Miguel and Bistro, one novel view on the bound |t| = E (of the 8 verify boundary views, the one whose single-layer warp has the most holes). Columns: direct render of the novel view (reference); single-layer warp (layer 0 only); full BDP warp; EDP warp (paper configuration: EDP on hidden layers 1–2, UDP below, all layers). Magenta `#FF00FF` = hole (no warped fragment where the direct render has geometry); pixels empty in both show the background colour. The white box marks the region shown enlarged in the inset (the window with the most single-layer holes; same crop in all columns). Titles give the hole fraction.

Panels of 480×270 px (downscaled from 960×540, Lanczos), inset 240×135 px; JPEG quality 92, 4:4:4 chroma (the PNG exceeded 1 MiB).

Regenerate: `cd renderer && python -m pvhv figures --out ../figures --only novel_view`

Scene(s): `sanmiguel`, `bistro`

Model attribution:

- `sanmiguel` (San Miguel 2.0 (low-polygon real-time version)), licence CC BY 3.0 (https://creativecommons.org/licenses/by/3.0/):
  San Miguel 2.0 © Guillermo M. Leal Llaguno (Evolución Visual); 2017 version by Morgan McGuire, Guedis Cardenas, Michael Mara and Nicholas Hull; from Morgan McGuire's Computer Graphics Archive (https://casual-effects.com/data); licensed CC BY 3.0 (https://creativecommons.org/licenses/by/3.0/)
- `bistro` (Amazon Lumberyard Bistro (exterior)), licence CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/):
  Amazon Lumberyard Bistro © 2017 Amazon Lumberyard; released in the NVIDIA ORCA collection (https://developer.nvidia.com/orca/amazon-lumberyard-bistro); via Morgan McGuire's Computer Graphics Archive; licensed CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)

Label: Re-implementation (2026), not the code used for the paper's experiments

Paper: Janghun Kim and Sungkil Lee. Potentially Visible Hidden-Volume Rendering for Multi-View Warping. ACM Transactions on Graphics 42(4) (Proc. SIGGRAPH 2023). https://doi.org/10.1145/3592108

### layers.png

Chess set: layers h = 0..3 of BDP (top row) and EDP (bottom row), showing how culling in the first hidden layers propagates to deeper layers (EDP uses the PVHV test on h = 1, 2 and the UDP rule on h = 3). Orange `#F2A65A` = empty pixel. Titles give the fraction of non-empty pixels.

Panels of 480×270 px (downscaled from 960×540, Lanczos); PNG.

Regenerate: `cd renderer && python -m pvhv figures --out ../figures --only layers`

Scene(s): `chess`

Model attribution:

- `chess` (Chess Set (Poly Haven, 1k glTF)), licence CC0 1.0 (https://creativecommons.org/publicdomain/zero/1.0/):
  Chess Set by Riley Queen, Poly Haven (https://polyhaven.com/a/chess_set), CC0 1.0 (https://creativecommons.org/publicdomain/zero/1.0/)

Label: Re-implementation (2026), not the code used for the paper's experiments

Paper: Janghun Kim and Sungkil Lee. Potentially Visible Hidden-Volume Rendering for Multi-View Warping. ACM Transactions on Graphics 42(4) (Proc. SIGGRAPH 2023). https://doi.org/10.1145/3592108
