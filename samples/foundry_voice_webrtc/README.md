# First Voice — documentation-generated web sample

This AI-assisted sample was generated starting from the Microsoft Learn
[Create a voice-based prompt agent quickstart](https://learn.microsoft.com/en-us/azure/foundry/agents/quickstarts/prompt-voice-agent?pivots=portal)
and extended with a browser UI, WebSocket audio, and WebRTC support.
Both WebSocket and WebRTC transports are generally available (GA).
This is a companion implementation, not code copied verbatim from the documentation.

The demo starts with **just a Foundry project; no existing Agent is needed**.
Before the first conversation, it creates and saves a voice Agent from the
included demo definition. Future visits and server restarts reuse that saved
Agent under the same stable name. Existing Agent
definitions are reused by default. An optional confirmed reset can delete and
recreate only the configured sample Agent. The agent retains its saved instructions, model, and
voice configuration by default. The backend sends a narrow `session.update` for
the **active session only** to enable automatic interruption truncation and
retained-item events, preserving the applied turn-detection settings, except for
Azure Realtime Flash, where automatic truncation and retained-item enrichment
are disabled. This
session update does not edit the saved Agent; the browser cannot send one.
Optional session-start personalization and output-voice selection do not edit the saved Agent.

Features include microphone and text input, spoken replies, live transcripts, mute,
interruption with authoritative retained history, model/version controls, read-only Agent YAML, downloadable conversation
logs with audio, and end-call cleanup. The frontend uses plain HTML/CSS/JavaScript
and needs no build step. A Python backend keeps Azure credentials out of the browser.

The session controls and saved model/version summary appear first, alongside the
conversation on desktop and above it on mobile. Expand **Agent & audio settings**
for model/transcription changes, connection and voice selection, audio-processing
notes, personalization, and advanced reset controls. The audio-processing notes
distinguish requested browser processing from the saved Agent's Azure denoising;
use **Show Agent settings** to inspect the actual saved definition.

### Weather-aware city scenery

Use the **City scene** selector in the floating scenery controls to choose
Seattle, Mumbai, Tokyo, Paris, Beijing, or New York. The selector remains
available in **Scenery only** view; press Escape or **Show panels** to return.
Switching scenery never starts, stops, or reconfigures a voice session.
The floating controls stay compact: a city selector, pause/view buttons, and
one short weather line. Hover the weather line for the full observation;
the selector's accessible description also retains weather and landmark details.

| City | Recognizable scene | Birds and foliage |
| --- | --- | --- |
| Seattle | Space Needle, Mount Rainier, evergreen shoreline | Gulls, evergreen trees, maple leaves |
| Mumbai | Gateway of India, palm-lined waterfront, warm streetlights | Soaring black kites, green palms and sparse windblown palm leaflets |
| Tokyo | Red-and-white Tokyo Tower, layered city skyline | Small, fast-flapping sparrows and fan-shaped ginkgo leaves |
| Paris | Eiffel Tower, mansard rooftops, Seine and stone bridge | Pigeons, broad plane-tree canopies and lobed plane leaves |
| Beijing | Temple of Heaven's three blue roofs, marble terraces and courtyard | Long-tailed black-and-white magpies, cypresses and ginkgo |
| New York | Brooklyn Bridge's twin Gothic towers and suspension cables, Manhattan skyline | Pigeons and pointed maple leaves |

These are original procedural Canvas illustrations, not photographs or live
camera feeds. Each city fetches its own current weather from Open-Meteo,
including temperature, cloud cover, precipitation, wind speed and direction,
and a timestamp in the city's timezone. Sky, haze, rain/snow, wet surfaces,
water ripples, and wind-driven trees respond to those observations. Solar
lighting uses the selected city's coordinates and the current UTC time, so
day/night and twilight do not follow the browser's timezone. Building windows,
streetlights, traffic, and boats add restrained animation; not every city has
every animation (for example, Beijing is a courtyard, not a waterfront).

Bird silhouettes and flight patterns follow the city profile: coastal gulls
are not reused over Tokyo Tower or the Temple of Heaven. Birds shelter at night,
in heavier rain, fog, snow, and in strong winds. Foliage follows the city's local calendar
month: temperate broadleaf trees turn gold/ochre/red in autumn and become bare
in winter, while Beijing's cypresses and Mumbai's palms stay green. Detached
leaves appear near canopy height, use the matching leaf shape, and respond to
wind; Mumbai has only a few green palm leaflets in stronger gusts, not an autumn
leaf storm. These are illustrative local nature choices and calendar-based
seasonal approximations, not live wildlife observations or measured leaf phenology.

Weather refreshes every 15 minutes. A city switch cancels the previous request
and clears its weather immediately, so a late response or failed lookup cannot
apply one city's conditions to another. Until a city's first successful lookup,
the canvas stays empty with a loading/unavailable message, rather than inventing
sunny weather. A failed refresh for the same city retains its last observation
and explicitly marks it stale. Weather requests time out after 10 seconds.

Only the selected city ID is saved in browser local storage. No geolocation
permission is requested. The city coordinates are sent to Open-Meteo without
cookies or referrer; this is separate from the Agent's weather Toolbox and does
not inject the selected city into the Agent's instructions. If browser storage
is unavailable, scenery still works for the current page with a console warning.
Use **Pause scenery** to freeze motion. Reduced-motion preferences are respected,
animation pauses while the tab is hidden, and the canvas resolution is capped
at twice CSS resolution to bound rendering work.

| Transport | Audio path | Requirements |
| --- | --- | --- |
| WebSocket (default) | Browser → local backend → Azure; mono PCM16 at 24 kHz | Outbound secure WebSocket/HTTPS; no ICE |
| WebRTC (GA) | Browser → Azure; backend relays signaling | ICE connectivity; STUN/TURN may be needed |

## Prerequisites

- Python 3.11 or later.
- [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) and an account with permission to invoke the agent.
- A Foundry project endpoint; an Agent name is optional. To create a missing Agent, the
  backend identity needs permission to create Agent versions and enable Agents,
  as well as access to the example managed model and voice.
- Browser microphone permission and a microphone/headset.
- A backend process allowed to reach Azure over HTTPS.

The bundled Agent already configures WebSocket audio. If reusing your own Agent,
configure it for mono **PCM16 at 24 kHz**
input/output, server-side turn detection with automatic responses, and input
transcription if user transcripts are wanted. Test the agent in Foundry first.
For WebRTC, ensure your browser and network allow ICE connectivity to Azure.

## Configure and run

Run from the repository root in PowerShell. Virtual-environment activation is not
required, avoiding PowerShell script execution-policy issues:

```powershell
cd .\samples\foundry_voice_webrtc
az login
# If necessary: az login --tenant YOUR-TENANT-ID
# If necessary: az account set --subscription YOUR-SUBSCRIPTION-ID
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
# Edit .env before starting the server.
.\.venv\Scripts\python server.py
```

On macOS/Linux, use `python3 -m venv .venv`, `.venv/bin/python` for the Python
commands, and copy `.env.example` to `.env` only if `.env` does not already exist.

Set these values in this sample's own `.env` (not the parent samples directory):

```dotenv
AZURE_AI_PROJECT_ENDPOINT=https://YOUR-RESOURCE.services.ai.azure.com/api/projects/YOUR-PROJECT
GPT_LIVE_DEPLOYMENT=gpt-live-1
GPT_LIVE_BACKEND_DEPLOYMENT=gpt-5.4
```

Leave `AZURE_VOICE_AGENT_NAME` unset or blank to use
`webrtc-personalized-hd-demo`. This stable default is used on every restart; the
Agent itself is saved in Foundry, not in browser storage. Set the variable only
to select a different name, either for a new demo Agent or your own existing
Agent. Use its exact **name**, not a model deployment name or classic agent ID.
This sample intentionally retains its own environment-variable names and
dependencies, independently of the sibling SDK samples. Restart after editing
configuration or source files.

Open [http://127.0.0.1:8080](http://127.0.0.1:8080). The demo first checks for the
saved Agent without creating or enabling anything:

1. **No Agent yet:** select **Create Agent & start**. The backend prepares the
   weather Toolbox, then creates, saves, and enables the Agent before requesting
   microphone access.
2. **Saved Agent found:** select **Start conversation** to reuse it. No new Agent
   or version is created.
3. **Next visit or restart:** the same project and stable name find the saved
   Agent automatically; there is no setup to repeat.

Choose either transport and allow the microphone when prompted. Set `PORT` to
another free port if needed. Use **End** to stop the microphone and connections.

The main conversation button checks the configured name and creates it from
`personalized-agent.json` only when the Agent is absent and the project's Agents
API is reachable. It then enables the Agent before requesting microphone access.
There is no separate setup button. The Agent panel also provides model/version
controls and read-only settings. No Agent
name, definition, or Azure credential is accepted from the browser.

The **Model** line reads the saved Agent's model, rather than assuming the demo
default. Before creation it shows `gpt-realtime-2.1 (after setup)`; a failed lookup
shows `Unavailable`. New demo Agents answer only the latest request, avoid
repeating earlier capability lists or adding routine follow-up questions, and
do not claim reminder or messaging tools that are not connected.
When asked to introduce itself, the Agent identifies itself as **Foundry Voice
Agent**, not ChatGPT, without repeating that identity in ordinary replies.
The saved setup uses managed **gpt-realtime-2.1**, **mai-transcribe-2** input transcription,
and the selected Azure Speech HD output voice. Azure semantic turn detection
keeps the headset noise-reduction processing on the service-owned audio path;
GPT Realtime's upstream-owned `server_vad` rejects that denoiser.
The Agent speaks one brief acknowledgement, such as "Let me check that for
you," before invoking web search on any topic. The saved prompt makes it follow the
session language policy and keeps the filler out of the final answer. This is
model-generated speech from the Agent, not browser speech synthesis or an
extra client response request. This explicit pre-tool prompt is used instead
of relying on service `interim_response` support for Toolbox calls.

An existing voice Agent is reused without changing its instructions, voice,
denoise settings, or versions. A non-voice Agent, or an Agent missing required
demo inputs when `PERSONALIZATION_REQUIRED=true`, fails visibly instead of being
overwritten. A lookup authorization failure is never treated as a missing Agent.
Agent preparation is serialized within the single-replica demo; do not scale this
sample without adding cross-replica creation coordination.

### Models, versions, and Agent YAML

The **Version** line shows the saved Agent's current version. End the call, choose
`gpt-realtime-2.1`, **GPT Realtime 2.1 mini** (`gpt-realtime-2.1-mini`),
`azure-realtime`, **Azure Realtime Flash**
(`azure-realtime-flash-2026-10-14`), or `gpt-5.4`, then select **Save new version**.
This publishes and enables a new version without deleting previous versions.
GPT Live uses a different protocol. Its action creates or reuses a separate Agent,
as described below, rather than publishing a version of the realtime Agent.
Instructions, tools, metadata, and other configuration are preserved except for
explicit transcription selections, turn-detection, and output-voice compatibility
changes. A stale version, active call, or publication failure is reported rather
than silently overwriting the saved Agent. These guards coordinate only this
single backend replica, not independent Foundry editors.

Selecting a model previews compatible voices immediately; **Model** and **Version**
continue to report the saved configuration until publication succeeds.
GPT Realtime 2.1 mini uses the same Azure Speech HD voice selector and independent
transcription choices as GPT Realtime 2.1.
`azure-realtime` uses native voice `ava` by default. Switching from native output
to a GPT model or Azure Realtime Flash selects `en-US-Tyler:DragonHDLatestNeural`.
Flash uses the Azure Speech HD voice selector, not the `azure-realtime` native
voice selector. Other supported HD settings are preserved. Model and voice
availability depend on the project; adding Flash to the selector does not deploy
or enable it in the Foundry project.

### Post-call API-returned cost

After a connected call ends, **Last call · API-returned cost** shows the saved
Agent API's finalized session `estimated_cost.amount` in USD, and that amount
divided by connected call minutes. The estimate uses the service's finalized
billing-meter quantities and public retail rates, not assumed tokens, speaking
time, cache hit rates, a silence discount, or the Cost Lab's planning profile.
It is not the customer's negotiated or invoiced amount.

The backend opts realtime Agent connections into
`features=include_estimated_cost:true`. **End call** mutes microphone input and
requests `session.close`, keeping the signaling connection alive for final
`session.done` / `session.closed` events. Repeated cumulative cost snapshots are
replacements, never added together or added to response-level costs. The last
result remains visible until another call connects. Average cost/minute uses the
time from connection readiness to the end request (or first terminal event),
excluding setup and the final close wait; the total itself is never prorated.

The UI preserves `complete`, `partial`, and `unavailable` semantics. A partial
amount is labeled **Priced subtotal (partial)**, with unpriced component names and
the returned price version in the details. Missing/invalid costs, an interrupted
connection without a final snapshot, and a close timeout show **Unavailable**,
not zero or a sum of observed turns. A failed connection before readiness does
not create a call result. Charges outside the service's returned estimate,
such as app hosting, networking and recording storage, are not added locally.

This contract was verified through the saved Agent's
`/agents/{name}/endpoint/protocols/voice` route with `gpt-realtime-2.1`.
GPT Live uses a separate native protocol: its `usage.seconds` is not a monetary
cost. It shows a cost only if its terminal event actually supplies
`estimated_cost`; otherwise the result is explicitly unavailable. Flash and
other models likewise rely on the returned API cost, not a local tier mapping.

### GPT Live

GPT Live uses a self-deployed `gpt-live-1` model and the separate native Live
protocol, not the realtime protocol. Configure its actual deployment name with
`GPT_LIVE_DEPLOYMENT` and its Responses backend with
`GPT_LIVE_BACKEND_DEPLOYMENT` (default `gpt-5.4`). Both deployments must exist in
the configured resource. For the `va-dev-use2` project, the verified endpoint is
`https://va-dev-use2.services.ai.azure.com/api/projects/va-dev-use2-project` and
the Live deployment is `gpt-live-1-test`.

GPT Live uses the voice in its saved Agent definition (`marin` for newly created
companions). There is no per-call voice selector: the Live endpoint rejects
the realtime `x-ms-voice-session-override` header with HTTP 400. To change the
Live voice, update the saved Agent definition instead.

Foundry does not allow changing `sub_protocol` across versions of the same Agent.
End the call, select **GPT Live**, then select **Create or use GPT Live Agent**.
This creates and enables `<configured-agent-name>-live` with `sub_protocol: live`,
or reuses the saved companion on subsequent visits. Long names get a shortened,
hashed suffix to stay within the Agent name limit. A conflicting existing name
is rejected, not overwritten. The original realtime Agent is never changed or
deleted by this action. The selection is stored per tab in `sessionStorage` and
sent explicitly with API requests and WebSocket connections. Reloading retains
that tab's selection; switching another tab does not change it or its recording
downloads. Legacy selection cookies are ignored. If browser storage is unavailable,
selection lasts until reload. The server's environment settings remain unchanged.
No Agent is created just by selecting the model or loading the page.

Select a realtime model and **Use realtime Agent** to reconnect to the original
saved realtime Agent. Its saved model and transcription are retained; after
switching, **Save new version** can change those settings normally. If the
configured original Agent is Live, configure `AZURE_VOICE_AGENT_NAME` to use a
separate realtime Agent instead. Reset is disabled for Live Agents.

The `live_definition` helper in `live.py` builds the initial Live definition with
a Responses backend and `web_search` for any topic,
including news, products, prices, documentation, travel, local information, and
weather. Explicit lookup requests and facts needing verification trigger search;
it is not limited to weather or current events. It does not attach the realtime
weather Toolbox.
The original realtime Agent's source instructions are supplied to that backend
when the companion is first created. Later switches reuse the saved Live
definition without republishing it. You can also configure
`AZURE_VOICE_AGENT_NAME` to use an existing standalone Live Agent directly.
The deployment script passes both Live deployment settings to Azure Container
Apps, and `check_websocket.py` detects Live Agents and checks native startup
and graceful close without sending microphone audio.

GPT Live uses **WebSocket audio only** in this sample. The target project's
saved-Agent Live WebRTC session-creation route returns HTTP 405, and its
realtime WebRTC signaling route rejects a Live Agent. The UI selects and locks
WebSocket for Live, while other models retain both transports. No direct-model
fallback bypasses the saved Agent or its Foundry conversation recording.
WebSocket startup waits for `session.started` and streams native
`session.input_audio.append` / `session.output_audio.delta` events through the
Agent's `/endpoint/protocols/voice/live/sessions` WebSocket endpoint.
Credentials never leave the server. **End** sends `session.close` and waits
for final cumulative usage.

GPT Live listens and speaks concurrently. Its timed transcript fragments are
shown as **captions, not authoritative retained history**; there is no realtime
truncation handshake or complete-turn guarantee. Typed messages use native Live
`response.item.create` followed by a bare `response.create` to invoke the existing
Responses delegation backend. Its `response.event` text deltas are displayed
separately from speech captions, with completion and failure status; they are not
realtime retained history. Spoken replies may also play through the active voice
session. Muting sends silence rather than stopping the audio stream, so voice
usage remains billable while typing. Independent transcription and realtime
conversation-item retrieval remain disabled for Live.
Browser echo cancellation/noise suppression still apply, but the
realtime Agent's Azure headset denoiser/VAD settings do not. Optional
personalization becomes trusted language-policy and untrusted-context append
events after `session.started`, rather than a realtime AI greeting. The bridge
waits for their acknowledgments before enabling microphone input; browsers
cannot submit arbitrary instruction events.
The [direct-model Live WebRTC guide](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/gpt-live-webrtc)
does not establish support for saved-Agent WebRTC. See the
[event reference](https://learn.microsoft.com/en-us/azure/foundry/openai/gpt-live-reference)
for Live audio and session events.

Use **Transcription model** to select input speech recognition independently of
the Agent model, then select **Save new version**. **Transcription** shows the
actual saved model and updates only after a successful save. A transcription-only
change preserves the existing prompt, AI greeting, tools, output voice, language,
and turn detection. Switching the Agent model also retains the saved
transcription choice. Controls are disabled during calls and publication.

`azure-realtime-flash-2026-10-14` disables the automatic greeting and waits for
the caller's first turn. Saving this model also removes a greeting from an
existing Flash definition. The previous greeting is retained in version metadata
and restored when switching back to another realtime model. Other models keep
their configured greetings. This does not resolve unrelated Flash inference errors.
Flash also disables automatic interruption truncation (`auto_truncate: false`)
for both WebSocket and WebRTC sessions, while keeping interruption enabled.
The retained-item include selector is removed for Flash, and the transcript
warns that interrupted text may include unheard output. Other realtime models
continue using automatic truncation and authoritative retained-item events.

New Agents default to **`mai-transcribe-2`**. Existing Agents retain their saved
choice until explicitly changed. The selector offers these exact IDs:
`azure-speech`, `azure-speech-streaming`, `azure-llm-speech`, `mai-transcribe`,
`mai-transcribe-2`, `mai-transcribe-2-streaming`, `gpt-live-transcribe`,
`gpt-realtime-whisper`, `gpt-transcribe`, `gpt-4o-transcribe`,
`gpt-4o-mini-transcribe`, `gpt-4o-transcribe-diarize`, and `whisper-1`.
Availability and compatibility depend on the project and Agent model; listing
an ID does not guarantee that every combination is supported. Unsupported
settings fail visibly without substituting another transcription model.
Publication alone does not prove runtime availability: session startup may still
reject a configuration. In the test project, `mai-transcribe-2` transcribed real
input audio with all three Agent models, while `azure-llm-speech` was rejected
at startup with `invalid_value` for `session.audio.input.transcription.model`.
The other listed IDs have allowlist/versioning coverage, not exhaustive live
compatibility verification.

**Show Agent settings** opens read-only YAML for the saved name, version, and
definition. Known credential fields are redacted. It is not a YAML editor, and
editing the bundled JSON does not automatically update an existing Agent.
Anyone with access to the demo can view these settings and publish a model
version using the backend identity; restrict access outside a controlled demo.

Creation uses the current backend identity (Azure CLI locally, managed identity
on ACA). It needs read/create permission for Toolboxes as well as Agent
read/create/enable permission. An invoke-only identity is not sufficient for this UI workflow. The
configured name is the only creation target, but anyone with access to the demo
can trigger preparation; use Entra sign-in for deployments outside a controlled demo.

### General web search through Foundry Toolbox

Web search is part of the normal Agent setup, not a browser-side function or a
weather-only API. The Toolbox retains its legacy **weather** name for
compatibility, but its prompt and tool descriptions allow searches on any topic.
Before publishing a new demo Agent, the backend checks for this Toolbox in the
same Foundry project:

- If present, it validates that the latest immutable version contains a
  `web_search` tool and pins that exact version in the Agent.
- If absent, it first verifies the Toolboxes API, then publishes
  [`weather-toolbox.json`](weather-toolbox.json) and attaches the returned version.
- Permission errors, incompatible Toolboxes, or failed creation stop setup
  visibly; an existing Toolbox is never overwritten.

The Agent searches when the caller requests a lookup or facts need verification,
then gives a concise answer grounded in the results. It asks for a location or
date only when relevant to the question, not before every search. Try **"Search
the web for the latest Azure AI announcements"** or **"Find the official Python
asyncio documentation"**. Weather searches retain location/date guidance; try
**"What is the weather in Seattle today?"**. Weather results are web-search
results, not a dedicated meteorological-feed SLA.

The Toolbox and Agent stay saved for future sessions; repeated setup does not
publish extra versions. Reset checks the weather dependency before deleting an
Agent and does not delete the shared Toolbox. If Agent publication fails after
Toolbox creation, retry setup to reuse that Toolbox.

Existing saved definitions remain unchanged when local templates are edited.
To broaden an existing weather-only Agent, publish a new Toolbox version with
the general-search descriptions, then publish an Agent version with the updated
prompt and pinned Toolbox version; no reset or deletion is required. For a Live
Agent, update both its speech instructions and Responses backend instructions.
If you maintain a separate realtime Agent, update its prompt and Toolbox version
independently. The UI advertises web search for new demo setup or when the saved Agent
has the legacy weather reference.
If publishing `personalized-agent.json` manually, provision the referenced
Toolbox first; automatic setup resolves the actual immutable version for you.

### Remove, recreate, and keep the Agent for next use

Set `ALLOW_AGENT_RECREATE=true` to enable **Reset Agent** inside the collapsed
**Advanced** section. This is disabled by default. End the current call, expand
Advanced, click the button, and type the exact configured Agent name in the
confirmation dialog. Cancelling or entering a
different name makes no change.

The server checks Origin, the server-side opt-in, the confirmation, and that the
configured Agent is recognized as this sample's Agent. It then removes that Agent
and its saved versions, verifies removal, creates a fresh Agent from the bundled
definition under the **same configured name**, and enables it. Older demo versions
are recognized by their template; newly created versions carry sample ownership
metadata. Unrelated Agents cannot be reset through this action.

The fresh Agent remains saved in Foundry. Reloading the page, restarting the
backend, or returning later reuses it automatically through the configured
project/name; there is no browser-local Agent selection or temporary Agent to
remember. This action does not save user context or microphone settings.

Removal is permanent and can affect other clients using that Agent. Replacement
is not transactional: if creation fails after removal, the UI reports the partial
failure; **Retry setup & start** recovers without deleting anything again. Clearing the
page transcript is unrelated to Agent removal, and this UI does not promise to
delete persisted conversation/audio records.

The backend identity needs Agent deletion permission in addition to creation and
enable permissions. Keep reset disabled on public/shared deployments unless that
destructive access is explicitly intended. With anonymous access, **any visitor
can reset the configured demo Agent** after confirming its name; the dialog is an
accident-prevention measure, not authentication. Use Entra sign-in for restricted
access.

For realtime models on either transport, type in **Message your agent** and press **Enter** or
**Send** once connected. Submitted text appears in the conversation and requests
a text-only reply. Microphone turns still use the saved agent's output settings.
Messages are limited to 4,000 characters;
the Send button waits while you are speaking or the agent is generating a reply.
Mute the microphone to converse by text without spoken replies to typed messages.
Session startup still requires microphone permission, even if you only plan to type.
For GPT Live on WebSocket, the same composer sends native Live user-text events
to the Responses backend and waits for backend completion before allowing another
message. Unlike realtime typed turns, this does not request text-only output or
disable spoken replies; the full-duplex audio session continues alongside text.

Spoken turns reserve a **You** bubble when speech starts, showing **Listening...**
and then **Transcribing...** until text arrives. Azure Realtime can start its
reply before input transcription finishes; updating the reserved bubble keeps
the question above its answer rather than sorting by text arrival time. Partial
user transcripts stream into the same bubble. Transcription failures or a call
ending before any transcript arrives are shown explicitly.

## Try an interruption

1. Start the server with `.\.venv\Scripts\python server.py`, open
   `http://127.0.0.1:8080`, choose WebSocket or WebRTC, and start a conversation.
   Wait for **Connected**. Both text and microphone input wait for Azure to
   acknowledge the truncation settings; WebRTC also waits for the media connection.
2. **Speak**: "Describe three places to visit, with a long explanation of each."
   Typed messages request text-only output, so use the microphone for this repro.
3. While the agent is speaking, say "Stop. Just tell me the first place."
   The same assistant card changes to **Agent - retained after interruption**.
   Its text is replaced with the service's post-truncation text, including any
   configured interruption suffix. No second copy or unheard full answer remains.
4. Wait for the response to finish. Late transcript and response-completion events
   must not restore the removed suffix. Interrupt another spoken reply very early
   to exercise an empty cut: the old text disappears and the card says no text was
   retained. Repeat with the other transport.

Streaming replies are labeled **live draft**, not confirmed retained context.
After a truncation, the full item is authoritative even when it contains no text.
If Azure omits the item, the previous text is immediately hidden while
`conversation.item.retrieve` runs. Failure or a 10-second timeout leaves an
explicit **retained history unavailable** card and an error, never the full answer.
Retrievals for the same item are serialized; a snapshot overtaken by another cut
is discarded and retrieved again. After a timeout, ambiguous late replies cannot
be used for a later retry: restart the session or wait for an enriched truncation.
**Clear** clears the page, not Azure context, and late events for cleared turns
remain hidden. Starting again creates fresh per-session history state.

This is **interrupted-output truncation**, not context-window/token truncation.
Auto truncation assumes real-time playback; local buffering, latency, or a paused
speaker can differ from what the user actually heard. The sample displays the
service's authoritative retained context rather than estimating text from
`audio_end_ms` or slicing characters locally.

Authentication uses `AzureCliCredential` and the scope
`https://ai.azure.com/.default`. Run the server as the Windows/OS user who ran
`az login`. No resource API key or application client secret is used.
See the [Foundry authentication matrix](https://learn.microsoft.com/en-us/azure/foundry/concepts/authentication-authorization-foundry).

## Session context, greeting language, and English-only mode

For this example, leave `AZURE_VOICE_AGENT_NAME` blank for the default name, or
choose a dedicated demo name. Select **Create Agent & start** if no Agent exists.
It is created using
[`personalized-agent.json`](personalized-agent.json) as its **definition**.
You can also publish that definition separately. Choose a managed model supported
by your project in the JSON if the example model is unavailable. Changing the
JSON does not update existing Agents; use a new name or explicitly publish a
version. The UI's model action changes the model, not the saved instructions.

Expand **Session personalization**, enable **Supply context before the greeting**,
and enter synthetic data such as display name `Alex` and context
`Interested in beginner hiking routes`. The browser suggests English or Spanish
from its language setting; you can override it before connecting.

**Multilingual replies are enabled by default.** Client language selects the
initial greeting preference, not a restriction on conversation languages.
The Agent is instructed to follow the language the caller speaks or requests,
including languages other than English and Spanish, and switch when requested.
The greeting uses the supplied display name when present, or no name when absent;
its wording can vary. Unclear audio should prompt clarification rather than an
assumed language.

**English-only instructions** remain an optional, unchecked setting. Enable it
only to demonstrate an English-restricted session: it overrides client language
and asks callers using other languages to continue in English. These settings
are supplied anew at session start, not saved as an Agent version.

The example declares string `structured_inputs`: `display_name`, `user_context`,
`client_language`, `language_policy`, and `greeting_text`. The backend constructs
the language policy from validated selections, rather than accepting arbitrary
browser instructions. The saved instructions reference the name, context, and
policy using Handlebars before the greeting is generated.

The saved `llm_generated` greeting uses this prompt:

> Give a brief, friendly greeting. Use the supplied display name if present; otherwise greet without a name. Follow the session language policy. Do not invent personal details.

Greeting tool calls are disabled. The backend continues supplying `greeting_text`
for compatibility with older saved Agents using a template greeting; the new
AI-generated greeting does not use that fixed text. Existing Agents keep their
saved greeting unless explicitly updated or reset.

The browser sends a bounded initialization frame over its local socket **before**
SDP or audio. The backend validates it and sends ASCII JSON in
`x-ms-voice-structured-inputs` when opening the Azure connection. This supplies
context before greeting generation, without a racing user message or
`session.update`. Context is not placed in URLs, saved in browser storage, or
logged by this app. It may be persisted in Azure because `store=true`.

Context is untrusted personalization, not authentication or authorization.
The instructions explicitly reject embedded instructions and identity claims.
Do not supply real personal or sensitive information. All settings are frozen
until the call ends, and each new call supplies its own values.

The example's Azure Speech recognition language is `en-US`; this is separate from
the greeting/response policy. For Spanish microphone recognition, configure a
matching saved Agent recognition language or supported multilingual recognition.
The English-only instruction is behavioral guidance, not a guarantee that
non-English input cannot be submitted or recognized.

The example enables server-side input denoising with
`audio.input.noise_reduction: {"type": "azure_deep_noise_suppression_headset"}`.
To try personal noise suppression instead, set its type to
`azure_personal_noise_suppression` in the saved Agent definition. These are
alternative modes, not stacked filters. The browser also requests its normal
microphone noise suppression. This sample does not set `detection_input` or
`turn_detection_only`; end-to-end support for those fields is not established.

Leave personalization off for existing Agents without these declared inputs.
Preparation detects the example's declared inputs and automatically enables
personalization for it.
Set `PERSONALIZATION_REQUIRED=true` in `.env` to enable and require it automatically
for this Agent. Keep this unset for saved Agents without structured inputs.
Missing input or unsupported template behavior should surface as a service error;
there is no silent fallback to a greeting without context.

### Output voice

The voice list displays full service IDs, with
`en-US-Tyler:DragonHDLatestNeural` selected by default. For GPT models, choose
an HD or MAI Flash voice before connecting, or choose **Saved Agent voice** to preserve the stored setting. The backend
allowlists these choices and sends an `x-ms-voice-session-override` containing
`audio.output.voice` and `voice_type: "azure-standard"` at connection time
(the HD model is selected by the voice name, not a separate `azure-hd` type).
This applies to the greeting and subsequent output without editing the Agent.

The configured HD voices are:

- `en-US-Tessa:DragonHDLatestNeural`
- `en-US-Tyler:DragonHDLatestNeural`
- `en-US-Paige:DragonHDLatestNeural`
- `en-US-Colin:DragonHDLatestNeural`
- `en-US-Brian:DragonHDLatestNeural`
- `en-US-Emma:DragonHDLatestNeural`
- `en-IN-Diya:DragonHDLatestNeural`
- `zh-CN-Xiaoqi:DragonHDFlashLatestNeural`
- `zh-CN-Xiaoke:DragonHDFlashLatestNeural`

The selector also includes these MAI Flash voices:

- `en-US-Harper:MAI-Voice-2.1-Flash`
- `en-GB-Harry:MAI-Voice-2.1-Flash`

They use the same `azure-standard` session override and do not change the saved
Agent or the default Tyler HD selection.

Voice availability must be checked for your project.
The example Agent defaults to Tyler HD. Unsupported selections fail visibly;
the sample does not substitute another voice. An English voice identifier alone
does not enforce English-only conversation.

HD and MAI Flash voice overrides require an Agent/model that accepts Azure Speech output, such
as the example managed `gpt-realtime-2.1` pipeline. An `azure-realtime` Agent accepts
only `azure-realtime-native` voices; changing the override type cannot make an
Speech voice compatible. Its separate native list offers `ava`, `andrew`,
`emma`, `clara`, `liam`, `natasha`, `rayn`, `alvaro`, `dalia`, and `jorge`.
The backend sends `voice_type: "azure-realtime-native"` for those selections.
Session voice selection never publishes an Agent version. Chinese HD voice
selection does not change the English/Spanish language controls or recognition
language; configure matching language support separately.

### Manual checks

For both transports, check English-only with Spanish preference, Spanish greeting
with English-only disabled, a context-aware follow-up, and a new call with empty
context. Try non-English text/audio and a request to switch languages in
English-only mode. Check the greeting and later replies for each available HD
voice. Offline tests verify initialization order and payload validation, not
availability of every model/voice combination in your Azure project.

## Deploy to Azure Container Apps

The included container uses Azure CLI authentication locally and automatically
switches to the Container App's system-assigned managed identity in Azure. The
local `.env` is excluded from the image and deployment build context.

From this directory, sign in and run:

```powershell
az login
.\deploy-azure.ps1 `
  -ResourceGroup foundry-voice-agent-rg `
  -Location canadacentral `
  -AppName foundry-voice-agent `
  -ServiceManagementReference YOUR-SERVICE-TREE-ID
```

The script reads `AZURE_AI_PROJECT_ENDPOINT`, optional `AZURE_VOICE_AGENT_NAME`, and the
non-secret protocol settings from the existing `.env`. It deploys the Bicep
infrastructure, cloud-builds the image in ACR, grants the managed identity
`Foundry User` on the matching Foundry resource (needed for Agent preparation), configures single-tenant
Microsoft Entra sign-in, and writes the HTTPS origin to `PUBLIC_ORIGIN` in `.env`.
The Service Tree ID is required by Microsoft tenant governance. If resource
discovery is unavailable, pass `-FoundryResourceId` with the Foundry resource's
full Azure resource ID.

The tenant policy used by this sample forbids application client secrets, so the
script configures secretless ID-token sign-in and leaves the token store disabled.
Unauthenticated browser requests redirect to Microsoft Entra; `/health` remains
public for probes. Restrict the registration to the intended tenant/users or
groups. Every accepted caller can consume Foundry quota through the app identity.
Container Apps terminates HTTPS and supports the browser WebSocket used here.

The app is intentionally limited to one active call per replica, and the deployment
sets a maximum of one replica. This is suitable for a controlled demo, not a
multi-user production service.

Windows users may optionally run `install-azure-cli.ps1` from an Administrator
PowerShell session if organizational policy permits scripts. It downloads the
official Microsoft x64 MSI, validates its Microsoft signature, and installs it
without rebooting. If scripts are blocked, follow the official CLI installation
guide instead; changing system execution policy is not required by this sample.
Open a new normal terminal after installation and run `az login`.

## Storage and privacy

**`store=true` is enabled for both transports.** Sessions request Azure-side
conversation/audio persistence where supported. The diagnostic check below uses
the same setting and can create a saved session. Azure retention/access policies
apply; clearing browser transcripts does not delete Azure-stored data.

The page prominently warns **Audio is saved by default** before starting.
After **End**, select **Download log + audio** to export a ZIP containing
`conversation.json` (conversation metadata and all item pages) and `audio.wav`
(the merged stereo caller/Agent recording). This uses Azure-persisted audio for
both transports, not a separate browser microphone recording. Azure may need
time to finalize a conversation; download failures are shown explicitly and
can be retried.

The backend issues a signed download capability only for a conversation observed
on that client's voice connection. Treat it as sensitive: anyone holding it can
download that conversation. It expires after 24 hours or a backend restart.
Only the latest call reference is retained in browser memory; reloading the page
loses it. Exports are limited to 16 MiB of log data and 128 MiB of audio. The ZIP
uses a temporary spooled file (larger archives may spill to temporary disk),
which is closed after delivery or failure; this is not a retained local archive.

Transcripts remain on the page until cleared or refreshed. Clearing transcripts
or deleting an Agent does not promise deletion of Azure recordings. Exported
logs and recordings can contain sensitive context, tool results, and speech.
Credentials remain server-side and are not printed.
Do not commit `.env`, access tokens, recordings, or logs. Use only
short-lived, limited TURN credentials: ICE configuration is sent to the browser.

## Check the Azure connection

```powershell
.\.venv\Scripts\python check_websocket.py
```

This opens a short-lived WebSocket session using Azure CLI authentication, applies
the same active-session truncation update as the app, waits for its acknowledgment,
and closes. It sends no microphone audio or user message.
It prints sanitized status information, not tokens or session contents. A saved
agent may still generate a greeting. Success establishes session readiness, not
an end-to-end spoken conversation.

After an HTTP 404, the backend and checker perform read-only lookups to distinguish
a missing agent, an unavailable project API, a non-voice agent, and an existing
voice agent whose connection route is unavailable. These checks do not provision
resources or switch routes automatically. They run only after a failed upgrade,
so successful callers do not need additional metadata-read permission.

## Transport implementation

Both modes connect through the local backend to:

```text
wss://RESOURCE.services.ai.azure.com/api/projects/PROJECT/agents/AGENT/endpoint/protocols/voice?api-version=v1&agent_session_id=...&store=true
```

The backend sends an Entra bearer token and `Foundry-Features: VoiceAgents=V1Preview`.
The header value is retained for protocol compatibility; it does not describe
the availability status of the WebRTC transport.

- **WebSocket:** an AudioWorklet captures 100-ms PCM16 chunks. The browser sends
  `input_audio_buffer.append` events after session readiness and plays returned
  audio deltas. Muting supplies silence so server-side turn detection can finish.
- **WebRTC:** adds `transport=webrtc`. The backend relays `rtc.call.sdp.create`
  with `sdp_offer` and receives `rtc.call.sdp.created` with `sdp_answer`. Audio
  travels over WebRTC; PCM deltas are filtered to avoid duplicate playback.
  The browser opens the signaling socket while gathering ICE candidates, so
  backend authentication and Azure connection setup overlap with ICE gathering.
  The complete SDP offer is sent only after ICE gathering and socket opening.
- **Text (both transports):** the browser sends a user `conversation.item.create`
  with one `input_text` content part, followed by `response.create`, through the
  same backend socket, selecting `output_modalities: ["text"]` for typed turns.
  The bridge translates this to `modalities: ["text"]` for the WebRTC protocol.
  The backend validates text and allows only this text-only response override,
  not other browser-controlled response or session configuration changes. Read-only
  `conversation.item.retrieve` requests are also validated and relayed.
  WebRTC uses its socket for events
  and WebRTC for audio, not the data channel for text.

### Active-session truncation configuration

The first `session.updated` supplies the applied saved-agent settings. The bridge
preserves the reported VAD type/options and the **whole existing include list**,
adds `conversation.item.truncated.item` without duplicates, and sets
`auto_truncate: true` and its prerequisite `interrupt_response: true`.
It does not change instructions, model, voice, tools, formats, or saved agents.
For `azure-realtime-flash-2026-10-14`, the same handshake instead sets
`auto_truncate: false`, keeps `interrupt_response: true`, and removes only
`conversation.item.truncated.item` from the include list. Input remains disabled
until Azure acknowledges truncation is off and the selector is absent.
The runtime model ID `azure-realtime-flash-1` receives the same Flash policy.
A disabled/missing VAD or disabled automatic responses is reported as a setup
error rather than silently replacing the saved turn-detection mode.

For the [stable realtime session schema](https://learn.microsoft.com/javascript/api/@azure/ai-projects/voiceagentsessionupdateconfig),
the update has this shape (the shown VAD type is illustrative; the actual saved
type and its other options are preserved):

```json
{
  "type": "session.update",
  "session": {
    "type": "realtime",
    "include": ["conversation.item.truncated.item"],
    "audio": {
      "input": {
        "turn_detection": {
          "type": "azure_semantic_vad",
          "auto_truncate": true,
          "interrupt_response": true
        }
      }
    }
  }
}
```

For the older [Voice Live session shape](https://learn.microsoft.com/azure/ai-services/speech-service/how-to-voice-live-auto-truncation)
used by WebRTC, `turn_detection` is directly under `session`, without the stable
`type`/`audio` wrapper. The bridge follows the shape Azure actually reports rather
than inferring it solely from the transport or API version.
`session.include` is top-level in both shapes; sending it replaces the whole list.
The retained-item selector is opt-in and supported by preview, dated GA, and v1
APIs. Startup fails explicitly if the endpoint does not acknowledge both flags
and all requested selectors within 30 seconds.

`conversation.item.truncated.item`, when present, is the full **post-truncation**
item (equivalent to retrieval, with base64 audio omitted), not just the affected
content part. Text can be in `content[].text` or `content[].transcript`.
The UI replaces the matching `item_id` in place and locks it against subsequent
full-answer events, including modern/legacy transcript events, output items, and
`response.done`. A later, shorter truncation can update it again. Multi-item
responses keep separate item histories. The same handling also applies to
client-issued truncation events, although this UI does not issue manual cuts.

This sample uses the Foundry voice-agent WebRTC signaling protocol described
above, not the separate Azure OpenAI `/openai/v1/realtime/calls` flow.

## Troubleshooting

The call panel shows the Azure **Session ID** when the service reports it and
keeps it visible after a failed response, connection failure, or disconnect.
Copy it when reporting a problem. If startup fails before Azure returns an ID,
the panel explicitly reports that the Session ID is unavailable. Starting a new
call clears the previous call's ID; session IDs are not saved in browser storage.

| Symptom | Action |
| --- | --- |
| Setup needed | Fill in this directory's `.env` and restart. |
| CLI token failure / 401 | Run `az login` for the correct tenant as the same user running the backend. |
| 403 | Check agent/project permissions and network policies. |
| 404 | Use the lookup diagnostic; verify project endpoint, saved agent name, invokable version, and voice endpoint availability. |
| OS denied outbound connection | Run in a permitted environment or ask your administrator to allow Python HTTPS access. |
| WebSocket audio-format error | Set input/output to mono PCM16 at 24 kHz in the saved agent. |
| ICE gathering timeout | Check STUN/TURN/firewall settings, or use WebSocket. `ICE_SERVERS_JSON=[]` disables external ICE servers but may limit connectivity. |
| Slow WebRTC startup | Check the ICE servers for invalid hostnames, placeholder values, or blocked STUN/TURN ports. Failed lookups can add seconds before signaling. The deployment script forwards `ICE_SERVERS_JSON` from `.env`; use valid servers, or `[]` only if your network supports direct connectivity. |
| Slow first request after inactivity | ACA uses `minReplicas=0` and may cold-start. Subsequent warm requests are faster. Keeping one replica warm avoids scale-to-zero cold starts but adds ongoing compute cost. |
| Silent output | Check the output device and enable speaker audio if autoplay is blocked. |
| No user transcript / automatic reply | Enable transcription / server-side turn detection in Foundry. |
| Truncation settings not acknowledged | Check this agent endpoint/API version supports the reported session shape, `auto_truncate`, `interrupt_response`, and the include selector. The sample does not enable input without acknowledgment. |
| Retained history unavailable | The full answer is hidden. Check the surfaced retrieval error; restart after a timeout. |

## Validation and limitations

Run offline validation from this directory (Node.js is needed only for JavaScript checks):

```powershell
.\.venv\Scripts\python -m unittest discover -v
.\.venv\Scripts\python -m compileall -q server.py auth.py check_websocket.py
node --check public\app.js
node --check public\pcm-audio.mjs
node --check public\pcm-capture.js
node --check public\retained-history.mjs
node --check public\weather.mjs
node --check public\city-scenes.mjs
node --check public\city-nature.mjs
node --test test_text_input.mjs test_retained_history.mjs test_weather.mjs test_city_scenes.mjs test_city_nature.mjs
```

Offline regressions cover session update/acknowledgment and preserved settings,
both transports' input readiness, text input, per-item retained history, empty and
repeated cuts, delayed full-answer events, retrieval/error/timeout races, and
clear/reconnect behavior. They also check `store=true` and transport selection.
Scenery checks cover per-city weather URLs and solar lighting, stale-request
isolation, failed refreshes, native browser fetch binding, and deterministic
landmark geometry at mobile and desktop sizes.
Nature regressions cover city-specific bird and leaf silhouettes, local-month
season changes, tropical/evergreen exceptions, and weather-dependent visibility.
For visual checks, try all cities
in normal and scenery-only views, pause/play, reduced motion, and a failed weather
lookup; use controlled weather fixtures for rain, snow, fog, and night scenarios.
Synthetic/offline UI verification is not an Azure audio test. An end-to-end Azure
spoken interruption is **not verified by these tests**; run the connection check
and the manual microphone repro in your own project.

This is a **local-development sample**, not a production service. It binds to
loopback, checks browser Origin/Host, limits frames, and allows one active call.
Production hosting needs authentication, authorization, HTTPS, quotas, and a
security review. Client-executed function tools and approval flows are not
implemented; start with a simple conversational agent.
