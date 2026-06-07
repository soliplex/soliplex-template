# A demonstration room that wires in a tool from this project's own
# '${package_name}' package (defined in src/${package_name}/tools.py). The
# dotted 'tool_name' below is importable because src/ is on the backend's
# PYTHONPATH (see docker-compose.yml). Delete this room once you have your own.
id: "custom"
name: "Custom Tool Demo"
description: "Demonstrates a tool provided by this project's own package."

agent:
  template_id: "default_chat"
  system_prompt: |
    You are a helpful assistant.

    When the user asks to be greeted, call the 'greeting' tool provided by
    this project's package.

tools:
  - tool_name: "${package_name}.tools.greeting"

allow_mcp: false
