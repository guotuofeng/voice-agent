"""Offline checks; no Azure or Gemini service calls."""

import asyncio
import argparse
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

import app
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
    async def test_audio_startup_error_survives_cleanup_failure(self):
        for error_type in (OSError, asyncio.CancelledError):
            for failure_method in ("stop", "close"):
                with self.subTest(error=error_type, cleanup=failure_method):
                    primary = error_type("device startup failed")
                    cleanup_error = RuntimeError("stream cleanup failed")
                    output_stream, input_stream = MagicMock(), MagicMock()
                    input_stream.start.side_effect = primary
                    getattr(input_stream, failure_method).side_effect = cleanup_error
                    sounddevice = MagicMock()
                    sounddevice.RawOutputStream.return_value = output_stream
                    sounddevice.RawInputStream.return_value = input_stream
                    audio = AudioDevice(microphone=True, playback=True)
                    with patch.dict("sys.modules", {"sounddevice": sounddevice}):
                        with self.assertRaises(error_type) as raised:
                            audio.start()
                    self.assertIs(raised.exception, primary)
                    self.assertIs(raised.exception.__cause__, cleanup_error)
                    for stream in (output_stream, input_stream):
                        stream.stop.assert_called_once()
                        stream.close.assert_called_once()
                    self.assertEqual(audio.streams, [])

    def test_artifact_initialization_failure_closes_acquired_files(self):
        for failure_stage in ("open", "setnchannels", "setsampwidth", "setframerate"):
            for cleanup_fails in (False, True):
                with (
                    self.subTest(stage=failure_stage, cleanup=cleanup_fails),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    primary = OSError("artifact initialization failed")
                    cleanup_error = OSError("log close failed")
                    log, recording = MagicMock(), MagicMock()
                    if cleanup_fails:
                        log.close.side_effect = cleanup_error
                    if failure_stage != "open":
                        getattr(recording, failure_stage).side_effect = primary
                    with patch.object(Path, "open", return_value=log), patch(
                        "app.wave.open", return_value=recording,
                        side_effect=primary if failure_stage == "open" else None,
                    ):
                        with self.assertRaises(OSError) as raised:
                            with EventSink(MagicMock(), Path(directory) / "run"):
                                self.fail("Initialization failure must prevent entry")
                    self.assertIs(raised.exception, primary)
                    if cleanup_fails:
                        self.assertIs(raised.exception.__cause__, cleanup_error)
                    log.close.assert_called_once()
                    if failure_stage == "open":
                        recording.close.assert_not_called()
                    else:
                        recording.close.assert_called_once()

    def test_artifact_cleanup_attempts_both_files_and_preserves_errors(self):
        for error_type in (None, ConnectionError, asyncio.CancelledError):
            for failure_source in ("recording", "log", "both"):
                with self.subTest(error=error_type, cleanup=failure_source), tempfile.TemporaryDirectory() as directory:
                    primary = error_type("original provider failure") if error_type else None
                    recording_error = OSError("recording close failed")
                    log_error = OSError("log close failed")
                    log, recording = MagicMock(), MagicMock()
                    if failure_source in ("recording", "both"):
                        recording.close.side_effect = recording_error
                    if failure_source in ("log", "both"):
                        log.close.side_effect = log_error
                    cleanup_error = recording_error if failure_source == "recording" else log_error
                    with patch.object(Path, "open", return_value=log), patch(
                        "app.wave.open", return_value=recording,
                    ):
                        with self.assertRaises(error_type or OSError) as raised:
                            with EventSink(MagicMock(), Path(directory) / "run"):
                                if primary is not None:
                                    raise primary
                    if primary is not None:
                        self.assertIs(raised.exception, primary)
                        self.assertIs(raised.exception.__cause__, cleanup_error)
                    else:
                        self.assertIs(raised.exception, cleanup_error)
                    recording.close.assert_called_once()
                    log.close.assert_called_once()

    async def test_audio_close_attempts_every_stream_after_failure(self):
        audio = AudioDevice(microphone=False, playback=False)
        output_stream, input_stream = MagicMock(), MagicMock()
        audio.streams = [output_stream, input_stream]
        input_stream.stop.side_effect = RuntimeError("stop failed")
        with self.assertRaisesRegex(RuntimeError, "stop failed"):
            audio.close()
        for stream in (output_stream, input_stream):
            stream.stop.assert_called_once()
            stream.close.assert_called_once()
        self.assertEqual(audio.streams, [])

    async def test_application_cleanup_continues_after_close_failure(self):
        async def wait_for_cancellation(*args):
            await asyncio.Event().wait()

        for failure_source in ("audio", "runner", "session"):
            with self.subTest(failure_source=failure_source), tempfile.TemporaryDirectory() as directory:
                runner = MagicMock()
                runner.close = AsyncMock()
                sessions = MagicMock()
                sessions.create_session = AsyncMock()
                sessions.delete_session = AsyncMock()
                audio = MagicMock()
                audio.failure = asyncio.get_running_loop().create_future()
                audio.drain = AsyncMock()
                cleanup = {
                    "audio": audio.close, "runner": runner.close,
                    "session": sessions.delete_session,
                }
                cleanup[failure_source].side_effect = RuntimeError("close failed")
                args = argparse.Namespace(
                    wav=[], no_playback=True, output_dir=Path(directory) / "run",
                    seconds=0.01, turn_timeout=1,
                )
                with ExitStack() as patches:
                    patches.enter_context(patch("app.create_model", return_value="unused-model"))
                    patches.enter_context(patch("app.Runner", return_value=runner))
                    patches.enter_context(patch("app.InMemorySessionService", return_value=sessions))
                    patches.enter_context(patch("app.AudioDevice", return_value=audio))
                    receive = patches.enter_context(patch(
                        "app.receive_events", side_effect=wait_for_cancellation,
                    ))
                    send = patches.enter_context(patch(
                        "app.send_microphone", side_effect=wait_for_cancellation,
                    ))
                    patches.enter_context(patch("builtins.print"))
                    with self.assertRaisesRegex(RuntimeError, "close failed"):
                        await app.run(args)
                audio.close.assert_called_once()
                runner.close.assert_awaited_once()
                sessions.delete_session.assert_awaited_once()
                receive.assert_awaited_once()
                send.assert_awaited_once()

    async def test_application_preserves_primary_error_during_cleanup(self):
        async def wait_for_cancellation(*args):
            await asyncio.Event().wait()

        for error_type in (ConnectionError, asyncio.CancelledError):
            for failure_source in ("audio", "runner", "session"):
                with self.subTest(error=error_type, cleanup=failure_source), tempfile.TemporaryDirectory() as directory:
                    primary = error_type("original provider failure")
                    cleanup_error = RuntimeError("close failed")
                    runner = MagicMock()
                    runner.close = AsyncMock()
                    sessions = MagicMock()
                    sessions.create_session = AsyncMock()
                    sessions.delete_session = AsyncMock()
                    audio = MagicMock()
                    audio.failure = asyncio.get_running_loop().create_future()
                    audio.drain = AsyncMock()
                    cleanup = {
                        "audio": audio.close, "runner": runner.close,
                        "session": sessions.delete_session,
                    }
                    cleanup[failure_source].side_effect = cleanup_error
                    args = argparse.Namespace(
                        wav=[], no_playback=True, output_dir=Path(directory) / "run",
                        seconds=1, turn_timeout=1,
                    )
                    with ExitStack() as patches:
                        patches.enter_context(patch("app.create_model", return_value="unused-model"))
                        patches.enter_context(patch("app.Runner", return_value=runner))
                        patches.enter_context(patch("app.InMemorySessionService", return_value=sessions))
                        patches.enter_context(patch("app.AudioDevice", return_value=audio))
                        patches.enter_context(patch("app.receive_events", new=AsyncMock(side_effect=primary)))
                        send = patches.enter_context(patch(
                            "app.send_microphone", side_effect=wait_for_cancellation,
                        ))
                        patches.enter_context(patch("builtins.print"))
                        with self.assertRaises(error_type) as raised:
                            await app.run(args)
                    self.assertIs(raised.exception, primary)
                    self.assertIs(raised.exception.__cause__, cleanup_error)
                    audio.close.assert_called_once()
                    runner.close.assert_awaited_once()
                    sessions.delete_session.assert_awaited_once()
                    send.assert_awaited_once()

    async def test_playback_drain_monitors_failures_and_completes(self):
        cases = (
            ("provider", ConnectionError("provider failed during drain")),
            ("disconnect", ConnectionError("Live provider disconnected before the application closed")),
            ("sender", ConnectionError("sender failed during drain")),
            ("audio", RuntimeError("playback failed during drain")),
            ("drain", TimeoutError("playback drain timed out")),
            ("cancel", asyncio.CancelledError("playback drain cancelled")),
            ("success", None),
        )
        for mode, primary in cases:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                ready, draining, drain_stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()
                runner = MagicMock()
                runner.close = AsyncMock()
                sessions = MagicMock()
                sessions.create_session = AsyncMock()
                sessions.delete_session = AsyncMock()
                audio = MagicMock()
                audio.failure = asyncio.get_running_loop().create_future()

                async def receive(*args):
                    args[-1].audio_bytes = 2
                    ready.set()
                    await draining.wait()
                    if mode == "provider":
                        raise primary
                    if mode == "disconnect":
                        return
                    await asyncio.Event().wait()

                async def send(*args, **kwargs):
                    await ready.wait()
                    if mode == "sender":
                        await draining.wait()
                        raise primary

                async def drain():
                    draining.set()
                    try:
                        if mode in ("drain", "cancel"):
                            raise primary
                        if mode == "success":
                            return
                        if mode == "audio":
                            audio.failure.set_result(str(primary))
                        await asyncio.Event().wait()
                    finally:
                        drain_stopped.set()

                audio.drain = AsyncMock(side_effect=drain)
                args = argparse.Namespace(
                    wav=[] if mode == "sender" else [Path("mock.wav")],
                    no_playback=False, output_dir=Path(directory) / "run",
                    seconds=0.01 if mode == "sender" else 1, turn_timeout=1,
                )
                with ExitStack() as patches:
                    patches.enter_context(patch("app.create_model", return_value="unused-model"))
                    patches.enter_context(patch("app.Runner", return_value=runner))
                    patches.enter_context(patch("app.InMemorySessionService", return_value=sessions))
                    patches.enter_context(patch("app.AudioDevice", return_value=audio))
                    patches.enter_context(patch("app.receive_events", side_effect=receive))
                    patches.enter_context(patch("app.send_wavs", side_effect=send))
                    patches.enter_context(patch("app.send_microphone", side_effect=send))
                    patches.enter_context(patch("builtins.print"))
                    if primary is None:
                        await asyncio.wait_for(app.run(args), timeout=1)
                    else:
                        with self.assertRaises(type(primary)) as raised:
                            await asyncio.wait_for(app.run(args), timeout=1)
                        self.assertEqual(str(raised.exception), str(primary))
                        if mode in ("provider", "sender", "drain"):
                            self.assertIs(raised.exception, primary)
                self.assertTrue(drain_stopped.is_set())
                audio.close.assert_called_once()
                runner.close.assert_awaited_once()
                sessions.delete_session.assert_awaited_once()

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

    def test_interrupted_response_failures_propagate(self):
        for status in ("failed", "incomplete"):
            with self.subTest(status=status):
                self.connection.translate({"type": "response.created", "response": {"id": "r1"}})
                self.connection.translate({"type": "input_audio_buffer.speech_started"})
                with self.assertRaisesRegex(AzureVoiceError, f"Azure response {status}.*backend failed"):
                    self.connection.translate({
                        "type": "response.done",
                        "response": {
                            "id": "r1", "status": status,
                            "status_details": {"error": {"message": "backend failed"}},
                        },
                    })

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

    async def test_send_failure_survives_transport_close_failure(self):
        error = ConnectionError("send failed")
        cleanup_error = RuntimeError("transport close failed")
        self.raw.input_audio_buffer.append.side_effect = error
        self.raw.close.side_effect = cleanup_error
        with self.assertRaises(ConnectionError) as raised:
            await self.connection.send_realtime(types.Blob(
                data=bytes(16000 * 2), mime_type="audio/pcm;rate=16000",
            ))
        self.assertIs(raised.exception, error)
        self.assertIs(raised.exception.__cause__, cleanup_error)
        self.assertIs(self.connection.failure, error)
        self.raw.close.assert_awaited_once()
        self.raw.recv.side_effect = ConnectionResetError("closed")
        with self.assertRaises(ConnectionError) as received:
            await anext(self.connection.receive())
        self.assertIs(received.exception, error)

    def test_reject_unsupported_session_configuration(self):
        session = session_event()["session"]
        session["audio"]["input"]["format"] = {"type": "audio/pcmu"}
        with self.assertRaisesRegex(AzureVoiceError, "audio/pcm"):
            self.connection.configure_audio(session)
        session = session_event()["session"]
        session["tools"] = [{"type": "function", "name": "client_tool"}]
        with self.assertRaisesRegex(AzureVoiceError, "function tools"):
            self.connection.configure_audio(session)

    def test_reject_disabled_automatic_responses(self):
        session = session_event()["session"]
        session["audio"]["input"]["turn_detection"]["create_response"] = False
        with self.assertRaisesRegex(AzureVoiceError, "automatic response creation"):
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

    async def test_send_error_survives_connection_and_client_cleanup_failures(self):
        error = ConnectionError("original send failure")
        cleanup_error = RuntimeError("credential close failure")
        raw = FakeTransport()
        raw.input_audio_buffer.append.side_effect = error
        raw.close = AsyncMock(side_effect=RuntimeError("transport close failure"))
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=raw)
        manager.__aexit__ = AsyncMock(side_effect=RuntimeError("SDK context close failure"))
        connect = self.project.beta.voice_agents.realtime.connect
        connect.side_effect = None
        connect.return_value = manager
        self.project.__aexit__.side_effect = RuntimeError("project close failure")
        self.credential.__aexit__.side_effect = cleanup_error
        model = create_model(session_id="adk-one", user_id="user-one")
        with self.assertRaises(ConnectionError) as raised:
            async with model.connect(LlmRequest()) as connection:
                await connection.send_realtime(types.Blob(
                    data=bytes(32000), mime_type="audio/pcm;rate=16000",
                ))
        self.assertIs(raised.exception, error)
        self.assertIs(raised.exception.__cause__, cleanup_error)
        self.assertEqual(raw.close.await_count, 2)
        manager.__aexit__.assert_awaited_once()
        self.project.__aexit__.assert_awaited_once()
        self.credential.__aexit__.assert_awaited_once()

    async def test_authentication_error_survives_credential_cleanup_failure(self):
        from azure.core.exceptions import ClientAuthenticationError

        error = ClientAuthenticationError("login required")
        self.project.__aenter__.side_effect = error
        self.credential.__aexit__.side_effect = RuntimeError("credential close failure")
        model = create_model(session_id="adk-one", user_id="user-one")
        with self.assertRaises(ClientAuthenticationError) as raised:
            async with model.connect(LlmRequest()):
                self.fail("Authentication failure must prevent connection")
        self.assertIs(raised.exception, error)
        self.credential.__aexit__.assert_awaited_once()
        self.project.__aexit__.assert_not_awaited()
        self.assertEqual(self.transports, [])

    async def test_cancellation_survives_transport_cleanup_failure(self):
        error = asyncio.CancelledError()
        model = create_model(session_id="adk-one", user_id="user-one")
        with self.assertRaises(asyncio.CancelledError) as raised:
            async with model.connect(LlmRequest()) as connection:
                connection.raw.close = AsyncMock(side_effect=RuntimeError("transport close failure"))
                raise error
        self.assertIs(raised.exception, error)
        self.transports[0].close.assert_awaited_once()
        self.project.__aexit__.assert_awaited_once()
        self.credential.__aexit__.assert_awaited_once()

    async def test_cleanup_failure_without_primary_error_propagates(self):
        model = create_model(session_id="adk-one", user_id="user-one")
        with self.assertRaisesRegex(RuntimeError, "transport close failure"):
            async with model.connect(LlmRequest()) as connection:
                connection.raw.close = AsyncMock(side_effect=RuntimeError("transport close failure"))
        self.transports[0].close.assert_awaited_once()
        self.project.__aexit__.assert_awaited_once()
        self.credential.__aexit__.assert_awaited_once()

    async def make_runner(self, session_id="adk-one", user_id="user-one"):
        model = create_model(session_id=session_id, user_id=user_id)
        agent = Agent(name="voice_agent", model=model, include_contents="none")
        sessions = InMemorySessionService()
        await sessions.create_session(
            app_name=APP_NAME, user_id=user_id, session_id=session_id
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

    async def test_distinct_adk_users_keep_audio_and_transcripts_isolated(self):
        users = [("user-one", "adk-one"), ("user-two", "adk-two")]
        runners = [await self.make_runner(session_id, user_id) for user_id, session_id in users]
        queues = [LiveRequestQueue(), LiveRequestQueue()]
        sinks = [RecordingSink(), RecordingSink()]
        tasks = []
        try:
            for runner, requests, sink, (user_id, session_id) in zip(runners, queues, sinks, users):
                self.connected.clear()
                tasks.append(asyncio.create_task(receive_events(
                    runner, user_id, session_id, requests, sink,
                )))
                await asyncio.wait_for(self.connected.wait(), 5)
                await asyncio.wait_for(self.transports[-1].ready.wait(), 5)
            for index, (requests, raw) in enumerate(zip(queues, self.transports)):
                requests.send_realtime(types.Blob(
                    data=np.full(16000, index * 10000, dtype="<i2").tobytes(),
                    mime_type="audio/pcm;rate=16000",
                ))
                raw.events.put_nowait({
                    "type": "conversation.item.input_audio_transcription.completed",
                    "conversation_id": f"private-conversation-{index}",
                    "transcript": f"private transcript {index}",
                })
                raw.events.put_nowait(models.RealtimeServerEventResponseAudioDelta(
                    event_id=f"event-{index}", response_id=f"response-{index}",
                    item_id=f"item-{index}", output_index=0, content_index=0,
                    delta=np.full(240, index * 1000, dtype="<i2").tobytes(),
                ))
            for index, (sink, (user_id, session_id)) in enumerate(zip(sinks, users)):
                await sink.wait_for(lambda events: any(event.content for event in events))
                transcripts = [event for event in sink.events if event.input_transcription]
                self.assertEqual(len(transcripts), 1)
                self.assertEqual(transcripts[0].input_transcription.text, f"private transcript {index}")
                audio_events = [event for event in sink.events if event.content]
                self.assertEqual(len(audio_events), 1)
                self.assertEqual(
                    audio_events[0].content.parts[0].inline_data.data,
                    np.full(240, index * 1000, dtype="<i2").tobytes(),
                )
                metadata = audio_events[0].custom_metadata
                self.assertEqual(metadata["adk_user_id"], user_id)
                self.assertEqual(metadata["adk_session_id"], session_id)
                self.assertEqual(metadata["azure_conversation_id"], f"private-conversation-{index}")
            for requests in queues:
                requests.close()
            await asyncio.wait_for(asyncio.gather(*tasks), 5)
            first_audio, second_audio = [
                raw.input_audio_buffer.append.call_args.kwargs["audio"] for raw in self.transports
            ]
            self.assertNotEqual(first_audio, second_audio)
            self.assertTrue(all(raw.closed for raw in self.transports))
        finally:
            for requests in queues:
                requests.close()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.gather(*(runner.close() for runner in runners))

    async def test_startup_timeout_closes_resources(self):
        raw = FakeTransport()
        while not raw.events.empty():
            raw.events.get_nowait()
        model = create_model(session_id="adk-one", user_id="user-one")
        model.ready_timeout = 0.01
        with patch(f"{__name__}.FakeTransport", return_value=raw):
            with self.assertRaises(TimeoutError):
                async with model.connect(LlmRequest()):
                    self.fail("A session without readiness events must time out")
        self.assertTrue(raw.closed)
        self.credential.__aexit__.assert_awaited_once()
        self.project.__aexit__.assert_awaited_once()

    async def assert_receive_failure_cleanup(self, event, error_type, message):
        runner = await self.make_runner()
        requests = LiveRequestQueue()
        task = asyncio.create_task(receive_events(
            runner, "user-one", "adk-one", requests, RecordingSink(),
        ))
        try:
            await asyncio.wait_for(self.connected.wait(), 5)
            raw = self.transports[0]
            await asyncio.wait_for(raw.ready.wait(), 5)
            raw.events.put_nowait(event)
            with self.assertRaisesRegex(error_type, message):
                await asyncio.wait_for(task, 5)
            self.assertTrue(raw.closed)
            self.credential.__aexit__.assert_awaited_once()
            self.project.__aexit__.assert_awaited_once()
        finally:
            requests.close()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runner.close()

    async def test_unexpected_disconnect_closes_resources(self):
        await self.assert_receive_failure_cleanup(
            ConnectionResetError("remote disconnected"), ConnectionResetError, "remote disconnected",
        )

    async def test_receive_service_error_closes_resources(self):
        await self.assert_receive_failure_cleanup(
            {"type": "error", "error": {"code": "denied", "message": "no access"}},
            AzureVoiceError, "denied: no access",
        )

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
