
import datetime
from pathlib import Path

class DailyLog:
    def __init__(self, memory_dir: Path):
        self.memory_dir = memory_dir
        self.memory_dir.mkdir(parents=True, exist_ok=True)

    def append(self, text: str, *, date_str: str = None) -> None:
        if date_str is None:
            date_str = datetime.date.today().isoformat()
        log_file = self.memory_dir / f"{date_str}.md"
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        with open(log_file, "a") as f:
            f.write(f"[{timestamp}] {text}\n")

    def read_today(self) -> str:
        date_str = datetime.date.today().isoformat()
        return self.read_date(date_str)

    def read_date(self, date_str: str) -> str:
        log_file = self.memory_dir / f"{date_str}.md"
        if not log_file.exists():
            return ""
        with open(log_file, "r") as f:
            return f.read()

    def list_dates(self) -> list[str]:
        if not self.memory_dir.exists():
            return []
        files = list(self.memory_dir.glob("*.md"))
        dates = [f.stem for f in files if f.stem.count("-") == 2]
        return sorted(dates)
