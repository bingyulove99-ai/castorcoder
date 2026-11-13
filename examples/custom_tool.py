"""最小化的“每个 Agent 自定义工具”示例。"""

from castorcoder import Agent, LLM, Workspace
from castorcoder.tools.base import Tool


class ProjectFactsTool(Tool):
    name = "project_facts"
    description = "返回宿主应用提供的可信项目事实。"
    parameters = {
        "type": "object",
        "properties": {"topic": {"type": "string"}},
        "required": ["topic"],
        "additionalProperties": False,
    }

    def execute(self, topic: str) -> str:
        facts = {"release": "1.0.0", "language": "Python 3.10+"}
        return facts.get(topic, "unknown topic")


def build_agent(api_key: str) -> Agent:
    llm = LLM(model="gpt-4o", api_key=api_key, temperature=0)
    return Agent(llm, tools=[ProjectFactsTool()], workspace=Workspace("."))
