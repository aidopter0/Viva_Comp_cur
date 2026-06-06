from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASKET_CSV_PATH = ROOT / "Top 50_ Price_Basket_Items.csv"
DATA_DIR = ROOT / "data"
EXPORTS_DIR = ROOT / "exports"
RUNS_EXPORT_DIR = EXPORTS_DIR / "runs"
MAX_RUN_EXPORT_RETENTION = 5
CATALOGS_DIR = ROOT / "catalogs"
CONFIG_DIR = ROOT / "config"
AIUSE_DIR = ROOT / "aiuse"
OPENAI_USAGE_XLSX = AIUSE_DIR / "openai_usage.xlsx"
OPENAI_DEFAULT_MODEL = "gpt-5.4-mini"
DB_PATH = DATA_DIR / "viva_tracker.db"
