# Scene models

The renderer's textured test scenes are third-party models. Two of them, San Miguel and Bistro, are scenes that
QUASAR (Lu & Rowe, ACM TOG 44(4), SIGGRAPH 2025) also evaluates on. The third is a CC0 chess set from Poly
Haven. Each is downloaded from its original publisher. **The original model downloads are not committed to this
repository** because of their size. Apart from rendered images, the only committed model data is the demo's re-exported CC0 chess
geometry and its three unchanged diffuse JPEGs in `docs/assets/demo/chess/` (see
[`../docs/assets/demo/ATTRIBUTION.md`](../docs/assets/demo/ATTRIBUTION.md)).
`fetch_models.py` downloads the original files into `models/cache/`, which is git-ignored,
and checks the byte size and SHA-256 of every downloaded file against `manifest.json`.

| scene id | model | QUASAR reference | copyright | licence | source |
|---|---|---|---|---|---|
| `sanmiguel` | San Miguel 2.0, low-polygon real-time version | San Miguel (QUASAR paper Sec. 4.1, Fig. 11) | © Guillermo M. Leal Llaguno (Evolución Visual) | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) (see the licence note) | [McGuire Computer Graphics Archive](https://casual-effects.com/data/) |
| `bistro` | Amazon Lumberyard Bistro, exterior | Bistro (exterior) (QUASAR repository asset credits) | © 2017 Amazon Lumberyard | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | [NVIDIA ORCA](https://developer.nvidia.com/orca/amazon-lumberyard-bistro), via the [McGuire Computer Graphics Archive](https://casual-effects.com/data/) |
| `chess` | Chess Set by Riley Queen (1k glTF) | none: a CC0 model, added alongside the QUASAR scenes | none (public domain dedication) | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) ([Poly Haven licence](https://polyhaven.com/license)) | [Poly Haven](https://polyhaven.com/a/chess_set) |

## Attribution

Images rendered from these scenes carry these lines (copied verbatim from `manifest.json`). CC0 does not
require attribution; the chess line is given as a courtesy.

- San Miguel 2.0 © Guillermo M. Leal Llaguno (Evolución Visual); 2017 version by Morgan McGuire, Guedis Cardenas, Michael Mara and Nicholas Hull; from Morgan McGuire's Computer Graphics Archive (https://casual-effects.com/data); licensed CC BY 3.0 (https://creativecommons.org/licenses/by/3.0/)
- Amazon Lumberyard Bistro © 2017 Amazon Lumberyard; released in the NVIDIA ORCA collection (https://developer.nvidia.com/orca/amazon-lumberyard-bistro); via Morgan McGuire's Computer Graphics Archive; licensed CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)
- Chess Set by Riley Queen, Poly Haven (https://polyhaven.com/a/chess_set), CC0 1.0 (https://creativecommons.org/publicdomain/zero/1.0/)

## Licence note

San Miguel and Bistro are not redistributed here. The CC0 chess set is redistributed only as the demo data named above.

- **San Miguel.** The McGuire archive lists CC BY 3.0 for San Miguel. The `license.txt` bundled in
  `San_Miguel.zip` (labelled "San Miguel 2.1") states: "This model is free for research and educational use with
  attribution." The renders in this repository are made for non-commercial research and educational purposes and
  carry the attribution above.
- **Bistro.** The McGuire archive lists CC BY 4.0, which permits sharing and adapting the material provided
  attribution is given.
- **Chess set.** Poly Haven publishes it under CC0 1.0 (https://polyhaven.com/license), a public domain
  dedication with no attribution requirement.

No model from the scenes of the PVHV paper (Janghun Kim and Sungkil Lee, ACM TOG 42(4), SIGGRAPH 2023) is used, including the elephant.

## Fetching

Use Python 3 (standard library only). From the repository root:

```
python3 models/fetch_models.py            # download, verify and extract every model
python3 models/fetch_models.py --only bistro
python3 models/fetch_models.py --check    # verify what is on disk; never downloads
```

The script exits with status 1 on any size or SHA-256 mismatch. Running it again skips work that is already
verified. Each model is processed under an exclusive lock (`models/cache/<id>.lock`):

- Every file is downloaded to a temporary file and renamed into place only after its size and SHA-256 match.
- Archives (San Miguel, Bistro) are extracted into a staging directory, which is checked and then renamed into
  place. The chess set is downloaded file by file, with no archive.
- A `.complete.json` marker is written last.

A marker counts as valid only if its SHA-256 values equal the manifest's and the files on disk still match it.
Otherwise the extracted tree (or, for the chess set, each mismatching file) is rebuilt. `manifest.json` is the only authority, and the script never rewrites it.

Layout after fetching (paths relative to `models/cache/`):

| path | content |
|---|---|
| `downloads/*.zip` | the five verified archives (1.95 GB) |
| `sanmiguel/San_Miguel/san-miguel-low-poly.obj` (+ `.mtl`, `textures/`) | San Miguel, low-polygon version (the zip also holds the full-detail `san-miguel.obj`, which is not used) |
| `bistro/Exterior/exterior.obj` (+ `.mtl`) | Bistro exterior; its MTL references textures as `..\BuildingTextures\`, `..\OtherTextures\`, `..\PropTextures\` |
| `bistro/BuildingTextures/`, `bistro/OtherTextures/`, `bistro/PropTextures/` | Bistro textures (PNG) |
| `chess/chess_set_1k.gltf` (+ `chess_set.bin`, `textures/*_1k.jpg`) | Chess set, glTF 2.0 with 1k JPEG textures (7.0 MB) |

Disk use: 1.95 GB of archives plus 3.58 GB extracted, plus 7 MB for the chess set, about 5.5 GB in total. After a successful fetch you can delete
`models/cache/downloads/`; `--check` then verifies the extracted trees from their markers. Do not delete files under `chess/`: they are
the model itself, not an archive.
