"""One ADK live application for Gemini Live and Azure Foundry Voice Agent."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import AsyncExitStack, ExitStack, aclosing
import json
from pathlib import Path
import threading
import time
from uuid import uuid4
import wave

from dotenv import load_dotenv
from google.adk.agents import Agent, LiveRequestQueue
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from providers import create_model

INPUT_RATE = 16000
OUTPUT_RATE = 24000
CHUNK_SAMPLES = 320
APP_NAME = "adk_voice_sample"


class AudioDevice:
    def __init__(self, *, microphone: bool, playback: bool):
        self.microphone = microphone
        self.playback = playback
        self.input = asyncio.Queue(maxsize=100)
        self.output = bytearray()
        self.lock = threading.Lock()
        self.streams = []
        self.loop = asyncio.get_running_loop()
        self.failure = self.loop.create_future()

    def fail(self, message: str) -> None:
        if not self.failure.done():
            self.failure.set_result(message)

    def enqueue(self, audio: bytes) -> None:
        try:
            self.input.put_nowait(audio)
        except asyncio.QueueFull:
            self.fail("Microphone input queue overflow; connection is too slow")

    def capture(self, data, frames, timing, status) -> None:
        if status:
            self.loop.call_soon_threadsafe(self.fail, f"Audio capture error: {status}")
        self.loop.call_soon_threadsafe(self.enqueue, bytes(data))

    def render(self, data, frames, timing, status) -> None:
        if status:
            self.loop.call_soon_threadsafe(self.fail, f"Audio playback error: {status}")
        with self.lock:
            count = min(len(data), len(self.output))
            data[:] = bytes(self.output[:count]) + bytes(len(data) - count)
            del self.output[:count]

    def queue_output(self, audio: bytes) -> None:
        if not self.playback:
            return
        with self.lock:
            if len(self.output) + len(audio) > OUTPUT_RATE * 2 * 30:
                raise RuntimeError("Playback exceeded its 30-second bounded buffer")
            self.output.extend(audio)

    def interrupt(self) -> None:
        with self.lock:
            self.output.clear()

    def start(self) -> None:
        if not self.microphone and not self.playback:
            return
        import sounddevice as sd

        try:
            if self.playback:
                stream = sd.RawOutputStream(
                    samplerate=OUTPUT_RATE, channels=1, dtype="int16",
                    blocksize=480, callback=self.render,
                )
                self.streams.append(stream)
                stream.start()
            if self.microphone:
                stream = sd.RawInputStream(
                    samplerate=INPUT_RATE, channels=1, dtype="int16",
                    blocksize=CHUNK_SAMPLES, callback=self.capture,
                )
                self.streams.append(stream)
                stream.start()
        except BaseException as error:
            try:
                self.close()
            except BaseException as cleanup_error:
                raise error from cleanup_error
            raise

    def close(self) -> None:
        streams, self.streams = self.streams, []
        with ExitStack() as cleanup:
            for stream in streams:
                cleanup.callback(stream.close)
                cleanup.callback(stream.stop)

    async def drain(self) -> None:
        async with asyncio.timeout(31):
            while True:
                with self.lock:
                    if not self.output:
                        return
                await asyncio.sleep(0.02)


class EventSink:
    def __init__(self, audio: AudioDevice, output_dir: Path):
        self.audio = audio
        self.output_dir = output_dir
        self.turn_done = asyncio.Event()
        self.audio_started = asyncio.Event()
        self.turn_count = 0
        self.audio_bytes = 0
        self.log = None
        self.recording = None
        self.cleanup = ExitStack()

    def __enter__(self):
        try:
            self.output_dir.mkdir(parents=True, exist_ok=False)
            self.log = (self.output_dir / "events.jsonl").open("w", encoding="utf-8")
            self.cleanup.callback(self.log.close)
            self.recording = wave.open(str(self.output_dir / "generated.wav"), "wb")
            self.cleanup.callback(self.recording.close)
            self.recording.setnchannels(1)
            self.recording.setsampwidth(2)
            self.recording.setframerate(OUTPUT_RATE)
            return self
        except BaseException as error:
            self.__exit__(type(error), error, error.__traceback__)
            raise

    def __exit__(self, exc_type, error, traceback):
        try:
            self.cleanup.close()
        except BaseException as cleanup_error:
            if error is not None and cleanup_error is not error:
                raise error from cleanup_error
            raise

    def handle(self, event) -> None:
        record = {
            "time": time.time(),
            "author": event.author,
            "interrupted": bool(event.interrupted),
            "turn_complete": bool(event.turn_complete),
            "correlation": event.custom_metadata,
        }
        if event.error_code:
            raise RuntimeError(f"{event.error_code}: {event.error_message}")
        if event.interrupted:
            self.audio.interrupt()
            self.turn_done.clear()
            print("[interrupted]", flush=True)
        if event.content and not event.interrupted:
            for part in event.content.parts or []:
                if part.inline_data:
                    blob = part.inline_data
                    if blob.mime_type != "audio/pcm;rate=24000":
                        raise ValueError(f"Unexpected output audio contract: {blob.mime_type}")
                    data = blob.data or b""
                    self.audio.queue_output(data)
                    self.recording.writeframesraw(data)
                    self.audio_bytes += len(data)
                    self.audio_started.set()
                    record["audio_bytes"] = len(data)
                elif part.text:
                    record["text"] = part.text
                    print(part.text, end="", flush=True)
        for field in ("input_transcription", "output_transcription"):
            transcript = getattr(event, field)
            if transcript and transcript.text:
                record[field] = transcript.model_dump(exclude_none=True)
                print(f"\n{event.author}: {transcript.text}", flush=True)
        if event.turn_complete:
            self.turn_count += 1
            self.turn_done.set()
            print(f"\n[turn {self.turn_count} complete]", flush=True)
        self.log.write(json.dumps(record) + "\n")
        self.log.flush()


async def receive_events(runner, user_id, session_id, requests, sink) -> None:
    config = RunConfig(
        streaming_mode=StreamingMode.BIDI,
        response_modalities=["AUDIO"],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        save_live_blob=False,
    )
    async with aclosing(runner.run_live(
        user_id=user_id, session_id=session_id,
        live_request_queue=requests, run_config=config,
    )) as events:
        async for event in events:
            sink.handle(event)


async def send_microphone(requests, audio) -> None:
    while True:
        requests.send_realtime(types.Blob(
            data=await audio.input.get(), mime_type="audio/pcm;rate=16000"
        ))


async def send_wavs(requests, paths, sink, timeout, *, barge_in=False) -> None:
    for index, path in enumerate(paths):
        with wave.open(str(path), "rb") as source:
            if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, INPUT_RATE):
                raise ValueError(f"{path} must be mono PCM16 WAV at {INPUT_RATE} Hz")
            sink.turn_done.clear()
            sink.audio_started.clear()
            while chunk := source.readframes(CHUNK_SAMPLES):
                requests.send_realtime(types.Blob(data=chunk, mime_type="audio/pcm;rate=16000"))
                await asyncio.sleep(len(chunk) / (INPUT_RATE * 2))
        for _ in range(50):
            requests.send_realtime(types.Blob(
                data=bytes(CHUNK_SAMPLES * 2), mime_type="audio/pcm;rate=16000"
            ))
            await asyncio.sleep(CHUNK_SAMPLES / INPUT_RATE)
        if barge_in and index == len(paths) - 2:
            await asyncio.wait_for(sink.audio_started.wait(), timeout)
            if sink.turn_done.is_set():
                raise RuntimeError("Response ended before barge-in; use a longer response prompt")
        else:
            await asyncio.wait_for(sink.turn_done.wait(), timeout)


async def run(args) -> None:
    user_id = uuid4().hex
    session_id = uuid4().hex
    sessions = InMemorySessionService()
    agent = Agent(
        name="voice_agent",
        model=create_model(session_id=session_id, user_id=user_id),
        include_contents="none",
    )
    runner = Runner(agent=agent, app_name=APP_NAME, session_service=sessions)
    await sessions.create_session(app_name=APP_NAME, user_id=user_id, session_id=session_id)
    requests = LiveRequestQueue()
    audio = AudioDevice(microphone=not args.wav, playback=not args.no_playback)
    output_dir = args.output_dir or Path("artifacts") / session_id
    tasks = []
    print(f"ADK session: {session_id}\nArtifacts: {output_dir}", flush=True)
    primary_error: BaseException | None = None
    try:
        with EventSink(audio, output_dir) as sink:
            audio.start()
            receive = asyncio.create_task(receive_events(
                runner, user_id, session_id, requests, sink
            ))
            send = asyncio.create_task(
                send_wavs(requests, args.wav, sink, args.turn_timeout,
                          barge_in=getattr(args, "barge_in", False))
                if args.wav else send_microphone(requests, audio)
            )
            tasks = [receive, send]
            done, _ = await asyncio.wait(
                [*tasks, audio.failure], timeout=args.seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if audio.failure in done:
                raise RuntimeError(audio.failure.result())
            if not done and args.wav:
                raise TimeoutError("WAV conversation did not finish before --seconds")
            for task in done:
                task.result()
            if receive in done and not requests.closed:
                raise ConnectionError("Live provider disconnected before the application closed")
            if args.wav and sink.audio_bytes == 0:
                raise RuntimeError("No audio output received")
            drain = asyncio.create_task(audio.drain())
            tasks.append(drain)
            pending = {*tasks, audio.failure}
            while True:
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED,
                )
                if audio.failure in done:
                    raise RuntimeError(audio.failure.result())
                for task in done:
                    task.result()
                if receive in done and not requests.closed:
                    raise ConnectionError("Live provider disconnected before the application closed")
                if drain in done:
                    break
    except BaseException as error:
        primary_error = error
        raise
    finally:
        try:
            requests.close()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            async with AsyncExitStack() as cleanup:
                cleanup.push_async_callback(
                    sessions.delete_session, app_name=APP_NAME,
                    user_id=user_id, session_id=session_id,
                )
                cleanup.push_async_callback(runner.close)
                cleanup.callback(audio.close)
        except BaseException as cleanup_error:
            if primary_error is not None and cleanup_error is not primary_error:
                raise primary_error from cleanup_error
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--wav", type=Path, action="append", default=[])
    parser.add_argument("--no-playback", action="store_true")
    parser.add_argument("--barge-in", action="store_true")
    parser.add_argument("--seconds", type=float, default=None)
    parser.add_argument("--turn-timeout", type=float, default=60)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    load_dotenv(args.env_file, override=True)
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nDisconnected.")


if __name__ == "__main__":
    main()
