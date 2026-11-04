"""Provider selection is confined to this integration/configuration module."""

import argparse
import asyncio
import json
import os

from google.adk.models.base_llm import BaseLlm

from tool_support import get_function_declarations, with_demo_tool


async def configure_azure_tools(*, apply: bool = False) -> None:
    from azure.ai.projects import models
    from azure.ai.projects.aio import AIProjectClient
    from azure.identity.aio import DefaultAzureCredential

    if required("VOICE_PROVIDER").lower() != "azure":
        raise ValueError("Select an Azure environment file for --configure-azure-tools")
    agent_name = required("AZURE_VOICE_AGENT_NAME")
    async with DefaultAzureCredential() as credential, AIProjectClient(
        endpoint=required("AZURE_PROJECT_ENDPOINT"), credential=credential, allow_preview=True,
    ) as project:
        latest = (await project.agents.get(agent_name)).versions.latest
        original = latest.definition.as_dict()
        updated = with_demo_tool(original)
        names = ", ".join(tool["name"] for tool in get_function_declarations())
        if original == updated:
            print(f"Agent {agent_name} version {latest.version} already declares {names}.")
            return
        print(f"Agent: {agent_name}; base version: {latest.version}")
        print(f"Configure functions: {names}")
        print("Preserve existing instructions, model, voice, audio, and server-managed tools.")
        if not apply:
            print("Preview only. Rerun with --apply to create a new latest agent version.")
            return
        current = (await project.agents.get(agent_name)).versions.latest
        if current.version != latest.version:
            raise RuntimeError("The latest agent version changed; rerun setup to review it")
        created = await project.agents.create_version(
            agent_name, definition=models.VoiceAgentDefinition(updated),
            metadata=latest.metadata, description=latest.description,
        )
        print(f"Created agent {agent_name} version {created.version}. Run app.py with --enable-tools.")


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"Set {name} in the selected provider environment file")
    return value


def create_model(*, session_id: str, user_id: str) -> BaseLlm:
    provider = required("VOICE_PROVIDER").lower()
    if provider == "azure":
        from azure_adk import AzureVoiceAgent

        return AzureVoiceAgent(
            project_endpoint=required("AZURE_PROJECT_ENDPOINT"),
            agent_name=required("AZURE_VOICE_AGENT_NAME"),
            adk_session_id=session_id,
            adk_user_id=user_id,
            structured_inputs=json.loads(os.environ["AZURE_STRUCTURED_INPUTS"])
            if os.environ.get("AZURE_STRUCTURED_INPUTS") else None,
        )
    if provider == "gemini":
        from google.adk.models.google_llm import Gemini

        return Gemini(model=required("GEMINI_MODEL"))
    if provider == "voicelive":
        from voicelive_adk import AzureVoiceLive

        return AzureVoiceLive(
            endpoint=required("AZURE_VOICELIVE_ENDPOINT"),
            model=required("AZURE_VOICELIVE_MODEL"),
            voice=required("AZURE_VOICELIVE_VOICE"),
            instructions=os.environ.get(
                "AZURE_VOICELIVE_INSTRUCTIONS", "You are a helpful voice assistant."
            ),
            adk_session_id=session_id,
            adk_user_id=user_id,
        )
    raise ValueError("VOICE_PROVIDER must be azure, voicelive, or gemini")


def list_gemini_models() -> None:
    from google import genai

    with genai.Client() as client:
        found = False
        for model in client.models.list():
            if "bidiGenerateContent" in (model.supported_actions or []):
                print(model.name)
                found = True
        if not found:
            raise RuntimeError("No Live models returned for this account/backend")


if __name__ == "__main__":
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True)
    commands = parser.add_mutually_exclusive_group(required=True)
    commands.add_argument("--list-gemini-models", action="store_true")
    commands.add_argument("--configure-azure-tools", action="store_true")
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()
    if arguments.apply and not arguments.configure_azure_tools:
        parser.error("--apply requires --configure-azure-tools")
    load_dotenv(arguments.env_file, override=True)
    if arguments.configure_azure_tools:
        asyncio.run(configure_azure_tools(apply=arguments.apply))
    else:
        list_gemini_models()
