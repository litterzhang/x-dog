
import re
from pathlib import Path

class LongTermMemory:
    def __init__(self, workspace_dir: Path):
        self.file_path = workspace_dir / "MEMORY.md"

    def read(self) -> str:
        if not self.file_path.exists():
            return ""
        with open(self.file_path, "r") as f:
            return f.read()

    def write(self, content: str) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.file_path, "w") as f:
            f.write(content)

    def append(self, text: str) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.file_path, "a") as f:
            f.write(f"\n{text}")

    def update_section(self, section_name: str, new_content: str) -> None:
        content = self.read()
        if not content:
            self.write(f"# {section_name}\n{new_content}\n")
            return

        pattern = re.compile(rf"(^#\s+{re.escape(section_name)}\s*\n)(.*?)(?=(^#\s+|\Z))", re.MULTILINE | re.DOTALL)
        if pattern.search(content):
            new_text = pattern.sub(rf"\1{new_content}\n", content)
            self.write(new_text)
        else:
            self.append(f"# {section_name}\n{new_content}\n")
