"""Read CSV/JSON bytes into (row_number, record) pairs. File-level problems raise FileRejected."""

import csv
import io
import json
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

from finrecon_ai.db.enums import IngestionMethod
from finrecon_ai.ingestion.mappings import SourceMapping

MAX_FILE_BYTES = 50 * 1024 * 1024

type RawRecord = dict[str, Any]


class FileRejected(ValueError):
    pass


def parse_file(content: bytes, file_format: IngestionMethod, mapping: SourceMapping) -> Iterator[tuple[int, RawRecord]]:
    """Row numbers are 1-based data rows (the CSV header is not counted)."""
    if len(content) > MAX_FILE_BYTES:
        raise FileRejected(f"file exceeds {MAX_FILE_BYTES // (1024 * 1024)} MB")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise FileRejected("file is not valid UTF-8") from None
    if "\x00" in text:
        raise FileRejected("file contains NUL characters; it is not a text file")

    match file_format:
        case IngestionMethod.CSV:
            return _parse_csv(text, mapping)
        case IngestionMethod.JSON:
            return _parse_json(text, mapping)
        case _:
            raise FileRejected(f"unsupported file format: {file_format}")


def _parse_csv(text: str, mapping: SourceMapping) -> Iterator[tuple[int, RawRecord]]:
    reader = csv.DictReader(io.StringIO(text, newline=""))
    try:
        headers = set(reader.fieldnames or [])
        missing = mapping.required_source_columns() - headers
        if missing:
            raise FileRejected(f"missing required columns: {sorted(missing)}")
        rows = list(reader)  # read fully so a malformed CSV fails before any row is accepted
    except csv.Error as exc:  # e.g. a field above the CSV module's size limit
        raise FileRejected(f"malformed CSV: {exc}") from None
    return ((i, _strip_extra(row)) for i, row in enumerate(rows, start=1))


def _strip_extra(row: dict[str | None, Any]) -> RawRecord:
    # DictReader puts surplus cells under the None key.
    extra = row.pop(None, None)
    record: RawRecord = {k: v for k, v in row.items() if k is not None}
    if extra:
        record["_extra_cells"] = extra
    return record


def _parse_json(text: str, mapping: SourceMapping) -> Iterator[tuple[int, RawRecord]]:
    try:
        # Decimal keeps amounts exact; float would silently round them.
        data = json.loads(text, parse_float=Decimal)
    except json.JSONDecodeError as exc:
        raise FileRejected(f"invalid JSON: {exc}") from None
    except RecursionError:  # hostile nesting depth
        raise FileRejected("JSON is nested too deeply") from None

    if mapping.json_records_key is not None and isinstance(data, dict):
        data = data.get(mapping.json_records_key)
    if not isinstance(data, list):
        expected = f'an object with a "{mapping.json_records_key}" list' if mapping.json_records_key else "a list"
        raise FileRejected(f"expected {expected} of records")

    return ((i, item if isinstance(item, dict) else {"_value": item}) for i, item in enumerate(data, start=1))


def contains_nul(value: Any) -> bool:
    """PostgreSQL text and JSONB cannot store NUL; JSON can still smuggle one in as \\u0000."""
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, dict):
        return any(contains_nul(k) or contains_nul(v) for k, v in value.items())
    if isinstance(value, list):
        return any(contains_nul(v) for v in value)
    return False


def to_json_safe(record: RawRecord) -> RawRecord:
    """Raw payloads go to JSONB; keep Decimals as strings so no precision is lost."""
    return json.loads(json.dumps(record, default=str))
