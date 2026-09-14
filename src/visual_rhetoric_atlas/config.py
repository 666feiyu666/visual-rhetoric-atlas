"""Local settings; credentials never enter research records."""
import os
from pathlib import Path
from dotenv import load_dotenv


def settings(project_root):
    root = Path(project_root).resolve()
    load_dotenv(root / ".env.local", override=False)
    load_dotenv(root / ".env", override=False)
    configured = os.getenv("ATLAS_DATA_DIR", "").strip()
    data = Path(configured) if configured else root / "data"
    if not data.is_absolute():
        data = root / data
    return {
        "data_dir": data.resolve(),
        "model": os.getenv("ATLAS_READING_MODEL", "").strip(),
        "api_key_available": bool(os.getenv("OPENAI_API_KEY", "").strip()),
    }
