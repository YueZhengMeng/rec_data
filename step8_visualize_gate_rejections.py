"""Build an offline visual inspection page for reviewed G5/G4 gate rejections.

Uses step8's edited partitions, not the obsolete point arrays in task final files.
G4 panels show the exact retained predicate named in the rejection decision.
Does not edit review state, merged subsets, or the gate policy.

    python step8_visualize_gate_rejections.py
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
from collections import Counter
from pathlib import Path

from PIL import Image

from recsub.config import CONFIG, path, resolve_dir
from recsub.console import configure_console
from recsub.gates import jaccard
from recsub.io import iter_jsonl, write_json_atomic
from step8_merge_review_results import load_review_batch

PAGE = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>REC-8K · G5 / G4 人工审核结果查看</title>
<style>
:root{color-scheme:light;--surface:#fcfcfb;--page:#f9f9f7;--ink:#0b0b0b;--muted:#52514e;--border:#e1e0d9;--yes:#2a78d6;--no:#eb6834;--unknown:#1baf7a}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);font:15px/1.6 system-ui,"Microsoft YaHei",sans-serif}
header{position:sticky;top:0;z-index:10;background:var(--surface);border-bottom:1px solid var(--border);padding:12px 24px}h1{font-size:20px;margin:0 0 8px}button,select,input{font:inherit}button,select,input[type=search]{border:1px solid #aaa;border-radius:6px;background:var(--surface);color:var(--ink);padding:6px 10px}button{cursor:pointer}button:disabled{opacity:.4;cursor:default}.controls{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.controls input[type=search]{width:265px}.controls input[type=number]{width:70px;padding:5px}main{max-width:1920px;margin:auto;padding:20px 24px}h2{font-size:22px;margin:0 0 8px}p{margin:6px 0}.muted{color:var(--muted)}.meta{overflow-wrap:anywhere}.notice{padding:12px 16px;border:1px solid var(--border);background:var(--surface);border-radius:8px;margin:12px 0}.panels{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.panels.single{grid-template-columns:minmax(0,1fr);max-width:1350px}.panel{border:1px solid var(--border);border-radius:8px;background:var(--surface);overflow:hidden}.panel h3{font-size:18px;margin:0}.panel-head{padding:12px 16px;border-bottom:1px solid var(--border)}.predicate{font-size:24px;font-weight:700;line-height:1.35;margin:6px 0}.visual{overflow:auto;background:#eee;max-height:78vh}.visual svg{display:block;height:auto;width:100%;background:#eee}.legend{display:flex;gap:18px;flex-wrap:wrap;margin:12px 0}.legend span{display:inline-flex;align-items:center;gap:6px}.symbol{display:inline-block;font-size:22px;font-weight:900;line-height:1}.yes{color:var(--yes)}.no{color:var(--no)}.unknown{color:var(--unknown)}.point{cursor:help}.point:hover{filter:drop-shadow(0 0 3px black)}.hide-marks .point{display:none}.hide-diffs .diff{display:none}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;background:var(--surface)}th,td{padding:7px 10px;border-bottom:1px solid var(--border);text-align:left}th{position:sticky;top:0;background:var(--surface)}tr.diffrow{font-weight:700;background:#f0efec}details{margin:16px 0}summary{cursor:pointer;font-weight:600}.table-wrap{max-height:400px;overflow:auto;border:1px solid var(--border)}a{color:#184f95}code{font-size:12px}.empty{padding:45px}.pill{border:1px solid var(--border);border-radius:4px;padding:2px 7px}footer{margin:25px 0;color:var(--muted)}@media(max-width:850px){.panels{grid-template-columns:1fr}header,main{padding:12px}.predicate{font-size:21px}}
</style></head><body>
<header><h1>REC-8K · 审核后门禁复验查看 <span id="totals" class="muted"></span></h1>
<div class="controls"><select id="gate" aria-label="筛选门禁"><option value="G5_balance">G5 · 平衡度不足</option><option value="G4_subset_redundancy">G4 · 支持集高度重叠</option><option value="all">全部</option></select><input id="search" type="search" placeholder="图片 / caption / 属性 / ID / 分组" aria-label="搜索"><button id="prev">← 上一条</button><span id="progress"></span><input id="jump" type="number" min="1" aria-label="跳转序号"><button id="next">下一条 →</button><label><input id="marks" type="checkbox" checked>显示标记（F）</label><label><input id="diffs" type="checkbox" checked>框出正集差异</label><label>缩放 <select id="zoom"><option value="100">100%</option><option value="150">150%</option><option value="200">200%</option><option value="300">300%</option></select></label></div></header>
<main><div id="content"></div><footer>只读可视化，不修改审核结果。← / → 切换，F 隐藏/显示实例标记；点位悬停查看 ID 和标签。页面、图片和 CSV 均在本地，无外部资源。若需放大，使用缩放选项或打开原图。</footer></main>
<script id="payload" type="application/json">__PAYLOAD__</script>
<script>
const data=JSON.parse(document.querySelector('#payload').textContent),cases=data.cases;
const $=s=>document.querySelector(s),esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let filtered=[],index=0;
function labels(node){const result={};for(const [f,l] of [['instance_ids','yes'],['negative_instance_ids','no'],['unknown_instance_ids','unknown']])for(const id of node[f])result[id]=l;return result}
function counts(node){return {yes:node.instance_ids.length,no:node.negative_instance_ids.length,unknown:node.unknown_instance_ids.length}}
function svg(node,c,other){const map=labels(node),op=other?labels(other):{},r=Math.max(3,Math.min(c.width,c.height)*.008),stroke=r*.48;const points=Object.entries(c.points).map(([id,p])=>{const lab=map[id],x=p[0],y=p[1],diff=other&&((lab==='yes')!==(op[id]==='yes'));let mark;
if(lab==='yes')mark=`<path d="M${x-r},${y-r}l${2*r},${2*r}m0,${-2*r}l${-2*r},${2*r}" fill="none" stroke="var(--yes)" stroke-width="${stroke}"/>`;
else if(lab==='no')mark=`<circle cx="${x}" cy="${y}" r="${r*.72}" fill="var(--no)"/>`;
else mark=`<path d="M${x},${y-r}l${r},${r}l${-r},${r}l${-r},${-r}z" fill="var(--unknown)"/>`;
const square=diff?`<rect class="diff" x="${x-r*1.7}" y="${y-r*1.7}" width="${r*3.4}" height="${r*3.4}" fill="none" stroke="black" stroke-width="${stroke*.6}"/>`:'';
return `<g class="point" style="paint-order:stroke;stroke:white;stroke-width:${stroke*.7};stroke-linejoin:round"><title>instance_id=${id} · ${lab}${other?' · 对侧='+op[id]:''}</title>${square}${mark}</g>`}).join('');
return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${c.width} ${c.height}" role="img" aria-label="人工审核后三值实例分区"><image href="${esc(c.image)}" width="${c.width}" height="${c.height}"/>${points}</svg>`}
function panel(node,c,title,other){const n=counts(node),parent=n.yes+n.no+n.unknown;return `<section class="panel"><div class="panel-head"><h3>${esc(title)}</h3><p class="predicate">${esc(node.predicate.display)}</p><p>yes ${n.yes} · no ${n.no} · unknown ${n.unknown} · parent ${parent}</p><p class="muted">balance ${(Math.min(n.yes,n.no)/parent).toFixed(4)} · unknown ${(n.unknown/parent*100).toFixed(1)}%</p><code>${esc(node.predicate.predicate_id)}<br>${esc(node.review_id)}</code></div><div class="visual">${svg(node,c,other)}</div></section>`}
function render(){if(!filtered.length){$('#content').innerHTML='<p class="empty">没有匹配条目。</p>';$('#progress').textContent='0 / 0';return}const c=filtered[index],n=counts(c.rejected),parent=n.yes+n.no+n.unknown,isG4=c.gate==='G4_subset_redundancy',a=labels(c.rejected),b=c.retained?labels(c.retained):{};
$('#progress').textContent=`${index+1} / ${filtered.length}`;$('#jump').max=filtered.length;$('#jump').value=index+1;$('#prev').disabled=index===0;$('#next').disabled=index===filtered.length-1;
const why=isG4?`|A ∩ B| = ${c.intersection}，|A ∪ B| = ${c.union}；Jaccard = ${c.jaccard.toFixed(6)} &gt; ${c.thresholds.max_support_jaccard}。A 独有 ${c.only_rejected.length} 个，B 独有 ${c.only_retained.length} 个。黑方框标出两侧正集差异，不表示标签错误。`:`balance = min(${n.yes}, ${n.no}) / ${parent} = ${(Math.min(n.yes,n.no)/parent).toFixed(6)} &lt; ${c.thresholds.min_balance}。G1 已通过；小侧仍至少 ${Math.min(c.thresholds.min_support,c.thresholds.min_complement)} 个，但相对父集占比不足。`;
const rows=Object.keys(c.points).map(Number).sort((x,y)=>x-y).map(id=>{const diff=isG4&&((a[id]==='yes')!==(b[id]==='yes'));return `<tr class="${diff?'diffrow':''}"><td>${id}</td><td>${c.points[id].map(x=>Number(x).toFixed(2)).join(', ')}</td><td>${a[id]}</td>${isG4?`<td>${b[id]}</td><td>${diff?'正集归属不同':'—'}</td>`:''}</tr>`}).join('');
$('#content').innerHTML=`<h2>${isG4?'G4 · 支持集高度重叠':'G5 · 平衡度不足'} <span class="pill">${esc(c.task_id)}</span></h2><p class="meta">${esc(c.image_name)} · image_idx=${c.rejected.image_idx} · 类别 ${esc(c.rejected.category)}</p><p><b>原始 caption：</b>${esc(c.caption)}</p><div class="notice">${why}</div><div class="legend"><span><b class="symbol yes">×</b>yes · 正集</span><span><b class="symbol no">●</b>no · 明确负集</span><span><b class="symbol unknown">◆</b>unknown · 未知（不是 no）</span>${isG4?'<span><b class="symbol">□</b>两侧正集归属不同</span>':''}</div><div class="panels ${isG4?'':'single'}">${panel(c.rejected,c,isG4?'A · 被 G4 拒绝的子集':'被 G5 拒绝的子集',c.retained)}${isG4?panel(c.retained,c,'B · 决策日志中对应的保留子集',c.rejected):''}</div><p><a href="${esc(c.image)}" target="_blank" rel="noopener">打开原图</a> · <a href="cases.csv">下载全部 160 条清单（CSV）</a> · <a href="manifest.json">完整实例分区数据（JSON）</a></p><details><summary>查看实例标签表（${parent} 个；ID 是当前 caption 根内标注实例）</summary><div class="table-wrap"><table><thead><tr><th>instance_id</th><th>原图坐标 x, y</th><th>${isG4?'A 标签':'标签'}</th>${isG4?'<th>B 标签</th><th>差异</th>':''}</tr></thead><tbody>${rows}</tbody></table></div></details>`;
document.body.classList.toggle('hide-marks',!$('#marks').checked);document.body.classList.toggle('hide-diffs',!$('#diffs').checked);zoom();history.replaceState(null,'','#'+c.rejected.review_id)}
function zoom(){document.querySelectorAll('.visual svg').forEach(s=>{s.style.width=$('#zoom').value+'%';s.style.maxHeight=$('#zoom').value==='100'?'65vh':'none'})}
function filter(id){const q=$('#search').value.toLowerCase(),gate=$('#gate').value;filtered=cases.filter(c=>(gate==='all'||c.gate===gate)&&JSON.stringify([c.image_name,c.caption,c.task_id,c.rejected.review_id,c.rejected.predicate,c.retained?.predicate]).toLowerCase().includes(q));index=Math.max(0,filtered.findIndex(c=>c.rejected.review_id===id));render()}
$('#gate').onchange=()=>filter();$('#search').oninput=()=>filter();$('#prev').onclick=()=>{index=Math.max(0,index-1);render()};$('#next').onclick=()=>{index=Math.min(filtered.length-1,index+1);render()};$('#jump').onchange=()=>{index=Math.max(0,Math.min(filtered.length-1,Number($('#jump').value)-1));render()};$('#marks').onchange=()=>document.body.classList.toggle('hide-marks',!$('#marks').checked);$('#diffs').onchange=()=>document.body.classList.toggle('hide-diffs',!$('#diffs').checked);$('#zoom').onchange=zoom;
document.addEventListener('keydown',e=>{if(['INPUT','SELECT','TEXTAREA'].includes(e.target.tagName))return;if(e.key==='ArrowLeft')$('#prev').click();if(e.key==='ArrowRight')$('#next').click();if(e.key.toLowerCase()==='f'){$('#marks').checked=!$('#marks').checked;$('#marks').onchange()}});
$('#totals').textContent=`G5 ${data.counts.G5_balance} 条 / G4 ${data.counts.G4_subset_redundancy} 条`;
const initial=cases.find(c=>c.rejected.review_id===location.hash.slice(1));if(initial)$('#gate').value=initial.gate;filter(initial?.rejected.review_id);
</script></body></html>'''


def build_cases(review_dir: Path, decisions_path: Path, final_path: Path) -> list[dict]:
    decisions = [row for row in iter_jsonl(decisions_path)
                 if row.get("gate") in {"G5_balance", "G4_subset_redundancy"}]
    reviewed = {row["review_id"]: row for row in load_review_batch(review_dir)}
    retained = {row["review_id"]: row for row in iter_jsonl(final_path)}
    cases = []
    for decision in decisions:
        rid = decision["review_id"]
        node = reviewed[rid]
        if rid in retained:
            raise ValueError(f"拒绝记录 {rid} 同时出现在最终保留文件中")
        counts = {"parent": sum(len(node[field]) for field in (
            "instance_ids", "negative_instance_ids", "unknown_instance_ids")),
                  "yes": len(node["instance_ids"]), "no": len(node["negative_instance_ids"]),
                  "unknown": len(node["unknown_instance_ids"])}
        numbers = decision["counts"]
        if any(numbers.get(field, numbers.get({"yes": "support", "no": "complement"}.get(field))) != value
               for field, value in counts.items()):
            raise ValueError(f"{rid} 的拒绝日志与当前审核分区不一致，请先重新合并")
        points = {}
        for ids, coords in (("instance_ids", "yes_points"), ("negative_instance_ids", "no_points"),
                            ("unknown_instance_ids", "unknown_points")):
            points.update((str(i), p) for i, p in zip(node[ids], node[coords]))
        case = {"gate": decision["gate"], "task_id": decision["task_id"],
                "image_name": node["image_name"], "caption": node["caption"],
                "thresholds": decision["thresholds"], "rejected": node, "retained": None,
                "points": points}
        if decision["gate"] == "G4_subset_redundancy":
            match = re.search(r"与已接受的 (.+?) 支持集 Jaccard", decision["detail"])
            if not match:
                raise ValueError(f"无法解析 {rid} 的 G4 对比谓词")
            partners = [other for other in retained.values()
                        if other["image_name"] == node["image_name"] and other["caption"] == node["caption"]
                        and other["predicate"]["predicate_id"] == match[1]]
            if len(partners) != 1:
                raise ValueError(f"{rid} 找不到唯一保留对比子集：{match[1]}")
            partner = partners[0]
            a, b = set(node["instance_ids"]), set(partner["instance_ids"])
            score = jaccard(a, b)
            if score <= decision["thresholds"]["max_support_jaccard"]:
                raise ValueError(f"{rid} 的当前 G4 对比不再超过阈值")
            if set(points) != {str(i) for f in ("instance_ids", "negative_instance_ids", "unknown_instance_ids")
                               for i in partner[f]}:
                raise ValueError(f"{rid} 的对比父实例集合不一致")
            case.update(retained=partner, jaccard=score, intersection=len(a & b), union=len(a | b),
                        only_rejected=sorted(a - b), only_retained=sorted(b - a))
        else:
            balance = min(counts["yes"], counts["no"]) / counts["parent"]
            if balance >= decision["thresholds"]["min_balance"]:
                raise ValueError(f"{rid} 的当前 G5 balance 不再低于阈值")
        cases.append(case)
    return sorted(cases, key=lambda c: (0 if c["gate"] == "G5_balance" else 1,
                                        c["rejected"]["image_idx"], c["caption"], c["rejected"]["review_id"]))


def run(args: argparse.Namespace) -> int:
    output = resolve_dir(args.output_dir)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"可视化目录非空，请使用新目录：{output}")
    cases = build_cases(resolve_dir(args.review_dir), resolve_dir(args.decisions), resolve_dir(args.subsets))
    image_dir = resolve_dir(args.image_dir)
    for case in cases:
        source = image_dir / case["image_name"]
        if not source.is_file():
            raise FileNotFoundError(f"找不到图片：{source}")
        with Image.open(source) as image:
            case["width"], case["height"] = image.size
        case["image"] = "images/" + source.name
    output.mkdir(parents=True, exist_ok=True)
    (output / "images").mkdir(exist_ok=True)
    for name in sorted({case["image_name"] for case in cases}):
        shutil.copyfile(image_dir / name, output / "images" / name)
    payload = {"counts": dict(Counter(case["gate"] for case in cases)), "cases": cases}
    write_json_atomic(output / "manifest.json", payload, indent=2)
    content = PAGE.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c"))
    (output / "index.html").write_text(content, encoding="utf-8")
    fields = ["gate", "task_id", "image_name", "caption", "review_id", "predicate", "yes", "no", "unknown",
              "parent", "balance", "paired_review_id", "paired_predicate", "jaccard", "only_A", "only_B", "link"]
    with (output / "cases.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for case in cases:
            node, partner = case["rejected"], case["retained"]
            y, n, u = (len(node[f]) for f in ("instance_ids", "negative_instance_ids", "unknown_instance_ids"))
            writer.writerow({"gate": case["gate"], "task_id": case["task_id"], "image_name": case["image_name"],
                             "caption": case["caption"], "review_id": node["review_id"],
                             "predicate": node["predicate"]["display"], "yes": y, "no": n, "unknown": u,
                             "parent": y + n + u, "balance": min(y, n) / (y + n + u),
                             "paired_review_id": partner["review_id"] if partner else "",
                             "paired_predicate": partner["predicate"]["display"] if partner else "",
                             "jaccard": case.get("jaccard", ""), "only_A": case.get("only_rejected", ""),
                             "only_B": case.get("only_retained", ""), "link": "index.html#" + node["review_id"]})
    print(f"✅ 可视化 {len(cases)} 条：{payload['counts']} → {output / 'index.html'}")
    return 0


def main() -> int:
    configure_console()
    parser = argparse.ArgumentParser(description="只读查看 G5 平衡度及 G4 冗余拒绝子集")
    parser.add_argument("--review-dir", type=Path, default=path("review_dir"))
    parser.add_argument("--decisions", type=Path, default=path("review_subset_decisions"))
    parser.add_argument("--subsets", type=Path, default=path("caption_subsets"))
    parser.add_argument("--image-dir", type=Path, default=Path(CONFIG["image_dir"]))
    parser.add_argument("--output-dir", type=Path, default=path("output_dir") / "gate_rejections")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
