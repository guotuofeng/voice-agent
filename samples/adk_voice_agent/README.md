# Google ADK With Foundry Voice Agents or Direct Voice Live

Run the same ADK voice application against Gemini Live, an existing Azure
Foundry Voice Agent, or the standalone Azure VoiceLive SDK by selecting a provider
environment file. This is a sample
ADK `BaseLlm`/`BaseLlmConnection` extension, not a built-in ADK provider or a
Hosted Agent bridge.

[app.py](app.py) uses one `Runner.run_live()`, `LiveRequestQueue`, `RunConfig`,
microphone/playback implementation, and event sink. Provider selection lives in
[providers.py](providers.py); Azure lifecycle and protocol conversion live in
[azure_adk.py](azure_adk.py). Direct Voice Live configuration and SDK adaptation
live in [voicelive_adk.py](voicelive_adk.py), which reuses the realtime event,
resampling, interruption, and function-result handling. Neither Azure provider
constructs a Google API client, needs a Gemini key/model, or calls Google services.

## Prerequisites

- Python 3.12 and the packages in [requirements.txt](requirements.txt).
- For microphone mode, working input/output audio devices. Linux may require
  a system PortAudio package for `sounddevice`.
- For the saved-agent Azure provider, a Foundry project endpoint and an **existing voice agent** you can
  invoke using Microsoft Entra ID. Authenticate through a supported
  `DefaultAzureCredential` source, such as a signed-in Azure CLI locally or a
  managed identity when hosted. Keep credentials server-side.
- The saved Azure agent must use the realtime voice protocol (not GPT Live's
  `sub_protocol: live`), mono PCM16 at 24 kHz in both directions, input
  transcription, automatic response creation, and server VAD with interruption
  enabled. For optional local function execution, declare the matching function
  on the saved agent as described below.
- For Gemini, a Google API key with access to a Live audio model. Discover a
  model available to your account as described below; do not assume an old
  preview model is still available.
- For direct Voice Live, an Azure resource endpoint, access to a supported model
  and Azure standard voice in its region, and Microsoft Entra ID invocation
  permissions. No Foundry project endpoint or saved agent is required.

The saved-agent adapter never creates or versions an Azure agent and never sends
`session.update`. Instructions, model, voice, VAD, and tool configuration remain
owned by the saved resource. Do not copy Azure instructions or server-managed
tools into the ADK `Agent`. Opt-in local functions have matching declarations
in ADK and on the saved Azure agent. The shared application uses `include_contents="none"`; conversational
state remains in the live provider session, not replayed ADK history.

## Install in an Isolated Environment

Run from the repository root, then remain in this sample directory:

```powershell
conda create -n adk python=3.12
conda activate adk
Set-Location samples/adk_voice_agent
python -m pip install -r requirements.txt
```

If the `adk` environment already exists, skip creation. No changes to another
environment, such as `voiceagent`, are necessary. Tested SDK versions are
`google-adk==2.12.0`, `google-genai==2.32.0`, and
`azure-ai-projects[voice]==2.7.0`. The Azure integration uses the supported async
Projects Voice SDK `project.beta.voice_agents.realtime.connect()` surface.
Direct Voice Live uses `azure-ai-voicelive[aiohttp]==1.3.0b1` and
`azure.ai.voicelive.aio.connect()`. These are distinct SDK paths, not aliases.

## Select a Provider

Copy the example files and edit their local values. They are git-ignored.

```powershell
Copy-Item .env.azure.example .env.azure
Copy-Item .env.gemini.example .env.gemini
```

| Azure: `.env.azure` | Gemini: `.env.gemini` |
| --- | --- |
| `VOICE_PROVIDER=azure` | `VOICE_PROVIDER=gemini` |
| `AZURE_PROJECT_ENDPOINT=https://RESOURCE.services.ai.azure.com/api/projects/PROJECT` | `GOOGLE_GENAI_USE_VERTEXAI=FALSE` |
| `AZURE_VOICE_AGENT_NAME=EXISTING-VOICE-AGENT` | `GOOGLE_API_KEY=YOUR-KEY` |
| No Gemini model or key | `GEMINI_MODEL=ACCOUNT-AVAILABLE-LIVE-MODEL` |

If the saved Azure agent declares required structured inputs, also supply their
values as one JSON object in `.env.azure`, using its actual input names:

```dotenv
AZURE_STRUCTURED_INPUTS='{"display_name":"ADK caller","client_language":"en-US"}'
```

These are session inputs, not replacement instructions. Missing required inputs
can cause HTTP 400 during the WebSocket handshake. Do not put credentials in
structured inputs: the SDK places them in the connection query string.

For Gemini, put your key in the local file, then list Live-capable models:

```powershell
python providers.py --env-file .env.gemini --list-gemini-models
```

Set `GEMINI_MODEL` to an audio-capable Live model from that account's results and
verify it with the application. Model discovery alone does not prove a live
audio conversation succeeds. Recorded validation used the account-discovered
`gemini-3.8-live` model for successful audio, memory and interruption checks;
see Verification below. Availability can change, so discover a model for your
own account rather than assuming this ID remains available.

## Run the Identical Application

```powershell
python app.py --env-file .env.azure
python app.py --env-file .env.gemini
```

For standalone Voice Live, see the next section; all application audio flags
also work with that provider.

Speak multiple turns, then speak over a long response to interrupt it. Use
headphones to avoid acoustic feedback. Ctrl+C disconnects and cleans up; use
`--seconds 60` for a bounded microphone session.

For repeatable input, supply mono, signed PCM16 WAV files at 16 kHz. Each file
is streamed in real time, followed by silence for server VAD, and the next file
normally waits for turn completion. `--no-playback` avoids audio-device access
while still recording generated output:

```powershell
python app.py --env-file .env.azure --wav question.wav --wav followup.wav --no-playback --seconds 90
python app.py --env-file .env.gemini --wav question.wav --wav followup.wav --no-playback --seconds 90
```

For a deliberate barge-in, use a short warm-up, a prompt requesting a long
response, and an interrupting utterance. `--barge-in` starts the final file after
the penultimate response begins producing audio instead of waiting for it to
complete. It fails if that response has already ended. Example utterances:
"Hello", "Count slowly from one to thirty", "Stop counting and say hello".

```powershell
python app.py --env-file .env.azure --wav warmup.wav --wav count.wav --wav stop.wav --barge-in --no-playback --seconds 90
```

The saved agent controls its responses, including greetings. WAV mode assumes
one input utterance per file; avoid files with several VAD-separated utterances
or unrelated autonomous responses. Check the transcripts, not just turn counts.

## Direct Azure Voice Live (No Saved Agent)

This provider uses the standalone
[`azure-ai-voicelive` SDK](https://github.com/Azure/azure-sdk-for-python/tree/main/sdk/voicelive/azure-ai-voicelive),
not the Projects Voice SDK and not a hosted-agent bridge:

```text
ADK Runner.run_live()
  -> AzureVoiceLive / VoiceLiveConnection
  -> azure.ai.voicelive.aio.connect(endpoint, credential, model)
  -> Voice Live model session
```

Copy [.env.voicelive.example](.env.voicelive.example) to `.env.voicelive`, which is
git-ignored. Edit its values before running:

```powershell
Copy-Item .env.voicelive.example .env.voicelive
python app.py --env-file .env.voicelive --seconds 60
```

| Variable | Meaning |
| --- | --- |
| `VOICE_PROVIDER=voicelive` | Select the standalone SDK provider |
| `AZURE_VOICELIVE_ENDPOINT` | HTTPS resource endpoint, e.g. `https://RESOURCE.services.ai.azure.com`, not a project endpoint |
| `AZURE_VOICELIVE_MODEL` | A supported model ID, e.g. `gpt-realtime`; confirm regional access |
| `AZURE_VOICELIVE_VOICE` | An Azure standard voice name supported by that model/region |
| `AZURE_VOICELIVE_INSTRUCTIONS` | Optional base instructions; defaults to `You are a helpful voice assistant.` |

Authentication uses server-side `DefaultAzureCredential`, as for the saved-agent
provider. The adapter sends one initial `session.update` with model-session
instructions, the Azure standard voice, mono PCM16 at 24 kHz, Azure Speech input
transcription, and server VAD with automatic responses, interruption, and history
auto-truncation. ADK string/text `Agent.instruction` content is appended to the
base instructions. No agent resource is read, created, or versioned.

The shared application still sends 16-kHz PCM16. The adapter resamples it to
24 kHz, then base64-encodes it for the VoiceLive SDK's input buffer API. Typed SDK
audio events contain decoded PCM bytes; the adapter maps them into the same
24-kHz ADK audio blobs used by the other providers.

To run local functions, no Azure agent setup or `--apply` is needed:

```powershell
python app.py --env-file .env.voicelive --enable-tools --seconds 60
python app.py --env-file .env.voicelive --wav question.wav --no-playback --seconds 90
```

ADK's function declarations are registered in the initial session configuration,
and ADK executes the functions locally. The provider accepts the JSON-schema
declarations generated by the pinned ADK version, including `parameters_json_schema`,
but rejects legacy `parameters` schemas, unmatched executors, and provider-specific
built-in tools. The shared tool registry needs only a callable for direct Voice
Live execution; its separate saved-agent declarations are used only by the
Projects provider's setup helper.

This is a live-audio provider, not general ADK feature parity. It shares the
existing restrictions on history replay, session resumption, automatic reconnect,
multimodal input, multi-agent transfers, and approval/long-running tool workflows.
Configure the voice through the environment rather than `RunConfig.speech_config`.
Only Azure standard voices are configured by this sample, not native OpenAI voice
names. Saved-agent MCP/Toolbox configuration is not imported into direct sessions.
Keep local functions short and read-only; barge-in cannot undo an operation.

Direct Voice Live has offline SDK-model and real ADK Runner coverage, but has
**not** been validated against a live Azure endpoint. The recorded saved-agent
and Gemini results below do not validate this new provider. Run a live smoke test
with your resource to verify audio, transcription, barge-in, tool execution, and
spoken tool results before relying on it.

## Run a Local Function Tool

Enable the same ADK `add_numbers(first_number, second_number)` function for any
provider. It runs locally, returns a JSON sum, and needs no external API or secret.
Without `--enable-tools`, the existing no-client-tools behavior is unchanged.

Shared tool implementations and registration live in
[tool_support.py](tool_support.py). To add a function, define its callable there
and add its callable/description/JSON-schema pair to `FUNCTION_TOOLS`. The app
uses `get_tools()` for every provider, and Azure setup uses the same registry
to build saved function declarations. No per-tool edits to the app, provider
selection, or protocol adapter are needed. ADK derives its schema from the
callable, so keep its signature and the registered JSON schema compatible.
After adding a tool, rerun Azure setup preview/apply before enabling it against
the saved agent. MCP execution is not added by this refactor; existing saved
Azure MCP/Toolbox tools remain service-managed.

For Azure, `--enable-tools` alone cannot register the function on the saved agent.
If startup reports `saved=[], ADK=['add_numbers']`, the Azure agent is missing
the declaration. The ADK experimental-schema warning is not the cause.

Run the setup preview using the same environment file as the application:

```powershell
python providers.py --env-file .env --configure-azure-tools
```

The preview reads the existing agent but does not modify it. After reviewing the
agent name and base version, explicitly apply the addition:

```powershell
python providers.py --env-file .env --configure-azure-tools --apply
python app.py --env-file .env --enable-tools
```

Use `.env.azure` instead of `.env` if that is your selected Azure file. Applying
creates a **new latest version of the configured Azure agent**, so other callers
of that agent may also receive local function calls. Prefer a dedicated demo
agent. The helper preserves the existing definition, including instructions,
model, voice, and server-managed tools, plus version metadata and description.
It does not overwrite a conflicting function schema or remove other client
functions, and skips version creation if the matching declaration already exists.
It requires agent-write permission; normal application invocation does not.

Alternatively, configure the saved `tools` yourself with this declaration:

```json
{
  "type": "function",
  "name": "add_numbers",
  "description": "Add two numbers using the client application.",
  "parameters": {
    "type": "object",
    "properties": {
      "first_number": {"type": "number"},
      "second_number": {"type": "number"}
    },
    "required": ["first_number", "second_number"],
    "additionalProperties": false
  }
}
```

Keep its existing MCP/Toolbox tools and audio configuration. The agent's saved
instructions must allow it to use `add_numbers`; for a reliable demo, ask it to
use that function for addition. The adapter does not modify or create the agent,
override its instructions, or register tools using `session.update`. See the
[local function sample](../voice_agent_with_local_function.py) for the SDK's
`VoiceAgentFunctionTool` declaration pattern. The sample above uses different
argument names from that standalone example; use this declaration with ADK.

```powershell
python app.py --env-file .env.azure --enable-tools --seconds 60
python app.py --env-file .env.gemini --enable-tools --seconds 60
```

Say: "Use the add_numbers tool to add twelve and thirty." Expect a
`[function_call]`, a local `[function_response]` containing `{"sum": 42}`, and a
spoken answer. The same flag works with `--wav` and `--no-playback`. For Gemini,
native ADK registers the function declaration; no Azure configuration is used.

### Tool Ownership

| Tool kind | Declaration/configuration | Execution owner |
| --- | --- | --- |
| Azure MCP/Toolbox | Existing saved Azure agent | Azure service; not copied to or executed by ADK |
| Local function, Azure | Saved Azure function declaration plus matching ADK function | ADK client process; adapter translates calls and results only |
| Local function, Gemini | Same ADK function | Native ADK live integration and client executor |

On Azure, saved function names must exactly match the locally registered ADK
functions, with compatible argument schemas. Missing or extra functions fail
at startup instead of leaving calls unanswered. Calls are buffered until Azure's
response completes, then sent to ADK as a batch. All correlated function results
are submitted before one continuation request; the intermediate function response
does not complete the user's voice turn. Unknown functions, invalid JSON arguments,
and mismatched/duplicate result IDs fail explicitly. Calls interrupted before
dispatch are discarded. Already-running local operations cannot be undone by
barge-in; keep functions short and read-only, and use asynchronous functions for
I/O so they do not block streaming audio.

MCP approval requests, ADK tool-confirmation/approval callbacks, long-running tool
workflows, and provider-specific built-in tools are outside this adapter contract.
Do not expose side-effecting tools that need approval through this sample.
Spoken filler before tool calls remains controlled by each provider and the saved
agent instructions; the adapter does not synthesize filler or promise equal latency.

## Provider Contract

| Area | Native ADK Gemini Live | Foundry Voice Agent adapter |
| --- | --- | --- |
| Abstraction | ADK's Gemini `BaseLlm` live connection | Sample `BaseLlm` wrapping an existing managed voice agent |
| Agent ownership | Gemini model selected locally; ADK can configure instructions/tools | Saved Azure resource owns instructions, model, voice, VAD, tools |
| Application input | Mono PCM16, `audio/pcm;rate=16000` | Same; stateful SOXR conversion to the saved 24-kHz input |
| Application output | Mono PCM16, `audio/pcm;rate=24000` | Same; SDK-decoded audio bytes normalized into ADK blobs |
| Events | Native ADK live responses | Final input/output transcripts, partial audio/text, interruption, turn completion translated into ADK responses |
| Interruption | Gemini live signaling clears local playback | Saved server VAD interrupts generation; adapter clears playback and drops late cancelled-response chunks |
| Tools | ADK client functions registered by native integration | Existing server-managed tools remain in Azure; matching saved functions execute through ADK with `--enable-tools` |
| Authentication | Google API key in this sample | Server-side `DefaultAzureCredential` |
| Sessions | Native Gemini live session semantics | Fresh Azure agent session per connection; ADK/Azure IDs attached to response metadata |
| Reconnect/resume | Native Gemini capabilities outside this sample | No resumption handle, history replay, or automatic reconnect |

## Lifecycle and Limits

- Sending and receiving run concurrently through ADK. Each Azure connection has
  independent resampler state, cancellation tracking, credentials/client
  ownership, and a new agent-session ID. Responses carry ADK user/session,
  Azure agent-session, server-session, and conversation IDs when available.
- Authentication, handshake, service/transcription failures, unexpected
  disconnects, failed/incomplete responses, and unsupported client execution
  flows raise errors. Cancellation closes the SDK socket, project client, and
  credential; the application closes audio devices and its local ADK session.
- Server-managed MCP/Toolbox execution remains in Azure. Local function calls and
  ADK function results are translated by the adapter; it has no separate executor.
  Approval callbacks, manual activity markers, partial text turns, and
  `audio_stream_end` are not implemented. Do not request session resumption/voice
  overrides in `RunConfig` on the Azure path.
- Barge-in clears queued playback; it cannot retract samples already played.
  The adapter does not send conversation truncation commands. Authoritative
  history truncation follows the saved agent's `auto_truncate` policy.
- A dropped connection ends the invocation. Start a fresh application session;
  reusing IDs is not a supported recovery mechanism. Closing a connection does
  not delete service-side conversations or the saved agent.
- This is a live-audio sample, not a general replacement for all ADK features.
  The Azure adapter supports `Runner.run_live()`, not non-live generation,
  multimodal video/image input, multi-agent transfers, or GPT Live transport.

## Artifacts and Cleanup

Every run prints a new artifact directory containing `events.jsonl` and a mono
24-kHz `generated.wav`. Use `--output-dir PATH` to select a new directory; existing
directories are never overwritten. The WAV contains all generated chunks,
including those later discarded from playback after interruption, so it is not
an exact recording of what a listener heard.

Transcripts, correlation IDs, and generated speech may contain sensitive data.
With tools enabled, `events.jsonl` and the console also include function names,
call IDs, arguments, and results. Do not send secrets to tools or log sensitive
tool data without adding an appropriate redaction policy.
Keep artifacts private and delete the printed run directory when no longer
needed. Credentials and recordings are not checked in. Local cleanup does not
remove Azure/Google service-side data; retention follows provider and saved
agent policy. Normal voice runs create no agent resources; the optional setup
command with `--apply` creates a saved agent version. Remove the isolated
Conda environment when finished if it is no longer needed.

## Verification

Run offline checks without any provider's credentials:

```powershell
python -m unittest discover -s . -p "test_*.py" -v
```

The direct Voice Live checks in [test_voicelive.py](test_voicelive.py) use typed
VoiceLive SDK events with a mocked transport. They cover connection/session
configuration, instructions, tool registration, PCM/base64 conversion, real ADK
Runner audio and function execution, multi-turn transcripts, interruption,
configuration errors, authentication, disconnects, timeout, and cleanup.
Network access and Google/Projects client construction are forbidden in these
tests; no provider credentials are needed.

Recorded on 2026-10-29, Windows, Python 3.12.16 in the isolated `adk` environment:

| Check | Evidence | Status |
| --- | --- | --- |
| Offline adapter/application checks | 36 tests: PCM resampling, SDK audio decoding, transcripts, completion, interruption, playback clearing, service/auth/send errors, unsupported flows, independent connections, correlation, cancellation and cleanup | Passed |
| Fault-injected cleanup and configuration | Stream stop, audio close and Runner close failures; primary send/authentication errors and cancellation preserved through transport, SDK context and client cleanup failures; application provider errors and cancellation preserved through audio, Runner and session cleanup failures, with all cleanup attempted; audio startup errors preserved during stream cleanup; acquired artifact files closed on partial initialization failure; both artifact files attempted on close failures without masking primary errors; cleanup-only failures still propagated; failed/incomplete responses after interruption; startup readiness timeout; unexpected disconnect; receive-side service error; disabled automatic responses rejected | Passed offline |
| Final playback drain | Provider/sender errors, unexpected disconnect and audio failures propagated during drain; drain timeout and cancellation propagated; normal completion preserved; drain task cleaned up | Passed offline |
| Real ADK Runner offline | Two turns through the shared `receive_events()`; separate users, sessions, conversations, audio and transcripts; outbound sockets and Google client construction forbidden | Passed |
| Azure live multi-turn memory | Synthetic 16-kHz WAV input; 166,800 output samples at 24 kHz; two completions; follow-up correctly recalled "What is 2 plus 2?" | Passed |
| Azure deliberate live interruption | Warm-up, counting prompt, stop prompt; 355,200 generated samples; replacement response "Hello! Stopping the counting now. What would you like to do next?" completed | Passed |
| Azure without Gemini | Google credentials removed; Google client constructor patched to fail during the successful real runs | Passed |
| Live Azure correlation | Distinct ADK/Azure sessions and conversation IDs recorded for separate real runs | Passed |
| Gemini live audio/model ID | Account discovery returned `gemini-3.8-live`; user-run microphone session recorded input/output transcripts, two completed turns and 897,122 bytes of generated 24-kHz PCM16 audio | Passed basic live conversation |
| Gemini live multi-turn memory | Same application with synthetic WAV input; two completed turns; correctly recalled that the first question was "What is 2 plus 2?" | Passed |
| Gemini deliberate live interruption | Warm-up, counting prompt, stop prompt; interruption event recorded; replacement response "Hello!" completed | Passed |
| Gemini bounded-run shutdown | Both WAV runs completed normally with exit code 0; original interactive run ended with `Disconnected.` and no traceback | Passed bounded runs |
| Physical speaker playback | Gemini output audio was recorded; actual speaker playback was not independently confirmed | Not verified |

Offline audio is generated in memory; temporary recordings and mocked transports
need no checked-in WAV files or local live-test artifacts. Fault-injected failures
verify cleanup attempts without contacting either provider.

The tool enhancement has offline coverage for saved-function matching alongside
server-managed tools, argument/call identity validation, parallel result batching,
interrupted calls, and a real ADK Runner executing `add_numbers` against a mocked
Azure transport. Setup tests cover preview-only behavior, configuration
preservation, idempotency, and conflicting definitions. These checks do not
contact Google or Azure. The setup preview was also verified against an existing
Azure agent without applying a change. Live tool execution
and spoken tool-result playback have not been verified for either provider; the
2026-10-29 evidence above predates this enhancement.

The initial Azure handshake returned HTTP 400 because required session inputs
were omitted. Passing them through the SDK's `structured_inputs` parameter
resolved it without changing the saved agent. Live tests invoked an existing
agent with server-managed Toolbox configuration; they did not verify an actual
tool call or approval workflow. Live audio artifacts remain local and ignored.
Offline mocks alone are not proof of provider interoperability. The user-run
Gemini session verifies basic live audio and transcripts with the same
application source and an account-discovered model ID. Subsequent bounded WAV
runs verified multi-turn memory, interruption, a completed replacement response
and normal shutdown. Physical speaker playback remains unverified. These live
checks do not validate local function tools, approval flows or resumption.
