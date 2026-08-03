"""Bounded HTTP parser protocol (REQUIREMENTS.md API-20).

Uvicorn 0.52.1's h11 path bounds the incomplete request-line+headers buffer
pre-allocation via ``h11_max_incomplete_event_size`` but maps every parser
error to 400 and has no header-count cap (STACK-02 finding, M0). This custom
protocol class — wired via ``uvicorn.Config(http=...)`` — replicates the
h11 event loop, maps h11's ``error_status_hint`` (431) to the correct status,
and enforces the configured header-count cap before request processing.
"""

from __future__ import annotations

import asyncio
import contextvars
import http
import sys
from typing import Any, cast
from urllib.parse import unquote

import h11
from uvicorn.protocols.http import h11_impl
from uvicorn.protocols.http.flow_control import HIGH_WATER_LIMIT, service_unavailable


class BoundedH11Protocol(h11_impl.H11Protocol):
    """API-20: 431 mapping + header-count cap on the h11 parser path."""

    def __init__(self, config, server_state, app_state, _loop=None) -> None:
        super().__init__(config, server_state, app_state, _loop)
        self._max_header_count = 100
        loaded = getattr(config, "loaded_app", None)
        if loaded is not None:
            cfg = getattr(loaded, "server_config", None)
            if cfg is not None:
                try:
                    self._max_header_count = int(getattr(cfg, "maxHeaderCount", 100))
                except (TypeError, ValueError):
                    self._max_header_count = 100

    def handle_events(self) -> None:
        while True:
            try:
                event = self.conn.next_event()
            except h11.RemoteProtocolError as exc:
                # API-20: honor h11's error_status_hint (431) instead of the
                # base 400 mapping; 414 is not produced by h11.
                status = int(getattr(exc, "error_status_hint", 400) or 400)
                self.send_error_response(status, "Invalid HTTP request received.")
                return

            if event is h11.NEED_DATA:
                break
            elif event is h11.PAUSED:
                self.flow.pause_reading()
                break
            elif isinstance(event, h11.Request):
                # API-20: header-count cap (h11 has none).
                if len(event.headers) > self._max_header_count:
                    self.send_error_response(431, "Request header fields too large")
                    return
                self._process_request(event)
            elif isinstance(event, h11.Data):
                if self.conn.our_state is h11.DONE:
                    continue
                self.cycle.body += event.data
                if len(self.cycle.body) > HIGH_WATER_LIMIT:
                    self.flow.pause_reading()
                self.cycle.message_event.set()
            elif isinstance(event, h11.EndOfMessage):
                if self.conn.our_state is h11.DONE:
                    self.transport.resume_reading()
                    self.conn.start_next_cycle()
                    continue
                self.cycle.more_body = False
                self.cycle.message_event.set()

    # -- request handling (mirrors uvicorn's h11_impl Request branch) ---------

    def _process_request(self, event: h11.Request) -> None:
        self.headers = [(key.lower(), value) for key, value in event.headers]
        raw_path, _, query_string = event.target.partition(b"?")
        path = unquote(raw_path.decode("ascii"))
        full_path = self.root_path + path
        full_raw_path = self.root_path.encode("ascii") + raw_path
        self.scope = cast(
            Any,
            {
                "type": "http",
                "asgi": {"version": self.asgi_version, "spec_version": "2.3"},
                "http_version": event.http_version.decode("ascii"),
                "server": self.server,
                "client": self.client,
                "scheme": self.scheme,
                "method": event.method.decode("ascii"),
                "root_path": self.root_path,
                "path": full_path,
                "raw_path": full_raw_path,
                "query_string": query_string,
                "headers": self.headers,
                "state": self.app_state.copy(),
            },
        )
        if self._should_upgrade():
            self.handle_websocket_upgrade(event)
            return

        if self.limit_concurrency is not None and (
            len(self.connections) >= self.limit_concurrency
            or len(self.tasks) >= self.limit_concurrency
        ):
            app = service_unavailable
            self.logger.warning("Exceeded concurrency limit.")
        else:
            app = self.app

        self._unset_keepalive_if_required()

        self.cycle = h11_impl.RequestResponseCycle(
            scope=self.scope,
            conn=self.conn,
            transport=self.transport,
            flow=self.flow,
            logger=self.logger,
            access_logger=self.access_logger,
            access_log=self.access_log,
            default_headers=self.server_state.default_headers,
            message_event=asyncio.Event(),
            on_response=self.on_response_complete,
        )
        if self.config.reset_contextvars and sys.version_info >= (3, 11):
            task = self.loop.create_task(
                self.cycle.run_asgi(cast(Any, app)), context=contextvars.Context()
            )
        else:
            task = self.loop.create_task(self.cycle.run_asgi(cast(Any, app)))
        task.add_done_callback(self.tasks.discard)
        self.tasks.add(task)

    def send_error_response(self, status: int, msg: str) -> None:
        """Body-free error response + close (API-00: parser-bound rejections
        may predate the ASGI scope and request id)."""
        phrase = _status_phrase(status)
        headers = [
            (b"content-type", b"text/plain; charset=utf-8"),
            (b"connection", b"close"),
        ]
        try:
            for event in (
                h11.Response(status_code=status, headers=headers, reason=phrase),
                h11.Data(data=msg.encode("ascii", errors="replace")),
                h11.EndOfMessage(),
            ):
                self.transport.write(self.conn.send(event))
        except h11.LocalProtocolError as exc:
            self.logger.warning("protocol error while sending response: %s", exc)
        self.transport.close()


def _status_phrase(status: int) -> str:
    try:
        return http.HTTPStatus(status).phrase
    except ValueError:
        return ""
