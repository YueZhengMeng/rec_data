"""step8：本地交互式人工审核工具。

step7 已先为每个正向子集生成独立可视化图。本脚本把每个待审「根 × 谓词」
变成一条审核条目，在浏览器中复用同一套单子集渲染器展示：

- 与 step7 相同的原图、右侧 category/caption/新增划分属性和点标记；
- 审核模式下额外显示 no/unknown 点；
- keep / discard / 改词 / 全标 unknown 的键盘操作。

使用标准库 ``http.server``，不引入新依赖，也只绑定 127.0.0.1。四个人并行审核
时用 ``--reviewer r1`` / ``r2`` 写不同的 decisions JSONL；``--shard`` 可按条目
下标分片。``--ingest`` 合并并计算 pairwise Cohen's kappa，再把 keep 条目导出成
``fine_grained_subsets_final.jsonl``。

    # 先可视化，再生成审核条目并启动浏览器
    python step7_visualize.py
    python step8_review_sheet.py --build
    python step8_review_sheet.py --serve --reviewer r1 --shard 0:3249
"""

from __future__ import annotations

import argparse
import csv
import http.server
import json
import re
import socketserver
import sys
import threading
import urllib.parse
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from recsub.config import CONFIG, path, resolve_dir
from recsub.console import configure_console
from recsub.io import append_jsonl, iter_jsonl, write_jsonl_atomic
from recsub.subset_visualization import load_subset_nodes, render_subset_png


# --------------------------------------------------------------------------
# 审核条目
# --------------------------------------------------------------------------
def parse_shard(value: str | None, total: int) -> tuple[int, int]:
    if not value:
        return 0, total
    match = re.fullmatch(r"(\d+)\s*:\s*(\d+)", value)
    if not match:
        raise ValueError("--shard 格式应为 START:END（左闭右开）")
    start, end = map(int, match.groups())
    if not 0 <= start <= end <= total:
        raise ValueError(f"分片 {start}:{end} 超出条目总数 {total}")
    return start, end


def build_review_items(subsets_path: Path, instances_path: Path | None = None) -> list[dict]:
    """Build one review item per positive step6 subset.

    Coordinates are resolved from the step6 caption roots by the shared loader;
    ``instances_path`` remains an accepted compatibility argument for old callers.
    """
    items = []
    for node in load_subset_nodes(subsets_path, positive_only=True):
        item = {
            "review_id": node["node_id"],
            "node_id": node["node_id"],
            "parent_id": (node.get("parent_ids") or [None])[0],
            "image_name": node["image_name"],
            "caption": node["caption"],
            "category": node["category"],
            "predicate": node.get("predicate") or {},
            "counts": node.get("counts") or {},
            "gates": node.get("gates") or {},
            "yes_ids": [int(value) for value in node.get("instance_ids", ())],
            "no_ids": [int(value) for value in node.get("negative_instance_ids", ())],
            "unknown_ids": [int(value) for value in node.get("unknown_instance_ids", ())],
            "yes_points": node.get("yes_points", []),
            "no_points": node.get("no_points", []),
            "unknown_points": node.get("unknown_points", []),
            "decision": {"status": "pending"},
        }
        items.append(item)
    items.sort(key=lambda item: (item["image_name"], item["caption"], item["review_id"]))
    for index, item in enumerate(items, start=1):
        item["subset_index"] = index
    return items


def write_review_artifacts(items: Sequence[Mapping[str, Any]], review_dir: Path) -> tuple[Path, Path]:
    review_dir.mkdir(parents=True, exist_ok=True)
    items_path = review_dir / "review_items.jsonl"
    csv_path = review_dir / "review_sheet.csv"
    write_jsonl_atomic(items_path, items)
    fields = ["review_id", "image_name", "caption", "category", "predicate_id", "display",
              "yes", "no", "unknown", "parent", "gate"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in items:
            predicate = item.get("predicate") or {}
            counts = item.get("counts") or {}
            writer.writerow({
                "review_id": item["review_id"], "image_name": item["image_name"],
                "caption": item["caption"], "category": item["category"],
                "predicate_id": predicate.get("predicate_id", ""),
                "display": predicate.get("display", ""),
                "yes": counts.get("yes", len(item.get("yes_ids", ()))),
                "no": counts.get("no", len(item.get("no_ids", ()))),
                "unknown": counts.get("unknown", len(item.get("unknown_ids", ()))),
                "parent": counts.get("parent", ""), "gate": json.dumps(item.get("gates", {}), ensure_ascii=False),
            })
    return items_path, csv_path


# --------------------------------------------------------------------------
# kappa / ingest
# --------------------------------------------------------------------------
def read_decisions(paths: Sequence[Path]) -> dict[str, dict[str, dict]]:
    by_reviewer: dict[str, dict[str, dict]] = defaultdict(dict)
    for path_ in paths:
        for row in iter_jsonl(path_, strict=False):
            reviewer = str(row.get("reviewer") or path_.stem.replace("decisions_", ""))
            review_id = str(row.get("review_id") or "")
            if review_id:
                by_reviewer[reviewer][review_id] = dict(row)
    return dict(by_reviewer)


def cohen_kappa(left: Sequence[str], right: Sequence[str]) -> float | None:
    if not left:
        return None
    if len(left) != len(right):
        raise ValueError("kappa 两个评审的样本长度不同")
    categories = sorted(set(left) | set(right))
    observed = sum(a == b for a, b in zip(left, right)) / len(left)
    expected = sum(
        (sum(x == category for x in left) / len(left))
        * (sum(x == category for x in right) / len(right))
        for category in categories
    )
    if expected == 1.0:
        return 1.0 if observed == 1.0 else 0.0
    return (observed - expected) / (1.0 - expected)


def pairwise_kappa(decisions: Mapping[str, Mapping[str, Mapping]]) -> dict[str, float | None]:
    reviewers = sorted(decisions)
    result = {}
    for index, left_name in enumerate(reviewers):
        for right_name in reviewers[index + 1:]:
            common = sorted(set(decisions[left_name]) & set(decisions[right_name]))
            left = [str(decisions[left_name][key].get("status", "")) for key in common]
            right = [str(decisions[right_name][key].get("status", "")) for key in common]
            result[f"{left_name}__vs__{right_name}"] = cohen_kappa(left, right) if common else None
    return result


def ingest_decisions(items_path: Path, decision_paths: Sequence[Path], output_path: Path) -> dict[str, Any]:
    items = {row["review_id"]: dict(row) for row in iter_jsonl(items_path)}
    decisions = read_decisions(decision_paths)
    # 多人有重叠时，只有所有有记录的评审都 keep 才保留；未审核 / disagreement 丢到 pending。
    by_id: dict[str, list[dict]] = defaultdict(list)
    for reviewer, rows in decisions.items():
        for review_id, decision in rows.items():
            by_id[review_id].append({**decision, "reviewer": reviewer})

    final = []
    counts = defaultdict(int)
    for review_id, item in items.items():
        votes = by_id.get(review_id, [])
        statuses = {str(vote.get("status")) for vote in votes}
        if statuses == {"keep"}:
            status = "keep"
        elif not statuses:
            status = "pending"
        elif len(statuses) > 1:
            # 冲突不能静默按 discard 处理：保留在 disagreement 里，等人工裁决。
            status = "disagreement"
        elif statuses == {"discard"}:
            status = "discard"
        elif statuses == {"unknown"}:
            status = "unknown"
        else:
            status = "pending"
        counts[status] += 1
        if status == "keep":
            overrides = [vote.get("display_override") for vote in votes
                         if vote.get("display_override")]
            final_item = dict(item)
            if overrides:
                final_item["predicate"] = {
                    **(item.get("predicate") or {}),
                    "display": str(overrides[-1])[:300],
                }
            final_item["node_type"] = "predicate_subset"
            final_item["parent_ids"] = [item.get("parent_id")]
            final_item["instance_ids"] = list(item.get("yes_ids") or ())
            final_item["negative_instance_ids"] = list(item.get("no_ids") or ())
            final_item["unknown_instance_ids"] = list(item.get("unknown_ids") or ())
            final_item["review"] = {
                "status": "keep", "reviewers": [vote.get("reviewer") for vote in votes],
                "notes": [vote.get("note", "") for vote in votes if vote.get("note")],
                "display_override": overrides[-1] if overrides else None,
            }
            final.append(final_item)

    write_jsonl_atomic(output_path, final)
    kappas = pairwise_kappa(decisions)
    return {"items": len(items), "final_kept": len(final), "statuses": dict(counts),
            "reviewers": sorted(decisions), "pairwise_kappa": kappas}


# --------------------------------------------------------------------------
# 本地 HTTP 服务
# --------------------------------------------------------------------------
_HTML = r"""<!doctype html>
<meta charset="utf-8">
<title>REC-8K 子集审核</title>
<style>
body{font:14px system-ui,sans-serif;margin:0;background:#f6f6f2;color:#171717}
header{position:sticky;top:0;background:#fff;padding:10px 18px;border-bottom:1px solid #ddd;z-index:2}
#layout{display:grid;grid-template-columns:minmax(520px,1fr) 390px;gap:18px;padding:18px}
#stage{background:#fff;border:1px solid #ddd;padding:10px;position:relative}
#preview{max-width:100%;height:auto;display:block}.yes{color:#087f5b}.no{color:#c92a2a}.unknown{color:#777}
button{font-size:16px;padding:9px 15px;margin:3px;border:1px solid #aaa;border-radius:6px;background:#fff;cursor:pointer}
button:hover{background:#eef}.keep{border-color:#087f5b}.discard{border-color:#c92a2a}
pre{white-space:pre-wrap;word-break:break-word;background:#f0f0ec;padding:8px}.small{color:#666;font-size:12px}
</style>
<header><b>REC-8K 子集审核（step8）</b> <span id="progress"></span> <span id="reviewer"></span></header>
<div id="layout"><div id="stage"><img id="preview" alt="subset preview"></div>
<aside><h2 id="title">审核控制</h2><pre id="meta"></pre><div>
<button class="keep" onclick="decide('keep')">K Keep</button><button class="discard" onclick="decide('discard')">D Discard</button>
<button onclick="decide('unknown')">U All unknown</button><button onclick="editLabel()">M Edit label</button>
<button onclick="previous()">B Previous</button><button onclick="next()">Space Next</button></div>
<p class="small">快捷键：K 保留，D 丢弃，U 全 unknown，M 改词，空格下一条，B 上一条。</p>
<p id="status"></p></aside></div>
<script>
const reviewer=new URLSearchParams(location.search).get('reviewer')||'r1';
let items=[],decisions={},index=0;
const preview=document.querySelector('#preview');
async function load(){let x=await fetch('/api/items');items=await x.json();let d=await fetch('/api/decisions?reviewer='+encodeURIComponent(reviewer));decisions=await d.json();render()}
function current(){return items[index]}
function render(){let x=current();if(!x)return;document.querySelector('#progress').textContent=`${index+1}/${items.length}`;document.querySelector('#reviewer').textContent=`reviewer=${reviewer}`;let n=x.counts;document.querySelector('#meta').textContent=JSON.stringify({review_id:x.review_id,category:x.category,caption:x.caption,predicate:x.predicate,counts:n,gates:x.gates,decision:decisions[x.review_id]||{status:'pending'}},null,2);preview.alt=`${x.category} · ${x.caption} · ${x.predicate.display}`;preview.src='/preview/'+encodeURIComponent(x.review_id)+'?mode=review&v='+Date.now();document.querySelector('#status').textContent=decisions[x.review_id]?`已保存：${decisions[x.review_id].status}`:'待审核'}
async function decide(status){let x=current(),note='';if(status==='unknown')note='人工审核：三值标签全部改为 unknown';let r=await fetch('/api/decision',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({review_id:x.review_id,status,reviewer,note})});if(!r.ok){alert(await r.text());return}decisions[x.review_id]={status,reviewer,note};render();if(status!=='unknown')next()}
function editLabel(){let x=current(),value=prompt('输入新的谓词写法（只改审核显示；原始 predicate_id 不自动改）：',x.predicate.display);if(value&&value!==x.predicate.display){fetch('/api/decision',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({review_id:x.review_id,status:'keep',reviewer,note:'改词：'+value,display_override:value})}).then(()=>{decisions[x.review_id]={status:'keep',reviewer,note:'改词：'+value};render();next()})}}
function next(){if(index<items.length-1){index++;render()}}function previous(){if(index>0){index--;render()}}
document.addEventListener('keydown',e=>{if(['INPUT','TEXTAREA'].includes(document.activeElement.tagName))return;if(e.key==='k'||e.key==='K')decide('keep');else if(e.key==='d'||e.key==='D')decide('discard');else if(e.key==='u'||e.key==='U')decide('unknown');else if(e.key==='m'||e.key==='M')editLabel();else if(e.key===' ') {e.preventDefault();next()}else if(e.key==='b'||e.key==='B')previous()});load();
</script>"""


class ReviewHandler(http.server.BaseHTTPRequestHandler):
    server_version = "RECReview/1.0"

    @property
    def state(self) -> dict[str, Any]:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:
        # 浏览器每次取图片都会产生日志，默认静音；错误通过响应返回。
        return

    def send_bytes(self, payload: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self.send_bytes(_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/items":
            self.send_bytes(json.dumps(self.state["items"], ensure_ascii=False).encode(),
                            "application/json; charset=utf-8")
            return
        if parsed.path == "/api/decisions":
            query = urllib.parse.parse_qs(parsed.query)
            reviewer = query.get("reviewer", ["r1"])[0]
            if not re.fullmatch(r"[\w.-]{1,64}", reviewer):
                self.send_bytes(b"invalid reviewer", "text/plain", 400)
                return
            decision_path = self.state["decision_paths"].setdefault(
                reviewer, Path(self.state["review_dir"]) / f"decisions_{reviewer}.jsonl"
            )
            result = {
                row["review_id"]: row for row in iter_jsonl(decision_path, strict=False)
            } if Path(decision_path).is_file() else {}
            self.send_bytes(json.dumps(result, ensure_ascii=False).encode(),
                            "application/json; charset=utf-8")
            return
        if parsed.path.startswith("/preview/"):
            review_id = urllib.parse.unquote(parsed.path[len("/preview/"):])
            item = self.state["items_by_id"].get(review_id)
            if item is None:
                self.send_bytes(b"preview not found", "text/plain", 404)
                return
            image_dir = Path(self.state["image_dir"]).resolve()
            target = (image_dir / Path(item["image_name"]).name).resolve()
            if image_dir not in target.parents or not target.is_file():
                self.send_bytes(b"image not found", "text/plain", 404)
                return
            payload = render_subset_png(
                target, item, subset_index=int(item.get("subset_index", 1)), mode="review",
                max_width=CONFIG["visualize"]["max_width"],
                max_height=CONFIG["visualize"]["max_height"],
                sidebar_width=CONFIG["visualize"]["sidebar_width"],
            )
            self.send_bytes(payload, "image/png")
            return
        self.send_bytes(b"not found", "text/plain", 404)

    def do_POST(self) -> None:  # noqa: N802
        if urllib.parse.urlparse(self.path).path != "/api/decision":
            self.send_bytes(b"not found", "text/plain", 404)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size > 32_000:
                raise ValueError("request too large")
            payload = json.loads(self.rfile.read(size))
            if payload.get("status") not in {"keep", "discard", "unknown"}:
                raise ValueError("status 必须是 keep/discard/unknown")
            reviewer = str(payload.get("reviewer") or "r1")
            if not re.fullmatch(r"[\w.-]{1,64}", reviewer):
                raise ValueError("非法 reviewer")
            review_id = str(payload.get("review_id") or "")
            if review_id not in self.state["item_ids"]:
                raise ValueError("未知 review_id")
            row = {"review_id": review_id, "status": payload["status"],
                   "reviewer": reviewer, "note": str(payload.get("note") or "")}
            if payload.get("display_override"):
                row["display_override"] = str(payload["display_override"])[:300]
            path_ = self.state["decision_paths"].setdefault(
                reviewer, Path(self.state["review_dir"]) / f"decisions_{reviewer}.jsonl"
            )
            with self.state["lock"]:
                append_jsonl(path_, row, fsync=False)
            self.send_bytes(json.dumps({"ok": True}).encode(), "application/json")
        except Exception as error:
            self.send_bytes(str(error).encode("utf-8"), "text/plain", 400)


def serve(items: Sequence[dict], review_dir: Path, image_dir: Path, reviewer: str,
          host: str, port: int, shard: str | None) -> None:
    start, end = parse_shard(shard, len(items))
    selected = list(items[start:end])
    decision_paths = {reviewer: review_dir / f"decisions_{reviewer}.jsonl"}
    state = {
        "items": selected, "item_ids": {item["review_id"] for item in selected},
        "items_by_id": {item["review_id"]: item for item in selected},
        "review_dir": review_dir, "image_dir": image_dir,
        "decision_paths": decision_paths, "lock": threading.Lock(),
    }

    class ThreadingReviewServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True

    server = ThreadingReviewServer((host, port), ReviewHandler)
    server.state = state  # type: ignore[attr-defined]
    print(f"✅ 审核服务：http://{host}:{port}/?reviewer={urllib.parse.quote(reviewer)}")
    print(f"   条目 {len(selected)}（总数 {len(items)}，分片 {start}:{end}），"
          f"结果 → {decision_paths[reviewer]}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("审核服务已停止")
    finally:
        server.server_close()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="REC-8K step8 子集人工审核工具")
    parser.add_argument("--subsets", type=Path, default=path("subsets"))
    parser.add_argument("--instances", type=Path, default=path("instance_data"),
                        help="兼容旧调用；坐标现在直接从 step6 caption_root 读取")
    parser.add_argument("--review-dir", type=Path, default=resolve_dir(CONFIG["paths"]["review_dir"]))
    parser.add_argument("--build", action="store_true", help="生成 review_items.jsonl + review_sheet.csv")
    parser.add_argument("--serve", action="store_true", help="启动本地浏览器审核服务")
    parser.add_argument("--reviewer", default=CONFIG["review"]["default_reviewer"])
    parser.add_argument("--shard", default=None, help="审核分片 START:END（左闭右开）")
    parser.add_argument("--image-dir", type=Path, default=Path(CONFIG["image_dir"]))
    parser.add_argument("--host", default=CONFIG["review"]["host"])
    parser.add_argument("--port", type=int, default=CONFIG["review"]["port"])
    parser.add_argument("--ingest", nargs="+", type=Path,
                        help="回读一个或多个 decisions_*.jsonl 并导出最终子集")
    parser.add_argument("--items", type=Path, default=None,
                        help="审核条目输入；默认 review/review_items.jsonl")
    parser.add_argument("--final-output", type=Path, default=path("subsets_final"))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    configure_console()
    args = parse_args(argv)
    args.review_dir.mkdir(parents=True, exist_ok=True)
    items_path = args.items or args.review_dir / "review_items.jsonl"
    try:
        if args.build:
            if not args.subsets.is_file():
                raise FileNotFoundError(f"找不到 subsets：{args.subsets}")
            items = build_review_items(args.subsets, args.instances)
            items_path, csv_path = write_review_artifacts(items, args.review_dir)
            print(f"✅ 审核条目 {len(items)} 条 → {items_path}")
            print(f"   CSV → {csv_path}")

        if args.serve:
            if not items_path.is_file():
                raise FileNotFoundError(f"找不到审核条目：{items_path}，先运行 --build")
            items = list(iter_jsonl(items_path))
            serve(items, args.review_dir, args.image_dir, args.reviewer,
                  args.host, args.port, args.shard)

        if args.ingest:
            if not items_path.is_file():
                raise FileNotFoundError(f"找不到审核条目：{items_path}，先运行 --build")
            report = ingest_decisions(items_path, args.ingest, args.final_output)
            print(f"✅ 回读完成：{report['items']} 条审核条目 → 保留 {report['final_kept']}")
            print("   状态：", report["statuses"])
            print("   评审者：", report["reviewers"])
            print("   pairwise Cohen kappa：", report["pairwise_kappa"])
            print(f"   最终子集 → {args.final_output}")

        if not (args.build or args.serve or args.ingest):
            print("请指定 --build、--serve 或 --ingest")
        return 0
    except KeyboardInterrupt:
        print("审核服务已停止")
        return 130
    except Exception as error:
        print(f"❌ 审核工具失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
