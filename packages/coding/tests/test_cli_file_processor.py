import pytest
from pathlib import Path
from coding.cli.file_processor import process_file, process_files

def test_process_file_text(tmp_path: Path):
    p = tmp_path / "test.txt"
    p.write_text("Hello, world!")
    
    result = process_file(p)
    assert result is not None
    assert result["type"] == "file_content"
    assert result["content"] == "Hello, world!"
    assert result["path"] == str(p.resolve())

def test_process_file_image(tmp_path: Path):
    p = tmp_path / "test.png"
    p.write_bytes(b"fake image data")
    
    result = process_file(p)
    assert result is not None
    assert result["type"] == "image"
    assert result["path"] == str(p.resolve())

def test_process_file_binary(tmp_path: Path):
    p = tmp_path / "test.zip"
    p.write_bytes(b"fake zip data")
    
    result = process_file(p)
    assert result is not None
    assert result["type"] == "file_reference"
    assert "Binary file" in result["note"]

def test_process_file_too_large(tmp_path: Path, monkeypatch):
    import coding.cli.file_processor as fp
    monkeypatch.setattr(fp, "MAX_FILE_SIZE", 10)
    
    p = tmp_path / "large.txt"
    p.write_text("This is definitely more than 10 bytes.")
    
    result = process_file(p)
    assert result is not None
    assert result["type"] == "file_reference"
    assert "File too large" in result["note"]
