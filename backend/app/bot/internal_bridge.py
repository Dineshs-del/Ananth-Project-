"""Localhost-only authenticated internal HTTP bridge for the Telegram bot process.

Allows the FastAPI backend process to invoke the bot's existing `notify_live_state`
function without creating a second Telegram bot or adding third-party packages.
Disabled unless BOT_INTERNAL_TOKEN is set in the environment.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
from typing import Any

from .live_call_alerts import notify_live_state

logger = logging.getLogger("voicegate.bot.bridge")

DEFAULT_INTERNAL_HOST = "127.0.0.1"
DEFAULT_INTERNAL_PORT = 8765
MAX_INTERNAL_BODY_BYTES = 64 * 1024


def _http_response(status_code: int, reason: str, payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    headers = (
        f"HTTP/1.1 {status_code} {reason}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode("ascii")
    return headers + body


class BotInternalBridge:
    """Minimal asyncio HTTP server bound strictly to 127.0.0.1 for internal live-state pushes."""

    def __init__(
        self,
        *,
        host: str = DEFAULT_INTERNAL_HOST,
        port: int | None = None,
        token: str | None = None,
    ) -> None:
        if host not in {"127.0.0.1", "::1"}:
            raise ValueError(
                "BotInternalBridge host must be a loopback address (127.0.0.1 or ::1)."
            )
        self._host = host
        if port is None:
            raw_port = os.getenv("BOT_INTERNAL_PORT", str(DEFAULT_INTERNAL_PORT)).strip()
            try:
                self._port = max(1024, min(int(raw_port), 65535))
            except ValueError:
                self._port = DEFAULT_INTERNAL_PORT
        else:
            self._port = port
        self._token = (
            token
            if token is not None
            else (os.getenv("BOT_INTERNAL_TOKEN", "").strip() or None)
        )
        self._server: asyncio.AbstractServer | None = None

    @property
    def port(self) -> int:
        return self._port

    @property
    def enabled(self) -> bool:
        return bool(self._token)

    async def start(self) -> bool:
        if not self._token:
            logger.info(
                "BOT_INTERNAL_TOKEN is not set; localhost internal live-state bridge is disabled."
            )
            return False
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self._host,
            port=self._port,
        )
        sockets = self._server.sockets or []
        if sockets:
            self._port = sockets[0].getsockname()[1]
        logger.info(
            "Bot internal live-state bridge listening on http://%s:%d/internal/live-state",
            self._host,
            self._port,
        )
        return True

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            raw_head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5.0)
        except Exception:
            writer.close()
            await writer.wait_closed()
            return

        try:
            head_text = raw_head.decode("iso-8859-1")
            lines = head_text.split("\r\n")
            request_line = lines[0].split(" ")
            if len(request_line) < 2:
                writer.write(_http_response(400, "Bad Request", {"detail": "Malformed request."}))
                await writer.drain()
                return

            method, path = request_line[0].upper(), request_line[1].split("?", 1)[0]
            headers: dict[str, str] = {}
            for line in lines[1:]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    headers[k.strip().lower()] = v.strip()

            if method != "POST" or path != "/internal/live-state":
                writer.write(_http_response(404, "Not Found", {"detail": "Not found."}))
                await writer.drain()
                return

            auth = headers.get("authorization", "")
            if not auth.startswith("Bearer "):
                writer.write(
                    _http_response(401, "Unauthorized", {"detail": "Missing Bearer token."})
                )
                await writer.drain()
                return
            provided = auth[len("Bearer ") :].strip().encode("utf-8")
            if not self._token or not hmac.compare_digest(provided, self._token.encode("utf-8")):
                writer.write(
                    _http_response(403, "Forbidden", {"detail": "Invalid Bearer token."})
                )
                await writer.drain()
                return

            try:
                content_length = int(headers.get("content-length", "0"))
            except ValueError:
                content_length = -1

            if content_length <= 0 or content_length > MAX_INTERNAL_BODY_BYTES:
                writer.write(
                    _http_response(400, "Bad Request", {"detail": "Invalid Content-Length."})
                )
                await writer.drain()
                return

            raw_body = await asyncio.wait_for(reader.readexactly(content_length), timeout=5.0)
            payload = json.loads(raw_body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object.")

            request_id = str(payload.get("request_id") or "").strip()
            state = str(payload.get("state") or "").strip()
            last_result = payload.get("last_result")
            if not request_id:
                writer.write(
                    _http_response(400, "Bad Request", {"detail": "request_id is required."})
                )
                await writer.drain()
                return

            result = await notify_live_state(
                request_id,
                state,
                last_result if isinstance(last_result, dict) else None,
            )
            writer.write(_http_response(200, "OK", result))
            await writer.drain()
        except Exception:
            logger.warning("Failed to process internal live-state request.")
            try:
                writer.write(
                    _http_response(500, "Internal Server Error", {"detail": "Internal error."})
                )
                await writer.drain()
            except Exception:
                pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
