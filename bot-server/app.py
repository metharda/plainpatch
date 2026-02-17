import argparse
import asyncio
import json
import logging
import random
import re
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
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


config = Config()
circuit_breaker = CircuitBreaker()


class EditRequest(BaseModel):
    text: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting bot-server")
    yield
    log.info("Shutting down bot-server gracefully")


app = FastAPI(title="Bot Service", version="1.0.0", lifespan=lifespan)
Instrumentator().instrument(app).expose(app)


def exponential_backoff(attempt: int) -> float:
    delay = min(config.base_delay * (2 ** attempt), config.max_delay)
    jitter = random.uniform(0, delay * 0.1)
    return delay + jitter


async def call_llm(system: str, user: str) -> str:
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
                        "options": {"temperature": 0.1, "num_predict": 8192}
                    }
                )
                resp.raise_for_status()
                circuit_breaker.record_success()
                return resp.json()["message"]["content"].strip()

        except httpx.TimeoutException:
            log.warning("LLM timeout", extra={"attempt": attempt + 1})
        except httpx.HTTPError as e:
            log.warning("LLM error", extra={"attempt": attempt + 1, "error": str(e)})

        if attempt < config.max_retries - 1:
            delay = exponential_backoff(attempt)
            log.info("Retrying", extra={"delay": delay})
            await asyncio.sleep(delay)

    circuit_breaker.record_failure()
    raise HTTPException(502, f"LLM unavailable after {config.max_retries} attempts")


async def fetch_json(url: str) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(url)
            if resp.status_code == 404:
                raise HTTPException(404, f"Not found: {url}")
            resp.raise_for_status()
            return resp.json()
        except httpx.RequestError as exc:
            raise HTTPException(503, f"Service unavailable: {url}") from exc


def extract_json(text: str) -> str:
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if match:
        return match.group(1)
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        return match.group(0)
    return text


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

    # Stage 1: Extract app name
    app_name = None
    for attempt in range(config.max_retries):
        prompt = EXTRACT_APP_USER.format(input=user_input)
        response = await call_llm(EXTRACT_APP_SYSTEM, prompt)
        candidate = response.lower().strip()

        if candidate in APPS:
            app_name = candidate
            break

        log.warning("Invalid app name", extra={"response": candidate, "attempt": attempt + 1})

    if not app_name:
        raise HTTPException(400, f"Could not identify app. Available: {', '.join(APPS)}")

    log.info("App identified", extra={"request_id": request_id, "app": app_name})

    # Fetch schema and values
    schema = await fetch_json(f"{config.schema_url}/{app_name}")
    values = await fetch_json(f"{config.values_url}/{app_name}")

    # Stage 2: Modify configuration
    for attempt in range(config.max_retries):
        prompt = MODIFY_CONFIG_USER.format(
            input=user_input,
            schema=json.dumps(schema, indent=2),
            values=json.dumps(values, indent=2)
        )

        response = await call_llm(MODIFY_CONFIG_SYSTEM, prompt)

        try:
            json_str = extract_json(response)
            result = json.loads(json_str)
            validate(instance=result, schema=schema)
            log.info("Request successful", extra={"request_id": request_id, "app": app_name})
            return result
        except json.JSONDecodeError as e:
            log.warning("JSON parse error", extra={"error": str(e), "attempt": attempt + 1})
        except ValidationError as e:
            log.warning("Schema validation error", extra={"error": e.message, "attempt": attempt + 1})

    raise HTTPException(500, "Failed to generate valid configuration")


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
    args = parser.parse_args()

    config.ollama_url = args.ollama_url
    config.model = args.model
    config.schema_url = args.schema_url
    config.values_url = args.values_url

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
