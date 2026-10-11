#!/usr/bin/env python3
"""
DeepSeek「思考过程可见」本地中转（Responses API）

背景：ChatGPT 桌面版（Codex）界面走的是「推理摘要（reasoning summary）」通道，
而 DeepSeek 的 /responses 只产出 reasoning_text、不产出 summary，
导致界面「思考中」块展开为空。

做法：在本地起一个极小的反向代理，把 DeepSeek 的 reasoning_text 流
同时翻译成 reasoning_summary_text 流，再原样透传其余事件。
界面即可正常显示思考过程。

用法：
    python3 ds-summary-proxy.py [--port 8791]
然后把 ~/.codex/config.toml 的 [model_providers.deepseek] base_url 指向
    http://127.0.0.1:8791/
"""

import argparse
import datetime
import html
import json
import os
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = "https://api.deepseek.com"
LOG_PATH = None
LINE_CHARS = 90
LINES_PER_PART = 5
THINK_DIR = os.path.expanduser("~/.codex/thinking-log")
THINK_ENABLED = True
THINK_MAX_VIEW = 60
SUMMARY_MAX_CHARS = 0          # 0 = 不限制；>0 时只向界面注入前 N 个字符的摘要
_log_lock = threading.Lock()
_think_lock = threading.Lock()


def _extract_turn(model: str, resp_obj: dict, fallback_thinking: str) -> dict:
    """从一次完成的响应里抽出：思考原文、回答文本、工具调用清单。"""
    thinking, answer, calls = [], [], []
    for item in resp_obj.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        itype = item.get("type")
        if itype == "reasoning":
            def _join(key):
                return "\n\n".join(
                    c.get("text", "").strip()
                    for c in (item.get(key) or [])
                    if isinstance(c, dict) and c.get("text", "").strip()
                )
            # 回溯档案要"完整"：取 summary 与原始 content 中更长的那个（限长注入时 content 才是全文）
            s_txt, c_txt = _join("summary"), _join("content")
            best = s_txt if len(s_txt) >= len(c_txt) else c_txt
            if best:
                thinking.append(best)
        elif itype == "message":
            text = "".join(
                c.get("text", "")
                for c in (item.get("content") or [])
                if isinstance(c, dict)
            )
            if text.strip():
                answer.append(text.strip())
        elif itype == "function_call":
            calls.append(
                {
                    "name": item.get("name") or "?",
                    "arguments": (item.get("arguments") or "")[:8000],
                }
            )
    if not thinking and fallback_thinking:
        thinking.append(fallback_thinking.strip())
    return {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "model": model or "?",
        "thinking": "\n\n".join(thinking).strip(),
        "answer": "\n\n".join(answer).strip(),
        "calls": calls,
    }


def _render_viewer(entries: list) -> str:
    rows = []
    for i, e in enumerate(entries):
        first = (e["thinking"].split("\n") or [""])[0][:80]
        cmds = "、".join(c["name"] for c in e.get("calls", [])[:6]) or "—"
        open_attr = " open" if i == 0 else ""
        rows.append(
            f"""<details class="entry"{open_attr}>
  <summary>
    <span class="ts">{html.escape(e['ts'].replace('T', ' '))}</span>
    <span class="model">{html.escape(e['model'])}</span>
    <span class="preview">{html.escape(first)}</span>
    <span class="meta">{len(e['thinking'])} 字 · 工具 {len(e.get('calls', []))}</span>
  </summary>
  <div class="block">
    <h4>思考过程</h4>
    <pre class="think">{html.escape(e['thinking']) or '（本次无思考文本）'}</pre>
    <h4>工具调用（{html.escape(cmds)}）</h4>
    <pre class="calls">{html.escape(chr(10).join(c['name'] + ' ' + c['arguments'] for c in e.get('calls', []))) or '（无）'}</pre>
    <h4>回答</h4>
    <pre class="answer">{html.escape(e['answer'][:4000]) or '（无）'}</pre>
  </div>
</details>"""
        )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Codex 思考回溯</title>
<style>
  :root {{ --ink:#1f2937; --muted:#6b7280; --line:#e5e7eb; --bg:#f7f8fa; --card:#ffffff; --accent:#2563eb; }}
  body {{ margin:0; padding:2rem 1.5rem 4rem; background:var(--bg); color:var(--ink);
         font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
         line-height:1.7; font-size:16px; }}
  header {{ max-width:960px; margin:0 auto 1.5rem; }}
  h1 {{ font-size:26px; margin:0 0 .3rem; }}
  .sub {{ color:var(--muted); font-size:14px; }}
  .wrap {{ max-width:960px; margin:0 auto; }}
  details.entry {{ background:var(--card); border:1px solid var(--line); border-radius:10px; margin:0 0 .75rem; }}
  details.entry > summary {{ cursor:pointer; padding:.85rem 1rem; display:flex; gap:.6rem; flex-wrap:wrap; align-items:baseline; }}
  details.entry > summary::marker {{ color:var(--accent); }}
  .ts {{ font-variant-numeric:tabular-nums; color:var(--ink); font-weight:600; }}
  .model {{ color:var(--accent); font-size:13px; }}
  .preview {{ color:var(--muted); flex:1 1 40%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
  .meta {{ color:var(--muted); font-size:13px; }}
  .block {{ padding:.25rem 1rem 1rem; border-top:1px solid var(--line); }}
  h4 {{ margin:1rem 0 .35rem; font-size:14px; color:var(--muted); font-weight:600; }}
  pre {{ margin:0; white-space:pre-wrap; word-break:break-word; font-size:14px; line-height:1.75;
        background:#fbfbfd; border:1px solid var(--line); border-radius:8px; padding:.7rem .8rem;
        font-family:ui-monospace,SFMono-Regular,Menlo,monospace; max-height:26rem; overflow:auto; }}
  pre.answer {{ background:#f8fafc; }}
  @media print {{ details.entry {{ break-inside:avoid; }} pre {{ max-height:none; }} }}
</style>
</head>
<body>
<header>
  <h1>Codex 思考回溯</h1>
  <div class="sub">由本地中转自动记录 · 最近 {len(entries)} 条 · 更新于 {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
</header>
<div class="wrap">
{chr(10).join(rows) if rows else '<p>暂无记录。</p>'}
</div>
</body>
</html>"""


def record_turn(model: str, resp_obj: dict, fallback_thinking: str = "") -> None:
    """把一次回合的思考/工具/回答落盘，并刷新可回溯的 HTML 视图。"""
    if not THINK_ENABLED:
        return
    try:
        entry = _extract_turn(model, resp_obj, fallback_thinking)
    except Exception as exc:  # 记录失败绝不影响主流程
        log(f"[thinking-log-error] {exc!r}")
        return
    if not entry["thinking"] and not entry["answer"] and not entry["calls"]:
        return
    try:
        with _think_lock:
            os.makedirs(THINK_DIR, exist_ok=True)
            with open(os.path.join(THINK_DIR, "entries.jsonl"), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            entries = []
            path = os.path.join(THINK_DIR, "entries.jsonl")
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        entries.append(json.loads(line))
                    except ValueError:
                        continue
            entries = entries[-THINK_MAX_VIEW:][::-1]
            with open(os.path.join(THINK_DIR, "index.html"), "w", encoding="utf-8") as fh:
                fh.write(_render_viewer(entries))
        log(f"[thinking-log] 已记录 1 条（思考 {len(entry['thinking'])} 字 / 工具 {len(entry['calls'])} 个）")
    except Exception as exc:
        log(f"[thinking-log-error] {exc!r}")


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    with _log_lock:
        print(line, flush=True)
        if LOG_PATH:
            try:
                with open(LOG_PATH, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass


def sse(event_type: str, payload: dict) -> bytes:
    return (
        f"event: {event_type}\n"
        f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
    ).encode("utf-8")


class Rewriter:
    """把 reasoning_text 流镜像成 reasoning_summary_text 流。

    额外做两件事，让界面上的思考过程"看得清"而不是一闪而过：

    1. **折行**：每累计 `line_chars` 个字符就插入一个换行，思考内容变成多行；
    2. **分段**：每 `lines_per_part` 行开一个新的 summary part，
       界面会把多个 part 按段落渲染（而不是挤成一行）。
    """

    # 断行优先落点：空格与中英文标点（避免把单词/词切断）
    BREAK_CHARS = " \t，。！？；：、,.!?;:）)]】」》"

    def __init__(self, line_chars: int = 40, lines_per_part: int = 1) -> None:
        self.parts_started: set = set()
        self.acc: dict = {}
        self.parts: dict = {}
        self.state: dict = {}
        self.injected = 0
        self.line_chars = max(16, int(line_chars))
        self.lines_per_part = max(1, int(lines_per_part))

    def _st(self, item_id: str) -> dict:
        return self.state.setdefault(
            item_id,
            {"part": 0, "lines": 0, "started": False, "buf": ""},
        )

    def _cut(self, buf: str):
        """从缓冲区切出一整行；不足一行返回 None。优先在空格/标点处断。"""
        nl = buf.find("\n")
        if 0 <= nl <= self.line_chars:
            return buf[:nl], buf[nl + 1 :]
        if len(buf) < self.line_chars:
            return None
        limit = self.line_chars
        cut = None
        for i in range(limit - 1, max(0, limit - 18) - 1, -1):
            if buf[i] in self.BREAK_CHARS:
                cut = i + 1
                break
        if cut is None:
            cut = limit
        return buf[:cut].rstrip(), buf[cut:].lstrip()

    def _line_events(self, item_id: str, line: str, out_index: int, seq: int) -> list:
        """把一整行作为摘要发出；按 lines_per_part 决定是否另起一段。"""
        st = self._st(item_id)
        # 限长：超过上限就不再向界面注入（完整思考仍由原始 content 通道传递、并进回溯档案）
        if SUMMARY_MAX_CHARS and st.get("emitted", 0) >= SUMMARY_MAX_CHARS:
            st["suppressed"] = st.get("suppressed", 0) + len(line)
            return []
        buckets = self.parts.setdefault(item_id, [])
        out = []
        if not st["started"]:
            st["started"] = True
            st["lines"] = 0
        elif st["lines"] >= self.lines_per_part:
            st["part"] += 1
            st["lines"] = 0
        idx = st["part"]
        while len(buckets) <= idx:
            buckets.append("")
        head = not buckets[idx]
        if head:
            out.append((
                "response.reasoning_summary_part.added",
                {
                    "type": "response.reasoning_summary_part.added",
                    "item_id": item_id,
                    "output_index": out_index,
                    "summary_index": idx,
                    "part": {"type": "summary_text", "text": ""},
                    "sequence_number": seq,
                },
            ))
        # ccswitch 同款做法①：首行加粗成"小标题"，满足桌面端"这条推理可展开渲染"的判定
        if head and idx == 0 and not st.get("head_bold"):
            st["head_bold"] = True
            line = "**%s**" % line
        delta = line if head else "\n" + line
        buckets[idx] += delta
        st["emitted"] = st.get("emitted", 0) + len(line)
        st["lines"] += 1
        out.append((
            "response.reasoning_summary_text.delta",
            {
                "type": "response.reasoning_summary_text.delta",
                "item_id": item_id,
                "output_index": out_index,
                "summary_index": idx,
                "delta": delta,
                "sequence_number": seq,
            },
        ))
        # ccswitch 同款做法②：补发 summary_text.done（每段=整行，发完即完整），补齐桌面端要的 summary 生命周期
        out.append((
            "response.reasoning_summary_text.done",
            {
                "type": "response.reasoning_summary_text.done",
                "item_id": item_id,
                "output_index": out_index,
                "summary_index": idx,
                "text": buckets[idx],
                "sequence_number": seq,
            },
        ))
        # ccswitch 同款做法②b：每段收尾补发 part.done（part 完整文本）
        out.append((
            "response.reasoning_summary_part.done",
            {
                "type": "response.reasoning_summary_part.done",
                "item_id": item_id,
                "output_index": out_index,
                "summary_index": idx,
                "part": {"type": "summary_text", "text": buckets[idx]},
                "sequence_number": seq,
            },
        ))
        self.injected += 1
        return out

    def _drain(self, item_id: str, out_index: int, seq: int, final: bool = False) -> list:
        """把缓冲区里已够长的内容按行吐出。"""
        st = self._st(item_id)
        out = []
        while True:
            if final and len(st["buf"]) < self.line_chars:
                line, st["buf"] = st["buf"].strip(), ""
                if not line:
                    break
            else:
                cut = self._cut(st["buf"])
                if cut is None:
                    break
                line, st["buf"] = cut[0].strip(), cut[1]
            if line:
                out.extend(self._line_events(item_id, line, out_index, seq))
        return out

    def _part_slot(self, item_id: str) -> int:
        return self._st(item_id)["part"]

    # ── ccswitch 同款做法③：把原生思考写回 item.summary ──────────────────
    # 桌面端渲染思考块读的是 reasoning item 的 summary 数组；上游（DeepSeek）
    # 只填 content、summary 恒为空 → 界面"计时器在转、展开却是空的/整行不显示"。
    # 这里在 output_item.added/done 与 response.completed 上补写 summary。
    def patch_item(self, item) -> None:
        if not isinstance(item, dict) or item.get("type") != "reasoning":
            return
        if item.get("summary"):
            return
        iid = item.get("id")
        buckets = [b for b in (self.parts.get(iid) or []) if b]
        if not buckets:
            text = self.acc.get(iid) or ""
            buckets = [text] if text else []
        if buckets:
            item["summary"] = [
                {"type": "summary_text", "text": b} for b in buckets
            ]

    def patch_envelope(self, data: dict) -> None:
        """SSE 事件级补写：output_item.* 直接带 item；response.completed 带 output 数组。"""
        if not isinstance(data, dict):
            return
        self.patch_item(data.get("item"))
        resp = data.get("response")
        if isinstance(resp, dict):
            for it in resp.get("output") or []:
                self.patch_item(it)

    def feed(self, evt: str, data: dict) -> list:
        """返回需要额外发出的（事件名, 负载）列表。"""
        out = []
        etype = data.get("type") or evt
        item_id = data.get("item_id")

        if etype == "response.reasoning_text.delta" and isinstance(data.get("delta"), str):
            st = self._st(item_id)
            st["buf"] += data["delta"]
            return self._drain(
                item_id,
                data.get("output_index", 0),
                data.get("sequence_number", 0),
            )

        if etype == "response.reasoning_text.done":
            out.extend(
                self._drain(
                    item_id,
                    data.get("output_index", 0),
                    data.get("sequence_number", 0),
                    final=True,
                )
            )
            self.acc[item_id] = "\n\n".join(self.parts.get(item_id, []))
            idx = self._part_slot(item_id)
            out.append((
                "response.reasoning_summary_text.done",
                {
                    "type": "response.reasoning_summary_text.done",
                    "item_id": item_id,
                    "output_index": data.get("output_index", 0),
                    "summary_index": idx,
                    "text": (self.parts.get(item_id) or [""])[idx],
                    "sequence_number": data.get("sequence_number", 0),
                },
            ))
            return out

        # 收尾事件：把完整摘要写进 reasoning item 的 summary 字段
        if etype in (
            "response.output_item.done",
            "response.output_item.added",
        ):
            item = data.get("item")
            if isinstance(item, dict) and item.get("type") == "reasoning":
                parts = [p for p in self.parts.get(item.get("id"), []) if p.strip()]
                if not parts:
                    text = self.acc.get(item.get("id"), "")
                    parts = [text] if text else []
                if parts:
                    item["summary"] = [
                        {"type": "summary_text", "text": p} for p in parts
                    ]
        return out


def rewrite_nonstream(payload: dict) -> dict:
    """非流式响应：把 reasoning item 的 content 文本补进 summary。"""
    for item in payload.get("output", []) or []:
        if isinstance(item, dict) and item.get("type") == "reasoning":
            text = "".join(
                c.get("text", "")
                for c in item.get("content", []) or []
                if isinstance(c, dict)
            )
            if text:
                item["summary"] = [{"type": "summary_text", "text": text}]
    return payload


def sanitize_request(payload: dict) -> tuple:
    """请求侧兼容修正：上游要求工具输出项必须带 call_id。

    Codex 的心跳/定时任务注入会把任务提示词写成一条孤立的 function_call_output
    （无 call_id、也没有配对的 function_call），原样透传会被上游 400 拒收
    （missing field call_id）。这里把这类无 call_id 的工具项降级为同文本的
    user 消息：内容不丢，校验通过。
    返回 (修正后的 payload, 修正条数)。
    """
    if not isinstance(payload, dict):
        return payload, 0
    items = payload.get("input")
    if not isinstance(items, list):
        return payload, 0

    out, fixed = [], 0
    for item in items:
        if not isinstance(item, dict):
            out.append(item)
            continue
        itype = item.get("type")
        if itype in ("function_call_output", "custom_tool_call_output") and not item.get("call_id"):
            text = item.get("output")
            if not isinstance(text, str):
                text = json.dumps(text, ensure_ascii=False)
            out.append({
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            })
            fixed += 1
        elif itype in ("function_call", "custom_tool_call") and not item.get("call_id"):
            fixed += 1  # 无 call_id 的调用项同样会被上游拒收，丢弃
        else:
            out.append(item)

    if fixed:
        payload = dict(payload)
        payload["input"] = out
    return payload, fixed


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ds-summary-proxy/1.0"

    def log_message(self, fmt, *args):  # 静音默认访问日志
        return

    def _proxy(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""

        try:
            req_body = json.loads(body.decode("utf-8")) if body else {}
            parsed = True
        except (ValueError, UnicodeDecodeError):
            req_body = {}
            parsed = False
        if parsed:
            req_body, fixed = sanitize_request(req_body)
            if fixed:
                body = json.dumps(req_body, ensure_ascii=False).encode("utf-8")
                log(f"[fix] 修正 {fixed} 条无 call_id 的工具项（转为 user 消息）")
        streaming = bool(req_body.get("stream"))

        # 除逐跳头外全部转发，保证与上游的兼容性
        skip = {
            "host",
            "content-length",
            "connection",
            "keep-alive",
            "transfer-encoding",
            "accept-encoding",
            "te",
            "trailer",
            "upgrade",
            "proxy-authorization",
            "proxy-connection",
        }
        headers = {
            k: v for k, v in self.headers.items() if k.lower() not in skip
        }
        headers.setdefault("Content-Type", "application/json")

        url = UPSTREAM + self.path
        req = urllib.request.Request(
            url, data=body, headers=headers, method=self.command
        )

        try:
            resp = urllib.request.urlopen(
                req, timeout=900, context=ssl.create_default_context()
            )
        except urllib.error.HTTPError as exc:
            detail = exc.read()
            log(f"[upstream-{exc.code}] {self.path} {detail[:300]!r}")
            self.send_response(exc.code)
            self.send_header("Content-Type", exc.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(detail)))
            self.end_headers()
            self.wfile.write(detail)
            return
        except Exception as exc:  # 网络类错误
            log(f"[upstream-error] {self.path} {exc!r}")
            self.send_response(502)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if not streaming:
            raw = resp.read()
            try:
                data = json.loads(raw.decode("utf-8"))
                raw = json.dumps(rewrite_nonstream(data), ensure_ascii=False).encode("utf-8")
            except (ValueError, UnicodeDecodeError):
                pass
            self.send_response(resp.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        self.send_response(resp.status)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        rw = Rewriter(line_chars=LINE_CHARS, lines_per_part=LINES_PER_PART)
        buf: list = []
        started = time.time()
        try:
            while True:
                line = resp.readline()
                if not line:
                    break
                if line.strip() == b"":
                    # 一个 SSE 事件结束：先补发合成事件，再按（可能被改写的）内容透传
                    evt_name = ""
                    data_raw = b""
                    for entry in buf:
                        if entry.startswith(b"event:"):
                            evt_name = entry.split(b":", 1)[1].strip().decode("utf-8", "replace")
                        elif entry.startswith(b"data:"):
                            data_raw += entry.split(b":", 1)[1].strip()
                    parsed = None
                    if data_raw and data_raw != b"[DONE]":
                        try:
                            parsed = json.loads(data_raw.decode("utf-8"))
                        except (ValueError, UnicodeDecodeError):
                            parsed = None
                    if evt_name:
                        self.wfile.write(f"event: {evt_name}\n".encode("utf-8"))
                    if isinstance(parsed, dict):
                        for name, payload in rw.feed(evt_name, parsed):
                            self.wfile.write(sse(name, payload))
                        if parsed.get("type") == "response.completed":
                            record_turn(
                                req_body.get("model", ""),
                                parsed.get("response") or {},
                                "\n\n".join(rw.acc.values()),
                            )
                        rw.patch_envelope(parsed)   # ccswitch 同款：补写 item.summary
                        self.wfile.write(
                            b"data: "
                            + json.dumps(parsed, ensure_ascii=False).encode("utf-8")
                            + b"\n"
                        )
                    elif data_raw:
                        self.wfile.write(b"data: " + data_raw + b"\n")
                    self.wfile.write(b"\n")
                    self.wfile.flush()
                    buf = []
                    continue
                buf.append(line)
            if buf:
                for entry in buf:
                    self.wfile.write(entry)
                self.wfile.write(b"\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            resp.close()
            self.wfile.flush()
            log(
                f"done {self.path} stream={streaming} "
                f"injected_summary_deltas={rw.injected} "
                f"elapsed={time.time() - started:.1f}s"
            )

    do_POST = _proxy
    do_GET = _proxy


def main() -> int:
    global LOG_PATH, LINE_CHARS, LINES_PER_PART, THINK_DIR, THINK_ENABLED
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--log", default=None)
    ap.add_argument(
        "--thinking-log",
        default=THINK_DIR,
        help="思考回溯档案目录（默认 ~/.codex/thinking-log）",
    )
    ap.add_argument(
        "--no-thinking-log",
        action="store_true",
        help="关闭思考回溯记录",
    )
    ap.add_argument(
        "--line-chars",
        type=int,
        default=90,
        help="思考内容每多少字符折一行（默认 90，便于界面上多行展示）",
    )
    ap.add_argument(
        "--lines-per-part",
        type=int,
        default=5,
        help="每多少行开一个新的摘要段落（默认 5，界面按段落渲染）",
    )
    ap.add_argument(
        "--summary-max-chars",
        type=int,
        default=0,
        help="只向界面注入前 N 个字符的摘要（0=不限制）；"
             "也可在同目录写 summary-cap.json: {\"max_chars\": 600}（无需改 root 级 plist）",
    )
    args = ap.parse_args()
    LOG_PATH = args.log
    LINE_CHARS = args.line_chars
    LINES_PER_PART = args.lines_per_part
    global SUMMARY_MAX_CHARS
    SUMMARY_MAX_CHARS = max(0, args.summary_max_chars)
    if not SUMMARY_MAX_CHARS:
        cap_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "summary-cap.json")
        try:
            with open(cap_file, encoding="utf-8") as fh:
                cap = json.load(fh)
                SUMMARY_MAX_CHARS = max(0, int(cap.get("max_chars", 0)))
                # ccswitch 同款：整段思考作为**单个** summary part 透传（不做 40 字切分），
                # 由 plist/配置文件覆盖折行与分段参数
                if cap.get("line_chars"):
                    LINE_CHARS = max(16, int(cap["line_chars"]))
                if cap.get("lines_per_part"):
                    LINES_PER_PART = max(1, int(cap["lines_per_part"]))
        except (OSError, ValueError, TypeError):
            pass
    THINK_DIR = os.path.expanduser(args.thinking_log)
    THINK_ENABLED = not args.no_thinking_log
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    log(
        f"listening on http://{args.host}:{args.port}  upstream={UPSTREAM}  "
        f"line_chars={LINE_CHARS} lines_per_part={LINES_PER_PART} "
        f"summary_max_chars={SUMMARY_MAX_CHARS or '∞'} "
        f"thinking_log={THINK_DIR if THINK_ENABLED else 'off'}"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
