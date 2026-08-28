from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path

from .models import ParseResult


class ProgressManager:
    VERSION = 1

    def __init__(
        self,
        queries_file: Path,
    ) -> None:
        self.queries_file = queries_file.resolve()

        self.file_hash = self._calculate_file_hash()

        digest = hashlib.sha256(str(self.queries_file).encode("utf-8")).hexdigest()[:16]

        self.progress_file = self.queries_file.parent / (
            f".wordstat_progress_{digest}.json"
        )

    def _calculate_file_hash(self) -> str:
        sha256 = hashlib.sha256()

        with self.queries_file.open("rb") as file:
            while chunk := file.read(1024 * 1024):
                sha256.update(chunk)

        return sha256.hexdigest()

    def exists(self) -> bool:
        return self.progress_file.exists()

    def save(
        self,
        next_index: int,
        total: int,
        results: list[ParseResult],
    ) -> None:
        data = {
            "version": self.VERSION,
            "queries_file": str(self.queries_file),
            "queries_hash": self.file_hash,
            "next_index": next_index,
            "total": total,
            "results": [asdict(result) for result in results],
        }

        temporary_file = self.progress_file.with_suffix(".json.tmp")

        temporary_file.write_text(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        os.replace(
            temporary_file,
            self.progress_file,
        )

    def load(
        self,
        total: int,
    ) -> tuple[
        int,
        list[ParseResult],
    ]:
        if not self.progress_file.exists():
            return 0, []

        try:
            data = json.loads(self.progress_file.read_text(encoding="utf-8"))

        except (
            OSError,
            json.JSONDecodeError,
        ):
            return 0, []

        if data.get("version") != self.VERSION:
            return 0, []

        if data.get("queries_hash") != self.file_hash:
            return 0, []

        if data.get("total") != total:
            return 0, []

        next_index = data.get(
            "next_index",
            0,
        )

        if not isinstance(
            next_index,
            int,
        ):
            return 0, []

        next_index = max(
            0,
            min(
                next_index,
                total,
            ),
        )

        results: list[ParseResult] = []

        raw_results = data.get(
            "results",
            [],
        )

        if isinstance(
            raw_results,
            list,
        ):
            for item in raw_results:
                if not isinstance(
                    item,
                    dict,
                ):
                    continue

                try:
                    results.append(
                        ParseResult(
                            query=str(item["query"]),
                            normal_count=int(item["normal_count"]),
                            quoted_count=int(item["quoted_count"]),
                        )
                    )

                except (
                    KeyError,
                    TypeError,
                    ValueError,
                ):
                    continue

        return (
            next_index,
            results,
        )

    def remove(self) -> None:
        try:
            self.progress_file.unlink(missing_ok=True)

        except OSError:
            pass
