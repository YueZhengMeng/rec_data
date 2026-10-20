"""step8：REC-8K 本地单人浏览器审核工具。

从 step6 的正向子集生成审核条目，并在 loopback 浏览器界面展示原图与实例点。
可从指定 source ``image_idx`` 开始，拖框选择点并翻转 yes/no；unknown 保持不变。
审核状态实时保存在单个 JSONL 文件中，子集默认保留，空格显式废弃并跳到下一条。

    python step8_review_sheet.py --image-idx 120
"""

from __future__ import annotations

import argparse
import csv
import http.server
import json
import mimetypes
import socketserver
import sys
import threading
import urllib.parse
from pathlib import Path
from typing import Any, Mapping, Sequence

from recsub.config import CONFIG, path, resolve_dir
from recsub.console import configure_console
from recsub.io import append_jsonl, iter_jsonl, write_jsonl_atomic
from recsub.subset_visualization import load_subset_nodes


def build_review_items(subsets_path: Path, instances_path: Path | None = None) -> list[dict]:
    """Build positive subset items and attach the source image order."""
    image_indices: dict[tuple[str, str], int] = {}
    if instances_path is not None and instances_path.is_file():
        for instance in iter_jsonl(instances_path, strict=False):
            key = (str(instance.get("image_name", "")), str(instance.get("caption", "")))
            if "image_idx" not in instance:
                continue
            image_idx = int(instance["image_idx"])
            image_indices[key] = min(image_idx, image_indices.get(key, image_idx))

    items = []
    for node in load_subset_nodes(subsets_path, positive_only=True):
        key = (str(node["image_name"]), str(node["caption"]))
        item = {
            "review_id": node["node_id"],
            "node_id": node["node_id"],
            "parent_id": (node.get("parent_ids") or [None])[0],
            "image_name": node["image_name"],
            "image_idx": image_indices.get(key),
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
            "other_instance_ids": node["other_instance_ids"],
            "other_points": node["other_points"],
        }
        items.append(item)
    if any(item["image_idx"] is None for item in items):
        missing = sorted({item["image_name"] for item in items if item["image_idx"] is None})
        raise ValueError(f"实例表没有这些子集对应的 image_idx：{missing[:5]}；请提供 step1 实例表")
    items.sort(key=lambda item: (item["image_idx"], item["caption"], item["review_id"]))
    for index, item in enumerate(items, start=1):
        item["subset_index"] = index
    return items


def write_review_artifacts(items: Sequence[Mapping[str, Any]], review_dir: Path) -> tuple[Path, Path]:
    review_dir.mkdir(parents=True, exist_ok=True)
    items_path = review_dir / "review_items.jsonl"
    csv_path = review_dir / "review_sheet.csv"
    write_jsonl_atomic(items_path, items)
    fields = ["review_id", "image_idx", "image_name", "caption", "category", "predicate_id", "display",
              "yes", "no", "unknown", "parent", "gate"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in items:
            predicate = item.get("predicate") or {}
            counts = item.get("counts") or {}
            writer.writerow({
                "review_id": item["review_id"], "image_idx": item["image_idx"],
                "image_name": item["image_name"], "caption": item["caption"], "category": item["category"],
                "predicate_id": predicate.get("predicate_id", ""),
                "display": predicate.get("display", ""),
                "yes": counts.get("yes", len(item.get("yes_ids", ()))),
                "no": counts.get("no", len(item.get("no_ids", ()))),
                "unknown": counts.get("unknown", len(item.get("unknown_ids", ()))),
                "parent": counts.get("parent", ""), "gate": json.dumps(item.get("gates", {}), ensure_ascii=False),
            })
    return items_path, csv_path


# --------------------------------------------------------------------------
# 单人审核状态与最终导出
# --------------------------------------------------------------------------
def load_review_state(state_path: Path) -> dict[str, dict[str, Any]]:
    """Load saved edits, or start empty before the first state file is written."""
    if not state_path.exists():
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in iter_jsonl(state_path, strict=False):
        review_id = str(row.get("review_id") or "")
        if review_id:
            result[review_id] = dict(row)
    return result


def effective_partitions(item: Mapping[str, Any], state: Mapping[str, Any]) -> dict[str, list[int]]:
    """Apply explicit yes/no flips without changing unknown instances."""
    yes = set(map(int, item.get("yes_ids", ())))
    no = set(map(int, item.get("no_ids", ())))
    unknown = set(map(int, item.get("unknown_ids", ())))
    for raw_id, label in (state.get("labels") or {}).items():
        instance_id = int(raw_id)
        if instance_id not in yes | no | unknown or label not in {"yes", "no"}:
            continue
        yes.discard(instance_id)
        no.discard(instance_id)
        unknown.discard(instance_id)
        (yes if label == "yes" else no).add(instance_id)
    return {"yes_ids": sorted(yes), "no_ids": sorted(no), "unknown_ids": sorted(unknown)}


def finalize_items(items: Sequence[Mapping[str, Any]], states: Mapping[str, Mapping[str, Any]],
                   output_path: Path) -> dict[str, int]:
    """Export all items except explicitly discarded ones; untouched items default to keep."""
    final = []
    discarded = 0
    for item in items:
        state = states.get(str(item["review_id"]), {})
        if state.get("discard") is True:
            discarded += 1
            continue
        final_item = dict(item)
        final_item.update(effective_partitions(item, state))
        final_item["instance_ids"] = final_item.pop("yes_ids")
        final_item["negative_instance_ids"] = final_item.pop("no_ids")
        final_item["unknown_instance_ids"] = final_item.pop("unknown_ids")
        final_item["node_type"] = "predicate_subset"
        final_item["parent_ids"] = [item.get("parent_id")]
        final_item["review"] = {
            "status": "keep", "reviewed": state.get("reviewed") is True or bool(state.get("labels")),
            "defaulted": not (state.get("reviewed") is True or bool(state.get("labels")) or state.get("discard") is True),
            "discarded": False,
            "label_overrides": dict(state.get("labels") or {}),
            "flipped_instance_ids": sorted(int(key) for key in (state.get("labels") or {})),
        }
        final.append(final_item)
    write_jsonl_atomic(output_path, final)
    return {"items": len(items), "kept": len(final), "discarded": discarded}


# --------------------------------------------------------------------------
# 本地 HTTP 服务
# --------------------------------------------------------------------------
_HTML = r"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>REC-8K 子集审核</title>
<style>
*{box-sizing:border-box}body{font:16px system-ui,sans-serif;margin:0;background:#f4f4f0;color:#171717}
header{position:sticky;top:0;background:#fff;padding:12px 18px;border-bottom:1px solid #ddd;z-index:2}
#layout{display:grid;grid-template-columns:minmax(0,1fr) minmax(380px,520px);gap:20px;padding:18px;align-items:start}
#stage{background:#fff;border:1px solid #ddd;padding:10px;position:relative;min-width:0}
#canvas{display:block;max-width:100%;height:auto;cursor:crosshair;touch-action:none}
#info{min-width:0}
.info-card{background:#fff;border:1px solid #ddd;border-left:6px solid currentColor;border-radius:8px;margin-bottom:14px;padding:16px 18px;overflow-wrap:anywhere}
.info-card h2{font-size:24px;margin:0 0 8px;font-weight:700}
.info-card p{font-size:32px;line-height:1.4;font-weight:750;margin:0;white-space:pre-wrap}
.category-card{color:#194e96;background:#f2f6fd}
.caption-card{color:#87305c;background:#fdf3f8}
.predicate-card{color:#007054;background:#f0faf6}
#review-state{display:block;border:2px solid #987018;border-radius:9px;padding:14px 16px;margin:16px 0 12px;background:#fff8df;color:#765400;font-size:24px;font-weight:800;line-height:1.35}
#review-state[data-reviewed="true"][data-discarded="false"]{border-color:#16805f;background:#e9f7f0;color:#075c43}
#review-state[data-reviewed="true"][data-discarded="true"]{border-color:#c92a2a;background:#fff0f0;color:#a11}
#review-state .state-detail{display:block;font-size:15px;font-weight:500;margin-top:4px}
#counts,#selection-info{font-size:18px;line-height:1.6;margin:6px 0}
#controls{border-top:1px solid #ddd;margin-top:16px;padding-top:12px}
#controls h2{font-size:20px;margin:0 0 8px}
button{font-size:16px;padding:9px 12px;margin:3px;border:1px solid #aaa;border-radius:6px;background:#fff;cursor:pointer}
button:hover{background:#eef}.discard{border-color:#c92a2a;color:#a11}.flip{border-color:#087f5b;color:#075c43}
#meta{white-space:pre-wrap;overflow-wrap:anywhere;background:#f0f0ec;padding:10px;max-height:35vh;overflow:auto;font-size:13px}
details{margin-top:18px}summary{cursor:pointer;color:#555}
.small{color:#555;font-size:14px}#status{min-height:1.5em;color:#075c43}
@media(max-width:900px){#layout{grid-template-columns:1fr}.info-card p{font-size:28px}}
</style>
<header><b>REC-8K 子集审核</b> <span id="progress"></span> <span id="image-index"></span></header>
<div id="layout"><div id="stage"><canvas id="canvas"></canvas></div>
<aside id="info" aria-label="当前子集信息">
<section class="info-card category-card"><h2>原始类别</h2><p id="category">—</p></section>
<section class="info-card caption-card"><h2>原始 caption</h2><p id="caption">—</p></section>
<section class="info-card predicate-card"><h2>新增划分属性</h2><p id="predicate">—</p></section>
<span id="review-state"></span>
<p id="counts"></p><p id="selection-info">已框选 0 个实例点</p>
<p class="small">红叉 = 当前子集正样本；绿点 = 同根其余实例（包括 unknown，标签不会因此变成 no）。</p>
<div id="controls"><h2>审核操作</h2>
<div><button class="discard" onclick="discardCurrent()">空格：废弃此子集并下一条</button>
<button class="flip" onclick="flipSelected()">手动翻转所选 yes/no</button>
<button onclick="setSelectedLabel('yes')">将框选点全部设为 yes</button>
<button onclick="setSelectedLabel('no')">将框选点全部设为 no</button>
<button onclick="restoreCurrent()">恢复为保留</button></div>
<p class="small">← / A：上一子集；→ / D：下一子集；空格：废弃并下一条。拖框结束会自动翻转所选 yes/no 一次；如果还要再次切换，可点“手动翻转所选 yes/no”。W：框选点全设 yes；S：框选点全设 no（包括 unknown）。没有任何审核操作的子集默认保留，但状态显示为未审核。</p>
<button onclick="finalize()">保存最终子集</button><p id="status" role="status"></p></div>
<details><summary>查看技术详情（JSON）</summary><pre id="meta"></pre></details>
</aside></div>
<script>
let items=[],states={},index=0,selected=new Set(),drag=null,img=new Image(),ctx;
const canvas=document.querySelector('#canvas');ctx=canvas.getContext('2d');
async function request(url,options){const r=await fetch(url,options);if(!r.ok)throw Error(await r.text());return r.json()}
async function load(){try{const data=await request('/api/items');items=data.items;states=data.states;index=data.start_index;render()}catch(e){setStatus(e.message)}}
function current(){return items[index]}
function labelMap(item){const labels={};for(const id of item.yes_ids)labels[id]='yes';for(const id of item.no_ids)labels[id]='no';for(const id of item.unknown_ids)labels[id]='unknown';Object.assign(labels,states[item.review_id]?.labels||{});return labels}
function renderInfo(item){
 const state=states[item.review_id]||{},predicate=item.predicate||{},labels=labelMap(item);
 const values=Object.values(labels);
 const counts={yes:values.filter(x=>x==='yes').length,no:values.filter(x=>x==='no').length,unknown:values.filter(x=>x==='unknown').length};
 document.querySelector('#category').textContent=item.category||'—';
 document.querySelector('#caption').textContent=item.caption||'—';
 document.querySelector('#predicate').textContent=predicate.display||predicate.predicate_id||'—';
 document.querySelector('#counts').textContent=`正样本 yes：${counts.yes} · 负样本 no：${counts.no} · 未知：${counts.unknown}`;
 const reviewState=document.querySelector('#review-state');
 const reviewed=state.reviewed===true||state.discard===true||Object.keys(state.labels||{}).length>0;
 reviewState.dataset.reviewed=String(reviewed);
 reviewState.dataset.discarded=String(state.discard===true);
 reviewState.textContent=!reviewed?'⚠ 未审核 · 默认保留':state.discard===true?'✕ 已审核 · 已废弃':'✓ 已审核 · 保留';
 document.querySelector('#selection-info').textContent=`已框选 ${selected.size} 个实例点`;
 document.querySelector('#meta').textContent=JSON.stringify({review_id:item.review_id,predicate,counts,gates:item.gates||{},state,selected_points:selected.size},null,2);
}
function render(){const item=current();if(!item)return;selected.clear();document.querySelector('#progress').textContent=`子集 ${index+1}/${items.length}`;document.querySelector('#image-index').textContent=`image_idx=${item.image_idx} · ${item.image_name}`;
const state=states[item.review_id]||{};renderInfo(item);
img.onload=()=>{canvas.width=img.naturalWidth;canvas.height=img.naturalHeight;draw()};img.onerror=()=>setStatus('图片加载失败：'+item.image_name);img.src='/image/'+encodeURIComponent(item.review_id)+'?v='+Date.now();setStatus(state.discard?'已保存：该子集已废弃':'未废弃即默认保留；状态自动保存')}
function points(item){return [...item.yes_ids.map((id,i)=>({id,point:item.yes_points[i]})),...item.no_ids.map((id,i)=>({id,point:item.no_points[i]})),...item.unknown_ids.map((id,i)=>({id,point:item.unknown_points[i]}))]}
function draw(){
 const item=current();if(!item||!img.complete)return;
 ctx.clearRect(0,0,canvas.width,canvas.height);ctx.drawImage(img,0,0);const labels=labelMap(item);
 for(const {id,point} of points(item)){
  if(!point)continue;const [x,y]=point,yes=labels[id]==='yes',isSelected=selected.has(String(id));
  if(isSelected){ctx.beginPath();ctx.arc(x,y,12,0,Math.PI*2);ctx.lineWidth=3;ctx.strokeStyle='#ffd400';ctx.stroke()}
  ctx.beginPath();ctx.lineWidth=8;ctx.strokeStyle='white';ctx.fillStyle=yes?'#b51f32':'#199e70';
  if(yes){ctx.moveTo(x-7,y-7);ctx.lineTo(x+7,y+7);ctx.moveTo(x-7,y+7);ctx.lineTo(x+7,y-7);ctx.stroke();ctx.lineWidth=4;ctx.strokeStyle='#b51f32';ctx.beginPath();ctx.moveTo(x-7,y-7);ctx.lineTo(x+7,y+7);ctx.moveTo(x-7,y+7);ctx.lineTo(x+7,y-7);ctx.stroke()}
  else{ctx.arc(x,y,6,0,Math.PI*2);ctx.fill();ctx.lineWidth=3;ctx.strokeStyle='white';ctx.stroke()}
 }
 if(drag){ctx.fillStyle='rgba(255,215,0,.18)';ctx.strokeStyle='#d5a800';ctx.lineWidth=2;ctx.fillRect(drag.x,drag.y,drag.w,drag.h);ctx.strokeRect(drag.x,drag.y,drag.w,drag.h)}
}
function canvasPoint(e){const r=canvas.getBoundingClientRect();return{x:(e.clientX-r.left)*canvas.width/r.width,y:(e.clientY-r.top)*canvas.height/r.height}}
canvas.addEventListener('pointerdown',e=>{const p=canvasPoint(e);drag={x:p.x,y:p.y,w:0,h:0};canvas.setPointerCapture(e.pointerId);draw()});
canvas.addEventListener('pointermove',e=>{if(!drag)return;const p=canvasPoint(e);drag.w=p.x-drag.x;drag.h=p.y-drag.y;draw()});
canvas.addEventListener('pointerup',async()=>{if(!drag)return;const x1=Math.min(drag.x,drag.x+drag.w),x2=Math.max(drag.x,drag.x+drag.w),y1=Math.min(drag.y,drag.y+drag.h),y2=Math.max(drag.y,drag.y+drag.h);selected.clear();for(const {id,point} of points(current()))if(point&&point[0]>=x1&&point[0]<=x2&&point[1]>=y1&&point[1]<=y2)selected.add(String(id));drag=null;draw();renderInfo(current());if(selected.size){await flipSelected()}else setStatus('没有框选到实例点')});
async function saveState(patch){const item=current();const next={...(states[item.review_id]||{}),...patch,review_id:item.review_id,reviewed:true};states[item.review_id]=await request('/api/state',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(next)});renderInfo(item);draw()}
async function flipSelected(){const item=current();if(!selected.size)return setStatus('没有框选点');const labels=labelMap(item);let changed=0;for(const id of selected){if(labels[id]==='yes'){labels[id]='no';changed++}else if(labels[id]==='no'){labels[id]='yes';changed++}}if(!changed)return setStatus(`已框选 ${selected.size} 个点，但没有可翻转的 yes/no 标签`);const changes={};for(const id of selected)if(labels[id]==='yes'||labels[id]==='no')changes[id]=labels[id];await saveState({labels:{...(states[item.review_id]?.labels||{}),...changes}});setStatus(`框选后已自动翻转并保存 ${changed} 个 yes/no 标签`)}
async function setSelectedLabel(label){const item=current();if(!selected.size)return setStatus('没有框选点');const changes={};for(const id of selected)changes[id]=label;await saveState({labels:{...(states[item.review_id]?.labels||{}),...changes}});setStatus(`已将 ${selected.size} 个框选点全部设为 ${label} 并保存`)}
async function discardCurrent(){await saveState({discard:true});setStatus('已废弃并保存');next()}
async function restoreCurrent(){await saveState({discard:false});setStatus('已恢复为默认保留')}
function next(){if(index<items.length-1){index++;render()}else setStatus('已到最后一个子集')};function previous(){if(index>0){index--;render()}}
async function finalize(){try{const result=await request('/api/finalize',{method:'POST'});setStatus(`最终子集已保存：保留 ${result.kept}，废弃 ${result.discarded}`)}catch(e){setStatus(e.message)}}
function setStatus(text){document.querySelector('#status').textContent=text}
document.addEventListener('keydown',e=>{if(['INPUT','TEXTAREA'].includes(document.activeElement.tagName))return;if(e.key==='ArrowLeft'||e.key==='a'||e.key==='A')previous();else if(e.key==='ArrowRight'||e.key==='d'||e.key==='D')next();else if(e.key==='w'||e.key==='W')setSelectedLabel('yes');else if(e.key==='s'||e.key==='S')setSelectedLabel('no');else if(e.key===' '){e.preventDefault();discardCurrent()}});load();
</script>"""


class ReviewHandler(http.server.BaseHTTPRequestHandler):
    server_version = "RECReview/2.0"

    @property
    def state(self) -> dict[str, Any]:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:
        return

    def send_bytes(self, payload: bytes, content_type: str, status: int = 200) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # The review page may close/reload after a successful finalize response;
            # a disconnected browser must not turn that completed write into a traceback.
            return

    def send_json(self, value: Any, status: int = 200) -> None:
        self.send_bytes(json.dumps(value, ensure_ascii=False).encode("utf-8"),
                        "application/json; charset=utf-8", status)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self.send_bytes(_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif parsed.path == "/api/items":
            self.send_json({"items": self.state["items"], "states": self.state["states"],
                            "start_index": self.state["start_index"]})
        elif parsed.path.startswith("/image/"):
            review_id = urllib.parse.unquote(parsed.path[len("/image/"):])
            item = self.state["items_by_id"].get(review_id)
            if item is None:
                self.send_bytes(b"image not found", "text/plain", 404)
                return
            image_dir = Path(self.state["image_dir"]).resolve()
            target = (image_dir / Path(item["image_name"]).name).resolve()
            if image_dir not in target.parents or not target.is_file():
                self.send_bytes(b"image not found", "text/plain", 404)
                return
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self.send_bytes(target.read_bytes(), content_type)
        else:
            self.send_bytes(b"not found", "text/plain", 404)

    def do_POST(self) -> None:  # noqa: N802
        route = urllib.parse.urlparse(self.path).path
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 64_000:
                raise ValueError("invalid request size")
            payload = json.loads(self.rfile.read(size))
            if route == "/api/state":
                review_id = str(payload.get("review_id") or "")
                if review_id not in self.state["item_ids"]:
                    raise ValueError("未知 review_id")
                item = self.state["items_by_id"][review_id]
                valid_ids = set(map(str, [*item["yes_ids"], *item["no_ids"], *item["unknown_ids"]]))
                labels = payload.get("labels", {})
                if not isinstance(labels, dict) or any(
                        str(key) not in valid_ids or value not in {"yes", "no"}
                        for key, value in labels.items()):
                    raise ValueError("labels 仅能将该子集的 yes/no/unknown 实例设置为 yes 或 no")
                row = {"review_id": review_id, "discard": payload.get("discard") is True,
                       "reviewed": payload.get("reviewed") is True,
                       "labels": {str(key): value for key, value in labels.items()}}
                with self.state["lock"]:
                    append_jsonl(self.state["state_path"], row, fsync=True)
                    self.state["states"][review_id] = row
                self.send_json(row)
            elif route == "/api/finalize":
                report = finalize_items(self.state["items"], self.state["states"],
                                        self.state["final_output"])
                self.send_json(report)
            else:
                self.send_bytes(b"not found", "text/plain", 404)
        except Exception as error:
            self.send_bytes(str(error).encode("utf-8"), "text/plain; charset=utf-8", 400)


def start_index_for(items: Sequence[Mapping[str, Any]], image_idx: int) -> int:
    if image_idx < 0:
        raise ValueError("--image-idx 必须是非负整数")
    for index, item in enumerate(items):
        if int(item["image_idx"]) >= image_idx:
            return index
    raise ValueError(f"image_idx={image_idx} 超出可审核图片范围")


def serve(items: Sequence[dict], image_dir: Path, state_path: Path,
          final_output: Path, host: str, port: int, image_idx: int) -> None:
    start_index = start_index_for(items, image_idx)
    states = load_review_state(state_path)
    items = list(items)
    state = {
        "items": items, "item_ids": {item["review_id"] for item in items},
        "items_by_id": {item["review_id"]: item for item in items},
        "states": states, "state_path": state_path, "final_output": final_output,
        "image_dir": image_dir, "start_index": start_index, "lock": threading.Lock(),
    }

    class LocalReviewServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True

    server = LocalReviewServer((host, port), ReviewHandler)
    server.state = state  # type: ignore[attr-defined]
    url = f"http://{host}:{port}/"
    print(f"✅ 单人本地审核：{url}")
    print(f"   起始 image_idx={image_idx}，共 {len(items)} 个子集；状态 → {state_path}")
    try:
        import webbrowser
        webbrowser.open(url)
        server.serve_forever()
    except KeyboardInterrupt:
        print("审核已停止")
    finally:
        server.server_close()
        report = finalize_items(items, states, final_output)
        print(f"✅ 最终子集已保存：保留 {report['kept']}，废弃 {report['discarded']} → {final_output}")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="REC-8K step8 本地单人子集审核")
    parser.add_argument("--subsets", type=Path, default=path("subsets"))
    parser.add_argument("--instances", type=Path, default=path("instance_data"),
                        help="step1 实例表，用于恢复 source image_idx")
    parser.add_argument("--review-dir", type=Path, default=resolve_dir(CONFIG["paths"]["review_dir"]))
    parser.add_argument("--build", action="store_true", help="只生成 review_items.jsonl + review_sheet.csv")
    parser.add_argument("--serve", action="store_true", help="使用已有审核条目启动本地浏览器")
    parser.add_argument("--image-idx", type=int, default=0,
                        help="从此 source image_idx 开始（step1 从 0 编号；默认 0）")
    parser.add_argument("--image-dir", type=Path, default=Path(CONFIG["image_dir"]))
    parser.add_argument("--port", type=int, default=CONFIG["review"]["port"])
    parser.add_argument("--items", type=Path, default=None,
                        help="审核条目输入；默认 review/review_items.jsonl")
    parser.add_argument("--state", type=Path, default=None,
                        help="单人审核状态 JSONL；默认 review/review_state.jsonl")
    parser.add_argument("--final-output", type=Path, default=path("subsets_final"))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    configure_console()
    args = parse_args(argv)
    args.review_dir.mkdir(parents=True, exist_ok=True)
    items_path = args.items or args.review_dir / "review_items.jsonl"
    state_path = args.state or args.review_dir / "review_state.jsonl"
    try:
        should_build = args.build or not args.serve
        if should_build:
            if not args.subsets.is_file():
                raise FileNotFoundError(f"找不到 subsets：{args.subsets}")
            items = build_review_items(args.subsets, args.instances)
            items_path, csv_path = write_review_artifacts(items, args.review_dir)
            print(f"✅ 审核条目 {len(items)} 条 → {items_path}")
            print(f"   CSV → {csv_path}")
        if args.build and not args.serve:
            return 0
        if not items_path.is_file():
            raise FileNotFoundError(f"找不到审核条目：{items_path}，先移除 --serve 重新构建")
        items = list(iter_jsonl(items_path)) if not should_build else items
        serve(items, args.image_dir, state_path, args.final_output,
              "127.0.0.1", args.port, args.image_idx)
        return 0
    except KeyboardInterrupt:
        print("审核已停止")
        return 130
    except Exception as error:
        print(f"❌ 审核工具失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
