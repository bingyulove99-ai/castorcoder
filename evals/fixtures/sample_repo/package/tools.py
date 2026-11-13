class ToolRegistry:
    """Look up tools by name for one agent instance."""

    def __init__(self, tools):
        self.tools = {tool.name: tool for tool in tools}

    def get(self, name):
        return self.tools.get(name)
