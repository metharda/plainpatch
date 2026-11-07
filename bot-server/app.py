import argparse
import asyncio
import hashlib
import json
import logging
import random
import re
import sys
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

import httpx
import jsonpatch
import jsonpointer
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from jsonschema import ValidationError, validate
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel
from pythonjsonlogger.json import JsonFormatter

from prompts import (
    APPS,
    EXTRACT_APP_SYSTEM,
    EXTRACT_APP_USER,
    MODIFY_CONFIG_SYSTEM,
    MODIFY_CONFIG_USER,
    RETRY_FEEDBACK,
)


def setup_logging(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s"
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


log = setup_logging("bot-server")


@dataclass
class CircuitBreaker:
    failure_threshold: int = 5
    reset_timeout: float = 30.0
    failures: int = 0
    state: str = "closed"
    last_failure: float = 0.0

    def record_failure(self):
        self.failures += 1
        self.last_failure = time.time()
        if self.failures >= self.failure_threshold:
            self.state = "open"
            log.warning("Circuit breaker opened", extra={"failures": self.failures})

    def record_success(self):
        self.failures = 0
        if self.state == "half-open":
            self.state = "closed"
            log.info("Circuit breaker closed")

    def can_execute(self) -> bool:
        if self.state == "closed":
            return True
        if self.state == "open":
            if time.time() - self.last_failure > self.reset_timeout:
                self.state = "half-open"
                log.info("Circuit breaker half-open, testing...")
                return True
            return False
        return True


@dataclass
class Config:
    ollama_url: str = "http://ollama:11434"
    model: str = "qwen2.5:3b"
    schema_url: str = "http://schema-server:7001"
    values_url: str = "http://values-server:7002"
    timeout: float = 120.0
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    # one value for every call: Ollama reloads the model whenever num_ctx changes between requests
    num_ctx: int = 16384
    keep_alive: str = "30m"


config = Config()
circuit_breaker = CircuitBreaker()


class EditRequest(BaseModel):
    text: str
    apply: bool = False
    author: str = "anonymous"


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting bot-server")
    yield
    log.info("Shutting down bot-server gracefully")


app = FastAPI(title="Bot Service", version="1.0.0", lifespan=lifespan)
Instrumentator().instrument(app).expose(app)


class TruncatedOutputError(ValueError):
    pass


# Structured outputs: Ollama compiles these JSON Schemas into a grammar, so the model
# can only emit tokens that produce matching JSON (no prose, no code fences).
# The edit call's schema is built per config by patch_format().
APP_FORMAT = {
    "type": "object",
    "properties": {"app": {"type": "string", "enum": [*APPS, "none"]}},
    "required": ["app"],
}

def exponential_backoff(attempt: int) -> float:
    delay = min(config.base_delay * (2 ** attempt), config.max_delay)
    jitter = random.uniform(0, delay * 0.1)
    return delay + jitter


async def call_llm(system: str, user: str, fmt: dict, num_predict: int) -> str:
    if not circuit_breaker.can_execute():
        raise HTTPException(503, "Service temporarily unavailable (circuit open)")

    for attempt in range(config.max_retries):
        try:
            async with httpx.AsyncClient(timeout=config.timeout) as client:
                resp = await client.post(
                    f"{config.ollama_url}/api/chat",
                    json={
                        "model": config.model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user}
                        ],
                        "stream": False,
                        "format": fmt,
                        "keep_alive": config.keep_alive,
                        "options": {
                            "temperature": 0,
                            "num_ctx": config.num_ctx,
                            "num_predict": num_predict,
                        },
                    }
                )
                if resp.status_code == 500 and "prediction aborted" in resp.text:
                    # Ollama stopped a degenerate reply (e.g. "token repeat limit reached"): a bad answer, not an outage
                    circuit_breaker.record_success()
                    raise TruncatedOutputError(resp.json().get("error", "prediction aborted"))
                resp.raise_for_status()
                circuit_breaker.record_success()
                data = resp.json()
                break

        except httpx.TimeoutException:
            log.warning("LLM timeout", extra={"attempt": attempt + 1})
        except httpx.HTTPError as e:
            log.warning("LLM error", extra={"attempt": attempt + 1, "error": str(e)})

        if attempt < config.max_retries - 1:
            delay = exponential_backoff(attempt)
            log.info("Retrying", extra={"delay": delay})
            await asyncio.sleep(delay)

    else:
        circuit_breaker.record_failure()
        raise HTTPException(502, f"LLM unavailable after {config.max_retries} attempts")

    # Ollama silently drops the start of prompts longer than num_ctx; this is the only trace of it
    # ponytail: heuristic (prompt caching lowers the count); count tokens up front if this ever misfires
    if data.get("prompt_eval_count", 0) + num_predict >= config.num_ctx:
        log.warning("Prompt may have been truncated, raise --num-ctx", extra={
            "prompt_eval_count": data.get("prompt_eval_count"), "num_ctx": config.num_ctx,
        })
    log.info("LLM call", extra={
        "prompt_tokens": data.get("prompt_eval_count"),
        "cached_tokens": data.get("prompt_eval_cached_count"),  # prefix reused from Ollama's prompt cache
        "prompt_ms": round(data.get("prompt_eval_duration", 0) / 1e6),
        "eval_ms": round(data.get("eval_duration", 0) / 1e6),
        "load_ms": round(data.get("load_duration", 0) / 1e6),  # large values mean the model was (re)loaded
    })
    if data.get("done_reason") == "length":
        raise TruncatedOutputError(f"reply was cut off at the {num_predict}-token limit")
    return data["message"]["content"]


async def fetch_json(url: str, method: str = "GET", payload: dict | None = None) -> dict:
    path = urlsplit(url).path  # never echo internal hostnames back to the caller
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.request(method, url, json=payload)
        except httpx.RequestError as exc:
            raise HTTPException(503, f"Service unavailable: {path}") from exc

    if resp.status_code == 404:
        raise HTTPException(404, f"Not found: {path}")
    if resp.status_code >= 400:
        try:
            body = resp.json()
            detail = body.get("detail") if isinstance(body, dict) else None
        except ValueError:
            detail = None
        # client errors (409 nothing to undo, 422) pass through; upstream crashes become 502
        status = resp.status_code if resp.status_code < 500 else 502
        raise HTTPException(status, detail or f"Upstream error {resp.status_code}: {path}")
    return resp.json()


def reject_constant(name: str):
    # Python's json accepts NaN/Infinity, which then can't be serialised in the response
    raise ValueError(f"{name} is not valid JSON")


PATCH_OPS = {"add", "remove", "replace", "move", "copy", "test"}


def check_op(op) -> dict:
    if not (isinstance(op, dict) and op.get("op") in PATCH_OPS and isinstance(op.get("path"), str)):
        raise jsonpatch.InvalidJsonPatch(f"Invalid patch operation: {op!r}")
    if op["op"] in ("move", "copy") and not isinstance(op.get("from"), str):
        raise jsonpatch.InvalidJsonPatch(f"'{op['op']}' needs a string 'from': {op!r}")
    if op["op"] in ("add", "replace", "test") and "value" not in op:
        raise jsonpatch.InvalidJsonPatch(f"'{op['op']}' needs a 'value': {op!r}")
    return op


MAP_CONTAINERS = ("envs",)  # objects where "add" may create new keys


def escape(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


def editable_paths(values: dict) -> list[str]:
    """Every leaf pointer in the config, plus map containers that accept new keys."""
    paths = []

    def walk(node, path):
        if path.rsplit("/", 1)[-1] in MAP_CONTAINERS:
            paths.append(path)
        if isinstance(node, dict) and node:
            for k, v in node.items():
                walk(v, f"{path}/{escape(k)}")
        elif isinstance(node, list) and node:
            for i, v in enumerate(node):
                walk(v, f"{path}/{i}")
        elif path not in paths and node != {}:  # empty objects only count as maps (MAP_CONTAINERS)
            paths.append(path)

    walk(values, "")
    return paths


def patch_format(values: dict) -> dict:
    # the grammar only allows paths that exist, so the model can't invent pointers
    paths = editable_paths(values)
    leaves = [p for p in paths if p.rsplit("/", 1)[-1] not in MAP_CONTAINERS]
    maps = [p for p in paths if p.rsplit("/", 1)[-1] in MAP_CONTAINERS]
    scalar = {"anyOf": [{"type": "string"}, {"type": "number"}, {"type": "boolean"}]}
    edit = {
        "type": "object",
        "properties": {"op": {"type": "string", "enum": ["replace", "remove"]},
                       "path": {"type": "string", "enum": leaves}, "value": scalar},
        "required": ["op", "path"],
    }
    # adding a map entry is its own shape with "key" required, or the model skips it
    add_key = {
        "type": "object",
        "properties": {"op": {"type": "string", "enum": ["add"]}, "path": {"type": "string", "enum": maps},
                       "key": {"type": "string"}, "value": {"type": "string"}},
        "required": ["op", "path", "key", "value"],
    }
    return {
        "type": "object",
        "properties": {"operations": {"type": "array", "items": {"anyOf": [edit, add_key] if maps else [edit]}}},
        "required": ["operations"],
    }


def to_patch(parsed) -> list[dict]:
    if not (isinstance(parsed, dict) and isinstance(parsed.get("operations"), list)):
        raise jsonpatch.InvalidJsonPatch('Expected {"operations": [...]}')
    ops = []
    for op in parsed["operations"]:
        if isinstance(op, dict) and "key" in op:
            # {"op": "add", "path": <envs>, "key": NAME} → {"op": "add", "path": <envs>/NAME}
            op = dict(op)
            op["path"] = f"{op.get('path')}/{escape(str(op.pop('key')))}"
        ops.append(check_op(op))
    return ops


def describe(patch: list[dict], values: dict) -> list[dict]:
    return [
        {
            "op": op["op"],
            "path": op["path"],
            # "/-" means "append to array"; there is no old value (resolve_pointer would return a sentinel)
            "old": None if op["path"].endswith("/-") else jsonpointer.resolve_pointer(values, op["path"], None),
            "new": jsonpointer.resolve_pointer(values, op["from"], None) if "from" in op else op.get("value"),
        }
        for op in patch
    ]


def route_app(text: str) -> str | None:
    """Pick the app without the LLM when exactly one app name appears as a word."""
    found = {a for a in APPS if re.search(rf"\b{a}\b", text, re.IGNORECASE)}
    return found.pop() if len(found) == 1 else None


# exact-match cache: same request against the same config → same patch, no LLM call
# ponytail: in-process LRU, lost on restart and not shared between replicas
PATCH_CACHE: OrderedDict[str, list[dict]] = OrderedDict()
PATCH_CACHE_SIZE = 256


def cache_key(user_input: str, app_name: str, values: dict, schema: dict) -> str:
    normalized = " ".join(user_input.lower().split())
    # hashing schema too means a cached patch is always valid again (same input, same validation)
    digest = hashlib.sha256(json.dumps([values, schema], sort_keys=True).encode()).hexdigest()
    return f"{app_name}|{digest}|{normalized}"


def require_known_app(app_name: str):
    if app_name not in APPS:
        raise HTTPException(404, f"Unknown app: {app_name}. Available: {', '.join(APPS)}")


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "circuit_breaker": circuit_breaker.state
    }


@app.post("/edits")
async def process_message(req: EditRequest):
    user_input = req.text.strip()
    if not user_input:
        raise HTTPException(400, "Input cannot be empty")

    request_id = f"{int(time.time() * 1000)}"
    log.info("Processing request", extra={"request_id": request_id, "text": user_input[:100]})

    # Stage 1: Extract app name; the LLM is only asked when the request doesn't name exactly one app
    app_name = route_app(user_input)
    if app_name is None:
        # the enum in APP_FORMAT means no free-text parsing or retries
        response = await call_llm(EXTRACT_APP_SYSTEM, EXTRACT_APP_USER.format(input=user_input), APP_FORMAT, 32)
        try:
            app_name = json.loads(response)["app"]
        except (ValueError, KeyError, TypeError):
            app_name = None

    if app_name not in APPS:
        raise HTTPException(400, f"Could not identify app. Available: {', '.join(APPS)}")

    log.info("App identified", extra={"request_id": request_id, "app": app_name})

    # Fetch schema and values
    schema = await fetch_json(f"{config.schema_url}/{app_name}")
    values = await fetch_json(f"{config.values_url}/{app_name}")

    # Stage 2: Modify configuration
    # The schema isn't sent: the path enum in patch_format() plus local validation do its job,
    # which cuts the prompt from ~5k to ~1k tokens (benchmarked: same or better accuracy).
    prompt = MODIFY_CONFIG_USER.format(input=user_input, values=json.dumps(values, separators=(",", ":")))
    key = cache_key(user_input, app_name, values, schema)
    feedback = ""
    for attempt in range(config.max_retries):
        try:
            if key in PATCH_CACHE:
                PATCH_CACHE.move_to_end(key)
                patch = PATCH_CACHE[key]
            else:
                response = await call_llm(MODIFY_CONFIG_SYSTEM, prompt + feedback, patch_format(values), 256)
                patch = to_patch(json.loads(response, parse_constant=reject_constant))
            result = jsonpatch.apply_patch(values, patch)
            validate(instance=result, schema=schema)
            PATCH_CACHE[key] = patch
            if len(PATCH_CACHE) > PATCH_CACHE_SIZE:
                PATCH_CACHE.popitem(last=False)
            break
        except ValueError as e:  # includes json.JSONDecodeError and TruncatedOutputError
            error = f"invalid JSON: {e}"
        except (jsonpatch.JsonPatchException, jsonpointer.JsonPointerException) as e:
            error = f"patch could not be applied: {e}"
        except ValidationError as e:
            error = f"result violates the schema at /{'/'.join(map(str, e.absolute_path))}: {e.message}"

        log.warning("Rejected LLM output", extra={"error": error, "attempt": attempt + 1})
        # temperature is 0, so retrying the same prompt would reproduce the same mistake
        feedback = RETRY_FEEDBACK.format(error=error[:500])
    else:
        raise HTTPException(500, "Failed to generate valid configuration")

    response = {
        "app": app_name,
        "applied": False,
        "revision": None,
        "changes": describe(patch, values),
        "patch": patch,
        "config": result,
    }

    if req.apply and patch:
        # ponytail: last write wins if two requests edit the same app at once; add an If-Match revision check if that matters
        entry = await fetch_json(
            f"{config.values_url}/{app_name}", "PUT",
            {"values": result, "author": req.author, "message": user_input},
        )
        response.update(applied=True, revision=entry["id"])

    log.info("Request successful", extra={"request_id": request_id, "app": app_name, "applied": response["applied"]})
    return response


@app.get("/history/{app_name}")
async def history(app_name: str, limit: int = Query(20, ge=1, le=500)):
    require_known_app(app_name)
    return await fetch_json(f"{config.values_url}/{app_name}/history?{urlencode({'limit': limit})}")


@app.post("/undo/{app_name}")
async def undo(app_name: str, author: str = "anonymous"):
    require_known_app(app_name)
    return await fetch_json(f"{config.values_url}/{app_name}/undo?{urlencode({'author': author})}", "POST")


@app.exception_handler(Exception)
async def handle_error(request: Request, exc: Exception):
    log.error("Unhandled error", extra={"error": str(exc), "path": request.url.path})
    return JSONResponse(status_code=500, content={"detail": "Internal error"})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", default="0.0.0.0:7000")
    parser.add_argument("--ollama-url", default="http://ollama:11434")
    parser.add_argument("--model", default="qwen2.5:3b")
    parser.add_argument("--schema-url", default="http://schema-server:7001")
    parser.add_argument("--values-url", default="http://values-server:7002")
    parser.add_argument("--num-ctx", type=int, default=Config.num_ctx)
    args = parser.parse_args()

    config.ollama_url = args.ollama_url
    config.model = args.model
    config.schema_url = args.schema_url
    config.values_url = args.values_url
    config.num_ctx = args.num_ctx

    host, port = args.listen.rsplit(":", 1)

    log.info("Starting server", extra={
        "listen": args.listen,
        "model": config.model
    })

    uv_config = uvicorn.Config(
        app, host=host, port=int(port),
        log_level="warning",
        timeout_graceful_shutdown=10
    )
    server = uvicorn.Server(uv_config)
    server.run()


if __name__ == "__main__":
    main()
