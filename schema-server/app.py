import argparse
import json
import logging
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
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


log = setup_logging("schema-server")

app = FastAPI(title="Schema Service", version="1.0.0")
Instrumentator().instrument(app).expose(app)

schema_dir: Path = Path("/data/schemas")


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.get("/{app_name}")
async def get_schema(app_name: str):
    if not app_name or not app_name.isalnum():
        raise HTTPException(404, f"Invalid application name: {app_name}")

    schema_file = schema_dir / f"{app_name}.schema.json"

    if not schema_file.exists():
        log.warning("Schema not found", extra={"app": app_name})
        raise HTTPException(404, f"Schema not found: {app_name}")

    try:
        return json.loads(schema_file.read_text())
    except json.JSONDecodeError as exc:
        log.error("Invalid schema JSON", extra={"app": app_name})
        raise HTTPException(500, "Schema file is corrupted") from exc


@app.exception_handler(Exception)
async def handle_error(request, exc):
    log.error("Unhandled error", extra={"error": str(exc)})
    return JSONResponse(status_code=500, content={"detail": "Internal error"})


def main():
    global schema_dir

    parser = argparse.ArgumentParser()
    parser.add_argument("--schema-dir", default="/data/schemas")
    parser.add_argument("--listen", default="0.0.0.0:7001")
    args = parser.parse_args()

    schema_dir = Path(args.schema_dir)
    host, port = args.listen.rsplit(":", 1)

    log.info("Starting server", extra={"dir": str(schema_dir), "listen": args.listen})
    uvicorn.run(app, host=host, port=int(port), log_level="warning")


if __name__ == "__main__":
    main()
