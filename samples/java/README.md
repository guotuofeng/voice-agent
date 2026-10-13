# Java live audio Voice Agent sample

This Maven module ports the asynchronous Java live-audio example from
[`microsoft-foundry/foundry-samples`](https://github.com/microsoft-foundry/foundry-samples/blob/main/samples/java/voice-agents/src/main/java/com/azure/ai/agents/voice/VoiceAgentLiveAudioConversationAsyncSample.java).
It creates a stored Voice Agent, streams 24 kHz mono PCM16 microphone audio,
plays responses, supports barge-in, and prints the persisted conversation.

## Prerequisites

- JDK 8 or later
- Apache Maven
- An Azure AI Foundry Project and permission to create Agents
- Azure CLI authentication (`az login`) or another credential supported by
  `DefaultAzureCredential`
- A microphone and speakers or headset

## Configure

Set the Foundry Project endpoint. The optional Agent name must not already be
in use because this sample creates and normally deletes that Agent.

PowerShell:

```powershell
$env:FOUNDRY_PROJECT_ENDPOINT = "https://<account>.services.ai.azure.com/api/projects/<project>"
$env:FOUNDRY_VOICE_AGENT_NAME = "sample-live-audio-conversation-agent-async-java"
```

Bash:

```bash
export FOUNDRY_PROJECT_ENDPOINT="https://<account>.services.ai.azure.com/api/projects/<project>"
export FOUNDRY_VOICE_AGENT_NAME="sample-live-audio-conversation-agent-async-java"
```

Set `FOUNDRY_KEEP_VOICE_AGENT=true` to retain an Agent created by the sample.
The default is `false`.

## Run

Run directly through Maven from the repository root:

```text
mvn -f samples/java/pom.xml compile exec:java -Dexec.mainClass=com.azure.ai.agents.voice.VoiceAgentLiveAudioConversationAsyncSample
```

Focus the terminal and press Enter to end the call. A disconnect or audio
failure also ends it and releases the audio devices.

## Build and run the JAR

Build the sample and copy its runtime dependencies:

```text
mvn -f samples/java/pom.xml clean package
```

The package phase generates the thin `target/voice-agents-1.0.0.jar` and copies
its runtime dependencies to `target/dependency`. Run it with those dependencies
and the sample's main class.

PowerShell, from the repository root:

```powershell
java -cp "samples/java/target/voice-agents-1.0.0.jar;samples/java/target/dependency/*" com.azure.ai.agents.voice.VoiceAgentLiveAudioConversationAsyncSample
```

Bash, from the repository root:

```bash
java -cp "samples/java/target/voice-agents-1.0.0.jar:samples/java/target/dependency/*" com.azure.ai.agents.voice.VoiceAgentLiveAudioConversationAsyncSample
```

The JAR does not contain its dependencies or a `Main-Class` manifest, so it
cannot be launched directly with `java -jar`.
