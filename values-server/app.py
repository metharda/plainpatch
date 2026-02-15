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


log = setup_logging("values-server")

app = FastAPI(title="Values Service", version="1.0.0")
Instrumentator().instrument(app).expose(app)

values_dir: Path = Path("/data/values")


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.get("/{app_name}")
async def get_values(app_name: str):
    if not app_name or not app_name.isalnum():
        raise HTTPException(404, f"Invalid application name: {app_name}")

    values_file = values_dir / f"{app_name}.value.json"

    if not values_file.exists():
        log.warning("Values not found", extra={"app": app_name})
        raise HTTPException(404, f"Values not found: {app_name}")

    try:
        return json.loads(values_file.read_text())
    except json.JSONDecodeError as exc:
        log.error("Invalid values JSON", extra={"app": app_name})
        raise HTTPException(500, "Values file is corrupted") from exc


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
