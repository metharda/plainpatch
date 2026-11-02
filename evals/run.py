"""End-to-end eval: real Ollama, real services, exact-config scoring.

    python evals/run.py                      # all sets, needs Ollama on localhost:11434 with the model pulled
    python evals/run.py --set tuned --label my-change --ollama http://gpu-box:11434

Starts schema/values/bot-server from this repo on spare ports (values on a temp copy, preview only),
puts a recording proxy between the bot and Ollama, runs the labeled requests in evals/cases.py, and
writes evals/results/<label>.json. A request passes only if the resulting config equals the expected
config exactly (or, for requests that must be refused, if the bot answers 4xx/5xx).
"""
import argparse
import copy
import json
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import jsonpatch
from jsonschema import validate

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cases import REMOVE, SETS  # noqa: E402

OLLAMA = "http://127.0.0.1:11434"
ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "evals" / "results"
PORTS = {"schema": 16001, "values": 16002, "bot": 16003, "proxy": 16434}

calls: list[dict] = []
current_case: dict = {}


class Proxy(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 (name required by http.server)
        body = self.rfile.read(int(self.headers["Content-Length"]))
        req = urllib.request.Request(OLLAMA + self.path, data=body, headers={"Content-Type": "application/json"})
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                out, status = resp.read(), resp.status
        except urllib.error.HTTPError as e:  # pass Ollama errors through untouched
            out, status = e.read(), e.code
        data, sent = json.loads(out), json.loads(body)
        fmt = sent.get("format") or {}
        calls.append({
            "case": current_case.get("i"),
            "stage": "app" if "app" in json.dumps(fmt)[:200] else "edit",
            "wall_s": time.perf_counter() - t0,
            "http": status, "error": data.get("error"),
            **{k: data.get(k) for k in ("prompt_eval_count", "prompt_eval_cached_count", "eval_count", "done_reason")},
            **{k: (data.get(k) or 0) / 1e9 for k in ("total_duration", "load_duration", "prompt_eval_duration", "eval_duration")},
        })
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def wait_up(url, proc, name):
    for _ in range(60):
        if proc.poll() is not None:
            sys.exit(f"{name} exited early")
        try:
            urllib.request.urlopen(url, timeout=1)
            return
        except Exception:
            time.sleep(0.5)
    sys.exit(f"{name} did not start")


def post(path, payload):
    req = urllib.request.Request(f"http://127.0.0.1:{PORTS['bot']}{path}", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def expected_config(values, changes):
    cfg = copy.deepcopy(values)
    ops = [{"op": "remove", "path": p} if v is REMOVE else {"op": "add", "path": p, "value": v} for p, v in changes.items()]
    return jsonpatch.apply_patch(cfg, ops)


def main():
    global OLLAMA
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", choices=[*SETS, "all"], default="all")
    ap.add_argument("--label", default="latest")
    ap.add_argument("--passes", type=int, default=1, help="repeat the set; later passes exercise the caches")
    ap.add_argument("--ollama", default=OLLAMA)
    args = ap.parse_args()
    OLLAMA = args.ollama.rstrip("/")
    cases = [c for name, s in SETS.items() if args.set in (name, "all") for c in s]
    repo = ROOT
    py = sys.executable
    RESULTS.mkdir(exist_ok=True)

    data = Path(tempfile.mkdtemp()) / "data"
    shutil.copytree(repo / "data", data)
    schemas = {a: json.loads((data / f"schemas/{a}.schema.json").read_text()) for a in ("storefront", "ledger", "orders")}
    values = {a: json.loads((data / f"values/{a}.value.json").read_text()) for a in schemas}
    for _req, app, changes in cases:  # the labels themselves must be valid configs
        if app:
            validate(expected_config(values[app], changes), schemas[app])

    server = ThreadingHTTPServer(("127.0.0.1", PORTS["proxy"]), Proxy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    procs = [
        subprocess.Popen([py, "app.py", f"--schema-dir={data}/schemas", f"--listen=127.0.0.1:{PORTS['schema']}"],
                         cwd=repo / "schema-server", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
        subprocess.Popen([py, "app.py", f"--values-dir={data}/values", f"--listen=127.0.0.1:{PORTS['values']}"],
                         cwd=repo / "values-server", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
    ]
    log = open(RESULTS / f"{args.label}.bot.log", "w")  # noqa: SIM115 (lives as long as the bot process)
    procs.append(subprocess.Popen(
        [py, "app.py", f"--listen=127.0.0.1:{PORTS['bot']}", f"--ollama-url=http://127.0.0.1:{PORTS['proxy']}",
         f"--schema-url=http://127.0.0.1:{PORTS['schema']}", f"--values-url=http://127.0.0.1:{PORTS['values']}"],
        cwd=repo / "bot-server", stdout=log, stderr=log))
    try:
        for name, port, proc in zip(("schema", "values", "bot"), (16001, 16002, 16003), procs, strict=False):
            wait_up(f"http://127.0.0.1:{port}/health", proc, name)

        results = []
        for pass_no in range(args.passes):
            for i, (req, app, changes) in enumerate(cases):
                current_case["i"] = i
                n_before = len(calls)
                t0 = time.perf_counter()
                status, body = post("/edits", {"text": req})
                wall = time.perf_counter() - t0
                if app is None:
                    ok = status >= 400
                else:
                    ok = status == 200 and body.get("app") == app and body.get("config") == expected_config(values[app], changes)
                results.append({"pass": pass_no, "case": i, "request": req, "ok": ok, "status": status, "wall_s": wall,
                                "llm_calls": len(calls) - n_before,
                                "got": body.get("patch") if status == 200 else body.get("detail"),
                                "intent": body.get("intent")})
                print(f"[{args.label} p{pass_no}] {'OK ' if ok else 'BAD'} {wall:6.2f}s {req}", flush=True)
    finally:
        for p in procs:
            p.terminate()
        server.shutdown()

    def stats(rows, key):
        xs = [r[key] for r in rows if r.get(key) is not None]
        return {"mean": round(statistics.mean(xs), 3), "median": round(statistics.median(xs), 3), "max": round(max(xs), 3)} if xs else None

    summary = {"label": args.label, "passes": []}
    for pass_no in range(args.passes):
        rs = [r for r in results if r["pass"] == pass_no]
        summary["passes"].append({
            "accuracy": f"{sum(r['ok'] for r in rs)}/{len(rs)}",
            "request_wall_s": stats(rs, "wall_s"),
            "llm_calls_per_request": round(statistics.mean(r["llm_calls"] for r in rs), 2),
        })
    n = len(cases)
    for pass_no, p in enumerate(summary["passes"]):
        pc = calls[sum(r["llm_calls"] for r in results[:pass_no * n]):sum(r["llm_calls"] for r in results[:(pass_no + 1) * n])]
        for stage in ("app", "edit"):
            sc = [c for c in pc if c["stage"] == stage]
            if sc:
                p[stage] = {"calls": len(sc), "prompt_tokens": stats(sc, "prompt_eval_count"),
                            "cached_tokens": stats(sc, "prompt_eval_cached_count"),
                            "prompt_eval_s": stats(sc, "prompt_eval_duration"), "eval_s": stats(sc, "eval_duration"),
                            "load_s_total": round(sum(c["load_duration"] for c in sc), 2),
                            "reloads(load>0.5s)": sum(c["load_duration"] > 0.5 for c in sc)}
    (RESULTS / f"{args.label}.json").write_text(json.dumps({"summary": summary, "results": results, "calls": calls}, indent=1))
    print(json.dumps(summary, indent=1))
    failures = [r for r in results if not r["ok"]]
    for r in failures:
        print(f"FAIL (pass {r['pass']}): {r['request']} -> {r['status']} {json.dumps(r['got'])[:200]}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
