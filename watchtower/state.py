from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class StateStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def path_for(self, source_id: str) -> Path:
        safe = "".join(c for c in source_id if c.isalnum() or c in "-_")
        if safe != source_id:
            raise ValueError("unsafe source id")
        return self.root / f"{safe}.json"

    def load(self, source_id: str) -> dict[str, Any] | None:
        path = self.path_for(source_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"invalid state for {source_id}")
        return data

    def save(self, source_id: str, state: dict[str, Any]) -> None:
        path = self.path_for(source_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = (json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            tmp = Path(handle.name)
        os.replace(tmp, path)
