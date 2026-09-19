# Mining Color Patterns in the Mucha Corpus

This reproducible exploratory experiment analyzes the 163 assets whose Bridges
catalogue filenames designate them as color plates. It asks which corpus-level
color families recur, which families co-occur, whether images form stable color
composition groups, and which images are color outliers.

The unit of analysis is the catalogue object. The frozen asset input retains
`asset_id`, `object_id`, catalogue identifiers, the repository-relative image
path, and SHA-256. Original images remain in the corpus and are not copied into
this experiment.

## Method

Pylette 6.0.0 extracts deterministic OKLab palettes at 8, 16, and 32 colors
after resizing each image to 256 x 256 pixels. The 32-color swatches seed a
weighted corpus vocabulary. Candidate vocabulary sizes of 12, 16, and 20 are
compared with a sampled silhouette score. Every scale is then projected onto
the selected shared vocabulary.

The experiment computes:

- weighted corpus color-family frequencies;
- pairwise co-occurrence at 0.5%, 1%, and 2% presence thresholds;
- support, conditional probability, Jaccard similarity, lift, and PMI;
- agglomerative image clusters selected from 3--10 clusters by silhouette;
- cluster-centroid outlier scores;
- cross-scale pair-pattern stability;
- series summaries when at least two images share a series code.

The experiment reports patterns in the current digital corpus. It does not
assign art-historical meaning automatically.

## Run

Set `PYLETTE_ROOT` only if the Pylette checkout is not next to this repository.
Run the script with the Python interpreter from the Pylette environment:

```powershell
$env:PYTHONUTF8 = "1"
& "D:\DH-Research\Pylette\.venv\Scripts\python.exe" experiments\color\run_experiment.py all
```

The stages can also be run independently:

```powershell
& "D:\DH-Research\Pylette\.venv\Scripts\python.exe" experiments\color\run_experiment.py prepare
& "D:\DH-Research\Pylette\.venv\Scripts\python.exe" experiments\color\run_experiment.py extract --max-workers 4
& "D:\DH-Research\Pylette\.venv\Scripts\python.exe" experiments\color\run_experiment.py mine
& "D:\DH-Research\Pylette\.venv\Scripts\python.exe" experiments\color\run_experiment.py validate
```

Existing raw extraction outputs are reused. Pass `--force` to `extract` or
`all` to repeat the expensive Pylette stage.

## Outputs

- `inputs/color_assets.jsonl`: frozen input selection;
- `outputs/raw/`: combined Pylette records for each scale and the A59 pilot;
- `outputs/normalized/`: long-form swatches, run records, and failures;
- `outputs/mining/`: vectors, color families, associations, clusters, outliers,
  patterns, and the machine-readable analysis summary;
- `outputs/figures/`: A59, corpus summaries, cluster contact sheets, and outliers;
- `outputs/validation_report.json`: completion and integrity checks.

`color_patterns.ipynb` is a concise reading and rerun interface. The script is
the canonical executable implementation, so notebook and command-line runs use
the same calculations.

