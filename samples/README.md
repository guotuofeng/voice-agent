# Voice Agent samples

Choose a sample to learn how to create, connect, and extend a Foundry Voice
Agent. This directory includes Python, C#, Java, browser, and end-to-end
application samples, plus a design guide.

Start with the sample's linked documentation for its prerequisites, supported
regions, model choices, and run commands. The [common Python setup](#common-python-sample-setup)
below applies to the top-level Python scripts; directory-based samples may have
their own dependencies and setup.

For the general browser UI, use the local
[`portal/`](../portal/README.md). For the Finance CLI samples and the
independent shared MCP reference implementation, start with the
[Finance documentation index](../docs/README.md).

**Default Portal Templates use the public no-auth MCP.** Local MCP hosting,
Dev Tunnel, `setup-local-examples.sh`, and local E2E are optional development
paths, not prerequisites for browsing, publishing, or trying Templates.
Finance CLI examples have separate managed-MCP configuration requirements;
an existing customer-hosted MCP does not need a local tunnel.

## Choose a sample

| Sample | What it demonstrates |
| --- | --- |
| [Voice Agent Cost Lab](../tools/cost_calculator/README.md) | Standalone browser list-price calculator in `tools`: South India model prices, labeled Central India HD/Voice Live references, managed/BYOM model selection, prompt sensitivity, and CSV export. No Azure credentials required. |
| [Add voice to an existing customer agent](existing_agent_voice_design/README.md) | Design guide: use a Hosted Agent as the Foundry Voice Agent conversation engine to connect managed voice to an existing customer agent or API endpoint. Includes a high-level architecture diagram; not a runnable sample. |
| [Google ADK with an existing Voice Agent](adk_voice_agent/README.md) | One ADK live-audio application with configuration-only selection of Gemini Live or an existing Foundry Voice Agent, SDK transport, PCM resampling, transcripts, interruption handling, and offline tests. |
| [Voice Live to Voice Agent migration](voice_live_to_voice_agent/README.md) | Compare the same Azure Speech -> GPT-4.1 mini -> Azure TTS cascade and shared local functions before and after migration. Includes dynamic session context and mid-session instruction-update examples. |
| [Vehicle voice agent with local tools](vehicle_local_tools/README.md) | Web cockpit with cloud voice interaction and locally executed simulated climate, media, and navigation functions. Includes an offline dashboard, local policy enforcement, and a tool execution log. |
| [Long tool waits with spoken reminders](long_tool_wait/README.md) | Keep callers informed during slow local tools using out-of-band Voice Agent speech, with both GPT Realtime and an Azure Speech / GPT-5.4 pipeline. |
| [Language learning with pronunciation assessment](language_learning/README.md) | Have a hands-free conversation with a voice coach that asks you to read specified sentences, then gives per-sentence and word-level feedback through an Azure Speech Pronunciation Assessment function tool. Supports spoken retries and a WAV-file mode. |
| [Spoken identifiers with digit recognition](digit_recognition/README.md) | Use the server-managed `digit_recognition` tool with GPT Realtime and MAI Transcribe 2, silent tool calls, digit-by-digit readback, and caller confirmation. Includes language selection and an explicit Azure Speech alternative. |
| [Realtime STT agent](realtime_stt/README.md) | Create or reuse a voice-first agent with VAD, disabled LLM responses, MAI Transcribe 2 or Azure Speech, microphone or paced WAV input, and final usage collection with explicit completeness status. |
| [Agent-based transcript refinement, summary, and translation](audio_transcribe_and_translate/README.md) | Transcribe a WAV recording with mai-transcribe-2 and process it with a gpt-5.4 Voice Agent. Includes refinement and translation modes, guidance for adapting the instructions to summarization, and a 60-second input limit. Supports native Windows PowerShell. |
| [Knowledge, Andrew Dragon HD, and Harry Business](create_agent_with_iq_avatar_voice/README.md) | Portal creation is recommended; optional Python validation/create/readback is included. Supports optional custom photo avatar and Personal Voice workflows. |
| [Scenario-driven conversation simulation and evaluation](simulation_evaluation/README.md) | Use Foundry's cloud simulation API to generate adaptive conversations from user goals, score task completion and coherence, and preserve results. Text by default; optional cloud voice configuration requires a supported target/project. |
| [`simple_rest_lifecycle.py`](simple_rest_lifecycle.py) | Create a simple Agent with REST, or retrieve an existing Agent. |
| [`basic_voice_agent.py`](basic_voice_agent.py) | Create and version a basic Agent, or connect to an existing Agent, then converse through the microphone. |
| [Create, converse, and evaluate](conversation_evaluation/README.md) | Create one stored Voice Agent and run four three-turn sessions with real audio input/output; evaluate actual spoken transcripts, compare successful and deliberately failed tasks, and export WAVs/results with bounded polling and persistence checks. |
| [Data Zone and regional realtime Voice Agents](realtime_datazone_voice_agent.md) | Create or reuse a voice-first Agent with an explicit managed model and hands-free microphone conversation. See the sample guide for region/model combinations and routing prerequisites. |
| [Foundry GPT Live with Responses delegation](gpt_live/python/README.md) | Create an English-teacher agent or reuse `AGENT_NAME`, stream microphone audio with Responses delegation, and print the conversation ID and download command after close. |
| [SIP Voice Agent with GPT Live](sip_voice_agent/README.md) | Create a GPT Live Agent with direct model Responses delegation, create or reuse a SIP inbound Connection, bind it, inspect call records, and remove test routing. Includes digest/IP ACL setup and offline tests. |
| [Foundry GPT Live with managed prompt-agent delegation](gpt_live/python/README.md#managed-prompt-agent-delegation) | Create a prompt agent first, pin its version to a new GPT Live voice agent, then stream microphone audio with Foundry-managed delegation. |
| [Foundry GPT Live with prompt-agent tools](gpt_live/python/README.md#managed-prompt-agent-with-mcp-and-local-function-tools) | Give the managed prompt agent an MCP addition calculator and a local current date/time function. |
| [Foundry GPT Live in the browser](gpt_live/js/README.md) | Talk to the same Responses or managed prompt-agent English teacher from a browser with your microphone. The page opens the Live session directly with the preview `@azure/ai-projects` package; a small Node.js server creates the agents and gives the page an access token. The managed mode adds the MCP calculator and a local date/time function. |
| [`voice_agent_with_mcp.py`](voice_agent_with_mcp.py) | Create an MCP Agent, converse through the microphone, and display tool arguments and output. |
| [`voice_agent_with_foundry_iq.py`](voice_agent_with_foundry_iq.py) | Create a Foundry IQ Agent, converse through the microphone, and display tool activity. |
| [`voice_agent_with_local_function.py`](voice_agent_with_local_function.py) | Execute `add_numbers` in the client, return its output, and hear the response. |
| [`voice_agent_with_toolbox.py`](voice_agent_with_toolbox.py) | Create a Toolbox Agent, converse through the microphone, and display tool activity. |
| [Documentation-generated voice web sample](foundry_voice_webrtc/README.md) | Connect a browser to an existing voice Agent with generally available WebSocket or WebRTC transports; standalone Python backend, Azure CLI authentication, and Azure session storage enabled. |
| [Basic prompt subagent](subagent/voice_subagent_prompt_basic/README.md) | Create a poetry prompt subagent and a voice agent that delegates poem writing to it. |
| [Hosted GitHub Copilot subagent](subagent/voice_subagent_hosted_agent/README.md) | Create a voice agent that delegates research, learning, and coding tasks to an existing hosted GitHub Copilot agent. |
| [Finance with Handoff - Realtime](example1_finance_with_handoff_realtime/README.md) | Finance graph on the Realtime pipeline (`gpt-realtime-regional`) with a text Voice WebSocket CLI. |
| [Finance with Handoff - Cascade](example1_finance_with_handoff_cascade/README.md) | Finance graph on the Cascade pipeline (`gpt-4.1-mini`, Azure Speech) in its own directory, with its own CLI. |
| [Finance with Handoff - Realtime, Hindi](example1_finance_with_handoff_realtime_hi_in/README.md) | Independent `hi-IN` input/output version using GPT Realtime, Azure Speech, and Swara, with English business instructions. |
| [Finance with Handoff - Cascade, Hindi](example1_finance_with_handoff_cascade_hi_in/README.md) | Independent `hi-IN` input/output version using GPT-4.1 mini, Azure Speech, and Swara, with English business instructions. |
| [Finance with OTP and Officer Search](example2_finance_with_OTP_and_Officer_Search/README.md) | Publish a flat Finance Agent for OTP verification and loan-officer search, then run a text smoke test. |
| [Elevator Service with Safety, Zendesk, and Handoff](example3_elevator_service_with_safety_zendesk_and_handoff/README.md) | Publish an inbound field-service graph with deterministic safety checks, explicit Zendesk ticket confirmation, exact-ID status queries, and human-assistance routing. |
| [Front Desk with Fluency and Handoff](example4_frontdesk_with_fluency_and_handoff/README.md) | Load a response-first receptionist Template with company FAQ, grounded support search, honest transfer fallback, and callback-message persistence only after explicit confirmation. |
| [Customer Support with Handoff](example5_customer_support/README.md) | Run a support graph with staged handoff, spoken transfer messages, one-step troubleshooting, and an unsubmitted follow-up summary. |
| [Appointment Setter with Handoff](example6_appointment_setter/README.md) | Run a appointment graph that qualifies interest, collects proposed meeting times, and reads back a meeting request without booking it. |
| [Receptionist and Messaging with Handoff](example7_receptionist_messaging/README.md) | Run a front desk graph that answers company questions from sample facts, routes callers to a directory entry, and reads back a message for follow-up without sending it. |
| [Appointment Scheduler with Handoff](example8_appointment_scheduler/README.md) | Run a scheduling graph that captures new, reschedule, and cancel requests for a sample clinic and reads them back without booking or changing anything. |
| [Assessment and Quiz with Handoff](example9_assessment_quiz_agent/README.md) | Run a quiz graph that asks 10 security awareness questions one at a time and branches to a pass or needs-work debrief. |
| [Collections and Payment Recovery with Handoff](example10_collections_payment_recovery_agent/README.md) | Run a outbound collections graph that verifies the holder, explains a past-due balance, and captures a payment arrangement or hardship request for follow-up. |
| [Corporate Training Role-Play Coach with Handoff](example11_corporate_training_roleplay_coach/README.md) | Run a role-play graph that stays in character for a tough workplace conversation and then switches to a scored coaching debrief. |
| [Education and Tutoring with Handoff](example12_education_tutoring/README.md) | Run a tutoring graph that assesses the student, branches to fundamentals or advanced exploration, practices problems, and wraps up without booking a session. |
| [Healthcare Receptionist with Handoff](example13_healthcare_receptionist/README.md) | Run a medical front desk graph that captures appointment and change requests for a sample practice and routes emergencies to 911 without booking anything. |
| [Hotel Reservations with Handoff](example14_hotel_reservation_agent/README.md) | Run a hotel graph that quotes sample room rates, takes reservation and change requests for the front desk, and answers amenity questions. |
| [Inbound Lead Qualifier with Handoff](example15_inbound_lead_qualifier/README.md) | Run a BANT qualification graph that routes hot leads to a meeting request and others to a nurture follow-up without booking or creating records. |
| [Incident Response Coordinator with Handoff](example16_incident_response_coordinator/README.md) | Run a incident triage graph that classifies severity and prepares page or ticket details that someone must send manually. |
| [Insurance Advisor with Handoff](example17_insurance_advisor/README.md) | Run a insurance graph that explores coverage, explains typical ranges, captures an application for a licensed agent, and redirects claims. |
| [Internal Knowledge Assistant with Handoff](example18_internal_knowledge_assistant/README.md) | Run a internal Q&A graph that routes factual, multi-step, and unknown questions and notes documentation gaps without searching or filing anything. |
| [IT Access Request Agent with Handoff](example19_it_access_request_agent/README.md) | Run a access-request graph that classifies requests as standard, manager approval, or restricted and reads back the request without filing it. |
| [IT Help Desk with Handoff](example20_it_help_desk_internal_support/README.md) | Run a IT help desk graph that troubleshoots one step at a time and reads back an escalation without filing a ticket. |
| [Language Practice Tutor with Handoff](example21_language_practice_tutor/README.md) | Run a language practice graph that branches by level and recaps the session without booking the next one. |
| [Legal Intake Specialist with Handoff](example22_legal_intake_specialist/README.md) | Run a legal intake graph that screens emergencies and out-of-scope matters, collects case facts, and captures a consultation request without saving or booking it. |
| [Marketing Lead Capture with Handoff](example23_marketing_lead_capture_agent/README.md) | Run a lead-capture graph that routes hot leads to a sales-call request and others to a resource follow-up without booking or sending anything. |
| [New Hire Onboarding Assistant with Handoff](example24_new_hire_onboarding_assistant/README.md) | Run a onboarding graph that branches by role, covers benefits, and shares key contacts without booking a 1:1 or sending a text. |
| [Order Status and Tracking with Handoff](example25_order_status_tracking/README.md) | Run a order support graph that gives shipping timelines, captures return requests, and notes warranty claims without logging or texting anything. |
| [Outbound Sales Representative with Handoff](example26_outbound_sales_representative/README.md) | Run a outbound sales graph with a permission-based opener, objection handling, and a demo request that is not booked. |
| [Personal Voice Assistant with Handoff](example27_personal_voice_assistant/README.md) | Run a assistant graph that answers from sample facts, reads back notes, and identifies the right team without emailing or filing anything. |
| [Real Estate Lead Follow-Up with Handoff](example28_real_estate_lead_follow_up/README.md) | Run a real estate graph that qualifies a buyer, discusses sample listings, and captures showing or information requests without booking or sending anything. |
| [Restaurant Phone Order Taker with Handoff](example29_restaurant_phone_order_taker/README.md) | Run a order-taking graph that branches by pickup or delivery and reads back the order with its total without texting a summary. |
| [Salon and Spa Receptionist with Handoff](example30_salon_spa_beauty_receptionist/README.md) | Run a salon graph that answers menu questions and captures appointment requests without booking or texting. |
| [Survey and Feedback Intake with Handoff](example31_survey_feedback_intake/README.md) | Run a outbound survey graph that asks consent, runs four questions one at a time, and reads the answers back without saving them. |
| [Technical Support Agent with Handoff](example32_technical_support_agent/README.md) | Run a technical support graph that troubleshoots one step at a time and reads back an escalation without filing a ticket. |
| [Travel Agency Trip Planner with Handoff](example33_travel_agency_trip_planner/README.md) | Run a trip planning graph that recommends sample packages with ballpark pricing and captures a trip request without booking it. |
| [User Research Interviewer with Handoff](example34_user_research_interviewer/README.md) | Run a interview graph that branches on available time between a deep dive and priority questions and recaps the answers. |
| [C# voice agent sample](CSharp/README.md) | Create and manage a temporary Agent or use an existing Agent, stream audio over WebSocket, and read persisted conversations and recordings. |
| [Java live audio Voice Agent sample](java/README.md) | Create a temporary Agent and run an asynchronous microphone and speaker conversation with Enter-to-end, barge-in, persisted conversation readback, and cleanup. |
| [`download_conversation_artifacts.py`](download_conversation_artifacts.py) | Download conversation JSON, per-turn WAV files, and the merged stereo WAV. |
| [`download_conversation_traces.py`](download_conversation_traces.py) | Download correlated Application Insights rows by conversation ID. |

Examples 5–34 call mock tools with fixed, fictional data on the public no-auth
`/mcp/mock-tool-call` route by default, so they need no MCP setup and nothing
real is booked, sent, or charged. To use your own systems, see
[Connect real tools](../docs/05_connect_real_tools.md).

### Realtime sample: Project region, model, and GPU routing

The eight original Portal template cards (Examples 1–7) use 400 ms `azure_semantic_vad` silence.
Most use `gpt-realtime`; Finance Realtime uses `gpt-realtime-regional` and
Finance Cascade uses `gpt-4.1-mini`. Each card has a single version, its root
`agent.json`. Runtime configuration does not by itself establish improved
measured latency or VAD safety.

The separate regional samples below intentionally document their own model
and Project requirements; they are not additional Portal template versions.

| Foundry Project region | Model to select | GPU inference location |
| --- | --- | --- |
| East US 2 (`eastus2`) | `gpt-realtime-2.1-datazone` | Within the US data zone; not necessarily East US 2 specifically. |
| Japan East (`japaneast`) | `azure-realtime` | Japan East, with service-team-confirmed routing for the selected Project. |
| Central India (`centralindia`) | `gpt-realtime` | Standard (regional) processing in Central India, per the public model/region table; Voice Agent eligibility must be confirmed separately. |

**East US 2 Project: use the Data Zone model to keep inference on US GPUs.
Japan East Foundry Project: use Azure Realtime with confirmed Japan East GPU routing.**
The Japan East routing confirmation is Project-specific, not a guarantee for
every Japan-region Project or a change to the public Global Standard
classification. See the [full setup and commands](realtime_datazone_voice_agent.md).
For Central India, use the exact `gpt-realtime` model, not `gpt-realtime-2.1`
(the latter is listed as Global Standard there). The public table currently
does not list Agent support in Central India; confirm enablement before running
this voice-first Agent sample.

## Choose the right setup

The Voice Live migration, Finance, Elevator, language-learning, vehicle-local-tools, long-tool-wait, and documentation-generated voice web
samples use their own `requirements.txt` files. The voice web sample uses a standalone HTTP/WebSocket
backend and does not require the Projects SDK; follow its README for setup.
The common Python SDK samples require `azure-ai-projects>=2.7.0` from PyPI.

The GPT Live samples in `gpt_live/` share one `.env`. The Python terminal samples in
`gpt_live/python/` use the shared repository-root `.venv` with additional pinned packages
and a bundled preview `azure-ai-projects` 2.10.0 wheel from `dist/` for the GPT Live agent
models and Live WebSocket client. The browser sample in `gpt_live/js/` uses Node.js 22+ and
the bundled preview `@azure/ai-projects` package from `dist/` instead. Follow their READMEs
for configuration and installation.

The SIP sample in `sip_voice_agent/` is standalone and uses its own
`requirements.txt` and local `.env`. Follow its README for SIP client/PBX setup;
it does not open a local microphone or require the Projects SDK or PortAudio.

## Common Python sample prerequisites

- Python 3.10 or later.
- An Azure AI Foundry Project endpoint:
  `https://<account>.services.ai.azure.com/api/projects/<project>`.
- Azure CLI sign-in (`az login`) or another `DefaultAzureCredential` identity.
- A microphone, speakers or headset, and PortAudio for the audio samples.
- Azure AI Projects SDK 2.7.0 or later, installed by the requirements below.

## Common Python sample setup

Run these commands from the parent `VoiceAgent` directory.

PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r samples\requirements.txt
if (-not (Test-Path samples\.env)) { Copy-Item samples\.env.example samples\.env }
```

Linux, macOS, or WSL2:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r samples/requirements.txt
[ -f samples/.env ] || cp samples/.env.example samples/.env
```

The single `pip install` command installs every common Python sample
dependency, including Projects SDK 2.7.0 or later with its `[voice]`
dependencies. No Azure SDK source checkout or Voice Live SDK is required.

For an existing preview or source installation, rerun the requirements install
command above to replace it with the released SDK.

Set the Project endpoint in `samples/.env`:

```dotenv
AZURE_VOICE_AGENTS_ENDPOINT=https://<account>.services.ai.azure.com/api/projects/<project>
AZURE_VOICE_AGENTS_MODEL=gpt-realtime
```

## Run the agent samples

Run a sample without an Agent-name argument to create a new Agent. Pass an
existing Agent name to skip creation and use its stored model, audio,
instructions, and tools:

```text
python samples/<sample-name>.py
python samples/<sample-name>.py <agent-name>
```

On Linux, macOS, or WSL2, use `.venv/bin/python` if the virtual environment is
not activated. You can alternatively set `AZURE_VOICE_AGENTS_AGENT_NAME` in
`samples/.env`.

### Simple REST creation

The REST sample puts the Agent name in the URL and sends the description and
definition to the same version-creation endpoint used by the SDK:

```text
POST <project-endpoint>/agents/<agent-name>/versions?api-version=v1
Authorization: <Entra token>
Foundry-Features: VoiceAgents=V1Preview
Content-Type: application/json
```

Run:

```text
python samples/simple_rest_lifecycle.py
```

### Basic Agent: create, version, and microphone chat

The basic sample uses `VoiceAgentDefinition` and the audio/tool models from
`azure.ai.projects.models`. It creates an Agent version through
`AIProjectClient` with preview features enabled:

```python
from azure.ai.projects.aio import AIProjectClient

async with AIProjectClient(
    endpoint=endpoint,
    credential=credential,
    allow_preview=True,
) as client:
    version = await client.agents.create_version(
        agent_name=agent_name,
        definition=definition,
    )
```

To update the definition, call `client.agents.create_version` again with the
same Agent name. The returned version identifier is `version.version`.
Retrieve an existing Agent with `client.agents.get(agent_name=agent_name)`.
The same Projects client opens the Voice Agent realtime session:

```python
async with client.beta.voice_agents.realtime.connect(agent_name=agent_name) as connection:
    await connection.input_audio_buffer.append(audio=pcm_bytes)
    async for event in connection:
        # Handle transcripts, audio, and tool events.
        ...
```

The SDK owns authentication, the Agent WebSocket URL, and the
`Foundry-Features: VoiceAgents=V1Preview` header. No private URL overrides or
Voice Live SDK imports are needed. The Agent's stored definition controls the
session; the samples do not send a replacement `session.update`.

See the upstream [Voice Agent SDK samples](https://github.com/Azure/azure-sdk-for-python/tree/azure-ai-projects_2.7.0/sdk/ai/azure-ai-projects/samples/agents/voice)
for additional realtime usage.

Audio output uses a voice-name string and a separate `voice_type`, for example
`VoiceAgentAudioOutputConfig(voice="en-US-AvaNeural",
voice_type=VoiceType.AZURE_STANDARD)`.

Microphone samples use `RealtimeServerEventType` from
`azure.ai.projects.models` for event dispatch, including `SESSION_CREATED` and
`RESPONSE_OUTPUT_AUDIO_DELTA`. They follow the new Voice Agent protocol,
without legacy audio-event aliases.

```text
python samples/basic_voice_agent.py
```

### MCP with live microphone audio

The MCP tool references a Foundry Project connection that stores the target and
authentication configuration:

```json
{
  "type": "mcp",
  "server_label": "my-mcp-server",
  "project_connection_id": "<project-connection-id>",
  "require_approval": "never"
}
```

Do not put MCP credentials in the Agent definition. Create a Foundry Project
connection for the MCP server, then set:

```dotenv
AZURE_VOICE_AGENTS_MCP_CONNECTION_ID=<project-connection-id>
AZURE_VOICE_AGENTS_MCP_SERVER_LABEL=my-mcp-server
```

Run:

```text
python samples/voice_agent_with_mcp.py
```

Speak a request that the MCP tools can answer. Press Ctrl+C to finish. The
sample prints the persisted conversation ID.

### Foundry IQ with live microphone audio

A Foundry IQ knowledge base is represented as an MCP tool with its
knowledge-base URL and Project connection:

```json
{
  "type": "mcp",
  "server_label": "foundry-iq",
  "server_url": "https://<search-service>.search.windows.net/knowledgebases/<knowledge-base>/mcp?api-version=<version>",
  "project_connection_id": "<project-connection-id>",
  "require_approval": "never"
}
```

Set:

```dotenv
AZURE_VOICE_AGENTS_FOUNDRY_IQ_URL=https://<search-service>.search.windows.net/knowledgebases/<knowledge-base>/mcp?api-version=<version>
AZURE_VOICE_AGENTS_FOUNDRY_IQ_CONNECTION_ID=<project-connection-id>
```

Run:

```text
python samples/voice_agent_with_foundry_iq.py
```

Ask a question covered by the knowledge base. Use a headset to reduce echo;
talk over the Agent to test barge-in and press Ctrl+C to finish.

### Client-executed local function

A `function` tool is executed by the connected client. After the function-call
response's `response.done` event, the sample sends a typed
`RealtimeConversationItemFunctionCallOutput` and requests the model's follow-up
response. Waiting avoids a concurrent-response error.

```text
python samples/voice_agent_with_local_function.py
```

Say: **"Add 5 and 7. You must use the add_numbers function."** The sample
executes Python locally, prints `{"sum": 12.0}`, returns the result to the
session, and plays the spoken answer.

### Foundry Toolbox

A `toolbox` tool references a versioned Foundry Toolbox:

```json
{
  "type": "toolbox",
  "toolbox_name": "voice-agent-toolbox-azure-search",
  "toolbox_version": "1"
}
```

Toolbox calls are surfaced through MCP events, allowing clients to display the
tool name, arguments, and structured output. Configure the Toolbox name and
version in `samples/.env`, then run:

```text
python samples/voice_agent_with_toolbox.py
```

## Tracing and evaluation

When a conversation ends, use the URL printed in the terminal log to view the
trace in the Azure AI Foundry portal and run evaluations.

## Download conversation and audio

Microphone samples capture the top-level `conversation_id` from the
`session.created` event and print it when the session ends. Copy the printed
Agent name and conversation ID and run:

```text
python samples/download_conversation_artifacts.py <agent-name> <conversation-id>
```

The downloader waits for persistence to complete and writes:

```text
voice-agent-output/<conversation-id>/
|-- conversation.json
|-- merged.wav
`-- turns/
    |-- 001_user_<item-id>.wav
    |-- 002_agent_<item-id>.wav
    `-- ...
```

- `conversation.json` contains the conversation envelope, responses, ordered
  items, and audio metadata.
- `turns/` contains every persisted user and assistant audio segment.
- `merged.wav` is the final stereo recording: caller on the left channel and
  Agent on the right.

Set `AZURE_VOICE_AGENTS_OUTPUT_DIR` in `samples/.env` to change the output
directory. For bring-your-own storage, the JSON manifest records returned blob
URIs instead of downloading WAV data through the service.

Conversation and audio download requires the Agent to have been created with
`store=true`. All Agents created by these common samples enable it.

## Contributor resources

### Adding samples and portal templates

Use underscores between words in sample directory names, including subagent
examples.

Every example intended for the portal Templates page must follow
[Add an example to Portal Templates](./PORTAL_TEMPLATE_EXAMPLES.md). New
sample directories are not discovered automatically; they require the
documented portable `agent.json`, explicit allowlist entry, security rules,
tests, and source-sync update when applicable.

### Reusable coding-agent skills

The related workflows are under [`../skills/`](../skills/):

- [`debug-local-session`](../skills/debug-local-session/) resolves and analyzes
  recordings created by the portal.
- [`voice-agent-preview`](../skills/voice-agent-preview/) creates and tests
  Voice Agents with Projects SDK 2.7.0 and its preview voice APIs.
- `provision-foundry-iq` creates a Foundry IQ knowledge base from local files.
- `provision-foundry-toolbox` creates an Azure AI Search index and Foundry
  Toolbox.

### Validate without Azure access

With the common sample dependencies installed, run from `VoiceAgent`:

```text
python -m compileall -q samples skills
python -m unittest discover -s tests -v
```

The tests exercise the released SDK's WebSocket handshake, authentication and
preview headers, PCM serialization, event decoding, function-call ordering,
Agent versioning, and conversation/audio downloads. Only network and microphone
boundaries are mocked; the tests use the real Projects SDK. They do not create
Azure resources or open the microphone.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| `401` or `403` | Sign in again and confirm Project access. |
| `404` during create or connect | Confirm preview enablement and region support. |
| Model not found | Set a voice-capable managed model or Foundry deployment. |
| MCP or Foundry IQ call fails | Verify the connection target, credential, and service reachability. |
| Microphone sample cannot start | Install PortAudio and `pyaudio`, then confirm microphone permission. |
| Trace query returns `403` | Grant the signed-in identity permission to query the Application Insights resource or its linked Log Analytics workspace. |

Never place access tokens, API keys, or connection secrets in source files.
Use Foundry Project connections or environment-based credentials.
