"""Sample ADK live-model integration for an existing Foundry Voice Agent."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
import json
from typing import AsyncGenerator
from urllib.parse import urlsplit
from uuid import uuid4

import numpy as np
import soxr
from azure.ai.projects import models
from azure.ai.projects.aio import AIProjectClient
from azure.identity.aio import DefaultAzureCredential
from google.adk.models.base_llm import BaseLlm
from google.adk.models.base_llm_connection import BaseLlmConnection
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

INPUT_RATE = 16000
OUTPUT_RATE = 24000
INPUT_MIME = "audio/pcm;rate=16000"
OUTPUT_MIME = "audio/pcm;rate=24000"


class AzureVoiceError(RuntimeError):
    """An Azure service, protocol, or unsupported-feature failure."""


class PcmResampler:
    """Stateful mono signed little-endian PCM16 conversion."""

    def __init__(self, source_rate: int, target_rate: int):
        self.source_rate = source_rate
        self.target_rate = target_rate
        self.stream = soxr.ResampleStream(
            source_rate, target_rate, 1, dtype="float32", quality="HQ"
        )

    def convert(self, audio: bytes, *, last: bool = False) -> bytes:
        if len(audio) % 2:
            raise ValueError("PCM16 audio must contain whole two-byte samples")
        if self.source_rate == self.target_rate:
            return audio
        samples = np.frombuffer(audio, dtype="<i2").astype(np.float32)
        converted = self.stream.resample_chunk(samples, last=last)
        return np.clip(np.rint(converted), -32768, 32767).astype("<i2").tobytes()


class AzureVoiceAgent(BaseLlm):
    """ADK extension, not a built-in Google or Azure SDK provider."""

    model: str = "azure-foundry-voice-agent"
    project_endpoint: str
    agent_name: str
    adk_session_id: str
    adk_user_id: str
    structured_inputs: dict[str, str] | None = None
    ready_timeout: float = 60.0

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        raise NotImplementedError("AzureVoiceAgent supports Runner.run_live() only")
        yield LlmResponse()

    @asynccontextmanager
    async def connect(self, llm_request: LlmRequest):
        endpoint = urlsplit(self.project_endpoint)
        if endpoint.scheme != "https" or not endpoint.netloc:
            raise ValueError("AZURE_PROJECT_ENDPOINT must be an HTTPS project endpoint")
        tool_names = set(llm_request.tools_dict)
        for tool in llm_request.config.tools or []:
            if not tool.function_declarations:
                raise ValueError("Only ADK function tools are supported")
            if any(declaration.name not in tool_names for declaration in tool.function_declarations):
                raise ValueError("Every function declaration needs an ADK executor")
        live_config = llm_request.live_connect_config
        if live_config.session_resumption is not None:
            raise ValueError("Azure session resumption is not supported by this adapter")
        if live_config.speech_config is not None:
            raise ValueError("Configure the voice on the saved Azure agent, not RunConfig")
        agent_session_id = uuid4().hex
        primary_error: BaseException | None = None
        try:
            async with AsyncExitStack() as stack:
                try:
                    credential = await stack.enter_async_context(DefaultAzureCredential())
                    project = await stack.enter_async_context(
                        AIProjectClient(
                            endpoint=self.project_endpoint,
                            credential=credential,
                            allow_preview=True,
                        )
                    )
                    raw = await stack.enter_async_context(
                        project.beta.voice_agents.realtime.connect(
                            agent_name=self.agent_name, agent_session_id=agent_session_id,
                            structured_inputs=self.structured_inputs,
                        )
                    )
                    connection = AzureLiveConnection(
                        raw,
                        {
                            "provider": "azure",
                            "adk_session_id": self.adk_session_id,
                            "adk_user_id": self.adk_user_id,
                            "azure_agent_session_id": agent_session_id,
                        },
                        tool_names=tool_names,
                    )
                    stack.push_async_callback(connection.close)
                    await asyncio.wait_for(connection.wait_ready(), self.ready_timeout)
                    yield connection
                except BaseException as error:
                    primary_error = error
                    raise
        except BaseException as cleanup_error:
            if primary_error is not None and cleanup_error is not primary_error:
                raise primary_error from cleanup_error
            raise


class AzureLiveConnection(BaseLlmConnection):
    def __init__(self, raw, correlation: dict[str, str], *, tool_names: set[str] | None = None):
        self.raw = raw
        self.correlation = dict(correlation)
        self.closed = False
        self.failure: Exception | None = None
        self.pending: list[LlmResponse] = []
        self.input_resampler: PcmResampler | None = None
        self.output_resampler: PcmResampler | None = None
        self.active_response_id: str | None = None
        self.interrupted_response_ids: set[str] = set()
        self.tool_names = set(tool_names or ())
        self.function_calls: dict[str, list[types.Part]] = {}
        self.pending_calls: dict[str, str] = {}

    def response(self, **kwargs) -> LlmResponse:
        return LlmResponse(custom_metadata=dict(self.correlation), **kwargs)

    @staticmethod
    def payload(event) -> dict:
        return event.as_dict() if hasattr(event, "as_dict") else dict(event)

    def configure_audio(self, session: dict) -> None:
        audio = session.get("audio") or {}
        rates = []
        for direction in ("input", "output"):
            settings = audio.get(direction) or {}
            audio_format = settings.get("format") or {}
            if audio_format.get("type") != "audio/pcm":
                raise AzureVoiceError(f"Azure {direction} audio must be audio/pcm")
            rate = audio_format.get("rate", OUTPUT_RATE)
            if rate != OUTPUT_RATE:
                raise AzureVoiceError(f"Unsupported Azure {direction} PCM rate: {rate}")
            rates.append(rate)
        input_config = audio["input"]
        if not input_config.get("turn_detection"):
            raise AzureVoiceError("The saved Azure agent must enable server turn detection")
        if input_config["turn_detection"].get("create_response") is False:
            raise AzureVoiceError("The saved Azure agent must enable automatic response creation")
        if input_config["turn_detection"].get("interrupt_response") is False:
            raise AzureVoiceError("The saved Azure agent must enable interruption handling")
        if not input_config.get("transcription"):
            raise AzureVoiceError("The saved Azure agent must enable input transcription")
        saved_functions = {
            tool["name"] for tool in session.get("tools") or []
            if tool.get("type") == "function"
        }
        if saved_functions != self.tool_names:
            raise AzureVoiceError(
                "Saved Azure function tools must match ADK function tools: "
                f"saved={sorted(saved_functions)}, ADK={sorted(self.tool_names)}. "
                "For the registered function tools, preview setup with: python providers.py "
                "--env-file <your-env-file> --configure-azure-tools; "
                "then rerun setup with --apply to create a new saved agent version. "
                "Or omit --enable-tools to use an agent without client functions."
            )
        if self.input_resampler is None:
            self.input_resampler = PcmResampler(INPUT_RATE, rates[0])
            self.output_resampler = PcmResampler(rates[1], OUTPUT_RATE)
        self.correlation["azure_session_id"] = session["id"]

    async def wait_ready(self) -> None:
        ready = set()
        while ready != {"session.created", "session.updated"}:
            event = await self.raw.recv()
            payload = self.payload(event)
            event_type = payload.get("type")
            if event_type in {"session.created", "session.updated"}:
                if event_type == "session.updated":
                    self.configure_audio(payload["session"])
                ready.add(event_type)
            self.pending.extend(self.translate(event))

    async def send_history(self, history: list[types.Content]) -> None:
        if history:
            raise ValueError(
                "History replay/resumption is unsupported; start a fresh ADK session"
            )

    async def send_operation(self, operation) -> None:
        if self.closed:
            raise ConnectionError("Azure live connection is closed")
        try:
            await operation()
        except Exception as error:
            self.failure = error
            try:
                await self.raw.close()
            except Exception as cleanup_error:
                raise error from cleanup_error
            raise

    async def send_content(self, content: types.Content) -> None:
        await self.send_operation(lambda: self.send_text(content))

    async def send_text(self, content: types.Content, *, partial: bool = False) -> None:
        if partial:
            raise ValueError("Partial text turns are unsupported")
        if content.parts and all(part.function_response for part in content.parts):
            await self.send_tool_results(content)
            return
        if content.role not in (None, "user") or not content.parts:
            raise ValueError("Only nonempty user text content is supported")
        if any(part.text is None for part in content.parts):
            raise ValueError("Mixed tool results and non-text content are unsupported")
        await self.raw.send(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": part.text}
                            for part in content.parts
                        ],
                    },
                }
        )
        await self.raw.send({"type": "response.create"})

    async def send_tool_results(self, content: types.Content) -> None:
        results = [part.function_response for part in content.parts]
        result_ids = [result.id for result in results]
        if len(set(result_ids)) != len(result_ids) or any(
            not result.id or self.pending_calls.get(result.id) != result.name
            for result in results
        ):
            raise ValueError("Tool results must match pending function call IDs and names")
        outputs = [
            {"type": "function_call_output", "call_id": result.id,
             "output": json.dumps(result.response, allow_nan=False)}
            for result in results
        ]
        for output in outputs:
            await self.raw.send({"type": "conversation.item.create", "item": output})
            del self.pending_calls[output["call_id"]]
        if not self.pending_calls:
            await self.raw.send({"type": "response.create"})

    async def _send_content(self, content: types.Content, *, partial: bool = False):
        await self.send_operation(lambda: self.send_text(content, partial=partial))

    async def send_realtime(self, blob: types.Blob) -> None:
        await self.send_operation(lambda: self.send_audio(blob))

    async def send_audio(self, blob: types.Blob) -> None:
        if not isinstance(blob, types.Blob) or blob.mime_type != INPUT_MIME:
            raise ValueError(f"Expected mono PCM16 Blob with MIME {INPUT_MIME}")
        if self.input_resampler is None:
            raise RuntimeError("Azure session audio configuration is not ready")
        audio = self.input_resampler.convert(blob.data or b"")
        if audio:
            await self.raw.input_audio_buffer.append(audio=audio)

    def translate(self, event) -> list[LlmResponse]:
        payload = self.payload(event)
        event_type = payload.get("type", "")
        conversation_id = payload.get("conversation_id") or (
            payload.get("response") or {}
        ).get("conversation_id")
        if conversation_id:
            self.correlation["azure_conversation_id"] = conversation_id
        if event_type == "error":
            error = payload.get("error") or {}
            raise AzureVoiceError(f"{error.get('code', 'service_error')}: {error.get('message', '')}")
        if event_type.endswith("input_audio_transcription.failed"):
            raise AzureVoiceError(f"Azure input transcription failed: {payload.get('error')}")
        if event_type in {"session.created", "session.updated"}:
            if event_type == "session.updated":
                self.configure_audio(payload["session"])
            return [self.response()]
        if event_type == "response.created":
            self.active_response_id = payload["response"]["id"]
        if event_type == "input_audio_buffer.speech_started":
            if self.active_response_id:
                self.interrupted_response_ids.add(self.active_response_id)
            return [self.response(interrupted=True)]
        response_id = payload.get("response_id")
        if response_id in self.interrupted_response_ids:
            return []
        if event_type in {"response.audio.delta", "response.output_audio.delta"}:
            if not isinstance(event, models.RealtimeServerEventResponseAudioDelta):
                raise AzureVoiceError("Expected SDK-decoded audio event")
            if self.output_resampler is None:
                raise AzureVoiceError("Audio received before session configuration")
            audio = self.output_resampler.convert(event.delta)
            return [self.response(
                partial=True,
                content=types.Content(role="model", parts=[
                    types.Part(inline_data=types.Blob(data=audio, mime_type=OUTPUT_MIME))
                ]),
            )]
        if event_type == "conversation.item.input_audio_transcription.completed":
            return [self.response(input_transcription=types.Transcription(
                text=payload.get("transcript", ""), finished=True
            ))]
        if event_type in {"response.audio_transcript.done", "response.output_audio_transcript.done"}:
            return [self.response(output_transcription=types.Transcription(
                text=payload.get("transcript", ""), finished=True
            ))]
        if event_type in {"response.text.delta", "response.output_text.delta"}:
            return [self.response(partial=True, content=types.Content(
                role="model", parts=[types.Part(text=payload.get("delta", ""))]
            ))]
        if event_type == "response.output_item.added":
            item_type = (payload.get("item") or {}).get("type", "")
            if item_type == "mcp_approval_request":
                raise AzureVoiceError(f"Unsupported client execution/approval flow: {item_type}")
        if event_type == "response.function_call_arguments.done":
            name, call_id = payload.get("name"), payload.get("call_id")
            if name not in self.tool_names or not call_id or not response_id:
                raise AzureVoiceError("Unknown function or missing function call identity")
            try:
                arguments = json.loads(payload["arguments"])
            except (KeyError, TypeError, ValueError) as error:
                raise AzureVoiceError("Invalid function call arguments") from error
            if not isinstance(arguments, dict):
                raise AzureVoiceError("Function call arguments must be a JSON object")
            calls = self.function_calls.setdefault(response_id, [])
            if call_id in self.pending_calls or any(
                part.function_call.id == call_id
                for parts in self.function_calls.values() for part in parts
            ):
                raise AzureVoiceError("Duplicate function call ID")
            calls.append(types.Part(function_call=types.FunctionCall(
                id=call_id, name=name, args=arguments,
            )))
            return []
        if event_type == "response.done":
            response = payload["response"]
            calls = self.function_calls.pop(response["id"], [])
            status = response.get("status")
            if response["id"] == self.active_response_id:
                self.active_response_id = None
            interrupted = response["id"] in self.interrupted_response_ids
            self.interrupted_response_ids.discard(response["id"])
            if status not in ("completed", "cancelled"):
                raise AzureVoiceError(
                    f"Azure response {status}: {response.get('status_details')}"
                )
            if interrupted:
                return []
            if status == "cancelled":
                return [self.response(interrupted=True)]
            if calls:
                self.pending_calls.update({part.function_call.id: part.function_call.name for part in calls})
                return [self.response(content=types.Content(role="model", parts=calls))]
            return [self.response(turn_complete=True)]
        return []

    async def receive(self) -> AsyncGenerator[LlmResponse, None]:
        pending, self.pending = self.pending, []
        for response in pending:
            yield response
        while not self.closed:
            try:
                event = await self.raw.recv()
            except Exception:
                if self.failure is not None:
                    raise self.failure
                if self.closed:
                    return
                raise
            for response in self.translate(event):
                yield response

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            await self.raw.close()
