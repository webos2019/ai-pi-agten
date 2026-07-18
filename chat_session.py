"""聊天会话管理 — 消息格式转换 + 模型调用 (Tool Runtime 增强)"""

from typing import Any

from skill_registry import skill_registry
from tool_registry import tool_registry
from tool_runtime import tool_runtime
from deepseek import chat_completion


class ChatSession:
    """聊天会话"""

    def __init__(self, skill_id: str, messages: list[dict[str, Any]]):
        skill = skill_registry.get(skill_id)
        if not skill:
            raise ValueError(f'未知的 Skill: "{skill_id}"')

        self._skill_id = skill_id
        self._skill = skill
        self._messages = self._to_openai_messages(messages, skill_id)

    def get_messages(self) -> list[dict[str, Any]]:
        return self._messages

    def get_skill_id(self) -> str:
        return self._skill_id

    def get_system_prompt(self) -> str:
        return self._skill.system_prompt

    def get_tool_specs(self) -> list[dict[str, Any]]:
        return tool_registry.get_openai_tool_specs(self._skill.tool_names)

    async def invoke_model(
        self,
        messages: list[dict[str, Any]] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> Any:
        """异步调用模型"""
        msg_list = messages if messages is not None else self._messages
        tool_list = tools if tools is not None else self.get_tool_specs()

        full_messages = [{"role": "system", "content": self.get_system_prompt()}]
        full_messages.extend(self._to_openai_messages(msg_list, self._skill_id))

        return await chat_completion(
            messages=full_messages,
            tools=tool_list if tool_list else None,
        )

    def _to_openai_messages(
        self,
        messages: list[dict[str, Any]],
        skill_id: str,
    ) -> list[dict[str, Any]]:
        """将前端消息格式转换为 OpenAI 消息格式（处理结构化请求与文件上传）"""
        result = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "user":
                structured = msg.get("structured")
                if structured:
                    content = tool_runtime.enhance_context(content, skill_id, structured)
                if msg.get("files"):
                    file_parts = []
                    for f in msg["files"]:
                        file_parts.append(
                            f"```\n文件: {f.get('name', '')}\n```\n"
                            f"```{f.get('type', 'text')}\n{f.get('content', '')}\n```"
                        )
                    file_context = "\n\n".join(file_parts)
                    if content:
                        content = f"{content}\n\n---\n以下是用户上传的代码文件：\n\n{file_context}"
                    else:
                        content = f"用户上传了以下代码文件：\n\n{file_context}"

            if role == "system":
                result.append({"role": "system", "content": content})
            elif role == "assistant":
                result.append({"role": "assistant", "content": content})
            else:
                result.append({"role": "user", "content": content})

        return result


def create_chat_session(skill_id: str, messages: list[dict[str, Any]]) -> ChatSession:
    """创建聊天会话"""
    return ChatSession(skill_id, messages)
