"""ADK live-model integration using the standalone Azure VoiceLive SDK."""

from __future__ import annotations

import asyncio
import base64
from contextlib import AsyncExitStack, asynccontextmanager
from typing import AsyncGenerator
from urllib.parse import urlsplit

from azure.ai.voicelive import models
from azure.ai.voicelive.aio import connect
from azure.identity.aio import DefaultAzureCredential
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from azure_adk import AzureLiveConnection, AzureVoiceError, INPUT_RATE, OUTPUT_RATE, PcmResampler


class AzureVoiceLive(BaseLlm):
    """Direct Voice Live provider; no Projects client or saved agent required."""

    endpoint: str
    voice: str
    instructions: str = "You are a helpful voice assistant."
    adk_session_id: str
    adk_user_id: str
    ready_timeout: float = 60.0

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        raise NotImplementedError("AzureVoiceLive supports Runner.run_live() only")
        yield LlmResponse()

    def session_config(self, llm_request: LlmRequest) -> models.RequestSession:
        live_config = llm_request.live_connect_config
        if live_config.session_resumption is not None:
            raise ValueError("Voice Live session resumption is not supported by this adapter")
        if live_config.speech_config is not None:
            raise ValueError("Configure AZURE_VOICELIVE_VOICE, not RunConfig.speech_config")
        instruction = llm_request.config.system_instruction
        if isinstance(instruction, types.Content):
            if any(part.text is None for part in instruction.parts or []):
                raise ValueError("Voice Live supports text-only system instructions")
            instruction = "\n".join(part.text for part in instruction.parts or [])
        if instruction is not None and not isinstance(instruction, str):
            raise ValueError("Voice Live supports string or text Content system instructions")
        tools = []
        names = set()
        for tool in llm_request.config.tools or []:
            if not isinstance(tool, types.Tool) or not tool.function_declarations:
                raise ValueError("Only ADK function tools are supported")
            for declaration in tool.function_declarations:
                name = declaration.name
                if not name or name not in llm_request.tools_dict or name in names:
                    raise ValueError("Every function declaration needs one matching ADK executor")
                if declaration.parameters is not None:
                    raise ValueError(
                        "Voice Live requires parameters_json_schema function declarations; "
                        "use ADK's default JSON-schema function generation"
                    )
                tools.append(models.FunctionTool(
                    name=name,
                    description=declaration.description,
                    parameters=declaration.parameters_json_schema
                    if declaration.parameters_json_schema is not None
                    else {"type": "object", "properties": {}},
                ))
                names.add(name)
        if names != set(llm_request.tools_dict):
            raise ValueError("Every ADK function executor needs a matching function declaration")
        return models.RequestSession(
            modalities=[models.Modality.TEXT, models.Modality.AUDIO],
            instructions="\n\n".join(text for text in (self.instructions, instruction) if text),
            voice=models.AzureStandardVoice(name=self.voice),
            input_audio_format=models.InputAudioFormat.PCM16,
            input_audio_sampling_rate=OUTPUT_RATE,
            output_audio_format=models.OutputAudioFormat.PCM16,
            input_audio_transcription=models.AudioInputTranscriptionOptions(model="azure-speech"),
            turn_detection=models.ServerVad(
                create_response=True, interrupt_response=True, auto_truncate=True,
            ),
            tools=tools,
        )

    @asynccontextmanager
    async def connect(self, llm_request: LlmRequest):
        endpoint = urlsplit(self.endpoint)
        if endpoint.scheme != "https" or not endpoint.netloc:
            raise ValueError("AZURE_VOICELIVE_ENDPOINT must be an HTTPS resource endpoint")
        session = self.session_config(llm_request)
        primary_error: BaseException | None = None
        try:
            async with AsyncExitStack() as stack:
                try:
                    credential = await stack.enter_async_context(DefaultAzureCredential())
                    raw = await stack.enter_async_context(connect(
                        endpoint=self.endpoint, credential=credential, model=self.model,
                    ))
                    connection = VoiceLiveConnection(
                        raw,
                        {
                            "provider": "voicelive",
                            "adk_session_id": self.adk_session_id,
                            "adk_user_id": self.adk_user_id,
                        },
                        tool_names=set(llm_request.tools_dict),
                    )
                    stack.push_async_callback(connection.close)
                    await raw.session.update(session=session)
                    await asyncio.wait_for(connection.wait_ready(), self.ready_timeout)
                    yield connection
                except BaseException as error:
                    primary_error = error
                    raise
        except BaseException as cleanup_error:
            if primary_error is not None and cleanup_error is not primary_error:
                raise primary_error from cleanup_error
            raise


class VoiceLiveConnection(AzureLiveConnection):
    """Reuse realtime turn/tool handling, adapting the standalone SDK contract."""

    def configure_audio(self, session: dict) -> None:
        if (session.get("input_audio_format") != "pcm16"
                or session.get("output_audio_format") != "pcm16"
                or session.get("input_audio_sampling_rate", OUTPUT_RATE) != OUTPUT_RATE):
            raise AzureVoiceError("Voice Live requires mono PCM16 at 24 kHz in both directions")
        vad = session.get("turn_detection") or {}
        if vad.get("type") != "server_vad":
            raise AzureVoiceError("Voice Live must enable server VAD")
        if vad.get("create_response") is False or vad.get("interrupt_response") is False:
            raise AzureVoiceError("Voice Live must enable automatic responses and interruption")
        if not session.get("input_audio_transcription"):
            raise AzureVoiceError("Voice Live must enable input transcription")
        functions = {
            tool["name"] for tool in session.get("tools") or [] if tool.get("type") == "function"
        }
        if functions != self.tool_names:
            raise AzureVoiceError("Voice Live session function tools must match ADK function tools")
        if self.input_resampler is None:
            self.input_resampler = PcmResampler(INPUT_RATE, OUTPUT_RATE)
            self.output_resampler = PcmResampler(OUTPUT_RATE, OUTPUT_RATE)
        self.correlation["azure_session_id"] = session["id"]

    async def append_audio(self, audio: bytes) -> None:
        await self.raw.input_audio_buffer.append(audio=base64.b64encode(audio).decode("ascii"))

    def audio_delta(self, event) -> bytes:
        if not isinstance(event, models.ServerEventResponseAudioDelta):
            raise AzureVoiceError("Expected VoiceLive SDK-decoded audio event")
        return event.delta
