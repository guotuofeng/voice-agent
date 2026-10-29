# Google ADK With an Existing Foundry Voice Agent

Run the same ADK voice application against Gemini Live or an existing Azure
Foundry Voice Agent by selecting a provider environment file. This is a sample
ADK `BaseLlm`/`BaseLlmConnection` extension, not a built-in ADK provider or a
Hosted Agent bridge.

[app.py](app.py) uses one `Runner.run_live()`, `LiveRequestQueue`, `RunConfig`,
microphone/playback implementation, and event sink. Provider selection lives in
[providers.py](providers.py); Azure lifecycle and protocol conversion live in
[azure_adk.py](azure_adk.py). The Azure path does not construct a Google API
client, need a Gemini key/model, or call Google services.

## Prerequisites

- Python 3.12 and the packages in [requirements.txt](requirements.txt).
- For microphone mode, working input/output audio devices. Linux may require
  a system PortAudio package for `sounddevice`.
- For Azure, a Foundry project endpoint and an **existing voice agent** you can
  invoke using Microsoft Entra ID. Authenticate through a supported
  `DefaultAzureCredential` source, such as a signed-in Azure CLI locally or a
  managed identity when hosted. Keep credentials server-side.
- The saved Azure agent must use the realtime voice protocol (not GPT Live's
  `sub_protocol: live`), mono PCM16 at 24 kHz in both directions, input
  transcription, automatic response creation, and server VAD with interruption
  enabled. Only server-managed tools are supported.
- For Gemini, a Google API key with access to a Live audio model. Discover a
  model available to your account as described below; do not assume an old
  preview model is still available.

The adapter never creates or versions an Azure agent and never sends
`session.update`. Instructions, model, voice, VAD, and tool configuration remain
owned by the saved resource. Do not copy Azure instructions/tools into the ADK
`Agent`. The shared application uses `include_contents="none"`; conversational
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

## Provider Contract

| Area | Native ADK Gemini Live | Foundry Voice Agent adapter |
| --- | --- | --- |
| Abstraction | ADK's Gemini `BaseLlm` live connection | Sample `BaseLlm` wrapping an existing managed voice agent |
| Agent ownership | Gemini model selected locally; ADK can configure instructions/tools | Saved Azure resource owns instructions, model, voice, VAD, tools |
| Application input | Mono PCM16, `audio/pcm;rate=16000` | Same; stateful SOXR conversion to the saved 24-kHz input |
| Application output | Mono PCM16, `audio/pcm;rate=24000` | Same; SDK-decoded audio bytes normalized into ADK blobs |
| Events | Native ADK live responses | Final input/output transcripts, partial audio/text, interruption, turn completion translated into ADK responses |
| Interruption | Gemini live signaling clears local playback | Saved server VAD interrupts generation; adapter clears playback and drops late cancelled-response chunks |
| Tools | ADK client tools supported by native integration | Only Azure server-managed tools; no duplicate ADK tool loop |
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
- Server-managed MCP/Toolbox execution remains in Azure. Client function tools,
  ADK tool results, approval callbacks, manual activity markers, partial text
  turns, and `audio_stream_end` are not implemented. Do not attach ADK tools or
  request session resumption/voice overrides in `RunConfig` on the Azure path.
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
Keep artifacts private and delete the printed run directory when no longer
needed. Credentials and recordings are not checked in. Local cleanup does not
remove Azure/Google service-side data; retention follows provider and saved
agent policy. No cloud resource is created by this sample. Remove the isolated
Conda environment when finished if it is no longer needed.

## Verification

Run offline checks without either provider's credentials:

```powershell
python -m unittest discover -s . -p test_adapter.py -v
```

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
checks do not validate unsupported client tools, approval flows or resumption.
