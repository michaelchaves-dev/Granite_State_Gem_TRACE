"""Restricted run storage (spec §13). Local, 0600 files, outside any public repo.

This is a development store. Production needs encrypted, access-logged storage
with retention and deletion controls (see docs/CURSOR_BUILD_SLICES.md, Slice 5).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .engine import DiscoveryResult
from .privacy import ensure_private_dir, write_private_file
from .receipts import ReceiptBook


class RunStore:
    def __init__(self, data_dir: str | Path):
        self.root = ensure_private_dir(Path(data_dir) / "runs")

    def save(self, res: DiscoveryResult, book: ReceiptBook) -> Path:
        if not re.fullmatch(r"[0-9a-f-]{36}", res.run_id):
            raise ValueError("unexpected run id format")
        payload = {"run": res.to_dict(), "receipts": [r.to_dict() for r in book.receipts]}
        return write_private_file(self.root / f"{res.run_id}.json", json.dumps(payload, indent=2, default=str))

    def load(self, run_id: str) -> dict:
        if not re.fullmatch(r"[0-9a-f-]{36}", run_id):
            raise ValueError("unexpected run id format")
        return json.loads((self.root / f"{run_id}.json").read_text(encoding="utf-8"))

    def delete(self, run_id: str) -> bool:
        if not re.fullmatch(r"[0-9a-f-]{36}", run_id):
            raise ValueError("unexpected run id format")
        p = self.root / f"{run_id}.json"
        if p.exists():
            p.unlink()
            return True
        return False
