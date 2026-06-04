"""
Native message and document types

Replaces all LangChain message/document types with simple, self-contained dataclasses.
These types are used across memory, RAG, context, and tool modules.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Message:
    """Unified message type replacing LangChain AIMessage/HumanMessage/SystemMessage/BaseMessage."""

    role: str  # "human", "ai", "system"
    content: str
    additional_kwargs: Dict[str, Any] = field(default_factory=dict)

    @property
    def type(self) -> str:
        return self.role


def human_message(content: str, **kwargs: Any) -> Message:
    return Message(role="human", content=content, additional_kwargs=kwargs)


def ai_message(content: str, **kwargs: Any) -> Message:
    return Message(role="ai", content=content, additional_kwargs=kwargs)


def system_message(content: str, **kwargs: Any) -> Message:
    return Message(role="system", content=content, additional_kwargs=kwargs)


@dataclass
class Document:
    """Simple document type replacing LangChain Document."""

    page_content: str
    metadata: Dict[str, Any] = field(default_factory=dict)


class MessageStore:
    """Simple list-based message store replacing LangChain ChatMessageHistory."""

    def __init__(self):
        self._messages: List[Message] = []

    def add_user_message(self, content: str) -> None:
        self._messages.append(human_message(content))

    def add_ai_message(self, content: str) -> None:
        self._messages.append(ai_message(content))

    def add_message(self, message: Message) -> None:
        self._messages.append(message)

    @property
    def messages(self) -> List[Message]:
        return list(self._messages)

    def clear(self) -> None:
        self._messages.clear()

    def __len__(self) -> int:
        return len(self._messages)


class LLMProtocol:
    """Minimal protocol for LLM services that memory modules need.

    Replaces langchain_core.language_models.BaseLanguageModel.
    Any object with an invoke(prompt) -> Message method satisfies this.
    """

    def invoke(self, prompt: str) -> Message:
        raise NotImplementedError