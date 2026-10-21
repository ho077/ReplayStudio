"""obs-websocket v5 client, built on Qt's own QWebSocket.

Why QWebSocket and not a third-party websocket package: QtWebSockets ships with
PySide6, so Phase 3 adds **no new dependency**, and the socket lives on the Qt
event loop — no worker thread, no manual pumping, and every callback is a
signal that other widgets can connect to directly.

The wire protocol (obs-websocket 5.x) is small enough to state here in full:

    op 0  Hello            server greeting; carries the auth challenge/salt
    op 1  Identify         our reply: rpcVersion + derived secret
    op 2  Identified       handshake complete (carries negotiated rpcVersion)
    op 5  Event            server push (RecordStateChanged, ...)
    op 6  Request          our RPC call, correlated by `requestId`
    op 7  RequestResponse  the answer; `requestStatus.result` says if it worked

Auth secret derivation (RFC-ish, from the obs-websocket docs):

    secret = base64( sha256( base64( sha256(password + salt) ) + challenge ) )
"""
from __future__ import annotations

import base64
import hashlib
import itertools
import json

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtWebSockets import QWebSocket, QWebSocketProtocol

# --------------------------------------------------------------------- op codes
HELLO = 0
IDENTIFY = 1
IDENTIFIED = 2
REIDENTIFY = 3
EVENT = 5
REQUEST = 6
REQUEST_RESPONSE = 7
REQUEST_BATCH = 8
REQUEST_BATCH_RESPONSE = 9

RPC_VERSION = 1

# EventSubscription bit flags — we only ask for what the workbench reacts to
# (General=1, Scenes=4, Inputs=8, Outputs=64). Subscribing to "everything"
# also drags in SceneItems/MediaInputs, which are chatty and unused here.
SUB_GENERAL = 1 << 0
SUB_CONFIG = 1 << 1
SUB_SCENES = 1 << 2
SUB_INPUTS = 1 << 3
SUB_TRANSITIONS = 1 << 4
SUB_FILTERS = 1 << 5
SUB_OUTPUTS = 1 << 6
SUB_SCENE_ITEMS = 1 << 7
SUB_MEDIA_INPUTS = 1 << 8
SUB_VENDORS = 1 << 9
SUB_UI = 1 << 10
SUBSCRIPTIONS = SUB_GENERAL | SUB_SCENES | SUB_INPUTS | SUB_OUTPUTS

# requestStatus.code -> Chinese explanation. Anything unmapped falls back to
# OBS's own `comment`, which is usually precise enough.
STATUS_TEXT = {
    100: "成功",
    201: "请求类型为空",
    202: "请求类型无效",
    203: "缺少必填字段",
    204: "字段类型不对",
    205: "请求类型不存在（OBS 版本较旧？）",
    206: "该请求在 OBS 侧被禁用",
    207: "请求执行失败",
    208: "指定的场景或输入不存在",
    209: "同名资源已存在",
    210: "找不到该资源",
    211: "资源已处于目标状态",
    212: "资源不足（例如没有可用设备）",
    213: "没有正在运行的输出",
    214: "输出未暂停",
    215: "当前模式不支持该操作",
    216: "当前场景/输入被锁定",
}


class ObsError(Exception):
    """A request that OBS answered with `result: false`."""

    def __init__(self, code: int, comment: str, request_type: str = ""):
        text = STATUS_TEXT.get(code, comment or f"错误码 {code}")
        super().__init__(f"{request_type}：{text}" if request_type else text)
        self.code = code
        self.comment = comment
        self.request_type = request_type


def auth_secret(password: str, salt: str, challenge: str) -> str:
    """Derive the Identify `authentication` field."""
    def sha256_b64(raw: bytes) -> str:
        return base64.b64encode(hashlib.sha256(raw).digest()).decode("ascii")

    inner = sha256_b64((password + salt).encode("utf-8"))
    return sha256_b64((inner + challenge).encode("utf-8"))


class ObsClient(QObject):
    """One connection to one OBS instance. Reconnects on its own if asked to."""

    identified = Signal(dict)          # {"obsVersion":…, "obsWebSocketVersion":…, "rpcVersion":…}
    disconnected = Signal(str)         # human-readable reason
    failed = Signal(str)               # connection-level problem
    eventReceived = Signal(str, dict)  # (eventType, eventData)

    def __init__(self, host: str = "127.0.0.1", port: int = 4455,
                 password: str = "", parent=None):
        super().__init__(parent)
        self.host = host
        self.port = port
        self.password = password or ""
        self.auto_reconnect = True
        self.state = "closed"          # closed | connecting | hello | ready
        self.server: dict = {}

        # PySide6 keeps this enum on the module, not on the class (Qt5 had it as
        # QWebSocket.ProtocolVersion, which simply does not exist here).
        self._ws = QWebSocket("", QWebSocketProtocol.Version13, self)
        self._ws.connected.connect(self._on_connected)
        self._ws.disconnected.connect(self._on_disconnected)
        self._ws.textMessageReceived.connect(self._on_message)
        self._ws.errorOccurred.connect(self._on_error)
        self._ws.setParent(self)

        self._ids = itertools.count(1)
        self._pending: dict[str, dict] = {}
        self._attempts = 0

    # ---------------------------------------------------------------- lifecycle
    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}"

    def open(self):
        if self.state in ("connecting", "hello", "ready"):
            return
        self.state = "connecting"
        self._ws.open(QUrl(self.url))

    def close(self):
        self.auto_reconnect = False
        self.state = "closed"
        try:
            self._ws.close()
        except RuntimeError:
            pass

    @property
    def ready(self) -> bool:
        return self.state == "ready"

    # -------------------------------------------------------------- socket slots
    def _on_connected(self):
        self._attempts = 0
        self.state = "hello"           # waiting for Hello

    def _on_disconnected(self):
        was = self.state
        self.state = "closed"
        for rid, entry in list(self._pending.items()):
            timer: QTimer | None = entry.get("timer")
            if timer is not None:
                timer.stop()
            cb = entry.get("cb")
            if cb is not None:
                cb(None, "连接已断开")
            self._pending.pop(rid, None)
        self.disconnected.emit("连接已断开" if was == "ready" else "无法连接")
        if self.auto_reconnect:
            self._attempts += 1
            delay = min(1000 * self._attempts, 8000)
            QTimer.singleShot(delay, self.open)

    def _on_error(self, _code):
        err = self._ws.errorString()
        self.failed.emit(f"WebSocket 错误：{err}")
        # errorOccurred is followed by disconnected(), which schedules the retry.

    def _on_message(self, text: str):
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            return
        op = msg.get("op")
        d = msg.get("d") or {}
        if op == HELLO:
            self._send_identify(d)
        elif op == IDENTIFIED:
            self.state = "ready"
            # Identified only carries the negotiated RPC version — reporting that
            # as the software version made the UI claim "obs-websocket 1". The
            # real version comes from GetVersion.
            self.server = {"rpcVersion": d.get("negotiatedRpcVersion", RPC_VERSION)}
            self.identified.emit(self.server)
        elif op == EVENT:
            self.eventReceived.emit(d.get("eventType", ""), d.get("eventData") or {})
        elif op in (REQUEST_RESPONSE, REQUEST_BATCH_RESPONSE):
            self._finish(d)

    # ---------------------------------------------------------------- handshake
    def _send_identify(self, hello: dict):
        payload = {"rpcVersion": RPC_VERSION, "eventSubscriptions": SUBSCRIPTIONS}
        auth = hello.get("authentication")
        if auth:
            if not self.password:
                self.failed.emit("OBS 要求口令鉴权，但没有提供口令")
                self.close()
                return
            payload["authentication"] = auth_secret(
                self.password, auth.get("salt", ""), auth.get("challenge", ""))
        self._send(IDENTIFY, payload)

    def _send(self, op: int, data: dict):
        self._ws.sendTextMessage(json.dumps({"op": op, "d": data}, ensure_ascii=False))

    # ----------------------------------------------------------------- requests
    def call(self, request_type: str, data: dict | None = None,
             cb=None, timeout: float = 5.0) -> str:
        """Fire a request. `cb(status, payload)` receives either the response
        data dict, or (None, error message) on failure/timeout."""
        rid = str(next(self._ids))
        entry: dict = {"cb": cb, "type": request_type}
        if cb is not None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: self._timeout(rid))
            timer.start(int(timeout * 1000))
            entry["timer"] = timer
        self._pending[rid] = entry
        self._send(REQUEST, {"requestType": request_type, "requestId": rid,
                             "requestData": data or {}})
        return rid

    def _timeout(self, rid: str):
        entry = self._pending.pop(rid, None)
        if entry is None:
            return
        cb = entry.get("cb")
        if cb is not None:
            cb(None, f"{entry.get('type', '')} 超时（OBS 没在 5 秒内回应）")

    def _finish(self, d: dict):
        rid = str(d.get("requestId", ""))
        entry = self._pending.pop(rid, None)
        if entry is None:
            return
        timer: QTimer | None = entry.get("timer")
        if timer is not None:
            timer.stop()
        cb = entry.get("cb")
        if cb is None:
            return
        status = d.get("requestStatus") or {}
        if status.get("result"):
            cb(d.get("responseData") or {}, "")
        else:
            err = ObsError(int(status.get("code", -1) or -1),
                           status.get("comment", ""), d.get("requestType", ""))
            cb(None, str(err))

    def call_sync(self, request_type: str, data: dict | None = None,
                  timeout: float = 5.0):
        """Blocking variant for probes/tests and for the short queries the UI
        does while opening a page. Returns (data|None, error_message)."""
        from PySide6.QtCore import QEventLoop

        box: dict = {"done": False}

        def cb(payload, err):
            box["data"], box["err"] = payload, err
            box["done"] = True

        self.call(request_type, data, cb, timeout)
        loop = QEventLoop()
        guard = QTimer(self)
        guard.setSingleShot(True)
        guard.timeout.connect(loop.quit)
        guard.start(int(timeout * 1000) + 800)
        while not box["done"]:
            loop.exec()
            if not box["done"] and not guard.isActive():
                break
        guard.stop()
        if not box["done"]:
            return None, f"{request_type} 超时"
        return box.get("data"), box.get("err", "")

    # ------------------------------------------------------------- convenience
    def get_version(self):
        return self.call_sync("GetVersion")

    def get_record_status(self):
        return self.call_sync("GetRecordStatus")

    def start_record(self):
        return self.call_sync("StartRecord")

    def stop_record(self):
        return self.call_sync("StopRecord")

    def get_scene_list(self):
        return self.call_sync("GetSceneList")

    def get_input_kind_list(self):
        return self.call_sync("GetInputKindList")

    def get_input_list(self):
        return self.call_sync("GetInputList")

    def get_video_settings(self):
        return self.call_sync("GetVideoSettings")

    def get_record_directory(self):
        return self.call_sync("GetRecordDirectory")

    def exit_obs(self):
        """Ask OBS to shut down on its own (obs-websocket 5.1+).

        This is the only exit path that lets OBS delete its `.sentinel` marker,
        which is what keeps the "unclean shutdown" dialog from coming back.
        """
        return self.call_sync("Exit")
