from pathlib import Path

# --- ROOT ---
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# --- DATA ---
DATA_DIR = PROJECT_ROOT / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
PROCESSED_DATA_DIR = DATA_DIR / "processed"

# --- FIGURES ---
FIGURES_DIR = PROJECT_ROOT / "figures"

# --- LOGS ---
LOGS_DIR = PROJECT_ROOT / "logs"

# --- MODELS ---
MODELS_DIR = PROJECT_ROOT / "models"

# --- NOTEBOOKS ---
NOTEBOOKS_DIR = PROJECT_ROOT / "notebooks"

# --- SRC ---
SRC_DIR = PROJECT_ROOT / "src"

# --- WEB ---
WEB_DIR = PROJECT_ROOT / "web"

# --- GENERAL SETTINGS ---
RANDOM_SEED = 42