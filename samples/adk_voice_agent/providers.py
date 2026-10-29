"""Provider selection is confined to this integration/configuration module."""

import argparse
import json
import os

from google.adk.models.base_llm import BaseLlm


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
    raise ValueError("VOICE_PROVIDER must be azure or gemini")


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
    parser.add_argument("--list-gemini-models", action="store_true", required=True)
    arguments = parser.parse_args()
    load_dotenv(arguments.env_file, override=True)
    list_gemini_models()
