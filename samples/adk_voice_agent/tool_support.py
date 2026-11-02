"""Shared tool implementations, registration, and saved Azure declarations."""

from copy import deepcopy


def add_numbers(first_number: float, second_number: float) -> dict:
    """Add two numbers locally and return their sum.

    Args:
        first_number: The first number to add.
        second_number: The second number to add.

    Returns:
        A dictionary with a "sum" key containing the sum of both numbers.
    """
    return {"sum": first_number + second_number}


FUNCTION_TOOLS = (
    (add_numbers, {
        "description": "Add two numbers using the client application.",
        "parameters": {
            "type": "object",
            "properties": {
                "first_number": {"type": "number"},
                "second_number": {"type": "number"},
            },
            "required": ["first_number", "second_number"],
            "additionalProperties": False,
        },
    }),
)


def get_tools() -> list:
    return [function for function, declaration in FUNCTION_TOOLS]


def get_function_declarations() -> list[dict]:
    return [
        {"type": "function", "name": function.__name__, **deepcopy(declaration)}
        for function, declaration in FUNCTION_TOOLS
    ]


def with_demo_tool(definition: dict) -> dict:
    if (definition.get("kind") != "voice"
            or definition.get("sub_protocol") not in (None, "realtime")
            or definition.get("conversation_engine")):
        raise ValueError("Tool setup requires an existing realtime voice agent")
    updated = deepcopy(definition)
    tools = updated.get("tools") or []
    updated["tools"] = tools
    declarations = {tool["name"]: tool for tool in get_function_declarations()}
    found = set()
    for tool in tools:
        if tool.get("type") != "function":
            continue
        name = tool.get("name")
        if name not in declarations:
            raise ValueError("This agent has other client functions; use a dedicated demo agent")
        if tool.get("parameters") != declarations[name]["parameters"]:
            raise ValueError(f"Existing {name} schema differs; refusing to replace it")
        found.add(name)
    tools.extend(tool for name, tool in declarations.items() if name not in found)
    return updated
