import json
from pathlib import Path

def definitions():
    return json.loads(Path(__file__).with_name("metadata.json").read_text(encoding="utf-8"))["records"]
