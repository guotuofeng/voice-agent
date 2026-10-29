"""Offline checks; no Azure or Gemini service calls."""

import asyncio
from contextlib import ExitStack
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
from azure.ai.projects import models
from google.genai import types
from google.adk.agents import Agent, LiveRequestQueue
from google.adk.events import Event
from google.adk.models.llm_request import LlmRequest
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService

from app import APP_NAME, AudioDevice, EventSink, receive_events
from azure_adk import AzureLiveConnection, AzureVoiceError, PcmResampler
from providers import create_model


def session_event(event_type="session.updated", session_id="azure-session"):
    return {
        "type": event_type,
        "session": {
            "id": session_id,
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "turn_detection": {"type": "server_vad"},
                    "transcription": {"model": "azure-speech"},
                },
                "output": {"format": {"type": "audio/pcm", "rate": 24000}},
            },
        },
    }


class AudioTests(unittest.TestCase):
    def test_streaming_resampling_matches_whole_signal(self):
        samples = (np.sin(np.arange(16000) * 2 * np.pi * 440 / 16000) * 10000).astype("<i2")
        expected = PcmResampler(16000, 24000).convert(samples.tobytes(), last=True)
        converter = PcmResampler(16000, 24000)
        chunks = [converter.convert(chunk.tobytes()) for chunk in np.array_split(samples, 50)]
        chunks.append(converter.convert(b"", last=True))
        actual_samples = np.frombuffer(b"".join(chunks), dtype="<i2").astype(np.int32)
        expected_samples = np.frombuffer(expected, dtype="<i2").astype(np.int32)
        self.assertLessEqual(np.max(np.abs(actual_samples - expected_samples)), 1)
        self.assertEqual(len(expected), 24000 * 2)

    def test_reject_partial_pcm_sample(self):
        with self.assertRaises(ValueError):
            PcmResampler(16000, 24000).convert(b"x")


class ApplicationTests(unittest.IsolatedAsyncioTestCase):
    async def test_interrupt_clears_playback_and_stale_completion(self):
        audio = AudioDevice(microphone=False, playback=True)
        with tempfile.TemporaryDirectory() as directory:
            with EventSink(audio, Path(directory) / "output") as sink, patch("builtins.print"):
                sink.handle(Event(author="voice_agent", turn_complete=True))
                audio.queue_output(b"\0" * 32)
                sink.handle(Event(author="voice_agent", interrupted=True))
                self.assertFalse(sink.turn_done.is_set())
                self.assertFalse(audio.output)


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.raw = AsyncMock()
        self.connection = AzureLiveConnection(self.raw, {"adk_session_id": "adk-one"})
        self.connection.configure_audio(session_event()["session"])

    async def test_partial_text_rejection_closes_receiver(self):
        with self.assertRaisesRegex(ValueError, "Partial text"):
            await self.connection._send_content(
                types.Content(role="user", parts=[types.Part(text="hello")]), partial=True,
            )
        self.raw.close.assert_awaited_once()
        self.assertIsInstance(self.connection.failure, ValueError)

    def test_transcripts_and_turn_complete(self):
        response = self.connection.translate({
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": "hello",
        })[0]
        self.assertEqual(response.input_transcription.text, "hello")
        self.assertTrue(response.input_transcription.finished)
        output = self.connection.translate({
            "type": "response.audio_transcript.done", "transcript": "hi"
        })[0]
        self.assertEqual(output.output_transcription.text, "hi")
        done = self.connection.translate({
            "type": "response.done", "response": {"id": "r1", "status": "completed"}
        })[0]
        self.assertTrue(done.turn_complete)

    def test_sdk_decoded_audio(self):
        audio = b"\x01\x00" * 240
        event = models.RealtimeServerEventResponseAudioDelta(
            event_id="e1", response_id="r1", item_id="i1", output_index=0,
            content_index=0, delta=audio,
        )
        response = self.connection.translate(event)[0]
        blob = response.content.parts[0].inline_data
        self.assertEqual(blob.data, audio)
        self.assertEqual(blob.mime_type, "audio/pcm;rate=24000")

    def test_barge_in_discards_late_audio_and_completion(self):
        self.connection.translate({"type": "response.created", "response": {"id": "r1"}})
        interrupted = self.connection.translate({"type": "input_audio_buffer.speech_started"})
        self.assertTrue(interrupted[0].interrupted)
        self.assertEqual(self.connection.translate({
            "type": "response.audio.delta", "response_id": "r1"
        }), [])
        self.assertEqual(self.connection.translate({
            "type": "response.done", "response": {"id": "r1", "status": "cancelled"}
        }), [])

    def test_service_errors_are_not_successful_completion(self):
        for event in [
            {"type": "error", "error": {"code": "denied", "message": "no access"}},
            {"type": "response.done", "response": {"id": "r1", "status": "failed"}},
            {"type": "response.output_item.added", "item": {"type": "mcp_approval_request"}},
        ]:
            with self.subTest(event=event), self.assertRaises(AzureVoiceError):
                self.connection.translate(event)

    async def test_input_audio_is_resampled(self):
        await self.connection.send_realtime(types.Blob(
            data=b"\x00\x00" * 16000, mime_type="audio/pcm;rate=16000"
        ))
        self.raw.input_audio_buffer.append.assert_awaited_once()
        audio = self.raw.input_audio_buffer.append.call_args.kwargs["audio"]
        self.assertGreater(len(audio), 16000 * 2)

    async def test_explicit_audio_and_history_limits(self):
        with self.assertRaises(ValueError):
            await self.connection.send_realtime(types.Blob(data=b"", mime_type="audio/wav"))
        with self.assertRaises(ValueError):
            await self.connection.send_history([types.Content(role="user", parts=[])])

    async def test_close_is_idempotent(self):
        await self.connection.close()
        await self.connection.close()
        self.raw.close.assert_awaited_once()

    async def test_send_failure_closes_and_preserves_original_error(self):
        error = ConnectionError("send failed")
        self.raw.input_audio_buffer.append.side_effect = error
        with self.assertRaisesRegex(ConnectionError, "send failed"):
            await self.connection.send_realtime(types.Blob(
                data=bytes(16000 * 2), mime_type="audio/pcm;rate=16000"
            ))
        self.assertIs(self.connection.failure, error)
        self.raw.close.assert_awaited_once()

    def test_reject_unsupported_session_configuration(self):
        session = session_event()["session"]
        session["audio"]["input"]["format"] = {"type": "audio/pcmu"}
        with self.assertRaisesRegex(AzureVoiceError, "audio/pcm"):
            self.connection.configure_audio(session)
        session = session_event()["session"]
        session["tools"] = [{"type": "function", "name": "client_tool"}]
        with self.assertRaisesRegex(AzureVoiceError, "function tools"):
            self.connection.configure_audio(session)


class FakeTransport:
    def __init__(self):
        self.events = asyncio.Queue()
        self.sent = []
        self.closed = False
        self.ready = asyncio.Event()
        self.input_audio_buffer = MagicMock()
        self.input_audio_buffer.append = AsyncMock()
        self.events.put_nowait(session_event("session.created"))
        self.events.put_nowait(session_event())

    async def recv(self):
        event = await self.events.get()
        if isinstance(event, Exception):
            raise event
        if event.get("type") == "session.updated":
            self.ready.set()
        return event

    async def send(self, event):
        self.sent.append(event)

    async def close(self):
        if not self.closed:
            self.closed = True
            self.events.put_nowait(ConnectionResetError("closed"))


class RecordingSink:
    def __init__(self):
        self.events = []
        self.changed = asyncio.Event()

    def handle(self, event):
        self.events.append(event)
        self.changed.set()

    async def wait_for(self, predicate):
        async with asyncio.timeout(5):
            while not predicate(self.events):
                self.changed.clear()
                await self.changed.wait()


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(patch.dict(os.environ, {
            "VOICE_PROVIDER": "azure",
            "AZURE_PROJECT_ENDPOINT": "https://example.services.ai.azure.com/api/projects/test",
            "AZURE_VOICE_AGENT_NAME": "existing-agent",
        }, clear=True))
        self.patches.enter_context(patch(
            "google.genai.Client.__init__", side_effect=AssertionError("Google client must not be used")
        ))
        self.patches.enter_context(patch(
            "socket.socket.connect", side_effect=AssertionError("Offline test attempted network access")
        ))
        self.credential = MagicMock()
        self.credential.__aenter__ = AsyncMock(return_value=self.credential)
        self.credential.__aexit__ = AsyncMock(return_value=False)
        self.credential_factory = self.patches.enter_context(patch(
            "azure_adk.DefaultAzureCredential", return_value=self.credential
        ))
        self.project = MagicMock()
        self.project.__aenter__ = AsyncMock(return_value=self.project)
        self.project.__aexit__ = AsyncMock(return_value=False)
        self.project_factory = self.patches.enter_context(patch(
            "azure_adk.AIProjectClient", return_value=self.project
        ))
        self.transports = []
        self.connected = asyncio.Event()

        def connect(**kwargs):
            raw = FakeTransport()
            self.transports.append(raw)
            self.connected.set()
            manager = MagicMock()
            manager.__aenter__ = AsyncMock(return_value=raw)
            manager.__aexit__ = AsyncMock(return_value=False)
            return manager

        self.project.beta.voice_agents.realtime.connect.side_effect = connect

    async def make_runner(self, session_id="adk-one"):
        model = create_model(session_id=session_id, user_id="user-one")
        agent = Agent(name="voice_agent", model=model, include_contents="none")
        sessions = InMemorySessionService()
        await sessions.create_session(
            app_name=APP_NAME, user_id="user-one", session_id=session_id
        )
        runner = Runner(agent=agent, app_name=APP_NAME, session_service=sessions)
        return runner

    async def test_same_application_runner_multiple_turns_and_audio(self):
        runner = await self.make_runner()
        requests = LiveRequestQueue()
        sink = RecordingSink()
        task = asyncio.create_task(receive_events(
            runner, "user-one", "adk-one", requests, sink
        ))
        try:
            await asyncio.wait_for(self.connected.wait(), 5)
            raw = self.transports[0]
            await asyncio.wait_for(raw.ready.wait(), 5)
            requests.send_realtime(types.Blob(
                data=bytes(16000 * 2), mime_type="audio/pcm;rate=16000"
            ))
            for turn in (1, 2):
                raw.events.put_nowait({
                    "type": "response.created", "response": {"id": f"r{turn}", "conversation_id": "conv-one"}
                })
                raw.events.put_nowait({
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": f"question {turn}",
                })
                raw.events.put_nowait(models.RealtimeServerEventResponseAudioDelta(
                    event_id=f"e{turn}", response_id=f"r{turn}", item_id=f"i{turn}",
                    output_index=0, content_index=0, delta=bytes(480),
                ))
                raw.events.put_nowait({
                    "type": "response.output_audio_transcript.done", "transcript": f"answer {turn}"
                })
                raw.events.put_nowait({
                    "type": "response.done", "response": {"id": f"r{turn}", "status": "completed"}
                })
                await sink.wait_for(lambda events: sum(bool(event.turn_complete) for event in events) == turn)
            raw.events.put_nowait({"type": "response.created", "response": {"id": "r3"}})
            raw.events.put_nowait({"type": "input_audio_buffer.speech_started"})
            await sink.wait_for(lambda events: any(event.interrupted for event in events))
            requests.close()
            await asyncio.wait_for(task, 5)
            self.assertTrue(raw.closed)
            self.assertEqual(len(self.transports), 1)
            self.assertTrue(any(event.input_transcription for event in sink.events))
            self.assertTrue(any(event.output_transcription for event in sink.events))
            self.assertTrue(any(event.content and event.content.parts[0].inline_data for event in sink.events))
            correlated = [event for event in sink.events if event.custom_metadata]
            self.assertTrue(correlated, "ADK must retain Azure session correlation")
            self.assertEqual(correlated[-1].custom_metadata["azure_conversation_id"], "conv-one")
            self.credential.__aexit__.assert_awaited_once()
            self.project.__aexit__.assert_awaited_once()
            self.project.agents.create_version.assert_not_called()
            self.assertFalse(any(event.get("type") == "session.update" for event in raw.sent))
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runner.close()

    async def test_concurrent_connections_are_isolated(self):
        model = create_model(session_id="adk-one", user_id="user-one")
        async with model.connect(LlmRequest()) as first:
            async with model.connect(LlmRequest()) as second:
                self.assertIsNot(first.raw, second.raw)
                self.assertIsNot(first.input_resampler, second.input_resampler)
                first.correlation["azure_conversation_id"] = "private-one"
                self.assertNotIn("azure_conversation_id", second.correlation)
        identifiers = [
            call.kwargs["agent_session_id"]
            for call in self.project.beta.voice_agents.realtime.connect.call_args_list
        ]
        self.assertNotEqual(*identifiers)
        self.assertTrue(all(raw.closed for raw in self.transports))

    async def test_runner_cancellation_closes_resources(self):
        runner = await self.make_runner()
        sink = RecordingSink()
        task = asyncio.create_task(receive_events(
            runner, "user-one", "adk-one", LiveRequestQueue(), sink
        ))
        try:
            await asyncio.wait_for(self.connected.wait(), 5)
            await asyncio.wait_for(self.transports[0].ready.wait(), 5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(self.transports[0].closed)
            self.credential.__aexit__.assert_awaited_once()
            self.project.__aexit__.assert_awaited_once()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runner.close()

    async def test_authentication_failure_propagates_and_closes(self):
        from azure.core.exceptions import ClientAuthenticationError

        self.project.__aenter__.side_effect = ClientAuthenticationError("login required")
        model = create_model(session_id="adk-one", user_id="user-one")
        with self.assertRaises(ClientAuthenticationError):
            async with model.connect(LlmRequest()):
                self.fail("Authentication failure must not create a connection")
        self.credential.__aexit__.assert_awaited_once()
        self.assertEqual(self.transports, [])

    async def test_structured_inputs_remain_provider_configuration(self):
        with patch.dict(os.environ, {"AZURE_STRUCTURED_INPUTS": '{"name":"sample"}'}):
            model = create_model(session_id="adk-one", user_id="user-one")
        async with model.connect(LlmRequest()):
            self.assertEqual(
                self.project.beta.voice_agents.realtime.connect.call_args.kwargs["structured_inputs"],
                {"name": "sample"},
            )


if __name__ == "__main__":
    unittest.main()
