"""GPT Live versioning, transport and event isolation regressions."""

import asyncio
import copy
import json
from pathlib import Path
import re
import unittest
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp.test_utils import TestClient, TestServer

from check_websocket import check_live
from live import (LiveSessionError, connect_live, live_client_frame,
                  live_definition, personalization_events, relay_live)
from server import (ACTIVE, AgentPreparationError, Settings, companion_settings, create_app,
                    recreate_configured_agent)
from test_agent_creation import Response
from test_model_management import ServiceClient


async def frames(events):
    for event in events:
        yield aiohttp.WSMessage(aiohttp.WSMsgType.TEXT, json.dumps(event), "")


class LiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_bridge_does_not_send_realtime_voice_override(self):
        cfg = Settings("https://example.services.ai.azure.com/api/projects/demo", "demo")
        tokens = AsyncMock()
        tokens.get_token.return_value = "test-token"
        latest = {"version": "1", "definition": {"sub_protocol": "live"}}

        async def connect(client, browser, cfg, headers, *args):
            self.assertNotIn("x-ms-voice-session-override", headers)
            self.assertEqual(cfg.agent, "demo-live")
            await browser.send_json({"type": "test.ready"})

        with (
            patch("server.lookup_configured_agent", AsyncMock(return_value=latest)),
            patch("server.connect_live", connect),
        ):
            async with TestClient(TestServer(create_app(cfg, tokens))) as client:
                origin = str(client.make_url("")).rstrip("/")
                async with client.ws_connect("/api/voice?transport=websocket&protocol=live&voice=marin&agent_selection=live",
                                             headers={"Origin": origin}) as socket:
                    self.assertEqual((await socket.receive_json())["type"], "test.ready")

    def definition(self):
        return json.loads(Path(__file__).with_name("personalized-agent.json").read_text())

    def test_live_voice_uses_saved_settings_without_a_selector(self):
        page = Path(__file__).with_name("public").joinpath("index.html").read_text(encoding="utf-8")
        self.assertNotIn('<select id="live-voice"', page)
        self.assertIn('<p id="live-voice-info" hidden>Saved Agent voice</p>', page)

    def test_live_definition_uses_deployments_and_no_realtime_only_fields(self):
        source = self.definition()
        before = copy.deepcopy(source)
        live = live_definition(source, "gpt-live-1-test", "gpt-5.4")
        self.assertEqual(source, before)
        self.assertEqual(live["model_type"], "self_deployed")
        self.assertEqual(live["sub_protocol"], "live")
        self.assertEqual(live["model"], "gpt-live-1-test")
        self.assertEqual(live["audio"]["output"], {"voice": "marin"})
        self.assertNotIn("input", live["audio"])
        self.assertNotIn("tools", live)
        self.assertNotIn("greeting", live)
        self.assertNotIn("output_modalities", live)
        backend = live["delegation"]["responses"]
        self.assertEqual(backend["model"], "gpt-5.4")
        self.assertEqual(backend["tools"], [{"type": "web_search"}])
        self.assertNotIn("{{", backend["instructions"])
        self.assertIn("web search on any topic", live["instructions"])
        self.assertIn("search is not limited to weather or current events", backend["instructions"])
        self.assertIn("only when the requested search needs it", backend["instructions"])

    def test_personalization_is_trusted_policy_then_bounded_untrusted_context(self):
        values = {"language_policy": "Use English.", "display_name": "A", "client_language": "en-US",
                  "user_context": "\u4e2d" * 2000}
        events = personalization_events(values)
        self.assertEqual(events[0], {"type": "session.instructions.append",
                                    "delegation_id": None, "content": "Use English."})
        self.assertTrue(all(event["type"] == "session.thinking.append" for event in events[1:]))
        self.assertTrue(all(len(event["content"].encode("utf-8")) < 500 for event in events))
        self.assertEqual(personalization_events(None), [])

    async def test_protocol_changes_require_separate_agents_without_publishing_or_enabling(self):
        source = self.definition()
        live = live_definition(source, "live-deploy", "backend")
        cfg = Settings("https://example.services.ai.azure.com/api/projects/demo")
        for definition, model in ((source, "gpt-live-1"), (live, source["model"])):
            with self.subTest(model=model):
                old = {"versions": {"latest": {"version": "1", "definition": definition,
                                               "metadata": {"realtime_source_version": "1"}}}}
                service = ServiceClient([Response(200, old)])
                async with TestClient(TestServer(create_app(cfg, AsyncMock()))) as client:
                    with patch("server.aiohttp.ClientSession", return_value=service):
                        response = await client.post(
                            "/api/agent/model",
                            headers={"Origin": str(client.make_url("")).rstrip("/")},
                            json={"model": model, "version": "1"})
                        result = await response.json()
                self.assertEqual(response.status, 409, result)
                self.assertIn("separate Agent", result["error"])
                self.assertIn("sub_protocol", result["error"])
                self.assertEqual(service.writes, [])
                self.assertEqual(service.removals, [])

    async def test_existing_live_agent_remains_usable_without_publishing_another_version(self):
        definition = live_definition(self.definition(), "live-deploy", "backend")
        agent = {"versions": {"latest": {"version": "1", "definition": definition}}}
        service = ServiceClient([Response(200, agent), Response(200, agent)], [Response(204)])
        cfg = Settings("https://example.services.ai.azure.com/api/projects/demo")
        async with TestClient(TestServer(create_app(cfg, AsyncMock()))) as client:
            with patch("server.aiohttp.ClientSession", return_value=service):
                response = await client.post(
                    "/api/agent/model",
                    headers={"Origin": str(client.make_url("")).rstrip("/")},
                    json={"model": "gpt-live-1", "version": "1"})
                result = await response.json()
        self.assertEqual(response.status, 200, result)
        self.assertEqual(result["modelChoice"], "gpt-live-1")
        self.assertEqual(len(service.writes), 1)
        self.assertTrue(service.writes[0][0].endswith(":enable"))
        self.assertEqual(service.removals, [])

    async def test_tab_selection_is_explicit_and_ignores_shared_legacy_cookie(self):
        cfg = Settings("https://example.services.ai.azure.com/api/projects/demo", "demo-agent")
        async with TestClient(TestServer(create_app(cfg, AsyncMock()))) as client:
            client.session.cookie_jar.update_cookies(
                {"voice_agent_protocol": "live"}, response_url=client.make_url("/"))
            for selection, agent in (("base", cfg.agent), ("live", "demo-agent-live"),
                                     ("base", cfg.agent)):
                response = await client.get(f"/api/config?agent_selection={selection}")
                self.assertEqual((await response.json())["agent"], agent)
            response = await client.get("/api/config")
            self.assertEqual((await response.json())["agent"], cfg.agent)
            response = await client.get("/api/config?agent_selection=arbitrary-agent")
            self.assertEqual(response.status, 400)

    async def test_switch_creates_companion_and_switch_back_reuses_original_agent(self):
        cfg = Settings("https://example.services.ai.azure.com/api/projects/demo", "demo-agent",
                       personalization_required=True)
        source = self.definition()
        original = {"versions": {"latest": {"version": "1", "definition": source}}}
        live = live_definition(source, cfg.live_deployment, cfg.live_backend_deployment)
        companion = {"versions": {"latest": {
            "version": "1", "definition": live, "metadata": {"companion_of": cfg.agent}
        }}}
        service = ServiceClient(
            [Response(200, original), Response(404), Response(200), Response(200, companion)],
            [Response(201, {"version": "1"}), Response(204)])
        async with TestClient(TestServer(create_app(cfg, AsyncMock()))) as client:
            origin = {"Origin": str(client.make_url("")).rstrip("/")}
            with patch("server.aiohttp.ClientSession", return_value=service):
                response = await client.post("/api/agent/switch", headers=origin,
                                             json={"model": "gpt-live-1", "version": "1"})
                result = await response.json()
            self.assertEqual(response.status, 200, result)
            self.assertEqual(result["agent"], "demo-agent-live")
            self.assertEqual(result["agentSelection"], "live")
            self.assertNotIn("voice_agent_protocol", response.cookies)
            self.assertTrue(result["created"])
            self.assertIn("/agents/demo-agent-live/versions", service.writes[0][0])
            self.assertEqual(service.writes[0][1]["json"]["definition"], live)
            self.assertEqual(service.removals, [])
            with patch("server.aiohttp.ClientSession", return_value=ServiceClient([Response(200, companion)])):
                response = await client.get("/api/agent?agent_selection=live")
                self.assertEqual((await response.json())["agent"], "demo-agent-live")
            response = await client.get("/api/config?agent_selection=live")
            self.assertEqual((await response.json())["agent"], "demo-agent-live")
            restored = ServiceClient([Response(200, companion), Response(200, original),
                                      Response(200, original), Response(200, original)], [Response(204)])
            with patch("server.aiohttp.ClientSession", return_value=restored):
                response = await client.post("/api/agent/switch?agent_selection=live", headers=origin,
                                             json={"model": source["model"], "version": "1"})
                result = await response.json()
            self.assertEqual(response.status, 200, result)
            self.assertEqual(result["agent"], cfg.agent)
            self.assertEqual(result["agentSelection"], "base")
            self.assertFalse(result["created"])
            self.assertEqual(len(restored.writes), 1)
            self.assertIn("/agents/demo-agent:enable", restored.writes[0][0])
            self.assertEqual(restored.removals, [])
            reused = ServiceClient([Response(200, original), Response(200, companion), Response(200, companion)],
                                   [Response(204)])
            with patch("server.aiohttp.ClientSession", return_value=reused):
                response = await client.post("/api/agent/switch", headers=origin,
                                             json={"model": "gpt-live-1", "version": "1"})
                result = await response.json()
            self.assertEqual(response.status, 200, result)
            self.assertFalse(result["created"])
            self.assertEqual(len(reused.writes), 1)
            self.assertTrue(reused.writes[0][0].split("?")[0].endswith(":enable"))

    async def test_switch_validation_active_calls_stale_versions_and_collisions_do_not_publish(self):
        cfg = Settings("https://example.services.ai.azure.com/api/projects/demo", "demo-agent")
        original = {"versions": {"latest": {"version": "1", "definition": self.definition()}}}
        tokens = AsyncMock()
        app = create_app(cfg, tokens)
        async with TestClient(TestServer(app)) as client:
            origin = {"Origin": str(client.make_url("")).rstrip("/")}
            response = await client.post("/api/agent/switch", json={"model": "gpt-live-1", "version": "1"})
            self.assertEqual(response.status, 403)
            for body in ([], {}, {"model": "unknown", "version": "1"},
                         {"model": "gpt-live-1", "version": "1", "agent": "injected"}):
                response = await client.post("/api/agent/switch", headers=origin, json=body)
                self.assertEqual(response.status, 400)
            app[ACTIVE].active = True
            response = await client.post("/api/agent/switch", headers=origin,
                                         json={"model": "gpt-live-1", "version": "1"})
            self.assertEqual(response.status, 409)
            tokens.get_token.assert_not_awaited()
            app[ACTIVE].active = False
            for version, gets, expected in (
                    ("old", [Response(200, original)], 409),
                    ("1", [Response(200, original), Response(200, original)], 502)):
                service = ServiceClient(gets)
                with patch("server.aiohttp.ClientSession", return_value=service):
                    response = await client.post("/api/agent/switch", headers=origin,
                                                 json={"model": "gpt-live-1", "version": version})
                self.assertEqual(response.status, expected)
                self.assertEqual(service.writes, [])
                self.assertNotIn("voice_agent_protocol", response.cookies)

    async def test_failed_live_creation_leaves_browser_on_original_agent(self):
        cfg = Settings("https://example.services.ai.azure.com/api/projects/demo", "demo-agent")
        original = {"versions": {"latest": {"version": "1", "definition": self.definition()}}}
        for posts in ([Response(400)], [Response(201, {"version": "1"}), Response(500)]):
            service = ServiceClient([Response(200, original), Response(404), Response(200)], posts)
            async with TestClient(TestServer(create_app(cfg, AsyncMock()))) as client:
                with patch("server.aiohttp.ClientSession", return_value=service):
                    response = await client.post(
                        "/api/agent/switch", headers={"Origin": str(client.make_url("")).rstrip("/")},
                        json={"model": "gpt-live-1", "version": "1"})
                self.assertEqual(response.status, 502)
                self.assertNotIn("voice_agent_protocol", response.cookies)
                response = await client.get("/api/config")
                self.assertEqual((await response.json())["agent"], cfg.agent)
                self.assertEqual(service.removals, [])

    async def test_configured_live_agent_does_not_create_a_realtime_companion(self):
        cfg = Settings("https://example.services.ai.azure.com/api/projects/demo", "live-agent")
        live = live_definition(self.definition(), "live", "backend")
        original = {"versions": {"latest": {"version": "1", "definition": live}}}
        service = ServiceClient([Response(200, original)])
        async with TestClient(TestServer(create_app(cfg, AsyncMock()))) as client:
            with patch("server.aiohttp.ClientSession", return_value=service):
                response = await client.post(
                    "/api/agent/switch", headers={"Origin": str(client.make_url("")).rstrip("/")},
                    json={"model": "gpt-5.4", "version": "1"})
                result = await response.json()
        self.assertEqual(response.status, 409, result)
        self.assertIn("separate realtime Agent", result["error"])
        self.assertEqual(service.writes, [])
        self.assertEqual(service.removals, [])

    async def test_missing_selected_companion_falls_back_without_creating_an_agent(self):
        cfg = Settings("https://example.services.ai.azure.com/api/projects/demo", "demo-agent")
        original = {"versions": {"latest": {"version": "1", "definition": self.definition()}}}
        service = ServiceClient([Response(404), Response(200), Response(200, original)])
        async with TestClient(TestServer(create_app(cfg, AsyncMock()))) as client:
            client.session.cookie_jar.update_cookies(
                {"voice_agent_protocol": "live"}, response_url=client.make_url("/"))
            with patch("server.aiohttp.ClientSession", return_value=service):
                response = await client.get("/api/agent?agent_selection=live")
                result = await response.json()
            self.assertEqual(response.status, 200, result)
            self.assertEqual(result["agent"], cfg.agent)
            self.assertEqual(result["agentSelection"], "base")
            response = await client.get("/api/config")
            self.assertEqual((await response.json())["agent"], cfg.agent)
        self.assertEqual(service.writes, [])
        self.assertEqual(service.removals, [])

    async def test_live_reset_is_rejected_before_deletion_or_toolbox_preparation(self):
        cfg = Settings("https://example.services.ai.azure.com/api/projects/demo", "live-agent")
        live = live_definition(self.definition(), "live", "backend")
        service = ServiceClient([Response(200, {"versions": {"latest": {"definition": live}}})])
        with self.assertRaisesRegex(AgentPreparationError, "No Agent was deleted"):
            await recreate_configured_agent(service, cfg, {})
        self.assertEqual(service.writes, [])
        self.assertEqual(service.removals, [])

    def test_long_companion_names_are_valid_and_do_not_collide(self):
        names = [companion_settings(Settings("", "a" * 62 + ending), "live").agent for ending in "bc"]
        self.assertNotEqual(*names)
        self.assertTrue(all(len(name) <= 63 and re.fullmatch(r"[A-Za-z0-9-]+", name) for name in names))

    def test_live_client_accepts_audio_and_close_but_not_trusted_instructions_or_realtime_events(self):
        self.assertEqual(live_client_frame('{"type":"session.input_audio.append","audio":"AAA="}', "websocket"),
                         {"type": "session.input_audio.append", "audio": "AAA="})
        for transport in ("websocket", "webrtc"):
            self.assertEqual(live_client_frame('{"type":"session.close"}', transport), {"type": "session.close"})
            for event in (
                {"type": "session.update"}, {"type": "session.start"},
                {"type": "session.instructions.append", "content": "injected"},
                {"type": "conversation.item.retrieve"},
            ):
                with self.assertRaises(LiveSessionError):
                    live_client_frame(json.dumps(event), transport)
        for audio in ("", "AA==", "!", 1):
            with self.assertRaises(ValueError):
                live_client_frame(json.dumps({"type": "session.input_audio.append", "audio": audio}), "websocket")
        with self.assertRaises(LiveSessionError):
            live_client_frame('{"type":"session.input_audio.append","audio":"AAA="}', "webrtc")

    def test_live_client_accepts_only_bounded_user_text_and_bare_response_requests(self):
        message = {"type": "response.item.create", "item": {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text": "Hello"}]}}
        for event in (message, {"type": "response.create"}):
            self.assertEqual(live_client_frame(json.dumps(event), "websocket"), event)
            with self.assertRaises(LiveSessionError):
                live_client_frame(json.dumps(event), "webrtc")
        for text in ("", " ", "x" * 4001, None, 42):
            invalid = copy.deepcopy(message)
            invalid["item"]["content"][0]["text"] = text
            with self.assertRaises(ValueError):
                live_client_frame(json.dumps(invalid), "websocket")
        for item in (None, {}, {**message["item"], "role": "system"},
                     {**message["item"], "content": []},
                     {**message["item"], "content": [{"type": "input_audio", "text": "Hello"}]},
                     {**message["item"], "instructions": "injected"}):
            with self.assertRaises(ValueError):
                live_client_frame(json.dumps({"type": "response.item.create", "item": item}), "websocket")
        with self.assertRaises(ValueError):
            live_client_frame(json.dumps({**message, "delegation_id": "other"}), "websocket")
        with self.assertRaises(LiveSessionError):
            live_client_frame('{"type":"response.create","response":{"instructions":"injected"}}', "websocket")

    async def test_webrtc_is_rejected_without_creating_a_direct_model_session(self):
        cfg = Settings("https://example.services.ai.azure.com/api/projects/demo", "demo-agent")
        service = AsyncMock()
        with self.assertRaisesRegex(LiveSessionError, "require WebSocket"):
            await connect_live(service, AsyncMock(), cfg, {}, "webrtc", "4", AsyncMock())
        service.post.assert_not_called()
        service.ws_connect.assert_not_called()

    async def test_live_ready_does_not_send_realtime_truncation_update(self):
        started = {"type": "session.started", "conversation_id": "conversation-1",
                   "session": {"audio": {"format": {"type": "audio/pcm", "rate": 24000}}}}
        upstream, browser, announce = AsyncMock(), AsyncMock(), AsyncMock()
        browser.closed = False
        upstream.__aiter__.side_effect = lambda: frames([
            started, {"type": "session.input_transcript.delta", "delta": "hi"},
            {"type": "session.closed", "usage": {"seconds": 1}},
        ])
        browser.__aiter__.side_effect = lambda: frames([])
        await relay_live(browser, upstream, announce)
        self.assertIn({"type": "bridge.ready", "protocol": "live"},
                      [call.args[0] for call in browser.send_json.call_args_list])
        announce.assert_awaited_once_with("conversation-1")
        self.assertFalse(any(call.args[0]["type"] == "session.update" for call in upstream.send_json.call_args_list))

    async def test_live_relay_forwards_native_text_pair_after_readiness(self):
        upstream, browser = AsyncMock(), AsyncMock()
        browser.closed = False
        ready, closing = asyncio.Event(), asyncio.Event()
        message = {"type": "response.item.create", "item": {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text": "Hello"}]}}

        async def receive_frames():
            yield aiohttp.WSMessage(aiohttp.WSMsgType.TEXT, json.dumps({
                "type": "session.started", "session": {
                    "audio": {"format": {"type": "audio/pcm", "rate": 24000}}}}), "")
            await closing.wait()
            yield aiohttp.WSMessage(aiohttp.WSMsgType.TEXT, '{"type":"session.closed"}', "")

        async def send_frames():
            await ready.wait()
            async for frame in frames([message, {"type": "response.create"}, {"type": "session.close"}]):
                yield frame

        async def to_browser(frame):
            if frame["type"] == "bridge.ready":
                ready.set()

        async def to_upstream(frame):
            if frame["type"] == "session.close":
                closing.set()

        upstream.__aiter__.side_effect = receive_frames
        browser.__aiter__.side_effect = send_frames
        browser.send_json.side_effect = to_browser
        upstream.send_json.side_effect = to_upstream
        async with asyncio.timeout(2):
            await relay_live(browser, upstream, AsyncMock())
        self.assertEqual([call.args[0] for call in upstream.send_json.call_args_list],
                         [message, {"type": "response.create"}, {"type": "session.close"}])

    async def test_personalization_acknowledgments_are_required_before_microphone_readiness(self):
        values = {"language_policy": "Use English.", "display_name": "", "client_language": "en-US", "user_context": ""}
        upstream, browser = AsyncMock(), AsyncMock()
        browser.closed = False
        upstream.__aiter__.side_effect = lambda: frames([
            {"type": "session.started", "session": {"audio": {"format": {"type": "audio/pcm", "rate": 24000}}}},
            {"type": "session.instructions.appended"}, {"type": "session.thinking.appended"},
            {"type": "session.closed", "usage": {"seconds": 0}},
        ])
        browser.__aiter__.side_effect = lambda: frames([])
        await relay_live(browser, upstream, AsyncMock(), values)
        self.assertEqual([call.args[0]["type"] for call in upstream.send_json.call_args_list],
                         ["session.instructions.append", "session.thinking.append"])
        self.assertIn({"type": "bridge.ready", "protocol": "live"},
                      [call.args[0] for call in browser.send_json.call_args_list])

    async def test_diagnostic_checks_live_startup_and_native_close_without_audio(self):
        cfg = Settings("https://example.services.ai.azure.com/api/projects/demo")
        socket = AsyncMock()
        socket.__aenter__.return_value = socket
        socket.__aiter__.side_effect = lambda: frames([
            {"type": "session.started", "session": {"audio": {"format": {"type": "audio/pcm", "rate": 24000}}}},
            {"type": "session.closed", "reason": "close_requested", "usage": {"seconds": 0}},
        ])
        client = ServiceClient([])
        client.ws_connect = lambda *args, **kwargs: socket
        with patch("builtins.print"):
            self.assertEqual(await check_live(client, cfg, {}, "2"), 0)
        socket.send_json.assert_awaited_once_with({"type": "session.close"})

    def test_container_includes_live_protocol_module(self):
        dockerfile = Path(__file__).with_name("Dockerfile").read_text()
        self.assertRegex(dockerfile, r"COPY[^\n]*\blive\.py\b")


if __name__ == "__main__":
    unittest.main()
