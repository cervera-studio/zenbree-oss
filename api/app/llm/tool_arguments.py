from app.llm.base import ToolCall


def parsed_tool_call(id: str, name: str, parsed: object) -> ToolCall:
    """A ToolCall from parsed arguments: a non-object (string, list, number) becomes a call the loop fails alone."""
    if isinstance(parsed, dict):
        return ToolCall(id, name, parsed)
    return ToolCall(id, name, {}, error='Tool arguments must be a JSON object.')
