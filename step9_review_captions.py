"""step9 人工审核：直接确认 yes，或修改属性短语后确认 yes。

    python step9_review_captions.py
    python step9_review_captions.py --check-only
    python step9_review_captions.py --input anno/subset_captions_local.jsonl

默认读取完整在线结果及全部历史。选用历史版本原地确认；手动编辑则追加新记录。
不调用 LLM，不改实例成员，不改写 .run.json。审核时请停止其他 step9 和分离进程。
"""

from __future__ import annotations

import argparse
import http.server
import json
import secrets
import sys
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Sequence

from recsub.caption_review import CaptionReviewSession
from recsub.config import CONFIG, path
from recsub.console import configure_console

_HTML = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>step9 属性短语人工审核</title>
<style>
:root{font-family:system-ui,"Microsoft YaHei",sans-serif;color:#172b3a;background:#f2f5f8;line-height:1.6}
*{box-sizing:border-box}body{margin:0}header{background:#183c50;color:white;padding:18px 26px}
h1{font-size:23px;margin:0 0 4px}header p{margin:0;color:#d8e6ee}main{display:grid;grid-template-columns:285px 1fr;gap:20px;padding:20px;max-width:1450px;margin:auto}
aside,.card{background:white;border:1px solid #dbe3e9;border-radius:10px;padding:16px}aside{align-self:start;position:sticky;top:12px}
#list{overflow:auto;max-height:76vh}.item{display:block;width:100%;text-align:left;background:white;border:1px solid #dbe3e9;padding:10px;margin:6px 0;border-radius:6px;cursor:pointer;overflow-wrap:anywhere}
.item.active{border:2px solid #287898;background:#eff8fc}.item.done{border-left:5px solid #27825c}.item small{display:block;color:#596d7b}
section{min-width:0}.card{margin-bottom:14px}h2{font-size:15px;color:#596d7b;margin:0 0 5px}.value{font-size:21px;font-weight:600;overflow-wrap:anywhere;white-space:pre-wrap}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.note{font-size:14px;color:#596d7b}.negative{background:#fff7e8;border-color:#edd3a0}
textarea{display:block;width:100%;min-height:100px;margin:10px 0;border:1px solid #acbdc9;border-radius:6px;padding:12px;font:19px/1.6 system-ui;resize:vertical}
#preview{background:#edf6fa;padding:12px;border-radius:6px;min-height:55px;font-size:21px;overflow-wrap:anywhere}button{font:inherit}.actions{display:flex;gap:10px;flex-wrap:wrap;margin:16px 0}
.actions button{padding:10px 16px;border:1px solid #a4b8c5;border-radius:6px;cursor:pointer;background:white}.actions .primary{background:#176746;color:white;border-color:#176746}
button:disabled{opacity:.45;cursor:not-allowed}#message{white-space:pre-wrap;overflow-wrap:anywhere;min-height:27px;color:#176746}#message.error{color:#af2929}#paths{overflow-wrap:anywhere;font-size:13px;color:#627786}
#empty{display:none}.status{font-weight:600}details pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}@media(max-width:850px){main{grid-template-columns:1fr}aside{position:static}#list{max-height:190px}.grid{grid-template-columns:1fr}}
</style></head><body>
<header><h1>step9 属性短语人工审核</h1><p>只处理失败、no 或未审核记录；不调用模型，不修改原属性与子集成员。</p></header>
<main><aside><b id="progress">载入中…</b><div id="list"></div></aside><section>
<div id="empty" class="card">当前没有待人工审核的记录。</div>
<div id="editor">
<div class="card"><h2>来源</h2><div id="root" class="value"></div><div id="meta" class="note"></div></div>
<div class="grid"><div class="card"><h2>类别（代码原样添加，不可修改）</h2><div id="category" class="value"></div></div>
<div class="card"><h2>原属性（需保留原意）</h2><div id="original" class="value"></div></div></div>
<div id="additionalCard" class="card"><h2>新增划分属性（含否定时保留否定范围）</h2><div id="additional" class="value"></div></div>
<div class="card"><h2>当前记录的合成结果</h2><div id="modelPhrase" class="value"></div><div id="checks" class="note"></div></div>
<div class="card"><h2>历次合成候选（点击选用，可继续编辑）</h2><div id="historySummary" class="note"></div><div id="candidates"></div></div>
<div class="card"><h2>新属性短语（可编辑）</h2><div class="note">只写属性短语，不要重复添加开头的类别。请同时保留原属性、新增条件及其否定含义；无法合理表达时可以跳过。</div>
<textarea id="phrase" spellcheck="false" aria-label="新属性短语"></textarea>
<h2>新 caption 预览（类别 + 空格 + 新属性短语）</h2><div id="preview"></div>
<div class="actions"><button id="originalYes">原样确认选中版本 yes</button><button id="editedYes" class="primary">保存当前文本并设为 yes</button></div>
<div class="note">人工确认会令 language_check=yes、accepted=true；string_check 会重新计算，即使为 no 也允许确认。历史选择原地更新，手动修改追加新记录；每次确认立即落盘。</div></div>
<div class="actions"><button id="previous">← 上一条</button><button id="next">跳过 / 下一条 →</button></div>
<details class="card"><summary>查看审核状态</summary><pre id="detail"></pre></details>
</div><div id="message" role="status"></div><div id="paths"></div></section></main>
<script>
const TOKEN=__TOKEN__;
let items=[],index=0,busy=false,loadedPhrase='',selection=null;
const $=id=>document.getElementById(id);
function tell(message,error=false){$('message').textContent=message;$('message').className=error?'error':'';}
async function api(route,data){const response=await fetch(route,{method:data?'POST':'GET',headers:data?{'Content-Type':'application/json','X-Review-Token':TOKEN}:{},body:data?JSON.stringify(data):undefined});const value=await response.json();if(!response.ok)throw new Error(value.error||'请求失败');return value;}
function dirty(){return $('phrase').value!==loadedPhrase;}
function navigate(to){if(busy||!items.length)return;if(dirty()&&!confirm('当前修改尚未保存，是否放弃修改并切换？'))return;index=(to+items.length)%items.length;tell('');render();}
function list(){const approved=items.filter(x=>x.accepted).length;$('progress').textContent=`本次 ${items.length} 条 · 已确认 ${approved} · 待确认 ${items.length-approved}`;$('list').replaceChildren();items.forEach((item,i)=>{const button=document.createElement('button');button.className='item'+(i===index?' active':'')+(item.accepted?' done':'');button.textContent=`${item.accepted?'✓':'○'} ${i+1}. ${item.caption}`;const subtitle=document.createElement('small');subtitle.textContent=item.image_name;button.append(subtitle);button.onclick=()=>navigate(i);$('list').append(button);});}
function preview(){const item=items[index];if(item)$('preview').textContent=item.category+' '+$('phrase').value.trim();}
function candidates(item){const values=item.candidates||[];$('candidates').replaceChildren();$('historySummary').textContent=values.length?`${values.length} 种不同短语 · 共 ${values.reduce((n,value)=>n+value.attempts.length,0)} 条历史记录；重复短语合并展示，保留历次审核状态。`:'没有保存的历史短语，可在下方直接填写。';[...values].reverse().forEach(value=>{const box=document.createElement('div');box.className='card';const phrase=document.createElement('div');phrase.className='value';phrase.textContent=value.new_attributes;const statuses=document.createElement('div');statuses.className='note';const counts={};value.attempts.forEach(attempt=>{const state=attempt.failed?'调用/解析失败':attempt.language_check||'未审核';counts[state]=(counts[state]||0)+1;});statuses.textContent=Object.entries(counts).map(([key,count])=>`${key} × ${count}`).join(' · ');const button=document.createElement('button');button.textContent='选用此版本（最近一次记录）';button.onclick=()=>{if(busy)return;if(dirty()&&!confirm('是否用此历史版本替换未保存的编辑？'))return;$('phrase').value=value.new_attributes;selection=value.attempts[value.attempts.length-1];$('originalYes').disabled=false;preview();$('phrase').focus();tell('已选用历史记录：确认将原地设为 yes；若编辑文本则作为新记录保存。');};const detail=document.createElement('details'),summary=document.createElement('summary'),pre=document.createElement('pre');summary.textContent='查看各次审核与来源';pre.textContent=JSON.stringify(value.attempts,null,2);detail.append(summary,pre);box.append(phrase,statuses,button,detail);$('candidates').append(box);});}
function render(){list();$('empty').style.display=items.length?'none':'block';$('editor').style.display=items.length?'block':'none';if(!items.length)return;const item=items[index];$('root').textContent=item.caption;$('meta').textContent=`${item.image_name} · ${item.node_id} · ${item.member_count} 个成员${item.complement_of?' · 否定补集':''}`;$('category').textContent=item.category;$('original').textContent=item.original_attributes||'（空）';$('additional').textContent=item.additional_attribute;$('additionalCard').className='card'+(item.complement_of||/^not\b/i.test(item.additional_attribute)?' negative':'');$('modelPhrase').textContent=item.manual_review?.previous_new_attributes??item.new_attributes??'（未生成属性短语）';$('checks').textContent=`${item.accepted?'已人工/模型通过':'待人工审核'} · language_check=${item.language_check??'未审核'} · string_check=${item.string_check??'未检查'}${item.reason?' · '+item.reason:''}`;loadedPhrase=item.new_attributes||'';$('phrase').value=loadedPhrase;selection=item.current_record;candidates(item);$('originalYes').disabled=busy||!loadedPhrase.trim();$('editedYes').disabled=busy;preview();$('detail').textContent=JSON.stringify(item,null,2);}
async function save(){
 if(busy||!items.length)return;
 const item=items[index],phrase=$('phrase').value;
 if(!phrase.trim()){tell('请先填写属性短语。',true);return;}
 busy=true;$('originalYes').disabled=true;$('editedYes').disabled=true;
 try{
  const approved=await api('/api/approve',{node_id:item.node_id,attributes:phrase,revision:item.revision,selection:selection||null});
  items[index]=approved;loadedPhrase=approved.new_attributes;
  const remaining=items.map((_,n)=>(index+n+1)%items.length).find(i=>!items[i].accepted);
  if(remaining!==undefined)index=remaining;
  render();
  tell((approved.save_mode==='updated_existing'?'已在原位置设为 yes，记录数不变。':'已追加新的人工记录，原记录未修改。')+(remaining===undefined?'本次已全部确认。':'已切换到下一条。'));
 }catch(error){tell(error.message,true);}
 finally{busy=false;if(items.length){$('originalYes').disabled=!selection||!$('phrase').value.trim();$('editedYes').disabled=false;}}
}
$('phrase').oninput=()=>{selection=null;$('originalYes').disabled=true;preview();};$('originalYes').onclick=save;$('editedYes').onclick=save;$('previous').onclick=()=>navigate(index-1);$('next').onclick=()=>navigate(index+1);
document.addEventListener('keydown',event=>{if(event.ctrlKey&&event.key==='Enter'){event.preventDefault();save();}});
window.addEventListener('beforeunload',event=>{if(dirty()||busy){event.preventDefault();event.returnValue='';}});
api('/api/items').then(data=>{items=data.items;index=Math.max(0,items.findIndex(item=>!item.accepted));$('paths').textContent=`结果文件：${data.input}；历史选择原地更新，手动修改追加新记录。`;render();}).catch(error=>tell(error.message,true));
</script></body></html>"""


class ReviewHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def send_json(self, value, status=200):
        self.send_content(json.dumps(value, ensure_ascii=False).encode("utf-8"),
                          "application/json; charset=utf-8", status)

    def send_content(self, content: bytes, content_type: str, status=200):
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; "
                                                        "style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def valid_host(self) -> bool:
        if self.headers.get("Host", "").lower() not in self.server.allowed_hosts:
            self.send_json({"error": "仅允许本机访问"}, 403)
            return False
        return True

    def do_GET(self):  # noqa: N802
        if not self.valid_host():
            return
        route = urllib.parse.urlsplit(self.path).path
        try:
            if route == "/":
                page = _HTML.replace("__TOKEN__", json.dumps(self.server.token))
                self.send_content(page.encode("utf-8"), "text/html; charset=utf-8")
            elif route == "/api/items":
                self.send_json(self.server.session.snapshot())
            else:
                self.send_json({"error": "not found"}, 404)
        except RuntimeError as error:
            self.send_json({"error": str(error)}, 409)

    def do_POST(self):  # noqa: N802
        if not self.valid_host():
            return
        origin = self.headers.get("Origin")
        if (self.headers.get("X-Review-Token") != self.server.token
                or origin is not None and origin not in self.server.allowed_origins):
            self.send_json({"error": "无效审核请求，请从本机审核页面提交"}, 403)
            return
        if urllib.parse.urlsplit(self.path).path != "/api/approve":
            self.send_json({"error": "not found"}, 404)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 64_000:
                raise ValueError("无效请求大小")
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError("请求必须是 JSON 对象")
            node_id, revision = payload.get("node_id"), payload.get("revision")
            if not isinstance(node_id, str) or not isinstance(revision, str):
                raise ValueError("缺少 node_id/revision")
            result = self.server.session.approve(node_id, payload.get("attributes"), revision,
                                                 payload.get("selection"))
            self.send_json(result)
        except RuntimeError as error:
            self.send_json({"error": str(error)}, 409)
        except (ValueError, TypeError, OSError) as error:
            self.send_json({"error": str(error)}, 400)


def make_server(session: CaptionReviewSession, port: int):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), ReviewHandler)
    actual_port = server.server_address[1]
    server.session = session
    server.token = secrets.token_urlsafe(32)
    server.allowed_hosts = {f"127.0.0.1:{actual_port}", f"localhost:{actual_port}"}
    server.allowed_origins = {"http://" + host for host in server.allowed_hosts}
    return server


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="step9 本地人工审核：确认 yes 或修改属性短语后确认")
    parser.add_argument("--input", type=Path, default=path("captions_online"),
                        help="完整 step9 结果；汇集全部历史，并筛选尚未通过的子集")
    parser.add_argument("--port", type=int, default=CONFIG["caption"]["review_port"])
    parser.add_argument("--open-browser", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--check-only", action="store_true", help="只检查输入、输出和待审核数量，不写文件或启动服务")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    configure_console()
    args = parse_args(argv)
    try:
        if not 0 <= args.port <= 65535:
            raise ValueError("port 必须在 0～65535 之间")
        output = args.input
        protected = [path(key).resolve() for key in ("annotations", "splits", "caption_subsets", "instance_data")]
        if output.resolve() in protected:
            raise ValueError("输出不能指向原标注、原划分、最终子集或实例表")
        session = CaptionReviewSession(args.input)
        candidates = sum(len(values) for values in session.histories.values())
        attempts = sum(len(value["attempts"]) for values in session.histories.values() for value in values)
        print(
            f"✅ 待人工审核 {len(session.items)} 条；历史 {attempts} 次 / {candidates} 种候选短语；输入 → {session.input_path}")
        print("   选择历史结果：原地修改该条状态；手动修改/输入：追加新记录。")
        print("   请勿同时运行其他 step9、结果分离或人工审核进程。未选中的历史记录和 .run.json 不改写。")
        if args.check_only or not session.items:
            return 0
        server = make_server(session, args.port)
        url = f"http://127.0.0.1:{server.server_address[1]}/"
        print(f"   人工审核页面 → {url}；Ctrl+C 停止，已确认结果即时保存")
        try:
            if args.open_browser:
                webbrowser.open(url)
            server.serve_forever()
        finally:
            server.server_close()
        return 0
    except KeyboardInterrupt:
        print("人工审核已停止；已确认结果已保存")
        return 130
    except (ValueError, OSError, RuntimeError) as error:
        print(f"❌ 无法启动人工审核：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
