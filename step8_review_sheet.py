"""step8：REC-8K 本地单人浏览器审核工具。

从 step6 的正向子集生成审核条目，并在 loopback 浏览器界面展示原图与实例点。
可按 source ``image_idx`` 左闭右开区间审核，拖框选择点并翻转 yes/no。
审核状态实时保存在单个 JSONL 文件中，支持确认保留、废弃、不确定及跳过已审核。
仅保存经过审核操作的子集结果；同一子集保留最新记录，unknown 留待二次审核。

    python step8_review_sheet.py --start 1000 --end 2000
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
from recsub.io import iter_jsonl, write_jsonl_atomic
from recsub.review_tasks import (
    claim_task_outputs, load_and_validate_task, prepare_review_tasks, validate_task_state,
)
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
    for node in load_subset_nodes(subsets_path):
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
REVIEW_STATUSES = frozenset({"pending", "keep", "discard", "unknown"})


def review_status(state: Mapping[str, Any]) -> str:
    """解析审核状态：优先使用显式 status，缺失时由 discard/reviewed/labels 推断。"""
    status = state.get("status")
    if isinstance(status, str) and status in REVIEW_STATUSES:
        return status
    if state.get("discard") is True:
        return "discard"
    if state.get("reviewed") is True or state.get("labels"):
        return "keep"
    return "pending"


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


def save_review_state(state_path: Path, states: dict[str, dict[str, Any]]) -> None:
    """Atomically save one latest operated row per subset, then commit the snapshot in memory."""
    saved = {review_id: dict(row, review_id=review_id) for review_id, row in states.items()
             if review_status(row) != "pending"}
    write_jsonl_atomic(state_path, saved.values())
    states.clear()
    states.update(saved)


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
    """Export unique operated subsets using their latest state; exclude pending/discard."""
    unique_items = {str(item["review_id"]): item for item in items}
    final = []
    discarded = unreviewed = 0
    for review_id, item in unique_items.items():
        state = states.get(review_id, {})
        status = review_status(state)
        if status == "pending":
            unreviewed += 1
            continue
        if status == "discard":
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
            "status": status, "reviewed": status == "keep",
            "defaulted": status == "keep" and state.get("defaulted") is True,
            "discarded": False,
            "label_overrides": dict(state.get("labels") or {}),
            "flipped_instance_ids": sorted(int(key) for key in (state.get("labels") or {})),
        }
        final.append(final_item)
    write_jsonl_atomic(output_path, final)
    return {"items": len(unique_items), "kept": len(final), "discarded": discarded, "unreviewed": unreviewed}


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
#review-state{display:block;border:2px solid #777;border-radius:9px;padding:14px 16px;margin:16px 0 12px;background:#f0f0ec;color:#444;font-size:24px;font-weight:800;line-height:1.35}
#review-state[data-status="pending"]{border-style:dashed}
#review-state[data-status="keep"]{border-color:#16805f;background:#e9f7f0;color:#075c43}
#review-state[data-status="discard"]{border-color:#c92a2a;background:#fff0f0;color:#a11}
#review-state[data-status="unknown"]{border-color:#194e96;background:#f2f6fd;color:#194e96}
#counts,#selection-info{font-size:18px;line-height:1.6;margin:6px 0}
#controls{border-top:1px solid #ddd;margin-top:16px;padding-top:12px}
#controls h2{font-size:20px;margin:0 0 8px}
button{font-size:16px;padding:9px 12px;margin:3px;border:1px solid #aaa;border-radius:6px;background:#fff;cursor:pointer}
button:hover{background:#eef}.discard{border-color:#c92a2a;color:#a11}.flip{border-color:#087f5b;color:#075c43}.uncertain{border-color:#194e96;color:#194e96}
#meta{white-space:pre-wrap;overflow-wrap:anywhere;background:#f0f0ec;padding:10px;max-height:35vh;overflow:auto;font-size:13px}
details{margin-top:18px}summary{cursor:pointer;color:#555}
.small{color:#555;font-size:14px}#status{min-height:1.5em;color:#075c43}
@media(max-width:900px){#layout{grid-template-columns:1fr}.info-card p{font-size:28px}}
</style>
<header><b>REC-8K 子集审核</b> <span id="review-range"></span> <span id="progress"></span> <span id="image-index"></span></header>
<div id="layout"><div id="stage"><canvas id="canvas"></canvas></div>
<aside id="info" aria-label="当前子集信息">
<section class="info-card category-card"><h2>原始类别</h2><p id="category">—</p></section>
<section class="info-card caption-card"><h2>原始 caption</h2><p id="caption">—</p></section>
<section class="info-card predicate-card"><h2>新增划分属性</h2><p id="predicate">—</p></section>
<span id="review-state" role="status" aria-live="polite"></span>
<p id="counts"></p><p id="selection-info">已框选 0 个实例点</p>
<p class="small">红叉 = 当前子集正样本；绿点 = 同根其余实例（包括 unknown，标签不会因此变成 no）。</p>
<div id="controls"><h2>审核操作</h2>
<div><button class="flip" onclick="restoreCurrent()">确认保留</button>
<button class="uncertain" onclick="unknownCurrent()">不确定（unknown）</button>
<button onclick="skipReviewed()">跳过已审核</button>
<button class="discard" onclick="discardCurrent()">空格：废弃此子集并下一条</button></div>
<div><button class="flip" onclick="flipSelected()">手动翻转所选 yes/no</button>
<button onclick="setSelectedLabel('yes')">将框选点全部设为 yes</button>
<button onclick="setSelectedLabel('no')">将框选点全部设为 no</button></div>
<p class="small">← / A：上一子集；→ / D：下一子集，并将当前未审核条目保存为“已审核 · 默认保留”；空格：废弃并下一条。“确认保留”表示明确保留；“不确定”保存为 unknown、留待二次审核。已有保留、废弃或 unknown 不会因下一条而改变。“跳过已审核”从下一条起跳过保留/废弃，停在 unknown 或未审核条目，不越过本次区间。拖框结束会自动翻转所选 yes/no 一次；如果还要再次切换，可点“手动翻转所选 yes/no”。W：框选点全设 yes；S：框选点全设 no（包括点级 unknown）。</p>
<button onclick="finalize()">保存最终子集</button><p id="status" role="status"></p></div>
<details><summary>查看技术详情（JSON）</summary><pre id="meta"></pre></details>
</aside></div>
<script>
let items=[],states={},index=0,selected=new Set(),drag=null,img=new Image(),ctx,saving=false;
const reviewTitles={pending:'○ 未审核 · 不导出结果',keep:'✓ 已审核 · 已确认保留',discard:'✕ 已审核 · 已废弃',unknown:'？ 不确定 · 待二次审核'};
const reviewHints={pending:'尚未完成审核，不会写入结果；按 D / → 默认保留并前进，也可明确保留、标记不确定或废弃',keep:'已保存：已明确确认保留',discard:'已保存：该子集已废弃',unknown:'已保存：unknown，等待二次审核；修改点标签不会取消此状态'};
const canvas=document.querySelector('#canvas');ctx=canvas.getContext('2d');
async function request(url,options){const r=await fetch(url,options);if(!r.ok)throw Error(await r.text());return r.json()}
async function load(){try{const data=await request('/api/items');items=data.items;states=data.states;index=data.start_index;const range=data.review_range||{start:0,end:null};document.querySelector('#review-range').textContent=`审核区间 [${range.start}, ${range.end??'不限'})`;render()}catch(e){setStatus(e.message)}}
function current(){return items[index]}
function reviewStatus(state={}){if(['pending','keep','discard','unknown'].includes(state.status))return state.status;if(state.discard===true)return 'discard';if(state.reviewed===true||Object.keys(state.labels||{}).length>0)return 'keep';return 'pending'}
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
 const status=reviewStatus(state);
 reviewState.dataset.status=status;
 reviewState.dataset.reviewed=String(status==='keep'||status==='discard');
 reviewState.dataset.discarded=String(status==='discard');
 const defaulted=status==='keep'&&state.defaulted===true;
 reviewState.textContent=defaulted?'✓ 已审核 · 默认保留':reviewTitles[status];
 document.querySelector('#selection-info').textContent=`已框选 ${selected.size} 个实例点`;
 document.querySelector('#meta').textContent=JSON.stringify({review_id:item.review_id,predicate,counts,gates:item.gates||{},state,selected_points:selected.size},null,2);
}
function render(){const item=current();if(!item)return;selected.clear();document.querySelector('#progress').textContent=`子集 ${index+1}/${items.length}`;document.querySelector('#image-index').textContent=`image_idx=${item.image_idx} · ${item.image_name}`;
const state=states[item.review_id]||{};renderInfo(item);
img.onload=()=>{canvas.width=img.naturalWidth;canvas.height=img.naturalHeight;draw()};img.onerror=()=>setStatus('图片加载失败：'+item.image_name);img.src='/image/'+encodeURIComponent(item.review_id)+'?v='+Date.now();setStatus(reviewStatus(state)==='keep'&&state.defaulted===true?'已保存：已审核，按下一张默认保留':reviewHints[reviewStatus(state)])}
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
async function saveState(patch){
 if(saving){setStatus('正在保存，请稍候');return false}
 const item=current(),previous=states[item.review_id]||{};
 let status=patch.status??reviewStatus(previous);
 if(patch.status===undefined&&patch.labels&&status==='pending')status='keep';
 const next={...previous,...patch,review_id:item.review_id,status,discard:status==='discard',reviewed:status==='keep'||status==='discard',defaulted:status==='keep'&&patch.defaulted===true};
 saving=true;
 try{states[item.review_id]=await request('/api/state',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(next)});renderInfo(item);draw();return true}
 catch(e){setStatus('保存失败：'+e.message);return false}
 finally{saving=false}
}
async function flipSelected(){const item=current();if(!selected.size)return setStatus('没有框选点');const labels=labelMap(item);let changed=0;for(const id of selected){if(labels[id]==='yes'){labels[id]='no';changed++}else if(labels[id]==='no'){labels[id]='yes';changed++}}if(!changed)return setStatus(`已框选 ${selected.size} 个点，但没有可翻转的 yes/no 标签`);const changes={};for(const id of selected)if(labels[id]==='yes'||labels[id]==='no')changes[id]=labels[id];if(await saveState({labels:{...(states[item.review_id]?.labels||{}),...changes}}))setStatus(`框选后已自动翻转并保存 ${changed} 个 yes/no 标签`)}
async function setSelectedLabel(label){const item=current();if(!selected.size)return setStatus('没有框选点');const changes={};for(const id of selected)changes[id]=label;if(await saveState({labels:{...(states[item.review_id]?.labels||{}),...changes}}))setStatus(`已将 ${selected.size} 个框选点全部设为 ${label} 并保存`)}
async function discardCurrent(){if(await saveState({status:'discard'})){setStatus('已废弃并保存');await next()}}
async function restoreCurrent(){if(await saveState({status:'keep'}))setStatus('已明确确认保留并保存')}
async function unknownCurrent(){if(await saveState({status:'unknown'}))setStatus('已保存为 unknown，等待二次审核')}
async function next(){
 if(saving)return setStatus('正在保存，请稍候');
 const item=current();if(!item)return;
 if(reviewStatus(states[item.review_id])==='pending'&&!await saveState({status:'keep',defaulted:true}))return;
 if(index<items.length-1){index++;render()}else setStatus('已到本次区间最后一个子集');
}
function previous(){if(saving)return setStatus('正在保存，请稍候');if(index>0){index--;render()}else setStatus('已到本次区间第一个子集')}
function skipReviewed(){
 if(saving)return setStatus('正在保存，请稍候');
 for(let candidate=index+1;candidate<items.length;candidate++){
  if(['keep','discard'].includes(reviewStatus(states[items[candidate].review_id])))continue;
  index=candidate;render();return;
 }
 setStatus('本次区间内没有后续 unknown 或未审核子集，已到区间末尾');
}
async function finalize(){if(saving)return setStatus('正在保存，请稍候');try{const result=await request('/api/finalize',{method:'POST'});setStatus(`最终子集已保存：已操作保留 ${result.kept}（含 unknown），废弃 ${result.discarded}，未审核未导出 ${result.unreviewed}`)}catch(e){setStatus(e.message)}}
function setStatus(text){document.querySelector('#status').textContent=text}
document.addEventListener('keydown',async e=>{if(['INPUT','TEXTAREA'].includes(document.activeElement.tagName))return;if(e.key==='ArrowLeft'||e.key==='a'||e.key==='A')previous();else if(e.key==='ArrowRight'||e.key==='d'||e.key==='D'){e.preventDefault();await next()}else if(e.key==='w'||e.key==='W')setSelectedLabel('yes');else if(e.key==='s'||e.key==='S')setSelectedLabel('no');else if(e.key===' '){e.preventDefault();await discardCurrent()}});load();
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
            with self.state["lock"]:
                states = {key: value for key, value in self.state["states"].items()
                          if key in self.state["item_ids"]}
            self.send_json({"items": self.state["items"], "states": states,
                            "start_index": self.state["start_index"],
                            "review_range": self.state["review_range"]})
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
            if route == "/api/finalize":
                with self.state["lock"]:
                    save_review_state(self.state["state_path"], self.state["states"])
                    report = finalize_items(self.state["all_items"], self.state["states"],
                                            self.state["final_output"])
                self.send_json(report)
                return
            if route != "/api/state":
                self.send_bytes(b"not found", "text/plain", 404)
                return
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 64_000:
                raise ValueError("invalid request size")
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError("审核状态必须是 JSON 对象")
            review_id = str(payload.get("review_id") or "")
            if review_id not in self.state["item_ids"]:
                raise ValueError("未知或不在本次审核区间内的 review_id")
            if "status" in payload and (
                    not isinstance(payload["status"], str) or payload["status"] not in REVIEW_STATUSES):
                raise ValueError("status 仅能为 pending、keep、discard 或 unknown")
            status = review_status(payload)
            item = self.state["items_by_id"][review_id]
            valid_ids = set(map(str, [*item["yes_ids"], *item["no_ids"], *item["unknown_ids"]]))
            labels = payload.get("labels", {})
            if not isinstance(labels, dict) or any(
                    str(key) not in valid_ids or value not in {"yes", "no"}
                    for key, value in labels.items()):
                raise ValueError("labels 仅能将该子集的 yes/no/unknown 实例设置为 yes 或 no")
            # status 是权威字段；同时写入 discard/reviewed，使缺少 status 的行
            # 也能按同一规则推断状态。
            row = {"review_id": review_id, "status": status, "discard": status == "discard",
                   "reviewed": status in {"keep", "discard"},
                   "defaulted": status == "pending" or (status == "keep" and payload.get("defaulted") is True),
                   "labels": {str(key): value for key, value in labels.items()}}
            with self.state["lock"]:
                updated = dict(self.state["states"])
                updated[review_id] = row
                save_review_state(self.state["state_path"], updated)
                self.state["states"].clear()
                self.state["states"].update(updated)
            self.send_json(row)
        except Exception as error:
            self.send_bytes(str(error).encode("utf-8"), "text/plain; charset=utf-8", 400)


def start_index_for(items: Sequence[Mapping[str, Any]], image_idx: int) -> int:
    if image_idx < 0:
        raise ValueError("--image-idx 必须是非负整数")
    for index, item in enumerate(items):
        if int(item["image_idx"]) >= image_idx:
            return index
    raise ValueError(f"image_idx={image_idx} 超出可审核图片范围")


def validate_review_range(start: int, end: int | None, image_idx: int | None = None) -> None:
    if start < 0:
        raise ValueError("--start 必须是非负整数")
    if end is not None and end <= start:
        raise ValueError("--end 必须大于 --start（审核区间左闭右开）")
    if image_idx is not None and (image_idx < start or (end is not None and image_idx >= end)):
        raise ValueError("--image-idx 必须位于本次审核区间内")


def select_review_items(items: Sequence[dict], start: int = 0, end: int | None = None) -> list[dict]:
    """Select every subset in the half-open source image range, including sibling subsets."""
    validate_review_range(start, end)
    selected = [item for item in items
                if int(item["image_idx"]) >= start and (end is None or int(item["image_idx"]) < end)]
    if not selected:
        upper = "不限" if end is None else str(end)
        raise ValueError(f"审核区间 [{start}, {upper}) 内没有可审核子集")
    return sorted(selected, key=lambda item: (
        int(item["image_idx"]), str(item.get("caption", "")), str(item["review_id"])))


def serve(items: Sequence[dict], image_dir: Path, state_path: Path,
          final_output: Path, host: str, port: int, image_idx: int | None = None,
          start: int = 0, end: int | None = None,
          task_manifest: Mapping[str, Any] | None = None) -> None:
    validate_review_range(start, end, image_idx)
    all_items = list(items)
    if task_manifest is not None:
        manifest_start, manifest_end = int(task_manifest["start"]), int(task_manifest["end"])
        if (start, end) != (manifest_start, manifest_end):
            raise ValueError("审核任务区间与 manifest 不一致")
    items = select_review_items(all_items, start, end)
    if task_manifest is not None:
        validate_task_state(load_review_state(state_path), items)
    image_idx = start if image_idx is None else image_idx
    start_index = start_index_for(items, image_idx)
    states = load_review_state(state_path)
    state = {
        "items": items, "all_items": all_items, "item_ids": {item["review_id"] for item in items},
        "items_by_id": {item["review_id"]: item for item in items},
        "states": states, "state_path": state_path, "final_output": final_output,
        "image_dir": image_dir, "start_index": start_index, "lock": threading.Lock(),
        "review_range": {"start": start, "end": end},
        "task_manifest": dict(task_manifest) if task_manifest is not None else None,
    }

    class LocalReviewServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True

    server = LocalReviewServer((host, port), ReviewHandler)
    server.state = state  # type: ignore[attr-defined]
    url = f"http://{host}:{port}/"
    upper = "不限" if end is None else str(end)
    print(f"✅ 单人本地审核：{url}")
    print(f"   审核区间 [{start}, {upper})，起始 image_idx={items[start_index]['image_idx']}，"
          f"共 {len(items)} 个子集；状态 → {state_path}")
    try:
        import webbrowser
        webbrowser.open(url)
        server.serve_forever()
    except KeyboardInterrupt:
        print("审核已停止")
    finally:
        server.server_close()
        with state["lock"]:
            save_review_state(state_path, states)
            report = finalize_items(all_items, states, final_output)
        print(f"✅ 最终子集已保存：保留 {report['kept']}（含 unknown），废弃 {report['discarded']}，"
              f"未审核未导出 {report['unreviewed']} → {final_output}")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="REC-8K step8 本地单人子集审核")
    parser.add_argument("--subsets", type=Path, default=path("subsets"))
    parser.add_argument("--instances", type=Path, default=path("instance_data"),
                        help="step1 实例表，用于恢复 source image_idx")
    parser.add_argument("--prepare-tasks", type=int, default=None,
                        help="一次生成多个冻结审核任务目录（指定任务数）")
    parser.add_argument("--images-per-task", type=int, default=1000,
                        help="每个任务分配的 image_idx 数量（默认 1000）")
    parser.add_argument("--review-dir", type=Path, default=resolve_dir(CONFIG["paths"]["review_dir"]))
    parser.add_argument("--build", action="store_true", help="只生成 review_items.jsonl + review_sheet.csv")
    parser.add_argument("--serve", action="store_true", help="使用已有审核条目启动本地浏览器")
    parser.add_argument("--start", type=int, default=None, help="审核区间起点 image_idx（含，默认 0）")
    parser.add_argument("--end", type=int, default=None, help="审核区间终点 image_idx（不含，默认不限）")
    parser.add_argument("--image-idx", type=int, default=None,
                        help="初始 source image_idx（默认区间起点；不改变审核范围）")
    parser.add_argument("--image-dir", type=Path, default=Path(CONFIG["image_dir"]))
    parser.add_argument("--port", type=int, default=CONFIG["review"]["port"])
    parser.add_argument("--items", type=Path, default=None,
                        help="审核条目输入；默认 review/review_items.jsonl")
    parser.add_argument("--state", type=Path, default=None,
                        help="单人审核状态 JSONL；默认 review/review_state.jsonl")
    parser.add_argument("--final-output", type=Path, default=None,
                        help="最终结果 JSONL；任务模式默认写入任务目录 final_subsets.jsonl")
    args = parser.parse_args(argv)
    if args.prepare_tasks is not None:
        if args.start is None:
            args.start = 0
        if args.prepare_tasks <= 0 or args.images_per_task <= 0:
            parser.error("--prepare-tasks 和 --images-per-task 必须是正整数")
        if args.end is not None and args.end != args.start + args.prepare_tasks * args.images_per_task:
            parser.error("准备任务时 --end 必须等于 start + prepare-tasks × images-per-task")
        return args
    try:
        validate_review_range(args.start or 0, args.end, args.image_idx)
    except ValueError as error:
        parser.error(str(error))
    return args


def main(argv: Sequence[str] | None = None) -> int:
    configure_console()
    args = parse_args(argv)
    if args.prepare_tasks is not None:
        if not args.subsets.is_file():
            raise FileNotFoundError(f"找不到 subsets：{args.subsets}")
        items = build_review_items(args.subsets, args.instances)
        batch = prepare_review_tasks(items, args.review_dir, args.prepare_tasks,
                                     args.images_per_task, write_review_artifacts,
                                     start=args.start)
        print(f"✅ 已生成 {batch['task_count']} 个冻结审核任务 → {args.review_dir}")
        for task in batch["tasks"]:
            print(f"   {task['task_id']}: image_idx=[{task['start']}, {task['end']})，"
                  f"{task['image_count']} 张图片、{task['item_count']} 个子集")
        return 0

    args.review_dir.mkdir(parents=True, exist_ok=True)
    task_manifest_path = args.review_dir / "review_manifest.json"
    task_manifest = None
    if task_manifest_path.is_file():
        if args.build or args.items is not None:
            raise ValueError("冻结审核任务不能使用 --build 或覆盖 --items")
        items, task_manifest, _ = load_and_validate_task(args.review_dir)
        manifest_start, manifest_end = int(task_manifest["start"]), int(task_manifest["end"])
        if args.start is not None or args.end is not None:
            if (args.start, args.end) != (manifest_start, manifest_end):
                raise ValueError("命令行区间与冻结审核任务 manifest 不一致")
        args.start, args.end = manifest_start, manifest_end
        items_path = args.review_dir / task_manifest.get("review_items", "review_items.jsonl")
        state_path = args.review_dir / task_manifest.get("state", "review_state.jsonl")
        final_output = args.review_dir / task_manifest.get("final_output", "final_subsets.jsonl")
        if args.state is not None and args.state != state_path:
            raise ValueError("冻结审核任务不能把 --state 写到任务目录外")
        if args.final_output is not None and args.final_output != final_output:
            raise ValueError("冻结审核任务不能把 --final-output 写到任务目录外")
        claim_task_outputs(args.review_dir, task_manifest,
                           select_review_items(items, manifest_start, manifest_end))
    else:
        args.start = args.start if args.start is not None else 0
        should_build = args.build or not args.serve
        items_path = args.items or args.review_dir / "review_items.jsonl"
        state_path = args.state or args.review_dir / "review_state.jsonl"
        final_output = args.final_output or path("subsets_final")
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

    try:
        serve(items, args.image_dir, state_path, final_output,
              CONFIG["review"]["host"], args.port, args.image_idx, args.start, args.end,
              task_manifest=task_manifest)
        return 0
    except KeyboardInterrupt:
        print("审核已停止")
        return 130
    except Exception as error:
        print(f"❌ 审核工具失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
