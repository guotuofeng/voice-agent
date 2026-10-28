"""Local Foundry voice bridge: text, WebRTC signaling and WebSocket PCM audio."""

import asyncio
import base64
import binascii
import copy
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import tempfile
import time
import uuid
import zipfile
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

import aiohttp
import yaml
from aiohttp import web
from dotenv import load_dotenv

from auth import AuthenticationError, AzureTokenProvider, authentication_mode, find_azure_cli
from live import LiveSessionError, connect_live, live_definition

ROOT = Path(__file__).resolve().parent
LOGGER = logging.getLogger(__name__)
MAX_FRAME = 256 * 1024
MAX_UPSTREAM_FRAME = 4 * 1024 * 1024
TRANSPORTS = {"websocket", "webrtc"}
TRUNCATED_ITEM_INCLUDE = "conversation.item.truncated.item"


class SessionConfigurationError(Exception):
    """An actionable, non-sensitive session configuration failure."""


def truncation_update(session):
    """Preserve the advertised VAD configuration and use its wire format."""
    audio_input = (session.get("audio") or {}).get("input") or {}
    stable = "turn_detection" in audio_input
    vad = audio_input.get("turn_detection") if stable else session.get("turn_detection")
    if not isinstance(vad, dict) or not vad.get("type") or vad.get("create_response") is False:
        raise SessionConfigurationError(
            "The saved agent must report server-side turn detection with automatic responses.")
    include = session.get("include", [])
    if include is None:
        include = []
    if not isinstance(include, list) or any(not isinstance(value, str) for value in include):
        raise SessionConfigurationError("Azure returned an invalid session include list.")
    auto_truncate = session.get("model") not in {FLASH_MODEL, "azure-realtime-flash-1"}
    include = list(dict.fromkeys(
        [*include, TRUNCATED_ITEM_INCLUDE] if auto_truncate else
        [value for value in include if value != TRUNCATED_ITEM_INCLUDE]))
    vad = {**vad, "auto_truncate": auto_truncate, "interrupt_response": True}
    settings = {"include": include}
    if stable:
        settings.update({"type": "realtime", "audio": {"input": {"turn_detection": vad}}})
    else:
        settings["turn_detection"] = vad
    return {"type": "session.update", "session": settings}


@dataclass
class SessionConfiguration:
    update: dict | None = None
    session_id: str | None = None
    ready: bool = False
    closed: asyncio.Event = field(default_factory=asyncio.Event)

    async def observe(self, frame, upstream):
        # The first session.updated applies the saved agent, unlike session.created.
        if frame.get("type") != "session.updated":
            return False
        session = frame.get("session") or {}
        if self.update is None:
            self.update = truncation_update(session)
            self.session_id = session.get("id")
            await upstream.send_json(self.update)
            return False
        expected = self.update["session"]
        stable = "audio" in expected
        expected_vad = expected["audio"]["input"]["turn_detection"] if stable else expected["turn_detection"]
        vad = (
            ((session.get("audio") or {}).get("input") or {}).get("turn_detection")
            if stable else session.get("turn_detection")
        )
        include = session.get("include", [])
        if (session.get("id") != self.session_id or not isinstance(vad, dict)
                or vad.get("auto_truncate") is not expected_vad["auto_truncate"]
                or vad.get("interrupt_response") is not True
                or not isinstance(include, list)
                or (not expected_vad["auto_truncate"] and TRUNCATED_ITEM_INCLUDE in include)
                or any(value not in include for value in expected["include"])):
            self.ready = False
            raise SessionConfigurationError(
                "Azure did not acknowledge the requested auto_truncate, interrupt_response and retained-item include settings. "
                "Input remains disabled; check support for this agent endpoint/API version.")
        first_ack = not self.ready
        self.ready = True
        return first_ack


DEFAULT_AGENT_NAME = "webrtc-personalized-hd-demo"
WEATHER_TOOLBOX_NAME = "weather"
LANGUAGES = {"en-US": "English", "es-ES": "Spanish"}
HD_VOICES = {f"en-US-{name}:DragonHDLatestNeural" for name in ("Tessa", "Tyler", "Paige", "Colin", "Brian", "Emma")} | {
    "en-IN-Diya:DragonHDLatestNeural", "zh-CN-Xiaoqi:DragonHDFlashLatestNeural",
    "zh-CN-Xiaoke:DragonHDFlashLatestNeural"}
MAI_FLASH_VOICES = {"en-US-Harper:MAI-Voice-2.1-Flash", "en-GB-Harry:MAI-Voice-2.1-Flash"}
NATIVE_VOICES = {"ava", "andrew", "emma", "clara", "liam", "natasha", "rayn", "alvaro", "dalia", "jorge"}
FLASH_MODEL = "azure-realtime-flash-2026-10-14"
MODELS = {"gpt-realtime-2.1", "gpt-realtime-2.1-mini", "azure-realtime", FLASH_MODEL, "gpt-5.4"}
LIVE_MODEL = "gpt-live-1"
TRANSCRIPTION_MODELS = {
    "azure-speech", "azure-speech-streaming", "azure-llm-speech",
    "mai-transcribe", "mai-transcribe-2", "mai-transcribe-2-streaming",
    "gpt-live-transcribe", "gpt-realtime-whisper", "gpt-transcribe",
    "gpt-4o-transcribe", "gpt-4o-mini-transcribe", "gpt-4o-transcribe-diarize", "whisper-1",
}
DEFAULT_TRANSCRIPTION_MODEL = "mai-transcribe-2"
MAX_RECORDING_BYTES = 128 * 1024 * 1024
MAX_LOG_BYTES = 16 * 1024 * 1024


class ConversationDownloadError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def conversation_download_token(secret, agent, conversation):
    if not isinstance(conversation, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", conversation):
        raise ValueError("Invalid service conversation ID.")
    payload = json.dumps({"agent": agent, "conversation": conversation,
                          "expires": int(time.time()) + 86400}, separators=(",", ":")).encode()
    signature = hmac.digest(secret, payload, "sha256")
    return ".".join(base64.urlsafe_b64encode(part).decode().rstrip("=") for part in (payload, signature))


def verify_download_token(secret, agent, token):
    try:
        if not isinstance(token, str) or len(token) > 1024:
            raise ValueError
        parts = token.split(".")
        if len(parts) != 2:
            raise ValueError
        payload, signature = (
            base64.b64decode(part + "=" * (-len(part) % 4), altchars=b"-_", validate=True)
            for part in parts
        )
        if not hmac.compare_digest(signature, hmac.digest(secret, payload, "sha256")):
            raise ValueError
        claims = json.loads(payload)
        if (not isinstance(claims, dict) or set(claims) != {"agent", "conversation", "expires"}
                or claims["agent"] != agent or type(claims["expires"]) is not int
                or claims["expires"] <= time.time()
                or not isinstance(claims["conversation"], str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", claims["conversation"])):
            raise ValueError
        return claims["conversation"]
    except (ValueError, TypeError, binascii.Error) as error:
        raise ConversationDownloadError(
            "This download link is invalid, expired, or from a restarted server. "
            "Existing recordings remain in Foundry.", 403) from error


async def recorded_json(response):
    if response.status != 200:
        raise ConversationDownloadError(
            f"Foundry could not read the stored conversation (HTTP {response.status}). "
            "Check the server identity's conversation-read permissions.")
    raw = await response.content.read(MAX_LOG_BYTES + 1)
    if len(raw) > MAX_LOG_BYTES:
        raise ConversationDownloadError(
            "The conversation log exceeds this demo's 16 MiB limit. Use Foundry to download it.", 413)
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ConversationDownloadError("Foundry returned an invalid conversation log.")
    return result, len(raw)


async def write_conversation_archive(client, cfg, headers, conversation, output):
    base = (f"{cfg.project_url()}/agents/{quote(cfg.agent, safe='')}/endpoint/protocols/voice/"
            f"conversations/{quote(conversation, safe='')}")
    query = {"api-version": cfg.api_version}
    for _ in range(30):
        async with client.get(base, params=query, headers=headers, allow_redirects=False) as response:
            if response.status not in {404, 409}:
                record, _ = await recorded_json(response)
                if record.get("status") == "completed":
                    break
                if record.get("status") in {"failed", "canceled", "cancelled"}:
                    raise ConversationDownloadError(
                        "The stored conversation did not complete successfully. Check its records in Foundry.", 409)
        await asyncio.sleep(2)
    else:
        raise ConversationDownloadError(
            "Azure is still finalizing this recording. Wait a little, then download again.", 409)

    items, cursors, log_bytes = [], set(), 0
    params = {**query, "limit": 100, "order": "asc"}
    while True:
        async with client.get(f"{base}/items", params=params, headers=headers, allow_redirects=False) as response:
            page, size = await recorded_json(response)
        log_bytes += size
        if log_bytes > MAX_LOG_BYTES:
            raise ConversationDownloadError(
                "The conversation log exceeds this demo's 16 MiB limit. Use Foundry to download it.", 413)
        if not isinstance(page.get("data"), list) or type(page.get("has_more")) is not bool:
            raise ConversationDownloadError("Foundry returned an invalid conversation items page.")
        items.extend(page["data"])
        if not page["has_more"]:
            break
        cursor = page.get("last_id")
        if not isinstance(cursor, str) or not cursor or cursor in cursors:
            raise ConversationDownloadError("Foundry returned an invalid conversation pagination cursor.")
        cursors.add(cursor)
        params = {**params, "after": cursor}

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("conversation.json", json.dumps(
            {"conversation": record, "items": items}, ensure_ascii=False, indent=2).encode("utf-8"))
        for _ in range(30):
            async with client.get(f"{base}/audio/content", params=query, headers=headers,
                                  allow_redirects=False) as response:
                if response.status in {404, 409}:
                    await asyncio.sleep(2)
                    continue
                if response.status != 200:
                    raise ConversationDownloadError(
                        f"Foundry could not download the recording (HTTP {response.status}). "
                        "Check recording permissions or customer-managed storage in Foundry.")
                size, prefix = 0, bytearray()
                with archive.open("audio.wav", "w") as audio:
                    async for chunk in response.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > MAX_RECORDING_BYTES:
                            raise ConversationDownloadError(
                                "The recording exceeds this demo's 128 MiB limit. Use Foundry to download it.", 413)
                        prefix.extend(chunk[:max(0, 12 - len(prefix))])
                        audio.write(chunk)
                if prefix[:4] != b"RIFF" or prefix[8:12] != b"WAVE":
                    raise ConversationDownloadError("Foundry did not return a WAV recording. No download was created.")
                break
        else:
            raise ConversationDownloadError(
                "Azure is still preparing the audio. Wait a little, then download again.", 409)


async def download_conversation(request):
    if not allowed_origin(request):
        return web.json_response({"error": "Open the page on this server."}, status=403)
    cfg = settings_for_request(request)
    try:
        body = await request.json()
    except ValueError:
        return web.json_response({"error": "Use the download button for your conversation."}, status=400)
    response = None
    try:
        if not isinstance(body, dict) or set(body) != {"downloadToken"}:
            return web.json_response({"error": "Use the download button for your conversation."}, status=400)
        conversation = verify_download_token(request.app[DOWNLOAD_SECRET], cfg.agent, body["downloadToken"])
        async with asyncio.timeout(90), aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90)) as client:
            token = await request.app[TOKENS].get_token(client)
            headers = {"Authorization": f"Bearer {token}", "Foundry-Features": cfg.features}
            with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b") as output:
                await write_conversation_archive(client, cfg, headers, conversation, output)
                response = web.StreamResponse(headers={
                    "Content-Type": "application/zip",
                    "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                    "Content-Disposition": f'attachment; filename="conversation-{conversation}.zip"',
                    "Content-Length": str(output.tell()),
                })
                output.seek(0)
                await response.prepare(request)
                while chunk := output.read(65536):
                    await response.write(chunk)
                await response.write_eof()
                return response
    except ConversationDownloadError as error:
        LOGGER.warning("Conversation download failed (%s)", type(error).__name__)
        return web.json_response({"error": str(error)}, status=error.status)
    except (AuthenticationError, aiohttp.ClientError, TimeoutError, ValueError, OSError) as error:
        LOGGER.warning("Conversation download failed (%s)", type(error).__name__)
        if response is not None and response.prepared:
            raise
        return web.json_response({"error": "Could not download the stored conversation. "
                                  "Check Azure access and retry after the recording is finalized."}, status=502)


def personalization_header(frame):
    if (not isinstance(frame, dict)
            or set(frame) != {"type", "display_name", "user_context", "client_language", "english_only"}):
        raise ValueError("Invalid personalization fields.")
    if frame["type"] != "bridge.personalize" or type(frame["english_only"]) is not bool:
        raise ValueError("Invalid personalization request.")
    for key, limit in (("display_name", 80), ("user_context", 2000)):
        value = frame[key]
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError(f"{key} must be text of at most {limit} characters.")
    language = frame["client_language"]
    if not isinstance(language, str) or language not in LANGUAGES:
        raise ValueError("Choose English or Spanish.")
    english = frame["english_only"]
    name = frame["display_name"].strip()
    greeting = (
        f"Hello, {name}! How can I help you?" if name else "Hello! How can I help you?"
    ) if english or language == "en-US" else (
        f"¡Hola, {name}! ¿En qué puedo ayudarte?" if name else "¡Hola! ¿En qué puedo ayudarte?"
    )
    values = {
        "display_name": name,
        "user_context": frame["user_context"].strip(),
        "client_language": language,
        "language_policy": (
            "Conduct the entire conversation in English, including greetings, written and spoken replies, "
            "clarifications, summaries, and tool-result explanations. If the caller uses another language, "
            "say in English: Please continue in English so I can help you. Do not fulfill or translate "
            "the non-English request. Do not switch languages even when requested. Proper names and "
            "identifiers may retain their original form. For unclear input, ask in English for repetition "
            "rather than assuming another language. This policy overrides the client language preference."
            if english else (
                f"Use {LANGUAGES[language]} for the initial greeting. After that, respond in the language "
                "the caller speaks or explicitly requests, including languages other than English or Spanish. "
                "Switch languages when requested. Do not ask the caller to use English or refuse a request "
                "just because it is not in English. For unclear audio, ask for clarification rather than "
                "guessing the language."
            )
        ),
        "greeting_text": greeting,
    }
    encoded = json.dumps(values, ensure_ascii=True, separators=(",", ":"))
    if len(encoded) > 16000:
        raise ValueError("Encoded personalization exceeds the 16 KiB limit.")
    return encoded


@dataclass
class CallState:
    active: bool = False


@dataclass(frozen=True)
class Settings:
    endpoint: str
    agent: str = DEFAULT_AGENT_NAME
    api_version: str = "v1"
    features: str = "VoiceAgents=V1Preview"
    ice_servers: tuple = ()
    public_origin: str = ""
    personalization_required: bool = False
    allow_agent_recreate: bool = False
    live_deployment: str = "gpt-live-1"
    live_backend_deployment: str = "gpt-5.4"
    companion_of: str = ""
    companion_protocol: str = ""

    def __post_init__(self):
        object.__setattr__(self, "agent", self.agent.strip() or DEFAULT_AGENT_NAME)

    @property
    def setup_issues(self):
        issues = []
        if not self.endpoint or "YOUR-" in self.endpoint:
            issues.append("Set AZURE_AI_PROJECT_ENDPOINT to your Foundry project endpoint.")
        return issues

    @property
    def configured(self):
        return not self.setup_issues

    def project_url(self):
        parsed = urlsplit(self.endpoint)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment
                or not parsed.path.startswith("/api/projects/")
                or len(parsed.path.rstrip('/').split('/')) != 4
                or not parsed.path.rstrip('/').split('/')[-1]):
            raise ValueError("Use the HTTPS Foundry project endpoint from the project overview.")
        return f"https://{parsed.netloc}{parsed.path.rstrip('/')}"

    def agent_url(self, collection=False):
        path = "/agents" if collection else f"/agents/{quote(self.agent, safe='')}"
        params = {"api-version": self.api_version}
        if collection:
            params["limit"] = 1
        return f"{self.project_url()}{path}?{urlencode(params)}"

    def voice_url(self, transport="webrtc"):
        if transport not in TRANSPORTS:
            raise ValueError("Unsupported transport.")
        project = self.project_url().replace("https://", "wss://", 1)
        params = {"api-version": self.api_version,
                  "agent_session_id": f"web-{uuid.uuid4().hex}", "store": "true",
                  "features": "include_estimated_cost:true"}
        # The default service transport is WebSocket; only WebRTC needs a selector.
        if transport == "webrtc":
            params["transport"] = "webrtc"
        query = urlencode(params)
        return (f"{project}/agents/"
                f"{quote(self.agent, safe='')}/endpoint/protocols/voice?{query}")


def companion_settings(cfg, protocol):
    name = f"{cfg.agent}-{protocol}"
    if len(name) > 63:
        digest = hashlib.sha256(cfg.agent.encode()).hexdigest()[:8]
        name = f"{cfg.agent[:42].rstrip('-')}-{digest}-{protocol}"
    return replace(cfg, agent=name, companion_of=cfg.agent, companion_protocol=protocol)


def settings_for_request(request):
    cfg = request.app[SETTINGS]
    protocol = request.query.get("agent_selection", "base")
    if protocol not in {"base", "live"}:
        raise web.HTTPBadRequest(text="Choose base or live Agent selection.")
    return companion_settings(cfg, "live") if protocol == "live" else cfg


def load_settings():
    load_dotenv(ROOT / ".env")
    ice = json.loads(os.getenv("ICE_SERVERS_JSON", '[{"urls":"stun:stun.l.google.com:19302"}]'))
    if not isinstance(ice, list) or any(not isinstance(s, dict) or not s.get("urls") for s in ice):
        raise ValueError("ICE_SERVERS_JSON must be a JSON array of ICE server objects.")
    public_origin = os.getenv("PUBLIC_ORIGIN", "").strip().rstrip("/")
    if public_origin:
        parsed = urlsplit(public_origin)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.path or parsed.query or parsed.fragment):
            raise ValueError("PUBLIC_ORIGIN must be an HTTPS origin without a path, query or fragment.")
    return Settings(os.getenv("AZURE_AI_PROJECT_ENDPOINT", "").strip().rstrip("/"),
                    os.getenv("AZURE_VOICE_AGENT_NAME", "").strip(),
                    os.getenv("AZURE_API_VERSION", "v1"),
                    os.getenv("FOUNDRY_FEATURES", "VoiceAgents=V1Preview"), tuple(ice),
                    public_origin, os.getenv("PERSONALIZATION_REQUIRED", "").lower() == "true",
                    os.getenv("ALLOW_AGENT_RECREATE", "").lower() == "true",
                    os.getenv("GPT_LIVE_DEPLOYMENT", "gpt-live-1").strip(),
                    os.getenv("GPT_LIVE_BACKEND_DEPLOYMENT", "gpt-5.4").strip())


def origin_allowed(host_value, scheme, origin, public_origin=""):
    host = urlsplit(f"http://{host_value}").hostname
    if host not in {"localhost", "127.0.0.1", "::1"}:
        return bool(public_origin and origin == public_origin
                    and host_value == urlsplit(public_origin).netloc)
    return origin == f"{scheme}://{host_value}"


def allowed_origin(request):
    cfg = request.app[SETTINGS]
    return origin_allowed(request.host, request.scheme, request.headers.get("Origin"),
                          cfg.public_origin)


@web.middleware
async def security_headers(request, handler):
    response = await handler(request)
    response.headers.update({
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer", "Permissions-Policy": "microphone=(self)",
        "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
                                   "connect-src 'self' https://api.open-meteo.com; media-src 'self' blob:; "
                                   "frame-ancestors 'none'; base-uri 'none'",
    })
    return response


async def public_config(request):
    cfg = settings_for_request(request)
    auth_mode = authentication_mode()
    return web.json_response({"configured": cfg.configured, "agent": cfg.agent,
                              "iceServers": list(cfg.ice_servers),
                              "authentication": auth_mode,
                              "azureCliInstalled": auth_mode != "azure_cli" or bool(find_azure_cli()),
                              "setupIssues": cfg.setup_issues,
                              "personalizationRequired": cfg.personalization_required,
                              "agentRecreateAllowed": cfg.allow_agent_recreate,
                              "models": sorted(MODELS | {LIVE_MODEL}),
                              "transcriptionModels": sorted(TRANSCRIPTION_MODELS),
                              "defaultTranscriptionModel": DEFAULT_TRANSCRIPTION_MODEL,
                              "transports": ["websocket", "webrtc"], "defaultTransport": "websocket"})


async def health(request):
    return web.json_response({"status": "ok"})


class AgentPreparationError(Exception):
    """A safe error from the configured Agent's lifecycle operations."""


def weather_toolbox_attached(definition):
    return any(tool.get("type") == "toolbox" and tool.get("toolbox_name") == WEATHER_TOOLBOX_NAME
               and bool(tool.get("toolbox_version")) for tool in definition.get("tools") or []
               if isinstance(tool, dict))


async def prepare_weather_toolbox(client, cfg, headers):
    query = urlencode({"api-version": cfg.api_version})
    url = f"{cfg.project_url()}/toolboxes/{WEATHER_TOOLBOX_NAME}"
    async with client.get(f"{url}?{query}", headers=headers, allow_redirects=False) as response:
        if response.status == 200:
            toolbox = await response.json()
            versions = toolbox.get("versions") if isinstance(toolbox, dict) else None
            version = versions.get("latest") if isinstance(versions, dict) else None
        elif response.status == 404:
            async with client.get(f"{cfg.project_url()}/toolboxes?{query}&limit=1",
                                  headers=headers, allow_redirects=False) as collection:
                if collection.status != 200:
                    raise AgentPreparationError(
                        f"Cannot verify the project Toolboxes API (HTTP {collection.status}). No Agent was created.")
            payload = json.loads((ROOT / "weather-toolbox.json").read_text())
            async with client.post(f"{url}/versions?{query}", headers=headers, json=payload,
                                   allow_redirects=False) as published:
                if published.status not in {200, 201}:
                    raise AgentPreparationError(
                        f"Weather Toolbox creation failed (HTTP {published.status}). "
                        "Check Toolbox permissions and web search availability. No Agent was created.")
                version = await published.json()
        else:
            raise AgentPreparationError(
                f"Weather Toolbox lookup failed (HTTP {response.status}). "
                "Check Toolbox permissions. No Agent was created.")
    number = version.get("version") if isinstance(version, dict) else None
    tools = version.get("tools") if isinstance(version, dict) else None
    if (not isinstance(number, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", number)
            or number.lower() == "latest" or not isinstance(tools, list) or not any(
                isinstance(tool, dict) and tool.get("type") == "web_search" for tool in tools)):
        raise AgentPreparationError(
            "The weather Toolbox must have a published version containing a web_search tool. "
            "It was not overwritten; no Agent was created.")
    return {"type": "toolbox", "toolbox_name": WEATHER_TOOLBOX_NAME, "toolbox_version": number}


async def lookup_configured_agent(client, cfg, headers, *, include_version=False):
    required = set(json.loads((ROOT / "personalized-agent.json").read_text())["structured_inputs"])
    async with client.get(cfg.agent_url(), headers=headers, allow_redirects=False) as response:
        if response.status == 200:
            agent = await response.json()
            latest = (agent.get("versions") or {}).get("latest") or {}
            definition = latest.get("definition") or {}
            if definition.get("kind") != "voice":
                raise AgentPreparationError("The configured Agent exists but is not a voice Agent. It was not changed.")
            if cfg.companion_of and (
                    (latest.get("metadata") or {}).get("companion_of") != cfg.companion_of
                    or (definition.get("sub_protocol") == "live") != (cfg.companion_protocol == "live")):
                raise AgentPreparationError(
                    "The companion Agent name is already in use by another configuration. It was not changed.")
            if cfg.personalization_required and definition.get("sub_protocol") != "live":
                if not required.issubset(definition.get("structured_inputs") or {}):
                    raise AgentPreparationError(
                        "The existing Agent lacks the demo structured inputs. "
                        "Choose a new Agent name; it was not overwritten.")
            return latest if include_version else definition
        elif response.status == 404:
            # Verify the project API exists before interpreting an Agent 404 as absence.
            async with client.get(cfg.agent_url(collection=True), headers=headers, allow_redirects=False) as project:
                if project.status != 200:
                    raise AgentPreparationError(
                        f"Cannot verify the project Agents API (HTTP {project.status}). No Agent was created.")
            return None
        else:
            raise AgentPreparationError(
                f"Agent lookup failed (HTTP {response.status}). Check Foundry permissions; no Agent was created.")


def transcription_model(definition):
    settings = ((definition.get("audio") or {}).get("input") or {}).get("transcription")
    return settings.get("model") if isinstance(settings, dict) else None


async def inspect_configured_agent(client, cfg, headers):
    latest = await lookup_configured_agent(client, cfg, headers, include_version=True)
    definition = latest["definition"] if latest is not None else None
    demo = json.loads((ROOT / "personalized-agent.json").read_text())
    required = set(demo["structured_inputs"])
    return {"agent": cfg.agent, "exists": definition is not None,
            "model": (demo if definition is None else definition).get("model"),
            "modelChoice": (LIVE_MODEL if (definition or {}).get("sub_protocol") == "live" else
                            (demo if definition is None else definition).get("model")),
            "transcriptionModel": transcription_model(demo if definition is None else definition),
            "version": latest.get("version") if latest is not None else None,
            "weatherEnabled": definition is not None and weather_toolbox_attached(definition),
            "personalizationRequired": cfg.personalization_required or (
                definition is not None and required.issubset(definition.get("structured_inputs") or {}))}


async def prepare_configured_agent(client, cfg, headers, *, initial_definition=None):
    latest = await lookup_configured_agent(client, cfg, headers, include_version=True)
    definition = latest["definition"] if latest is not None else None
    version = latest.get("version") if latest is not None else None
    created = definition is None
    if created:
        if cfg.companion_protocol == "live" and initial_definition is None:
            raise AgentPreparationError("Use Create or use GPT Live Agent to prepare this missing Live Agent.")
        definition = (copy.deepcopy(initial_definition) if initial_definition is not None else
                      json.loads((ROOT / "personalized-agent.json").read_text()))
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", cfg.agent):
            raise AgentPreparationError(
                "The configured Agent name must be 1-63 alphanumeric/hyphen characters, "
                "starting and ending with an alphanumeric character.")
        if definition.get("sub_protocol") != "live":
            definition["tools"] = [await prepare_weather_toolbox(client, cfg, headers)]
        metadata = {"sample": "foundry_voice_webrtc"}
        if cfg.companion_of:
            metadata["companion_of"] = cfg.companion_of
        url = (
            f"{cfg.project_url()}/agents/{quote(cfg.agent, safe='')}/versions?"
            f"{urlencode({'api-version': cfg.api_version})}"
        )
        async with client.post(url, headers=headers, json={
            "definition": definition, "metadata": metadata
        }, allow_redirects=False) as publish:
            if publish.status not in {200, 201}:
                raise AgentPreparationError(
                    f"Agent creation failed (HTTP {publish.status}). "
                    "Check creation permissions and model/voice availability.")
            published = await publish.json()
            if not isinstance(published, dict) or not isinstance(published.get("version"), str):
                raise AgentPreparationError(
                    "The Agent was published but its version could not be verified. Retry setup without resetting.")
            version = published["version"]
    url = f"{cfg.project_url()}/agents/{quote(cfg.agent, safe='')}:enable?{urlencode({'api-version': cfg.api_version})}"
    async with client.post(url, headers=headers, allow_redirects=False) as enabled:
        if enabled.status != 204:
            raise AgentPreparationError(
                f"Agent enable failed (HTTP {enabled.status}). "
                "Retry preparation; an existing version will not be overwritten.")
    required = set(json.loads((ROOT / "personalized-agent.json").read_text())["structured_inputs"])
    return {"agent": cfg.agent, "created": created, "model": definition.get("model"),
            "modelChoice": LIVE_MODEL if definition.get("sub_protocol") == "live" else definition.get("model"),
            "transcriptionModel": transcription_model(definition),
            "version": version,
            "weatherEnabled": weather_toolbox_attached(definition),
            "personalizationRequired": (cfg.personalization_required
                                        or required.issubset(definition.get("structured_inputs") or {}))}


def model_definition(definition, model, transcription=None):
    if model not in MODELS:
        raise AgentPreparationError("Choose one of the supported demo models.")
    if transcription is not None and (not isinstance(transcription, str) or transcription not in TRANSCRIPTION_MODELS):
        raise AgentPreparationError("Choose one of the supported transcription models.")
    updated = copy.deepcopy(definition)
    updated["model_type"], updated["model"] = "managed", model
    if model == FLASH_MODEL:
        updated.pop("greeting", None)
    audio = updated.setdefault("audio", {})
    input_audio, output = audio.setdefault("input", {}), audio.setdefault("output", {})
    if model == FLASH_MODEL and isinstance(input_audio.get("turn_detection"), dict):
        input_audio["turn_detection"]["auto_truncate"] = False
    if transcription is not None:
        settings = input_audio.get("transcription")
        input_audio["transcription"] = {**(settings if isinstance(settings, dict) else {}), "model": transcription}
    if definition.get("model") == model and definition.get("model_type") == "managed":
        return updated
    # Azure owns turn detection so the requested headset denoiser remains usable.
    detection = input_audio.get("turn_detection")
    if isinstance(detection, dict) and detection.get("type") in {"server_vad", "semantic_vad"}:
        detection["type"] = "azure_semantic_vad"
        detection.setdefault("languages", ["en", "es"])
    if model == "azure-realtime":
        for field in ("voice_locale", "voice_temperature", "custom_lexicon_url", "custom_text_normalization_url",
                      "prefer_locales", "style", "pitch", "volume", "custom_voice_endpoint_id", "personal_voice_model"):
            output.pop(field, None)
        output["voice"], output["voice_type"] = "ava", "azure-realtime-native"
    elif output.get("voice_type") == "azure-realtime-native" or (
            isinstance(output.get("voice"), dict) and output["voice"].get("type") == "azure-realtime-native"):
        output["voice"], output["voice_type"] = "en-US-Tyler:DragonHDLatestNeural", "azure-standard"
    return updated


class AgentYamlDumper(yaml.SafeDumper):
    pass


AgentYamlDumper.add_representer(str, lambda dumper, value: dumper.represent_scalar(
    "tag:yaml.org,2002:str", value, style="|" if "\n" in value else None))


def redact_settings(value):
    sensitive = {"headers", "authorization", "api_key", "apikey", "client_secret", "password",
                 "access_token", "refresh_token", "token", "secret", "credential", "credentials",
                 "connection_string", "private_key"}
    if isinstance(value, dict):
        return {key: "[redacted]" if key.lower().replace("-", "_") in sensitive else redact_settings(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [redact_settings(item) for item in value]
    return value


async def agent_settings(request):
    cfg = settings_for_request(request)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=35)) as client:
            token = await request.app[TOKENS].get_token(client)
            latest = await lookup_configured_agent(client, cfg, {
                "Authorization": f"Bearer {token}", "Foundry-Features": cfg.features}, include_version=True)
        if latest is None:
            return web.json_response({"error": "Create the Agent before viewing saved settings."}, status=404)
        text = yaml.dump(redact_settings({
            "name": cfg.agent, "version": latest.get("version"), "definition": latest["definition"]
        }), Dumper=AgentYamlDumper, sort_keys=False, allow_unicode=True)
        return web.json_response({
            "yaml": text, "model": latest["definition"].get("model"),
            "transcriptionModel": transcription_model(latest["definition"]), "version": latest.get("version")
        })
    except (AuthenticationError, AgentPreparationError, aiohttp.ClientError, TimeoutError, ValueError) as error:
        LOGGER.warning("Agent settings lookup failed (%s)", type(error).__name__)
        return web.json_response(
            {"error": "Could not read saved Agent settings. Check Foundry access and retry."}, status=502)


async def change_agent_model(request):
    if not allowed_origin(request):
        return web.json_response({"error": "Open the page on this server."}, status=403)
    cfg = settings_for_request(request)
    try:
        body = await request.json()
    except ValueError:
        return web.json_response({"error": "Choose a model and the current Agent version."}, status=400)
    if (
        not isinstance(body, dict)
        or set(body) not in ({"model", "version"}, {"model", "version", "transcriptionModel"})
        or not isinstance(body["model"], str) or body["model"] not in MODELS | {LIVE_MODEL}
        or not isinstance(body["version"], str) or not body["version"]
        or ("transcriptionModel" in body and (
            not isinstance(body["transcriptionModel"], str)
            or body["transcriptionModel"] not in TRANSCRIPTION_MODELS
        ))
    ):
        return web.json_response(
            {"error": "Choose supported Agent/transcription models and the current Agent version."}, status=400)
    async with request.app[PREPARATION]:
        if request.app[ACTIVE].active:
            return web.json_response({"error": "End the conversation before changing its model."}, status=409)
        try:
            async with asyncio.timeout(90), aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90)) as client:
                token = await request.app[TOKENS].get_token(client)
                headers = {"Authorization": f"Bearer {token}", "Foundry-Features": cfg.features}
                latest = await lookup_configured_agent(client, cfg, headers, include_version=True)
                if latest is None:
                    return web.json_response({"error": "Create the Agent before changing its model."}, status=404)
                if latest.get("version") != body["version"]:
                    return web.json_response(
                        {"error": "The Agent version changed. Reload the page before saving."}, status=409)
                expected_version = latest["version"]
                metadata = copy.deepcopy(latest.get("metadata") or {})
                source = latest["definition"]
                was_live = source.get("sub_protocol") == "live"
                if was_live != (body["model"] == LIVE_MODEL):
                    return web.json_response({"error": (
                        "Foundry does not allow changing a voice Agent's sub_protocol across versions. "
                        "GPT Live requires a separate Agent created with sub_protocol=live; "
                        "configure AZURE_VOICE_AGENT_NAME to use that Agent. "
                        "The current Agent and its previous versions were not changed."
                    )}, status=409)
                if body["model"] == LIVE_MODEL:
                    if "transcriptionModel" in body:
                        raise AgentPreparationError(
                            "GPT Live owns transcription; do not select a separate transcription model.")
                    definition = copy.deepcopy(source)
                else:
                    if body["model"] == FLASH_MODEL and "greeting" in source:
                        metadata.setdefault("flash_greeting", json.dumps(source["greeting"]))
                    elif body["model"] != FLASH_MODEL and "flash_greeting" in metadata:
                        source = copy.deepcopy(source)
                        source["greeting"] = json.loads(metadata.pop("flash_greeting"))
                    definition = model_definition(source, body["model"], body.get("transcriptionModel"))
                if definition != latest["definition"]:
                    url = f"{cfg.project_url()}/agents/{quote(cfg.agent, safe='')}/versions"
                    async with client.post(url, params={"api-version": cfg.api_version}, headers=headers,
                                           json={"definition": definition, "metadata": metadata},
                                           allow_redirects=False) as response:
                        if response.status not in {200, 201}:
                            raise AgentPreparationError(
                                f"Model change failed (HTTP {response.status}). "
                                "The Agent/transcription model or audio settings may be unavailable in this project. "
                                "Previous versions were not deleted.")
                        published = await response.json()
                        if not isinstance(published, dict) or not isinstance(published.get("version"), str):
                            raise AgentPreparationError(
                                "A version may have been saved, but its number could not be verified. "
                                "Reload before retrying.")
                        expected_version = published["version"]
                url = f"{cfg.project_url()}/agents/{quote(cfg.agent, safe='')}:enable"
                async with client.post(url, params={"api-version": cfg.api_version}, headers=headers,
                                       allow_redirects=False) as response:
                    if response.status != 204:
                        raise AgentPreparationError(
                            "The model version was saved but could not be enabled. "
                            "Retry setup before starting a call.")
                result = await inspect_configured_agent(client, cfg, headers)
                if (result.get("version") != expected_version or result.get("modelChoice") != body["model"]
                        or ("transcriptionModel" in body
                            and result.get("transcriptionModel") != body["transcriptionModel"])):
                    raise AgentPreparationError(
                        "The saved settings changed or could not be verified. Reload before retrying.")
                return web.json_response(result)
        except (AuthenticationError, AgentPreparationError) as error:
            LOGGER.warning("Agent model change failed (%s)", type(error).__name__)
            return web.json_response({"error": str(error)}, status=502)
        except (aiohttp.ClientError, TimeoutError, ValueError) as error:
            LOGGER.warning("Agent model change failed (%s)", type(error).__name__)
            return web.json_response({"error": "The model change could not be verified. "
                                      "Reload before retrying; a new version may have been saved."}, status=502)


async def switch_agent(request):
    if not allowed_origin(request):
        return web.json_response({"error": "Open the page on this server."}, status=403)
    try:
        body = await request.json()
    except ValueError:
        return web.json_response({"error": "Choose a model and the current Agent version."}, status=400)
    if (not isinstance(body, dict) or set(body) != {"model", "version"}
            or not isinstance(body["model"], str) or body["model"] not in MODELS | {LIVE_MODEL}
            or not isinstance(body["version"], str) or not body["version"]):
        return web.json_response({"error": "Choose a supported model and the current Agent version."}, status=400)
    cfg, base = settings_for_request(request), request.app[SETTINGS]
    protocol = "live" if body["model"] == LIVE_MODEL else "realtime"
    async with request.app[PREPARATION]:
        if request.app[ACTIVE].active:
            return web.json_response({"error": "End the conversation before switching Agents."}, status=409)
        try:
            async with asyncio.timeout(90), aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90)) as client:
                token = await request.app[TOKENS].get_token(client)
                headers = {"Authorization": f"Bearer {token}", "Foundry-Features": cfg.features}
                current = await lookup_configured_agent(client, cfg, headers, include_version=True)
                if current is None or current.get("version") != body["version"]:
                    return web.json_response(
                        {"error": "The Agent version changed. Reload before switching."}, status=409)
                original = current if cfg.agent == base.agent else await lookup_configured_agent(
                    client, base, headers, include_version=True)
                if original is None:
                    raise AgentPreparationError("The configured original Agent is missing. It was not recreated.")
                original_live = original["definition"].get("sub_protocol") == "live"
                if original_live and protocol != "live":
                    return web.json_response({"error": (
                        "The configured Agent is Live. Configure AZURE_VOICE_AGENT_NAME to use a separate "
                        "realtime Agent. No Agent was created or changed."
                    )}, status=409)
                target = base if original_live == (protocol == "live") else companion_settings(base, protocol)
                initial = None
                if target.companion_protocol == "live":
                    if not cfg.live_deployment or not cfg.live_backend_deployment:
                        raise AgentPreparationError("Configure GPT_LIVE_DEPLOYMENT and GPT_LIVE_BACKEND_DEPLOYMENT.")
                    initial = live_definition(original["definition"], cfg.live_deployment, cfg.live_backend_deployment)
                prepared = await prepare_configured_agent(client, target, headers, initial_definition=initial)
                result = await inspect_configured_agent(client, target, headers)
                if (not result["exists"] or result["version"] != prepared["version"]
                        or (result["modelChoice"] == LIVE_MODEL) != (protocol == "live")):
                    raise AgentPreparationError(
                        "The target Agent could not be verified. Your selection was not changed.")
                return web.json_response({**result, "created": prepared["created"],
                                          "agentSelection": target.companion_protocol or "base"})
        except (AuthenticationError, AgentPreparationError) as error:
            LOGGER.warning("Agent switch failed (%s)", type(error).__name__)
            return web.json_response({"error": str(error)}, status=502)
        except (aiohttp.ClientError, TimeoutError, ValueError, OSError) as error:
            LOGGER.warning("Agent switch failed (%s)", type(error).__name__)
            return web.json_response({"error": (
                "Could not verify the target Agent. Your selection was not changed; "
                "retry to reuse any saved Agent."
            )}, status=502)


async def recreate_configured_agent(client, cfg, headers):
    if cfg.companion_of:
        await lookup_configured_agent(client, cfg, headers)
    async with client.get(cfg.agent_url(), headers=headers, allow_redirects=False) as response:
        if response.status == 200:
            agent = await response.json()
            latest = (agent.get("versions") or {}).get("latest") or {}
            definition = latest.get("definition") or {}
            if definition.get("sub_protocol") == "live":
                raise AgentPreparationError("Reset is not supported for Live Agents. No Agent was deleted.")
            demo = json.loads((ROOT / "personalized-agent.json").read_text())
            # Also recognize the earlier demo versions published before ownership metadata.
            owned = ((latest.get("metadata") or {}).get("sample") == "foundry_voice_webrtc"
                     or (all(definition.get(key) == demo[key] for key in
                             ("kind", "model_type", "structured_inputs"))
                         and definition.get("greeting") in (
                             demo["greeting"], {"type": "template", "text": "{{greeting_text}}"})
                         and definition.get("instructions") in {
                             demo["instructions"], demo["instructions"].partition("\n\nWEATHER LOOKUP")[0],
                             demo["instructions"].partition("\n\nTURN FOCUS AND CAPABILITIES")[0],
                             demo["instructions"].partition("\n\nAGENT IDENTITY")[0]}))
            if not owned:
                raise AgentPreparationError(
                    "The configured Agent is not recognized as this sample's demo Agent. It was not deleted.")
        elif response.status != 404:
            raise AgentPreparationError(f"Agent lookup failed (HTTP {response.status}). No Agent was deleted.")
    await prepare_weather_toolbox(client, cfg, headers)
    if response.status == 200:
        async with client.delete(cfg.agent_url(), headers=headers, allow_redirects=False) as deleted:
            if deleted.status not in {200, 204, 404}:
                raise AgentPreparationError(
                    f"Agent removal failed (HTTP {deleted.status}). No replacement was published.")
        for _ in range(10):
            async with client.get(cfg.agent_url(), headers=headers, allow_redirects=False) as check:
                if check.status == 404:
                    break
                if check.status != 200:
                    raise AgentPreparationError(
                        f"Removal was requested but could not be verified (HTTP {check.status}). "
                        "No replacement was published; use Retry setup & start to recover.")
            await asyncio.sleep(0.5)
        else:
            raise AgentPreparationError(
                "Removal was requested but the previous Agent is still visible. "
                "No replacement was published; wait and use Retry setup & start to recover.")
    try:
        result = await prepare_configured_agent(client, cfg, headers)
    except AgentPreparationError as error:
        raise AgentPreparationError(
            f"The previous Agent was removed or already absent, but replacement preparation failed. "
            f"Use Retry setup & start to recover without resetting again. {error}"
        ) from error
    except (aiohttp.ClientError, TimeoutError, ValueError, OSError) as error:
        raise AgentPreparationError(
            "The previous Agent was removed or already absent, but the replacement could not be verified. "
            "Check Azure connectivity and use Retry setup & start to recover without resetting again."
        ) from error
    return {**result, "recreated": result["created"]}


async def prepare_agent(request):
    return await manage_agent(request)


async def recreate_agent(request):
    return await manage_agent(request, recreate=True)


async def agent_status(request):
    return await manage_agent(request, inspect=True)


async def manage_agent(request, *, recreate=False, inspect=False):
    if not inspect and not allowed_origin(request):
        return web.json_response({"error": "Open the page on this server."}, status=403)
    cfg = settings_for_request(request)
    if not cfg.configured:
        return web.json_response(
            {"error": "Configure the Foundry project endpoint first. No existing Agent is required."}, status=503)
    if recreate:
        if not cfg.allow_agent_recreate:
            return web.json_response({"error": "Agent recreation is disabled by the server."}, status=403)
        try:
            confirmation = await request.json()
        except (ValueError, aiohttp.ContentTypeError):
            return web.json_response({"error": "Confirm the configured Agent name before removal."}, status=400)
        if confirmation != {"confirm_agent": cfg.agent}:
            return web.json_response(
                {"error": "Confirmation must exactly match the configured Agent name."}, status=400)
    async with request.app[PREPARATION]:
        if not inspect and request.app[ACTIVE].active:
            return web.json_response({"error": "End the active conversation before preparing the Agent."}, status=409)
        try:
            async with asyncio.timeout(90), aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90)) as client:
                token = await request.app[TOKENS].get_token(client)
                operation = inspect_configured_agent if inspect else (
                    recreate_configured_agent if recreate else prepare_configured_agent)
                result = await operation(
                    client, cfg, {"Authorization": f"Bearer {token}", "Foundry-Features": cfg.features})
                if inspect and not result["exists"] and cfg.companion_of:
                    result = await inspect_configured_agent(client, request.app[SETTINGS], {
                        "Authorization": f"Bearer {token}", "Foundry-Features": cfg.features})
                    return web.json_response({**result, "agentSelection": "base"})
                return web.json_response(result)
        except (AuthenticationError, AgentPreparationError) as error:
            LOGGER.warning("Agent preparation failed (%s)", type(error).__name__)
            return web.json_response({"error": str(error)}, status=502)
        except (aiohttp.ClientError, TimeoutError, ValueError, OSError) as error:
            LOGGER.warning("Agent preparation failed (%s)", type(error).__name__)
            message = (
                "Agent reset could not be verified. Removal may have occurred. "
                "Use Retry setup & start to recover without resetting again."
                if recreate else
                "Agent preparation failed. Check Azure connectivity and the server's demo definition, then retry."
            )
            return web.json_response({"error": message}, status=502)


async def asset(request):
    name = request.match_info.get("file", "index.html")
    if name not in {"index.html", "app.js", "style.css", "favicon.svg", "pcm-audio.mjs", "pcm-capture.js", "retained-history.mjs", "call-cost.mjs", "weather.mjs", "city-scenes.mjs", "city-nature.mjs"}:
        raise web.HTTPNotFound()
    return web.FileResponse(ROOT / "public" / name)


def validate_client_frame(raw, started, transport="webrtc"):
    frame = json.loads(raw)
    if not isinstance(frame, dict):
        raise ValueError("Expected an event object.")
    kind = frame.get("type")
    if transport not in TRANSPORTS:
        raise ValueError("Unsupported transport.")
    if kind == "conversation.item.retrieve":
        item_id = frame.get("item_id")
        event_id = frame.get("event_id")
        if (not isinstance(item_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", item_id)
                or not isinstance(event_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", event_id)):
            raise ValueError("Retrieval requires valid item and event IDs.")
        return {"type": kind, "item_id": item_id, "event_id": event_id}
    if transport == "webrtc" and not started:
        if kind != "rtc.call.sdp.create" or not isinstance(frame.get("sdp_offer"), str):
            raise ValueError("The first event must contain a WebRTC SDP offer.")
        if not frame["sdp_offer"].startswith("v=0"):
            raise ValueError("Invalid SDP offer.")
        return {"type": kind, "sdp_offer": frame["sdp_offer"]}
    if kind == "session.close":
        if set(frame) != {"type"}:
            raise ValueError("Session close accepts no configuration overrides.")
        return {"type": kind}
    if kind == "conversation.item.create":
        item = frame.get("item")
        if not isinstance(item, dict) or item.get("type") != "message" or item.get("role") != "user":
            raise ValueError("Text input must be a user message.")
        content = item.get("content")
        if not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict):
            raise ValueError("Text input must contain one input_text part.")
        part = content[0]
        text = part.get("text")
        if part.get("type") != "input_text" or not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise ValueError("Text input must contain 1 to 4000 characters.")
        clean_item = {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}
        if "id" in item:
            item_id = item["id"]
            if not isinstance(item_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", item_id):
                raise ValueError("Text message ID must contain 1 to 32 letters, digits, underscores or hyphens.")
            clean_item["id"] = item_id
        return {"type": kind, "item": clean_item}
    if kind == "response.create":
        # Text turns can select text-only output; other settings stay in Foundry.
        if set(frame) == {"type", "response"} and frame["response"] == {"output_modalities": ["text"]}:
            modality_key = "modalities" if transport == "webrtc" else "output_modalities"
            return {"type": kind, "response": {modality_key: ["text"]}}
        if set(frame) != {"type"}:
            raise ValueError("Only text-only output can override the saved response configuration.")
        return {"type": kind}
    if transport == "websocket":
        if kind != "input_audio_buffer.append" or not isinstance(frame.get("audio"), str):
            raise ValueError("WebSocket mode accepts PCM audio, user text and response requests.")
        try:
            audio = base64.b64decode(frame["audio"], validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("Invalid base64 audio.") from error
        if not audio or len(audio) % 2 or len(audio) > 48000:
            raise ValueError("Audio must be PCM16, at most one second per event.")
        return {"type": kind, "audio": frame["audio"]}
    # Only the bridge can change the active session configuration.
    raise ValueError("Only user text, response requests and item retrieval are accepted after the SDP offer.")


async def forward_browser(browser, upstream, transport, configuration):
    started = False
    async for message in browser:
        if message.type == aiohttp.WSMsgType.TEXT:
            frame = validate_client_frame(message.data, started, transport)
            if not configuration.ready and frame["type"] not in {"rtc.call.sdp.create", "conversation.item.retrieve"}:
                raise SessionConfigurationError(
                    "Wait for the retained-history configuration acknowledgment before sending input.")
            await upstream.send_json(frame)
            if frame["type"] == "session.close":
                try:
                    async with asyncio.timeout(20):
                        await configuration.closed.wait()
                except TimeoutError:
                    raise SessionConfigurationError("Azure did not confirm final session usage and cost before the close timeout.") from None
                return
            if frame["type"] == "rtc.call.sdp.create":
                started = True
        elif message.type == aiohttp.WSMsgType.BINARY:
            raise ValueError("Send JSON events; binary frames are not supported.")


async def forward_upstream(upstream, browser, transport, configuration, on_conversation=None):
    announced = False
    try:
        async with asyncio.timeout(30) as deadline:
            async for message in upstream:
                if message.type == aiohttp.WSMsgType.TEXT:
                    frame = json.loads(message.data)
                    if not announced and frame.get("conversation_id") and on_conversation is not None:
                        await on_conversation(frame["conversation_id"])
                        announced = True
                    acknowledged = await configuration.observe(frame, upstream)
                    # WebRTC plays RTP; WebSocket mode needs base64 PCM audio deltas.
                    if (
                        transport == "websocket"
                        or frame.get("type") not in {"response.audio.delta", "response.output_audio.delta"}
                    ):
                        await browser.send_str(message.data)
                    if acknowledged:
                        deadline.reschedule(None)
                        await browser.send_json({"type": "bridge.ready"})
                    if frame.get("type") == "session.closed":
                        configuration.closed.set()
                        return
    except TimeoutError:
        raise SessionConfigurationError(
            "Timed out waiting for Azure to acknowledge retained-history session configuration.") from None


async def relay(browser, upstream, transport, on_conversation=None):
    configuration = SessionConfiguration()
    tasks = [asyncio.create_task(forward_browser(browser, upstream, transport, configuration)),
             asyncio.create_task(forward_upstream(upstream, browser, transport, configuration, on_conversation))]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class VoiceEndpointError(Exception):
    """A sanitized result of read-only checks after a failed voice upgrade."""


async def diagnose_voice_not_found(client, cfg, headers, transport):
    """Do not guess a new route or create an agent when an upgrade returns 404."""
    fallback = ("Azure returned HTTP 404 for the voice endpoint. The cause could not be verified. "
                "Check the exact project endpoint and saved voice agent name in .env, then restart. "
                "Also confirm voice endpoint availability for this project.")
    try:
        async with asyncio.timeout(12):
            async with client.get(cfg.agent_url(), headers=headers, allow_redirects=False) as response:
                status = response.status
                if status == 200:
                    # Never relay definitions, instructions, headers or arbitrary service errors.
                    raw = await response.content.read(65537)
                    if len(raw) > 65536:
                        return fallback
                    agent = json.loads(raw)
                    latest = (agent.get("versions") or {}).get("latest") or {}
                    kind = (latest.get("definition") or {}).get("kind")
                    if kind and kind != "voice":
                        return ("The configured agent exists, but its latest definition is not a voice agent. "
                                "Set AZURE_VOICE_AGENT_NAME to a saved voice agent in this project, then restart.")
                    if kind == "voice":
                        hint = ("Try WebSocket to check whether only WebRTC is unavailable. "
                                if transport == "webrtc" else "")
                        return ("The configured voice agent exists, but Azure returned HTTP 404 for its "
                                "voice connection endpoint. " + hint +
                                "Check that it has an invokable version and that this project's region/resource "
                                "supports the voice endpoint. Test the same agent in Foundry.")
                    return ("The configured agent exists, but Azure returned HTTP 404 for its voice endpoint. "
                            "Verify a saved voice-agent version and voice endpoint availability in Foundry.")
            if status == 404:
                # A missing agent and a missing project can both produce 404.
                async with client.get(cfg.agent_url(collection=True), headers=headers,
                                      allow_redirects=False) as response:
                    if response.status == 200:
                        return ("The project's agents API is reachable, but the configured agent was not found "
                                "(HTTP 404). Open the demo and choose Create Agent & start to save a new "
                                "demo Agent before connecting. To reuse a different existing Agent, set "
                                "AZURE_VOICE_AGENT_NAME to its exact name in .env and restart.")
                    if response.status == 404:
                        return ("Both the agent lookup and the project's agents API returned HTTP 404. "
                                "Copy the project endpoint from Foundry's project overview into "
                                "AZURE_AI_PROJECT_ENDPOINT, then restart. If it already matches, ask the "
                                "resource owner to confirm this API is available on the resource.")
            if status in {401, 403}:
                return ("The voice endpoint returned HTTP 404; the follow-up agent lookup was denied "
                        f"(HTTP {status}), so agent existence could not be verified. "
                        "Confirm the agent in Foundry or ask the project owner to check it. "
                        "Invoking an agent and reading its definition can require different permissions.")
    except (aiohttp.ClientError, TimeoutError, ValueError, AttributeError, TypeError):
        pass
    return fallback


@asynccontextmanager
async def connect_voice(client, cfg, headers, transport):
    # Only diagnose a failed handshake; do not make metadata-read access a
    # prerequisite for callers who have invoke-only permissions.
    try:
        upstream = await client.ws_connect(cfg.voice_url(transport), headers=headers,
                                           heartbeat=20, max_msg_size=MAX_UPSTREAM_FRAME)
    except aiohttp.WSServerHandshakeError as error:
        if error.status != 404:
            raise
        raise VoiceEndpointError(await diagnose_voice_not_found(client, cfg, headers, transport)) from None
    async with upstream:
        yield upstream


def describe_connection_error(error):
    """Return actionable diagnostics without exposing URLs, keys or raw exceptions."""
    if isinstance(error, (AuthenticationError, VoiceEndpointError, SessionConfigurationError, LiveSessionError)):
        return str(error)
    if isinstance(error, aiohttp.WSServerHandshakeError):
        if error.status == 401:
            return (
                "Foundry rejected the Azure CLI bearer token (HTTP 401). "
                "Run az login for the tenant containing this project and select the correct subscription. "
                "Resource API keys are not supported by Agent Service."
            )
        if error.status == 403:
            return (
                "Foundry denied your signed-in account access (HTTP 403). "
                "Ask the resource owner to grant you the appropriate Foundry role on this project or agent "
                "and check network access policies."
            )
        return (
            f"Azure rejected the WebSocket upgrade (HTTP {error.status}). "
            "Check the project endpoint, agent name and selected transport support."
        )
    if isinstance(error, (aiohttp.ClientConnectorCertificateError, aiohttp.ClientConnectorSSLError)):
        return (
            "TLS certificate validation failed. Check trusted certificates and your network proxy; "
            "do not disable certificate validation."
        )
    if isinstance(error, aiohttp.ClientConnectorError):
        cause = error.os_error
        if getattr(cause, "errno", None) in {1, 13} or getattr(cause, "winerror", None) in {5, 10013}:
            return (
                "The operating system denied the server's outbound connection to Azure. "
                "Run the server outside the restricted environment or allow Python outbound HTTPS "
                "to Entra and Foundry. "
                "Authentication has not completed."
            )
        if isinstance(error, aiohttp.ClientConnectorDNSError):
            return "Azure hostname lookup failed. Check the project endpoint and DNS/network connection."
        return (
            "The server could not reach Azure over HTTPS. Check the network, proxy and firewall. "
            "No Azure authentication response was received."
        )
    if isinstance(error, TimeoutError):
        return "Connecting to Azure timed out. Check outbound HTTPS access, proxy and firewall settings."
    if isinstance(error, ValueError):
        return (
            "Invalid connection configuration or client audio event. Check the project endpoint and selected transport."
        )
    return "The voice connection failed unexpectedly. Check the server log for the error type."


async def bridge(request):
    if not allowed_origin(request):
        raise web.HTTPForbidden(text="Open the page on this local server.")
    transport = request.query.get("transport", "webrtc")
    if transport not in TRANSPORTS:
        raise web.HTTPBadRequest(text="Choose websocket or webrtc.")
    cfg = settings_for_request(request)
    if not cfg.configured:
        raise web.HTTPServiceUnavailable(text="Configure .env first.")
    # One active local call prevents accidental duplicate usage.
    if request.app[ACTIVE].active:
        raise web.HTTPTooManyRequests(text="A call is already active.")
    if request.app[PREPARATION].locked():
        raise web.HTTPConflict(text="Wait for Agent preparation to finish before connecting.")
    request.app[ACTIVE].active = True
    browser = web.WebSocketResponse(max_msg_size=MAX_FRAME, heartbeat=20)
    try:
        await browser.prepare(request)
        structured = None
        live = request.query.get("protocol") == "live"
        voice = "" if live else request.query.get("voice", "")
        if voice and voice not in HD_VOICES | MAI_FLASH_VOICES | NATIVE_VOICES:
            raise ValueError("Choose a supported demo output voice.")
        if cfg.personalization_required and request.query.get("personalize") != "true":
            raise ValueError("This Agent requires session personalization.")
        if request.query.get("personalize") == "true":
            message = await browser.receive(timeout=10)
            if message.type != aiohttp.WSMsgType.TEXT:
                raise ValueError("Expected session personalization before connecting.")
            structured = personalization_header(json.loads(message.data))
        cfg.project_url()  # Validate before acquiring credentials.
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=35)) as client:
            token = await request.app[TOKENS].get_token(client)
            headers = {"Authorization": f"Bearer {token}", "Foundry-Features": cfg.features}

            async def announce_conversation(conversation):
                await browser.send_json({
                    "type": "bridge.conversation", "conversationId": conversation,
                    "downloadToken": conversation_download_token(request.app[DOWNLOAD_SECRET], cfg.agent, conversation),
                })
            if live:
                latest = await lookup_configured_agent(client, cfg, headers, include_version=True)
                if latest is None or latest["definition"].get("sub_protocol") != "live":
                    raise LiveSessionError("Save the GPT Live model version before starting a live session.")
                await connect_live(client, browser, cfg, headers, transport, latest["version"], announce_conversation,
                                   json.loads(structured) if structured is not None else None)
                return browser
            if structured is not None:
                headers["x-ms-voice-structured-inputs"] = structured
            if voice:
                headers["x-ms-voice-session-override"] = json.dumps({
                    "audio": {"output": {
                        "voice": voice,
                        "voice_type": "azure-realtime-native" if voice in NATIVE_VOICES else "azure-standard"
                    }}
                })
            async with connect_voice(client, cfg, headers, transport) as upstream:
                await relay(browser, upstream, transport, on_conversation=announce_conversation)
    except Exception as exc:
        # Never send auth exceptions, headers or tokens to the browser or logs.
        LOGGER.warning("Voice bridge failed (%s)", type(exc).__name__)
        message = describe_connection_error(exc)
        if not browser.closed and browser.prepared:
            await browser.send_json({"type": "bridge.error", "error": {"message": message}})
    finally:
        request.app[ACTIVE].active = False
        if browser.prepared:
            await browser.close()
    return browser


SETTINGS = web.AppKey("settings", Settings)
ACTIVE = web.AppKey("active", CallState)
TOKENS = web.AppKey("tokens", AzureTokenProvider)
PREPARATION = web.AppKey("preparation", asyncio.Lock)
DOWNLOAD_SECRET = web.AppKey("download_secret", bytes)


def create_app(settings=None, token_provider=None):
    app = web.Application(middlewares=[security_headers], client_max_size=MAX_FRAME)
    app[SETTINGS] = settings or load_settings()
    app[TOKENS] = token_provider or AzureTokenProvider()
    app[PREPARATION] = asyncio.Lock()
    app[ACTIVE] = CallState()
    app[DOWNLOAD_SECRET] = secrets.token_bytes(32)
    app.router.add_get("/health", health)
    app.router.add_get("/api/config", public_config)
    app.router.add_get("/api/voice", bridge)
    app.router.add_post("/api/agent/prepare", prepare_agent)
    app.router.add_post("/api/agent/recreate", recreate_agent)
    app.router.add_get("/api/agent", agent_status)
    app.router.add_get("/api/agent/settings", agent_settings)
    app.router.add_post("/api/agent/model", change_agent_model)
    app.router.add_post("/api/agent/switch", switch_agent)
    app.router.add_post("/api/conversation/download", download_conversation)
    app.router.add_get("/", asset)
    app.router.add_get("/{file}", asset)
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app = create_app()
    web.run_app(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
