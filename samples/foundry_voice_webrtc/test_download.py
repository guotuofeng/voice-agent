import asyncio
import io
import json
import unittest
import wave
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from server import (DOWNLOAD_SECRET, ConversationDownloadError, SessionConfiguration, Settings,
                    conversation_download_token, create_app, forward_upstream, truncation_update,
                    verify_download_token, write_conversation_archive)


def recording():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setparams((2, 2, 24000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x01\x00\x02\x00" * 24)
    return output.getvalue()


class DownloadTests(unittest.IsolatedAsyncioTestCase):
    def test_download_tokens_are_bound_to_agent_server_and_expiry(self):
        secret = b"test signing secret"
        token = conversation_download_token(secret, "agent", "conv_test")
        self.assertEqual(verify_download_token(secret, "agent", token), "conv_test")
        for key, agent, candidate in ((b"other", "agent", token), (secret, "other", token),
                                      (secret, "agent", token + "x"), (secret, "agent", None)):
            with self.assertRaises(ConversationDownloadError):
                verify_download_token(key, agent, candidate)
        with patch("server.time.time", return_value=10**12), self.assertRaises(ConversationDownloadError):
            verify_download_token(secret, "agent", token)
        with self.assertRaises(ValueError):
            conversation_download_token(secret, "agent", "../../other")

    async def test_download_endpoint_rejects_unauthorized_or_invalid_requests_before_authentication(self):
        tokens = AsyncMock()
        cfg = Settings("https://example.services.ai.azure.com/api/projects/demo", "agent")
        app = create_app(cfg, tokens)
        async with TestClient(TestServer(app)) as client:
            origin = {"Origin": str(client.make_url("")).rstrip("/")}
            for body, status in (({}, 400), ({"downloadToken": "forged"}, 403),
                                 ({"conversation": "conv_other"}, 400)):
                response = await client.post("/api/conversation/download", headers=origin, json=body)
                self.assertEqual(response.status, status)
            response = await client.post("/api/conversation/download", json={"downloadToken": "forged"})
            self.assertEqual(response.status, 403)
            tokens.get_token.assert_not_awaited()

    async def test_archive_contains_all_paginated_items_and_the_recorded_wav(self):
        calls = []
        async def azure(request):
            calls.append((request.path, dict(request.query)))
            if request.path.endswith("/audio/content"):
                return web.Response(body=recording(), content_type="audio/wav")
            if request.path.endswith("/items"):
                after = request.query.get("after")
                return web.json_response({"data": [{"id": "second" if after else "first"}],
                    "has_more": not bool(after), "last_id": "second" if after else "first"})
            return web.json_response({"id": "conv_test", "status": "completed"})
        service = web.Application()
        service.router.add_get("/{path:.*}", azure)
        async with TestServer(service) as azure_server, aiohttp.ClientSession() as client:
            cfg = SimpleNamespace(agent="agent", api_version="v1", project_url=lambda: str(azure_server.make_url("/project")))
            output = io.BytesIO()
            await write_conversation_archive(client, cfg, {}, "conv_test", output)
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(archive.namelist(), ["conversation.json", "audio.wav"])
            self.assertEqual([item["id"] for item in json.loads(archive.read("conversation.json"))["items"]], ["first", "second"])
            self.assertEqual(archive.read("audio.wav"), recording())
        self.assertEqual(calls[2][1]["after"], "first")

    async def test_download_returns_zip_with_no_store_and_not_a_success_shaped_error(self):
        cfg = Settings("https://example.services.ai.azure.com/api/projects/demo", "agent")
        app = create_app(cfg, AsyncMock())
        token = conversation_download_token(app[DOWNLOAD_SECRET], "agent", "conv_test")
        async def writing(client, cfg, headers, conversation, output):
            with zipfile.ZipFile(output, "w") as archive:
                archive.writestr("conversation.json", "{}")
                archive.writestr("audio.wav", recording())
        async with TestClient(TestServer(app)) as client:
            origin = {"Origin": str(client.make_url("")).rstrip("/")}
            with patch("server.write_conversation_archive", writing):
                client.session.cookie_jar.update_cookies(
                    {"voice_agent_protocol": "live"}, response_url=client.make_url("/"))
                response = await client.post("/api/conversation/download", headers=origin, json={"downloadToken": token})
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertEqual(response.content_type, "application/zip")
                with zipfile.ZipFile(io.BytesIO(await response.read())) as archive:
                    self.assertIn("audio.wav", archive.namelist())
                response = await client.post("/api/conversation/download?agent_selection=live",
                                             headers=origin, json={"downloadToken": token})
                self.assertEqual(response.status, 403)
                live_token = conversation_download_token(app[DOWNLOAD_SECRET], "agent-live", "conv_live")
                response = await client.post("/api/conversation/download?agent_selection=live",
                                             headers=origin, json={"downloadToken": live_token})
                self.assertEqual(response.status, 200)
                await response.read()
            with patch("server.write_conversation_archive", AsyncMock(side_effect=ConversationDownloadError("Not ready", 409))):
                response = await client.post("/api/conversation/download", headers=origin, json={"downloadToken": token})
                self.assertEqual(response.status, 409)
                self.assertEqual(await response.json(), {"error": "Not ready"})

    async def test_upstream_announces_the_conversation_once_for_both_transports(self):
        for transport in ("websocket", "webrtc"):
            session = {"id": "session_test", "model": "gpt-realtime-2.1",
                       "turn_detection": {"type": "azure_semantic_vad", "create_response": True}}
            if transport == "websocket":
                session["audio"] = {"input": {"turn_detection": session.pop("turn_detection")}}
            acknowledged = {**session, **truncation_update(session)["session"]}
            events = [
                {"type": "session.created", "conversation_id": "conv_test", "session": session},
                {"type": "session.updated", "conversation_id": "conv_test", "session": session},
                {"type": "session.updated", "conversation_id": "conv_test", "session": acknowledged},
            ]
            upstream = AsyncMock()
            upstream.__aiter__.return_value = [
                SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(event)) for event in events]
            configuration = SessionConfiguration()
            callback, browser = AsyncMock(), AsyncMock()
            await forward_upstream(upstream, browser, transport, configuration, on_conversation=callback)
            callback.assert_awaited_once_with("conv_test")
            self.assertTrue(configuration.ready)
            upstream.send_json.assert_awaited_once_with(truncation_update(session))
            browser.send_json.assert_awaited_once_with({"type": "bridge.ready"})
            self.assertEqual([json.loads(call.args[0]) for call in browser.send_str.call_args_list], events)
