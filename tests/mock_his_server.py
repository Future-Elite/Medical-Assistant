"""Read-only synthetic HIS fixtures for Agent integration; never touches a database."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "tests" / "fixtures" / "his_agent_cases.json"


def load_routes(dataset=DATASET):
    spec = json.loads(Path(dataset).read_text(encoding="utf-8"))
    if spec.get("synthetic_only") is not True:
        raise ValueError("This mock server accepts only explicitly synthetic datasets")
    routes = dict(spec["inline_routes"])
    for route, relative in spec["recorded_routes"].items():
        path = (ROOT / relative).resolve()
        path.relative_to(ROOT.resolve())
        routes[route] = json.loads(path.read_text(encoding="utf-8"))
    return routes


def make_handler(routes):
    class Handler(BaseHTTPRequestHandler):
        def send_json(self, status, body):
            content = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/api/patients":
                records = [body["data"] for key, body in routes.items() if re.fullmatch(r"/api/patients/\d+", key)]
                self.send_json(200, {"code": 0, "message": "synthetic fixtures", "data": {"records": records, "total": len(records)}})
            elif path in routes:
                self.send_json(200, routes[path])
            elif re.fullmatch(r"/api/patients/\d+", path):
                self.send_json(200, {"code": 0, "message": "success", "data": None})
            elif re.fullmatch(r"/api/patients/\d+/timeline", path):
                self.send_json(200, {"code": 0, "message": "success", "data": {"patientId": int(path.split("/")[3]), "events": []}})
            else:
                self.send_json(404, {"code": 404, "message": "fixture route not found", "data": None})

        def do_POST(self):
            self.send_json(405, {"code": 405, "message": "read-only synthetic mock", "data": None})

        do_PUT = do_POST
        do_DELETE = do_POST

    return Handler


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(load_routes()))
    print(f"Synthetic read-only HIS mock: http://{args.host}:{args.port} (patients 2001, 2002)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
