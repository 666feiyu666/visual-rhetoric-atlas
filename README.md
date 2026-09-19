# Visual Rhetoric Atlas

Visual Rhetoric Atlas is a research corpus and reproducible data-mining pipeline for studying visual rhetoric and semiotics. The current case study collects and structures Alphonse Mucha works; the schema is intended to support later comparative work on Art Nouveau posters.

The corpus follows three stages:

- **Data:** preserve source images, metadata, rights information, hashes, and variants.
- **Information:** resolve artwork identities and encode comparable visible facts.
- **Knowledge:** mine evidence-linked patterns, interpretations, counterexamples, and candidate claims.

Streamlit, RAG, image generation, and research interfaces are treated as downstream corpus applications rather than parts of the corpus core.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## Current pipeline

```powershell
# Inspect the local Wikimedia collection
.\.venv\Scripts\python.exe -m visual_rhetoric_atlas.cli data report

# Build normalized assets, artwork objects, variant relations, and review queues
.\.venv\Scripts\python.exe -m visual_rhetoric_atlas.cli information build

# Run the reproducibility and schema tests
.\.venv\Scripts\python.exe -m pytest -q
```

Generated corpus material is stored under `corpus/` and excluded from Git. Source code and tests remain independent of the local corpus checkout.
