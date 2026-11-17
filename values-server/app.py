import argparse
import hashlib
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import jsonpatch
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, Field, field_validator
from pythonjsonlogger.json import JsonFormatter


def setup_logging(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s"
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


log = setup_logging("values-server")

app = FastAPI(title="Values Service", version="1.0.0")
Instrumentator().instrument(app).expose(app)

values_dir: Path = Path("/data/values")


@app.get("/health")
async def health():
    return {"status": "healthy"}


def values_path(app_name: str) -> Path:
    if not app_name or not app_name.isalnum() or len(app_name) > 64:
        raise HTTPException(404, f"Invalid application name: {app_name[:64]}")

    values_file = values_dir / f"{app_name}.value.json"

    if not values_file.exists():
        log.warning("Values not found", extra={"app": app_name})
        raise HTTPException(404, f"Values not found: {app_name}")

    return values_file


def read_values(app_name: str) -> dict:
    try:
        return json.loads(values_path(app_name).read_text())
    except json.JSONDecodeError as exc:
        log.error("Invalid values JSON", extra={"app": app_name})
        raise HTTPException(500, "Values file is corrupted") from exc


def write_atomic(path: Path, data: dict):
    # write-then-rename so a crash never leaves a half-written config behind
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def history_file(app_name: str) -> Path:
    return values_dir / ".history" / f"{app_name}.jsonl"


def read_history(app_name: str) -> list[dict]:
    path = history_file(app_name)
    if not path.exists():
        return []
    entries = []
    for line in path.read_text().splitlines():
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            # a crash mid-append can leave a partial line; skip it rather than lock the app out
            log.warning("Skipping corrupted history line", extra={"app": app_name})
    return entries


def summary(entry: dict) -> dict:
    return {k: v for k, v in entry.items() if k != "before"}


def save(app_name: str, after: dict, author: str, message: str, reverts: int | None = None) -> dict:
    before = read_values(app_name)
    patch = jsonpatch.make_patch(before, after).patch
    # an undo is always recorded, even when it changes nothing, so the next undo moves further back
    if not patch and reverts is None:
        return {"id": None, "patch": []}

    entries = read_history(app_name)
    entry = {
        "id": max((e["id"] for e in entries), default=0) + 1,
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        "author": author,
        "message": message,
        "reverts": reverts,
        "patch": patch,
        "before": before,
    }
    path = history_file(app_name)
    path.parent.mkdir(exist_ok=True)
    size = path.stat().st_size if path.exists() else 0
    with path.open("ab+") as f:
        # start on a fresh line if a previous append was cut off
        if size and (f.seek(-1, os.SEEK_END), f.read(1))[1] != b"\n":
            f.write(b"\n")
        f.write(json.dumps(entry).encode() + b"\n")
    try:
        write_atomic(values_path(app_name), after)
    except Exception:
        # keep the audit log honest: drop the entry for a write that never happened
        with path.open("r+b") as f:
            f.truncate(size)
        raise

    log.info("Values updated", extra={"app": app_name, "revision": entry["id"], "author": author})
    return summary(entry)


class ValuesUpdate(BaseModel):
    values: dict
    author: str = Field("anonymous", max_length=100)
    message: str = Field("", max_length=2000)
    # sha256 of the config the writer based its change on; a mismatch means someone else wrote first
    expected_hash: str | None = None

    @field_validator("author")
    @classmethod
    def default_blank_author(cls, author: str) -> str:
        return author.strip() or "anonymous"


def config_hash(values: dict) -> str:
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


@app.get("/{app_name}")
async def get_values(app_name: str):
    return read_values(app_name)


@app.put("/{app_name}")
async def put_values(app_name: str, update: ValuesUpdate):
    try:
        json.dumps(update.values, allow_nan=False)
    except ValueError as exc:  # NaN/Infinity would be written, then break every later read
        raise HTTPException(422, "values must be plain JSON (no NaN or Infinity)") from exc
    # no await between this check and the write in save(), so nothing can interleave
    if update.expected_hash is not None and update.expected_hash != config_hash(read_values(app_name)):
        raise HTTPException(409, "The config changed since it was read (concurrent edit); retry the request")
    return save(app_name, update.values, update.author, update.message)


@app.get("/{app_name}/history")
async def get_history(app_name: str, limit: int = Query(20, ge=1, le=500)):
    values_path(app_name)
    return [summary(e) for e in reversed(read_history(app_name))][:limit]


@app.post("/{app_name}/undo")
async def undo(app_name: str, author: str = "anonymous"):
    # ponytail: assumes all writes go through PUT; edits made to the file by hand get overwritten by an undo
    values_path(app_name)
    entries = read_history(app_name)
    reverted = {e["reverts"] for e in entries if e["reverts"]}
    target = next((e for e in reversed(entries) if not e["reverts"] and e["id"] not in reverted), None)
    if target is None:
        raise HTTPException(409, f"Nothing to undo for {app_name}")
    return save(app_name, target["before"], author, f"undo #{target['id']}: {target['message']}", reverts=target["id"])


@app.exception_handler(Exception)
async def handle_error(request, exc):
    log.error("Unhandled error", extra={"error": str(exc)})
    return JSONResponse(status_code=500, content={"detail": "Internal error"})


def main():
    global values_dir

    parser = argparse.ArgumentParser()
    parser.add_argument("--values-dir", default="/data/values")
    parser.add_argument("--listen", default="0.0.0.0:7002")
    args = parser.parse_args()

    values_dir = Path(args.values_dir)
    host, port = args.listen.rsplit(":", 1)

    log.info("Starting server", extra={"dir": str(values_dir), "listen": args.listen})
    uvicorn.run(app, host=host, port=int(port), log_level="warning")


if __name__ == "__main__":
    main()
