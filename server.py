#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
POSSIM-Web · 珠江口风暴潮淹没推演平台 —— HTTP 服务（S3 服务代理）

严格实现 CONTRACT.md §6 的 7 个端点（不改字段、不改路径）：
    GET  /                     -> web/index.html
    GET  /static/*             -> web/ 下静态文件（按扩展名给 Content-Type）
    GET  /api/meta             -> data/meta.json 原样字节返回
    GET  /api/dem/preview.png  -> data/dem_preview.png
    GET  /api/typhoon/{id}     -> data/typhoons/{id}.json
    POST /api/simulate         -> engine.api.simulate(payload) 懒加载
    GET  /api/health           -> {"ok":true,"engine":bool,"dem":bool}

设计要点：
  * 纯标准库（http.server），不依赖 flask/fastapi/node。
  * 所有路径基于 __file__ 定位，从任意当前目录启动都能找到 data/ 与 web/。
  * 线程化（ThreadingHTTPServer）：5–15 s 的 /api/simulate 不会阻塞静态资源。
  * engine 懒加载 + 失败降级：engine 未就绪时返回 {"error":"engine 未就绪: ..."}，
    服务本身绝不崩，前端仍能看到中文错误提示。
  * 关闭默认日志噪音，只打印：启动地址 / 每个 /api/* 的路径-耗时-状态 / 错误堆栈。
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import re
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

# --------------------------------------------------------------------------
# 路径常量：全部以本文件所在目录为根，保证任意 cwd 下都能跑起来
# --------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(ROOT, "web")
DATA_DIR = os.path.join(ROOT, "data")
TYPHOON_DIR = os.path.join(DATA_DIR, "typhoons")
META_PATH = os.path.join(DATA_DIR, "meta.json")
DEM_PATH = os.path.join(DATA_DIR, "dem.npy")
PREVIEW_PATH = os.path.join(DATA_DIR, "dem_preview.png")
INDEX_PATH = os.path.join(WEB_DIR, "index.html")

JSON_CT = "application/json; charset=utf-8"
HTML_CT = "text/html; charset=utf-8"

# 契约 §6 要求的扩展名映射（其余走 application/octet-stream 兜底）
CONTENT_TYPES = {
    ".html": HTML_CT,
    ".htm": HTML_CT,
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".json": JSON_CT,
    ".geojson": JSON_CT,
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
    ".ico": "image/x-icon",
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".txt": "text/plain; charset=utf-8",
    ".map": JSON_CT,
}

MAX_BODY_BYTES = 8 * 1024 * 1024   # /api/simulate 请求体上限，防呆
TYPHOON_ID_RE = re.compile(r"[A-Za-z0-9_\-]{1,64}")

# --------------------------------------------------------------------------
# engine 懒加载状态（进程内单例；导入失败不缓存错误太久，便于 S2 后交付即生效）
# --------------------------------------------------------------------------
_engine_lock = threading.Lock()
_engine = {"simulate": None, "error": "尚未尝试导入", "last_try": 0.0}
ENGINE_RETRY_SECONDS = 2.0


def load_simulate():
    """懒加载 engine.api.simulate。

    返回 (fn, None) 或 (None, 错误描述)。任何异常都被吞掉并转成字符串，
    绝不把 import 失败抛给请求线程。
    """
    fn = _engine["simulate"]
    if fn is not None:
        return fn, None

    with _engine_lock:
        if _engine["simulate"] is not None:
            return _engine["simulate"], None
        now = time.monotonic()
        # 冷却：避免每个 /api/health 都把坏掉的 engine 重导一遍
        if _engine["last_try"] and (now - _engine["last_try"]) < ENGINE_RETRY_SECONDS:
            return None, _engine["error"]
        _engine["last_try"] = now
        try:
            if ROOT not in sys.path:
                sys.path.insert(0, ROOT)
            from engine.api import simulate  # noqa: WPS433 (契约 §4.1 唯一入口)
            if not callable(simulate):
                raise TypeError("engine.api.simulate 不是可调用对象")
            _engine["simulate"] = simulate
            _engine["error"] = None
            print("[engine] 已加载 engine.api.simulate")
            return simulate, None
        except Exception as exc:  # ImportError / SyntaxError / 任意导入期异常
            _engine["error"] = "%s: %s" % (type(exc).__name__, exc)
            return None, _engine["error"]


def engine_ready():
    fn, _err = load_simulate()
    return fn is not None


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------
def content_type_for(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return CONTENT_TYPES.get(ext, "application/octet-stream")


def read_file_bytes(path: str):
    """读文件全量字节；不存在返回 None（不抛异常）。"""
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def safe_join(base_dir: str, relative: str):
    """把 URL 相对路径安全地拼到 base_dir 下，阻止 ../ 目录穿越。"""
    rel = unquote(relative).replace("\\", "/").lstrip("/")
    if not rel:
        return None
    target = os.path.normpath(os.path.join(base_dir, rel))
    base = os.path.normpath(base_dir)
    if target != base and not target.startswith(base + os.sep):
        return None
    return target


# --------------------------------------------------------------------------
# 请求处理器
# --------------------------------------------------------------------------
class POSSIMServer(ThreadingHTTPServer):
    """线程化服务：5–15 s 的 /api/simulate 期间静态资源照常响应。"""

    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        """浏览器/curl 提前关闭连接不是服务错误（Windows 下表现为 ConnectionResetError），
        不打堆栈以免污染控制台；真正的异常仍然照打。"""
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    server_version = "POSSIM-Web/1.0"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # ---- 关闭 http.server 的默认 stderr 噪音，改为结构化自定义日志 ----
    def log_message(self, fmt, *args):  # noqa: D401
        return

    def log_error(self, fmt, *args):
        return

    # ------------------------------------------------------------------
    # 响应工具
    # ------------------------------------------------------------------
    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept, X-Requested-With")
        self.send_header("Access-Control-Max-Age", "86400")

    def _send_bytes(self, data: bytes, ctype: str, status: int = 200):
        self._status = status
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # 演示环境一律不缓存，避免改了 web/ 或 data/ 还看到旧结果
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self._cors_headers()
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass  # 浏览器提前取消（例如拖时间轴时刷帧），不影响服务

    def _send_json(self, obj, status: int = 200):
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send_bytes(payload, JSON_CT, status)

    def _send_error_json(self, message: str, status: int = 400):
        self._send_json({"error": message}, status)

    def _send_text(self, text: str, status: int = 404, ctype: str = "text/plain; charset=utf-8"):
        self._send_bytes(text.encode("utf-8"), ctype, status)

    def _send_file(self, path: str, missing_hint: str, missing_status: int = 404):
        data = read_file_bytes(path)
        if data is None:
            self._send_json({"error": missing_hint}, missing_status)
            return
        self._send_bytes(data, content_type_for(path))

    # ------------------------------------------------------------------
    # HTTP 方法
    # ------------------------------------------------------------------
    def do_OPTIONS(self):
        """CORS 预检。"""
        self._start_ts = time.perf_counter()
        self._status = 204
        self.send_response(204)
        self._cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self._start_ts = time.perf_counter()
        self._status = None
        path = urlparse(self.path).path or "/"
        try:
            self._route_get(path)
        except Exception as exc:  # ③ 错误堆栈
            traceback.print_exc()
            try:
                self._send_error_json("服务内部错误: %s: %s" % (type(exc).__name__, exc), 500)
            except Exception:
                pass
        finally:
            self._log_request(path)

    def do_POST(self):
        self._start_ts = time.perf_counter()
        self._status = None
        path = urlparse(self.path).path or "/"
        try:
            self._route_post(path)
        except Exception as exc:
            traceback.print_exc()
            try:
                self._send_error_json("服务内部错误: %s: %s" % (type(exc).__name__, exc), 500)
            except Exception:
                pass
        finally:
            self._log_request(path)

    # ------------------------------------------------------------------
    # 自定义日志：只打 /api/*，格式 [api] 方法 路径 状态 耗时
    # ------------------------------------------------------------------
    def _log_request(self, path: str):
        if not path.startswith("/api/"):
            return
        elapsed_ms = (time.perf_counter() - getattr(self, "_start_ts", time.perf_counter())) * 1000.0
        status = getattr(self, "_status", None)
        status_txt = str(status) if status is not None else "ERR"
        print("[api] %-4s %-28s %s %.1f ms" % (self.command, path, status_txt, elapsed_ms), flush=True)

    # ------------------------------------------------------------------
    # GET 路由（契约 §6）
    # ------------------------------------------------------------------
    def _route_get(self, path: str):
        if path in ("/", "/index.html"):
            self._serve_index()
            return

        if path == "/api/health":
            self._api_health()
            return

        if path == "/api/meta":
            self._send_file(META_PATH, "data/meta.json 不存在（S1 数据代理尚未交付）")
            return

        if path in ("/api/dem/preview.png", "/api/dem/preview"):
            self._send_file(PREVIEW_PATH, "data/dem_preview.png 不存在（S1 数据代理尚未交付）")
            return

        if path.startswith("/api/typhoon/"):
            self._api_typhoon(path[len("/api/typhoon/"):])
            return

        if path.startswith("/api/"):
            self._send_error_json("未知接口: %s" % path, 404)
            return

        if path == "/favicon.ico":
            ico = os.path.join(WEB_DIR, "favicon.ico")
            data = read_file_bytes(ico)
            if data is not None:
                self._send_bytes(data, "image/x-icon")
            else:
                self._send_bytes(b"", "image/x-icon", 204)
            return

        # /static/* -> web/ 下；同时兼容 web/ 内部相对引用（如 /css/style.css）
        if path.startswith("/static/"):
            target = safe_join(WEB_DIR, path[len("/static/"):])
        else:
            target = safe_join(WEB_DIR, path)
        if target is None:
            self._send_error_json("非法静态路径: %s" % path, 400)
            return
        if not os.path.isfile(target):
            if path.startswith("/static/"):
                self._send_error_json("静态资源不存在: %s（S4 前端代理尚未交付？）" % path, 404)
            else:
                self._send_error_json("路径不存在: %s" % path, 404)
            return
        self._send_bytes(read_file_bytes(target) or b"", content_type_for(target))

    def _serve_index(self):
        data = read_file_bytes(INDEX_PATH)
        if data is None:
            hint = (
                "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
                "<title>POSSIM-Web</title></head><body style=\"background:#04101f;color:#cfe6ff;"
                "font-family:Microsoft YaHei,sans-serif;padding:48px;line-height:1.9\">"
                "<h2>前端未就绪</h2><p>web/index.html 不存在（S4 前端代理尚未交付）。</p>"
                "<p>服务已正常运行：可先访问 <code>/api/health</code>、<code>/api/meta</code>。</p>"
                "</body></html>"
            )
            # 返回 200 + 可读页面：让浏览器直接看到"前端未就绪"，而不是一个孤零零的 500
            self._send_text(hint, 200, HTML_CT)
            return
        self._send_bytes(data, HTML_CT)

    def _api_health(self):
        self._send_json({
            "ok": True,
            "engine": engine_ready(),
            "dem": os.path.isfile(DEM_PATH),
        })

    def _api_typhoon(self, raw_id: str):
        tid = unquote(raw_id).strip()
        if not TYPHOON_ID_RE.fullmatch(tid):
            self._send_error_json("非法台风 id: %r（只允许字母、数字、下划线、连字符）" % tid, 400)
            return
        path = os.path.join(TYPHOON_DIR, tid + ".json")
        self._send_file(path, "台风样例不存在: data/typhoons/%s.json" % tid)

    # ------------------------------------------------------------------
    # POST 路由
    # ------------------------------------------------------------------
    def _route_post(self, path: str):
        if path == "/api/simulate":
            self._api_simulate()
            return
        # 未知接口也要把 body 读干净，避免 keep-alive 连接错位
        self._drain_body()
        self._send_error_json("未知接口: %s" % path, 404)

    def _drain_body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return
        if 0 < n <= MAX_BODY_BYTES:
            try:
                self.rfile.read(n)
            except Exception:
                pass

    def _read_body(self):
        """按 Content-Length 读全量 body，返回 (bytes, 错误描述)。"""
        raw_len = self.headers.get("Content-Length")
        if raw_len is None:
            return None, "缺少 Content-Length 请求头"
        try:
            n = int(raw_len)
        except (TypeError, ValueError):
            return None, "Content-Length 非法: %r" % raw_len
        if n < 0:
            return None, "Content-Length 为负数"
        if n > MAX_BODY_BYTES:
            return None, "请求体过大（%d 字节 > %d）" % (n, MAX_BODY_BYTES)
        chunks = []
        remaining = n
        while remaining > 0:
            chunk = self.rfile.read(remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks), None

    def _api_simulate(self):
        t_all = time.perf_counter()
        body, err = self._read_body()
        if err:
            self._send_error_json(err, 400)
            return
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send_error_json("请求体不是合法 JSON: %s" % exc, 400)
            return
        if not isinstance(payload, dict):
            self._send_error_json("请求体必须是 JSON 对象", 400)
            return

        simulate, ierr = load_simulate()
        if simulate is None:
            # engine 未就绪：降级返回中文错误，服务继续存活
            self._send_error_json("engine 未就绪: %s" % ierr, 500)
            return

        t_engine = time.perf_counter()
        try:
            result = simulate(payload)
        except Exception as exc:
            traceback.print_exc()
            self._send_error_json("engine 计算异常: %s: %s" % (type(exc).__name__, exc), 500)
            return
        engine_ms = (time.perf_counter() - t_engine) * 1000.0
        if not isinstance(result, dict):
            self._send_error_json("engine.simulate 返回值不是 dict（实际 %s）" % type(result).__name__, 500)
            return
        # 契约 §4.1：engine 内部失败返回 {"error": "..."}
        if result.get("error") and not result.get("ok"):
            self._send_json(result, 500)
            return
        print("[engine] simulate 完成 %.1f ms（总 %.1f ms）"
              % (engine_ms, (time.perf_counter() - t_all) * 1000.0), flush=True)
        self._send_json(result, 200)


# --------------------------------------------------------------------------
# 启动自检 + main
# --------------------------------------------------------------------------
def _prepare_console():
    """Windows 控制台强制 UTF-8，保证中文不乱码。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def readiness_check():
    """启动时自检：只报告，不退出（缺东西前端仍能看到错误提示）。"""
    meta_ok = os.path.isfile(META_PATH)
    dem_ok = os.path.isfile(DEM_PATH)
    index_ok = os.path.isfile(INDEX_PATH)
    preview_ok = os.path.isfile(PREVIEW_PATH)
    typhoons = []
    if os.path.isdir(TYPHOON_DIR):
        typhoons = sorted(f[:-5] for f in os.listdir(TYPHOON_DIR) if f.endswith(".json"))

    t0 = time.perf_counter()
    engine_ok = engine_ready()
    import_ms = (time.perf_counter() - t0) * 1000.0

    flag = lambda ok: "OK  " if ok else "缺失"
    print("[readiness] data/meta.json=%s | data/dem.npy=%s | engine=%s | web/index.html=%s"
          % (flag(meta_ok), flag(dem_ok), flag(engine_ok), flag(index_ok)), flush=True)

    missing = []
    if not meta_ok or not dem_ok or not preview_ok:
        missing.append("S1 数据代理：data/meta.json、data/dem.npy、data/dem_preview.png")
    if not engine_ok:
        missing.append("S2 计算代理：engine/（engine.api.simulate）—— 原因: %s" % _engine["error"])
    if not index_ok:
        missing.append("S4 前端代理：web/index.html")
    if missing:
        print("[readiness] 降级运行，缺失项：" + "；".join(missing), flush=True)
        print("[readiness] 服务照常监听，缺失能力会以中文 error 返回给前端（不会白屏崩溃）", flush=True)
    else:
        print("[readiness] 全部就绪（engine 导入耗时 %.0f ms，台风样例 %d 个：%s）"
              % (import_ms, len(typhoons), ", ".join(typhoons) or "无"), flush=True)
    return meta_ok and dem_ok and engine_ok and index_ok


def main(argv=None):
    _prepare_console()
    parser = argparse.ArgumentParser(
        description="POSSIM-Web 演示服务（纯标准库 http.server，契约 §6）")
    parser.add_argument("--port", type=int, default=8848, help="监听端口，默认 8848")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    args = parser.parse_args(argv)

    print("=" * 68)
    print("  POSSIM-Web · 珠江口风暴潮淹没推演平台（演示服务）")
    print("=" * 68)
    print("  项目根目录 : %s" % ROOT)
    print("  启动地址   : http://%s:%d/" % (args.host, args.port))
    print("  停止服务   : 本窗口按 Ctrl+C")
    print("-" * 68)

    readiness_check()
    print("-" * 68, flush=True)

    try:
        httpd = POSSIMServer((args.host, args.port), Handler)
    except OSError as exc:
        if exc.errno in (errno.EADDRINUSE, 10048):
            print("[错误] 端口 %d 已被占用。" % args.port)
            print("       换一个端口重试，例如：python server.py --port %d" % (args.port + 1))
        else:
            print("[错误] 无法监听 %s:%d -> %s" % (args.host, args.port, exc))
        return 2

    httpd.daemon_threads = True
    print("[server] 已启动，等待请求 ...（浏览器访问 http://%s:%d/）" % (args.host, args.port), flush=True)
    try:
        httpd.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\n[server] 收到 Ctrl+C，正在停止 ...", flush=True)
    finally:
        try:
            httpd.shutdown()
        except Exception:
            pass
        httpd.server_close()
        print("[server] 已停止", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
