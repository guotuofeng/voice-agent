"""Offline direct Voice Live checks using SDK models and the real ADK Runner."""

import asyncio
import base64
from contextlib import ExitStack
import json
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from azure.ai.voicelive import models
from azure.core.exceptions import ClientAuthenticationError
from google.adk.agents import Agent, LiveRequestQueue
from google.adk.models.llm_request import LlmRequest
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import FunctionTool
from google.genai import types

from app import APP_NAME, receive_events
from azure_adk import AzureVoiceError
from providers import create_model
from test_adapter import RecordingSink
from tool_support import add_numbers
from voicelive_adk import AzureVoiceLive, VoiceLiveConnection


def session_event(event_type="session.updated", *, tools=None, session_id="voice-session"):
    payload = {
        "type": event_type,
        "event_id": "session-event",
        "session": {
            "id": session_id,
            "input_audio_format": "pcm16",
            "input_audio_sampling_rate": 24000,
            "output_audio_format": "pcm16",
            "input_audio_transcription": {"model": "azure-speech"},
            "turn_detection": {
                "type": "server_vad", "create_response": True, "interrupt_response": True,
            },
            "tools": tools or [],
        },
    }
    cls = (models.ServerEventSessionCreated if event_type == "session.created"
           else models.ServerEventSessionUpdated)
    return cls(payload)


def tool_request():
    request = LlmRequest()
    request.append_tools([FunctionTool(add_numbers)])
    return request


class FakeVoiceLiveTransport:
    def __init__(self):
        self.events = asyncio.Queue()
        self.events.put_nowait(session_event("session.created"))
        self.sent = []
        self.ready = asyncio.Event()
        self.input_audio_buffer = MagicMock(append=AsyncMock())
        self.session = MagicMock(update=AsyncMock(side_effect=self.configure))
        self.closed = False
        self.close = AsyncMock(side_effect=self.disconnect)

    async def configure(self, *, session):
        self.config = session.as_dict()
        updated = session_event(tools=self.config["tools"]).as_dict()
        updated["session"].update(self.config)
        self.events.put_nowait(models.ServerEventSessionUpdated(updated))

    async def recv(self):
        event = await self.events.get()
        if isinstance(event, Exception):
            raise event
        if event["type"] == "session.updated":
            self.ready.set()
        return event

    async def send(self, event):
        self.sent.append(event)

    async def disconnect(self):
        if not self.closed:
            self.closed = True
            self.events.put_nowait(ConnectionResetError("closed"))


class VoiceLiveTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(patch.dict(os.environ, {
            "VOICE_PROVIDER": "voicelive",
            "AZURE_VOICELIVE_ENDPOINT": "https://example.services.ai.azure.com",
            "AZURE_VOICELIVE_MODEL": "gpt-realtime",
            "AZURE_VOICELIVE_VOICE": "en-US-Ava:DragonHDLatestNeural",
            "AZURE_VOICELIVE_INSTRUCTIONS": "Be concise.",
        }, clear=True))
        for target in ("google.genai.Client.__init__", "azure_adk.AIProjectClient",
                       "socket.socket.connect"):
            self.patches.enter_context(patch(
                target, side_effect=AssertionError(f"Offline Voice Live must not call {target}")
            ))
        self.credential = MagicMock()
        self.credential.__aenter__ = AsyncMock(return_value=self.credential)
        self.credential.__aexit__ = AsyncMock(return_value=False)
        self.patches.enter_context(patch(
            "voicelive_adk.DefaultAzureCredential", return_value=self.credential,
        ))
        self.transports = []
        self.connected = asyncio.Event()
        self.manager = None

        def connect(**kwargs):
            raw = FakeVoiceLiveTransport()
            self.transports.append(raw)
            self.manager = MagicMock()
            self.manager.__aenter__ = AsyncMock(return_value=raw)
            self.manager.__aexit__ = AsyncMock(return_value=False)
            self.connected.set()
            return self.manager

        self.connect = self.patches.enter_context(patch("voicelive_adk.connect", side_effect=connect))
        self.model = create_model(session_id="adk-one", user_id="user-one")

    def test_provider_and_session_configuration(self):
        self.assertIsInstance(self.model, AzureVoiceLive)
        request = tool_request()
        request.config.system_instruction = "Use the tool for sums."
        config = self.model.session_config(request).as_dict()
        self.assertEqual(config["instructions"], "Be concise.\n\nUse the tool for sums.")
        self.assertEqual(config["voice"]["type"], "azure-standard")
        self.assertEqual(config["voice"]["name"], os.environ["AZURE_VOICELIVE_VOICE"])
        self.assertEqual(config["modalities"], ["text", "audio"])
        self.assertEqual(config["input_audio_format"], "pcm16")
        self.assertEqual(config["input_audio_sampling_rate"], 24000)
        self.assertEqual(config["output_audio_format"], "pcm16")
        self.assertEqual(config["input_audio_transcription"]["model"], "azure-speech")
        self.assertTrue(config["turn_detection"]["create_response"])
        self.assertTrue(config["turn_detection"]["interrupt_response"])
        self.assertTrue(config["turn_detection"]["auto_truncate"])
        function = config["tools"][0]
        self.assertEqual(function["type"], "function")
        self.assertEqual(function["name"], "add_numbers")
        self.assertEqual(function["parameters"]["properties"]["first_number"]["type"], "number")
        self.assertEqual(function["parameters"]["required"], ["first_number", "second_number"])
        self.connect.assert_not_called()

    def test_text_content_instructions_and_unsupported_configuration(self):
        request = LlmRequest()
        request.config.system_instruction = types.Content(
            parts=[types.Part(text="First."), types.Part(text="Second.")]
        )
        self.assertEqual(
            self.model.session_config(request).instructions, "Be concise.\n\nFirst.\nSecond.",
        )
        request.config.system_instruction = types.Content(parts=[
            types.Part(inline_data=types.Blob(data=b"image", mime_type="image/png")),
        ])
        with self.assertRaisesRegex(ValueError, "text-only"):
            self.model.session_config(request)
        request = LlmRequest()
        request.live_connect_config.session_resumption = types.SessionResumptionConfig()
        with self.assertRaisesRegex(ValueError, "resumption"):
            self.model.session_config(request)
        request = LlmRequest()
        request.live_connect_config.speech_config = types.SpeechConfig()
        with self.assertRaisesRegex(ValueError, "AZURE_VOICELIVE_VOICE"):
            self.model.session_config(request)

    def test_function_registration_rejects_unsupported_or_mismatched_declarations(self):
        request = tool_request()
        request.tools_dict.clear()
        with self.assertRaisesRegex(ValueError, "matching ADK executor"):
            self.model.session_config(request)
        request = tool_request()
        request.config.tools.clear()
        with self.assertRaisesRegex(ValueError, "matching function declaration"):
            self.model.session_config(request)
        request = LlmRequest(config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
        ))
        with self.assertRaisesRegex(ValueError, "Only ADK function tools"):
            self.model.session_config(request)
        request = tool_request()
        declaration = request.config.tools[0].function_declarations[0]
        declaration.parameters_json_schema = None
        declaration.parameters = types.Schema(type=types.Type.OBJECT)
        with self.assertRaisesRegex(ValueError, "parameters_json_schema"):
            self.model.session_config(request)

    async def test_connect_uses_resource_endpoint_model_and_configures_session(self):
        async with self.model.connect(tool_request()) as connection:
            self.connect.assert_called_once_with(
                endpoint=os.environ["AZURE_VOICELIVE_ENDPOINT"],
                model="gpt-realtime", credential=self.credential,
            )
            raw = self.transports[0]
            raw.session.update.assert_awaited_once()
            self.assertEqual(connection.correlation["provider"], "voicelive")
            self.assertEqual(connection.correlation["azure_session_id"], "voice-session")
            self.assertNotIn("azure_agent_session_id", connection.correlation)
        raw.close.assert_awaited_once()
        self.manager.__aexit__.assert_awaited_once()
        self.credential.__aexit__.assert_awaited_once()

    async def test_invalid_endpoint_and_missing_provider_settings_fail_before_connect(self):
        self.model.endpoint = "http://example.test"
        with self.assertRaisesRegex(ValueError, "HTTPS resource endpoint"):
            async with self.model.connect(LlmRequest()):
                self.fail("Invalid endpoint must not connect")
        self.connect.assert_not_called()
        for name in ("AZURE_VOICELIVE_ENDPOINT", "AZURE_VOICELIVE_MODEL", "AZURE_VOICELIVE_VOICE"):
            with self.subTest(name=name), patch.dict(os.environ, {name: ""}):
                with self.assertRaisesRegex(ValueError, name):
                    create_model(session_id="s", user_id="u")

    async def test_pcm_is_resampled_then_base64_encoded_and_sdk_audio_is_decoded(self):
        async with self.model.connect(LlmRequest()) as connection:
            raw = self.transports[0]
            await connection.send_realtime(types.Blob(
                data=bytes(16000 * 2), mime_type="audio/pcm;rate=16000",
            ))
            encoded = raw.input_audio_buffer.append.call_args.kwargs["audio"]
            self.assertIsInstance(encoded, str)
            pcm = base64.b64decode(encoded, validate=True)
            self.assertGreater(len(pcm), 16000 * 2)
            self.assertLessEqual(len(pcm), 24000 * 2)
            audio = b"\x01\x00" * 240
            event = models.ServerEventResponseAudioDelta(
                event_id="e", response_id="r", item_id="i", output_index=0,
                content_index=0, delta=audio,
            )
            response = connection.translate(event)[0]
            self.assertEqual(response.content.parts[0].inline_data.data, audio)
            self.assertEqual(response.content.parts[0].inline_data.mime_type, "audio/pcm;rate=24000")
            with self.assertRaisesRegex(AzureVoiceError, "VoiceLive SDK-decoded"):
                connection.translate(event.as_dict())

    def test_session_contract_rejects_incompatible_server_configuration(self):
        connection = VoiceLiveConnection(MagicMock(), {})
        for changes in (
            {"input_audio_format": "g711_ulaw"},
            {"output_audio_format": "pcm16_16000hz"},
            {"input_audio_sampling_rate": 16000},
            {"turn_detection": None},
            {"turn_detection": {"type": "server_vad", "create_response": False}},
            {"turn_detection": {"type": "server_vad", "interrupt_response": False}},
            {"input_audio_transcription": None},
            {"tools": [{"type": "function", "name": "unknown"}]},
        ):
            with self.subTest(changes=changes):
                session = session_event().as_dict()["session"]
                session.update(changes)
                with self.assertRaises(AzureVoiceError):
                    connection.configure_audio(session)

    async def test_text_and_function_results_use_realtime_wire_events(self):
        async with self.model.connect(tool_request()) as connection:
            raw = self.transports[0]
            await connection.send_content(types.Content(role="user", parts=[types.Part(text="Hello")]))
            self.assertEqual(raw.sent[0]["item"]["content"], [{"type": "input_text", "text": "Hello"}])
            self.assertEqual(raw.sent[1], {"type": "response.create"})
            raw.sent.clear()
            connection.translate(models.ServerEvent({
                "type": "response.function_call_arguments.done", "response_id": "r1",
                "call_id": "call-one", "name": "add_numbers",
                "arguments": '{"first_number":12,"second_number":30}',
            }))
            self.assertEqual(raw.sent, [])
            response = connection.translate(models.ServerEvent({
                "type": "response.done", "response": {"id": "r1", "status": "completed"},
            }))[0]
            self.assertEqual(response.content.parts[0].function_call.id, "call-one")
            await connection.send_content(types.Content(parts=[
                types.Part(function_response=types.FunctionResponse(
                    id="call-one", name="add_numbers", response={"sum": 42},
                )),
            ]))
            self.assertEqual(raw.sent[0]["item"]["type"], "function_call_output")
            self.assertEqual(json.loads(raw.sent[0]["item"]["output"]), {"sum": 42})
            self.assertEqual(raw.sent[1], {"type": "response.create"})

    async def test_configuration_failure_and_timeout_close_all_resources(self):
        for failure in ("update", "timeout"):
            with self.subTest(failure=failure):
                raw = FakeVoiceLiveTransport()
                if failure == "update":
                    raw.session.update.side_effect = RuntimeError("configuration rejected")
                    error_type = RuntimeError
                else:
                    raw.session.update.side_effect = None
                    self.model.ready_timeout = 0.01
                    error_type = TimeoutError
                original_connect = self.connect.side_effect
                manager = MagicMock()
                manager.__aenter__ = AsyncMock(return_value=raw)
                manager.__aexit__ = AsyncMock(return_value=False)
                self.connect.side_effect = None
                self.connect.return_value = manager
                try:
                    with self.assertRaises(error_type):
                        async with self.model.connect(LlmRequest()):
                            self.fail("Failed configuration must not yield a connection")
                    self.assertTrue(raw.closed)
                    manager.__aexit__.assert_awaited_once()
                finally:
                    self.connect.side_effect = original_connect
        self.assertEqual(self.credential.__aexit__.await_count, 2)

    async def test_primary_error_and_cancellation_survive_cleanup_failure(self):
        for error in (ConnectionError("primary send failure"), asyncio.CancelledError()):
            with self.subTest(error=type(error)):
                with self.assertRaises(type(error)) as raised:
                    async with self.model.connect(LlmRequest()) as connection:
                        connection.raw.close.side_effect = RuntimeError("cleanup failed")
                        raise error
                self.assertIs(raised.exception, error)
                self.assertIsInstance(raised.exception.__cause__, RuntimeError)
        self.assertEqual(self.credential.__aexit__.await_count, 2)

    async def test_authentication_failure_never_connects(self):
        self.credential.__aenter__.side_effect = ClientAuthenticationError("login required")
        with self.assertRaisesRegex(ClientAuthenticationError, "login required"):
            async with self.model.connect(LlmRequest()):
                self.fail("Authentication failure must prevent connection")
        self.connect.assert_not_called()

    async def test_receive_errors_and_disconnects_are_not_silent(self):
        for failure in (
            ConnectionResetError("remote disconnected"),
            models.ServerEvent({"type": "error", "error": {"code": "denied", "message": "no access"}}),
        ):
            with self.subTest(failure=failure):
                with self.assertRaises((ConnectionResetError, AzureVoiceError)):
                    async with self.model.connect(LlmRequest()) as connection:
                        connection.raw.events.put_nowait(failure)
                        async for _ in connection.receive():
                            pass
                self.assertTrue(self.transports[-1].closed)

    async def test_real_runner_audio_tools_and_interruption(self):
        agent = Agent(
            name="voice_agent", model=self.model, instruction="Use functions for arithmetic.",
            include_contents="none", tools=[add_numbers],
        )
        sessions = InMemorySessionService()
        await sessions.create_session(app_name=APP_NAME, user_id="user-one", session_id="adk-one")
        runner = Runner(agent=agent, app_name=APP_NAME, session_service=sessions)
        requests, sink = LiveRequestQueue(), RecordingSink()
        task = asyncio.create_task(receive_events(
            runner, "user-one", "adk-one", requests, sink,
        ))
        try:
            await asyncio.wait_for(self.connected.wait(), 5)
            raw = self.transports[0]
            await asyncio.wait_for(raw.ready.wait(), 5)
            self.assertIn("Use functions for arithmetic.", raw.config["instructions"])
            self.assertEqual(raw.config["tools"][0]["name"], "add_numbers")
            requests.send_realtime(types.Blob(
                data=bytes(16000 * 2), mime_type="audio/pcm;rate=16000",
            ))
            raw.events.put_nowait(models.ServerEvent({
                "type": "response.function_call_arguments.done", "response_id": "r1",
                "call_id": "call-one", "name": "add_numbers",
                "arguments": '{"first_number":12,"second_number":30}',
            }))
            raw.events.put_nowait(models.ServerEvent({
                "type": "response.done", "response": {"id": "r1", "status": "completed"},
            }))
            await sink.wait_for(lambda events: any(event.get_function_responses() for event in events))
            for turn in (2, 3):
                raw.events.put_nowait(models.ServerEvent({
                    "type": "response.created",
                    "response": {"id": f"r{turn}", "conversation_id": "voice-conversation"},
                }))
                raw.events.put_nowait(models.ServerEvent({
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": f"question {turn}",
                }))
                raw.events.put_nowait(models.ServerEventResponseAudioDelta(
                    event_id=f"e{turn}", response_id=f"r{turn}", item_id=f"i{turn}",
                    output_index=0, content_index=0, delta=bytes(480),
                ))
                raw.events.put_nowait(models.ServerEvent({
                    "type": "response.audio_transcript.done", "transcript": f"answer {turn}",
                }))
                raw.events.put_nowait(models.ServerEvent({
                    "type": "response.done", "response": {"id": f"r{turn}", "status": "completed"},
                }))
                await sink.wait_for(lambda events: sum(bool(event.turn_complete) for event in events) == turn - 1)
            self.assertEqual(json.loads(raw.sent[0]["item"]["output"]), {"sum": 42})
            self.assertEqual(raw.sent[1], {"type": "response.create"})
            raw.events.put_nowait(models.ServerEvent({
                "type": "response.created", "response": {"id": "r4"},
            }))
            raw.events.put_nowait(models.ServerEvent({"type": "input_audio_buffer.speech_started"}))
            raw.events.put_nowait(models.ServerEventResponseAudioDelta(
                event_id="late", response_id="r4", item_id="i4",
                output_index=0, content_index=0, delta=b"\x01\x00" * 240,
            ))
            raw.events.put_nowait(models.ServerEvent({
                "type": "response.done", "response": {"id": "r4", "status": "cancelled"},
            }))
            await sink.wait_for(lambda events: any(event.interrupted for event in events))
            requests.close()
            await asyncio.wait_for(task, 5)
            audio_events = [
                event for event in sink.events if event.content and event.content.parts[0].inline_data
            ]
            self.assertEqual(len(audio_events), 2)
            self.assertEqual(sum(bool(event.input_transcription) for event in sink.events), 2)
            self.assertEqual(sum(bool(event.output_transcription) for event in sink.events), 2)
            self.assertEqual(audio_events[-1].custom_metadata["provider"], "voicelive")
            self.assertEqual(audio_events[-1].custom_metadata["azure_conversation_id"], "voice-conversation")
            raw.input_audio_buffer.append.assert_awaited()
            self.assertTrue(raw.closed)
        finally:
            requests.close()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runner.close()
            await sessions.delete_session(
                app_name=APP_NAME, user_id="user-one", session_id="adk-one",
            )


if __name__ == "__main__":
    unittest.main()
