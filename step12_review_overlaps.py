"""Step12：本地审核互补子集中的共享坐标，只保存审核状态。

    python step12_review_overlaps.py
"""
from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import mimetypes
import secrets
import socketserver
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any, Mapping, Sequence

from recsub.config import CONFIG, path
from recsub.console import configure_console
from recsub.extract import file_digest
from recsub.io import iter_jsonl, write_json_atomic, write_jsonl_atomic
from recsub.subset_audit import audit_pair_overlaps, point_key, read_json

HOST = "127.0.0.1"
MAX_BODY = 64_000


def task_id(image: str, root: str, yes_caption: str, no_caption: str) -> str:
    source = json.dumps([image, root, yes_caption, no_caption], ensure_ascii=False,
                        separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(source).hexdigest()[:20]


def build_review_items(original: dict[str, Any], subsets: dict[str, Any]) -> list[dict[str, Any]]:
    audit = audit_pair_overlaps(subsets, original)
    items = []
    for row in audit["overlap_pair_details"]:
        image = row["image"]
        yes_caption, no_caption, root = row["yes_caption"], row["no_caption"], row["root_caption"]
        yes_record, no_record = subsets[image][yes_caption], subsets[image][no_caption]
        yes_points = {point_key(point) for point in yes_record.get("points", [])}
        no_points = {point_key(point) for point in no_record.get("points", [])}
        shared = yes_points & no_points
        root_points = original[image][root].get("points", [])
        ordered_unique, seen = [], set()
        for point in root_points:
            key = point_key(point)
            if key not in seen:
                seen.add(key)
                ordered_unique.append((key, point))
        if yes_points | no_points != seen:
            raise ValueError(f"互补点集未覆盖 root，请先修复覆盖问题：{image}/{root}")
        shared_points = [point for key, point in ordered_unique if key in shared]
        review_id = task_id(image, root, yes_caption, no_caption)
        items.append({"review_id": review_id, "image_name": image, "root_caption": root,
                      "yes_caption": yes_caption, "_no_caption": no_caption,
                      "shared_points": shared_points,
                      "yes_only_points": [point for key, point in ordered_unique if key in yes_points - no_points],
                      "no_only_points": [point for key, point in ordered_unique if key in no_points - yes_points],
                      "source_unique_points": [point for _, point in ordered_unique],
                      "shared_count": len(shared), "yes_unique_before": len(yes_points),
                      "no_unique_before": len(no_points)})
    items.sort(key=lambda item: (item["image_name"], item["root_caption"], item["yes_caption"], item["review_id"]))
    for index, item in enumerate(items, 1):
        item["index"] = index
    return items


def validate_selection(item: Mapping[str, Any], selected: Any, status: Any) -> list[int]:
    if status not in {"draft", "confirmed"}:
        raise ValueError("status must be draft or confirmed")
    if (not isinstance(selected, list)
            or any(type(i) is not int or not 0 <= i < item["shared_count"] for i in selected)
            or len(selected) != len(set(selected))):
        raise ValueError("只能选择当前配对共享点的有效索引")
    return sorted(selected)


def load_states(state_path: Path, items_by_id: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    if not state_path.exists():
        return {}
    states = {}
    for row in iter_jsonl(state_path):
        review_id = str(row.get("review_id") or "")
        item = items_by_id.get(review_id)
        if item is None:
            raise ValueError(f"Review state contains unknown task ID: {review_id}")
        selected = validate_selection(item, row.get("selected_yes_indices"), row.get("status"))
        states[review_id] = {"review_id": review_id, "selected_yes_indices": selected,
                             "status": row["status"], "fingerprint": row.get("fingerprint")}
    return states


def save_states(state_path: Path, states: Mapping[str, Mapping[str, Any]]) -> None:
    write_jsonl_atomic(state_path, (dict(row, review_id=review_id)
                                    for review_id, row in sorted(states.items())))


_HTML = (Path(__file__).parent / "recsub" / "overlap_review.html").read_text(encoding="utf-8")


class ReviewHandler(http.server.BaseHTTPRequestHandler):
    server_version = "RECOverlapReview"

    @property
    def state(self) -> dict[str, Any]:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def send_bytes(self, payload: bytes, content_type: str, status: int = 200) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return

    def send_json(self, value: Any, status: int = 200) -> None:
        self.send_bytes(json.dumps(value, ensure_ascii=False).encode("utf-8"),
                        "application/json; charset=utf-8", status)

    def _check_post_security(self) -> None:
        origin = self.headers.get("Origin")
        expected_origin = f"http://{self.state['host']}:{self.state['port']}"
        if origin and origin.rstrip("/") != expected_origin:
            raise ValueError("Cross-origin POST rejected")
        if not secrets.compare_digest(self.headers.get("X-Review-Token", ""), self.state["token"]):
            raise ValueError("Invalid review session token")

    def _body(self) -> dict[str, Any]:
        size = int(self.headers.get("Content-Length", "0"))
        if size <= 0 or size > MAX_BODY:
            raise ValueError("invalid request size")
        payload = json.loads(self.rfile.read(size))
        if not isinstance(payload, dict):
            raise ValueError("request must be a JSON object")
        return payload

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self.send_bytes(_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif parsed.path == "/api/items":
            with self.state["lock"]:
                states = {key: dict(value) for key, value in self.state["states"].items()}
            public_items = [{key: value for key, value in item.items() if not key.startswith("_")}
                            for item in self.state["items"]]
            self.send_json({"items": public_items, "states": states,
                            "token": self.state["token"], "start_index": 0})
        elif parsed.path.startswith("/image/"):
            review_id = urllib.parse.unquote(parsed.path[len("/image/"):])
            item = self.state["items_by_id"].get(review_id)
            if item is None:
                self.send_bytes(b"image not found", "text/plain", 404)
                return
            image_dir = self.state["image_dir"].resolve()
            target = (image_dir / Path(item["image_name"]).name).resolve()
            if image_dir not in target.parents or not target.is_file():
                self.send_bytes(b"image not found", "text/plain", 404)
                return
            self.send_bytes(target.read_bytes(), mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        else:
            self.send_bytes(b"not found", "text/plain", 404)

    def do_POST(self) -> None:  # noqa: N802
        route = urllib.parse.urlparse(self.path).path
        try:
            self._check_post_security()
            if route != "/api/state":
                self.send_bytes(b"not found", "text/plain", 404)
                return
            payload = self._body()
            review_id = str(payload.get("review_id") or "")
            item = self.state["items_by_id"].get(review_id)
            if item is None:
                raise ValueError("Unknown review task")
            selected = validate_selection(item, payload.get("selected_yes_indices"), payload.get("status"))
            row = {"review_id": review_id, "selected_yes_indices": selected,
                   "status": payload["status"], "fingerprint": self.state["fingerprint"]}
            with self.state["lock"]:
                updated = dict(self.state["states"])
                updated[review_id] = row
                save_states(self.state["state_path"], updated)
                self.state["states"].clear()
                self.state["states"].update(updated)
            self.send_json(row)
        except Exception as error:
            self.send_bytes(str(error).encode("utf-8"), "text/plain; charset=utf-8", 400)


def _fingerprint(annotations_path: Path, subsets_path: Path, items: Sequence[Mapping[str, Any]]) -> str:
    payload = {"annotations_sha256": file_digest(annotations_path),
               "subsets_sha256": file_digest(subsets_path),
               "task_ids": [item["review_id"] for item in items]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _load_or_create_manifest(path_: Path, fingerprint: str, items: Sequence[Mapping[str, Any]]) -> None:
    expected = {"fingerprint": fingerprint, "task_ids": [item["review_id"] for item in items], "tasks": list(items)}
    if path_.exists():
        existing = read_json(path_)
        if existing != expected:
            raise ValueError(f"审核输入与已有状态不一致，拒绝续用：{path_}")
    else:
        write_json_atomic(path_, expected)


def create_server(*, items: list[dict], image_dir: Path, annotations_path: Path,
                  subsets_path: Path, review_dir: Path, port: int) -> http.server.HTTPServer:
    if not items:
        raise ValueError("没有需要审核的共享坐标配对")
    review_dir.mkdir(parents=True, exist_ok=True)
    state_path = review_dir / "review_state.jsonl"
    manifest_path = review_dir / "review_manifest.json"
    fingerprint = _fingerprint(annotations_path, subsets_path, items)
    _load_or_create_manifest(manifest_path, fingerprint, items)
    states = load_states(state_path, {item["review_id"]: item for item in items})
    if any(row.get("fingerprint") != fingerprint for row in states.values()):
        raise ValueError("审核状态与当前输入指纹不同，拒绝续审")
    state = {"items": items, "items_by_id": {item["review_id"]: item for item in items},
             "states": states, "state_path": state_path, "fingerprint": fingerprint,
             "image_dir": image_dir, "token": secrets.token_urlsafe(32),
             "host": HOST, "port": port, "lock": threading.RLock()}

    class LocalReviewServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True

    server = LocalReviewServer((HOST, port), ReviewHandler)
    state["port"] = server.server_port
    server.state = state  # type: ignore[attr-defined]
    return server


def serve(*, items: list[dict], image_dir: Path, annotations_path: Path,
          subsets_path: Path, review_dir: Path, port: int, open_browser: bool = True) -> None:
    server = create_server(items=items, image_dir=image_dir, annotations_path=annotations_path,
                           subsets_path=subsets_path, review_dir=review_dir, port=port)
    url = f"http://{HOST}:{server.server_port}/"
    print(f"✅ 共享坐标正集审核：{url}", flush=True)
    print(f"   配对 {len(items)} 个；状态 → {server.state['state_path']}", flush=True)
    try:
        if open_browser:
            webbrowser.open(url)
        server.serve_forever()
    except KeyboardInterrupt:
        print("审核服务已停止；审核状态已保存。")
    finally:
        server.server_close()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="审核互补子集共享坐标；只选择属于正集的点")
    parser.add_argument("--annotations", type=Path, default=path("annotations"))
    parser.add_argument("--subsets", type=Path, default=path("annotations_subset"))
    parser.add_argument("--image-dir", type=Path, default=Path(CONFIG["image_dir"]))
    parser.add_argument("--review-dir", type=Path, default=Path("review/overlaps"))
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--no-browser", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    configure_console()
    args = parse_args(argv)
    original = read_json(args.annotations)
    subsets = read_json(args.subsets)
    items = build_review_items(original, subsets)
    serve(items=items, image_dir=args.image_dir, annotations_path=args.annotations,
          subsets_path=args.subsets, review_dir=args.review_dir, port=args.port,
          open_browser=not args.no_browser)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"❌ Step12 overlap review failed: {error}", file=sys.stderr)
        raise SystemExit(1)
