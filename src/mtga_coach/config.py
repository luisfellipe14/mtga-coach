import os
from pathlib import Path


def default_data_dir():
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share")) / "mtga-coach"


def default_log_path():
    return Path.home() / "AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log"


def default_card_database():
    candidates = [
        Path("C:/Program Files (x86)/Steam/steamapps/common/MTGA/MTGA_Data/Downloads/Raw"),
        Path("C:/Program Files/Wizards of the Coast/MTGA/MTGA_Data/Downloads/Raw"),
    ]
    paths = [p for directory in candidates for p in directory.glob("Raw_CardDatabase_*.mtga")]
    return max(paths, key=lambda p: p.stat().st_mtime) if paths else None
