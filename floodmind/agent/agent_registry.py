"""
Agent registry — multiple agent types with permission-based tool access.

Inspired by OpenCode's agent.ts.
Each agent has: name, description, mode, permissions, optional model & prompt overrides.

Agent types:
  build    — primary agent, full tool access (default)
  plan     — planning mode, read-only + plan tools
  general  — subagent for parallel tasks, limited writes
  explore  — subagent for codebase search, read-only
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Permission helpers
# ---------------------------------------------------------------------------

PermissionSpec = Dict[str, Any]  # {tool_name: "allow"|"ask"|"deny"}


def _tool_list(*names: str) -> Set[str]:
    return set(names)


READONLY_TOOLS = _tool_list(
    "GetSkill", "Glob", "Grep", "Read",
    "KnowledgeSearch", "MemorySearch",
    "WebSearch", "WebFetch",
    "SearchTaskExperience", "BrowseExperienceTree", "DrillDownExperience",
    "ListScheduledTasks",
)

WRITE_TOOLS = _tool_list("Write", "Edit", "KnowledgeAdd", "MemoryAdd", "AddTaskExperience")

EXEC_TOOLS = _tool_list("Bash")

PLAN_TOOLS = _tool_list("create_plan")

DELEGATE_TOOLS = _tool_list("SubAgent", "ParallelTask")

SCHEDULE_TOOLS = _tool_list("CreateScheduledTask", "CancelScheduledTask")

INSTRUCT_TOOLS = _tool_list("UpdateProjectInstructions")

# ---------------------------------------------------------------------------
# Agent definitions
# ---------------------------------------------------------------------------

@dataclass
class AgentInfo:
    name: str
    description: str = ""
    mode: str = "primary"  # primary | subagent | all
    hidden: bool = False
    color: str = ""
    allow: Set[str] = field(default_factory=set)
    ask: Set[str] = field(default_factory=set)
    deny: Set[str] = field(default_factory=set)
    prompt: str = ""  # override system prompt
    model: Optional[str] = None  # override model (provider/model)
    temperature: Optional[float] = None
    steps: Optional[int] = None  # max tool call steps
    options: Dict[str, Any] = field(default_factory=dict)

    @property
    def tool_policy(self) -> str:
        """Determine overall tool access policy for system prompt generation."""
        if not self.deny:
            return "allow_all"
        if not self.allow or self.allow.issubset(READONLY_TOOLS):
            return "readonly"
        return "custom"

    def can_use(self, tool_name: str) -> str:
        """Check if agent can use a tool. Returns 'allow', 'ask', or 'deny'."""
        if tool_name in self.deny:
            return "deny"
        if tool_name in self.ask:
            return "ask"
        if self.allow and tool_name not in self.allow:
            return "deny"
        return "allow"


# Pre-defined agents
BUILTIN_AGENTS: Dict[str, AgentInfo] = {
    "build": AgentInfo(
        name="build",
        description="The default agent. Full tool access based on configured permissions.",
        mode="primary",
        color="#81c784",
        allow=READONLY_TOOLS | WRITE_TOOLS | EXEC_TOOLS | DELEGATE_TOOLS | SCHEDULE_TOOLS | INSTRUCT_TOOLS | {"create_plan"} | {"LoadMcpServer"},
    ),
    "plan": AgentInfo(
        name="plan",
        description="Plan mode. Disallows write/edit/exec tools. Use for scoping and design before committing changes.",
        mode="primary",
        color="#ffa726",
        allow=READONLY_TOOLS | PLAN_TOOLS | DELEGATE_TOOLS,
        deny=WRITE_TOOLS | EXEC_TOOLS,
        prompt="""You are in PLANNING MODE. You cannot modify files or execute commands.
Your job is to:
1. Analyze the user's request thoroughly
2. Use Glob/Grep/Read to explore the codebase
3. Create a structured execution plan using create_plan
4. Present your findings and plan to the user
Do NOT call Write/Edit/Bash — those tools are disabled in this mode.""",
    ),
    "general": AgentInfo(
        name="general",
        description="General-purpose subagent for parallel tasks. Has file read/write access but no delegation.",
        mode="subagent",
        color="#4fc3f7",
        allow=READONLY_TOOLS | WRITE_TOOLS | EXEC_TOOLS,
        deny=DELEGATE_TOOLS | INSTRUCT_TOOLS,
    ),
    "explore": AgentInfo(
        name="explore",
        description="Fast read-only agent for exploring codebases. Grep, Glob, Read only.",
        mode="subagent",
        color="#ce93d8",
        allow={"Glob", "Grep", "Read", "WebSearch", "WebFetch"},
        deny=WRITE_TOOLS | EXEC_TOOLS | DELEGATE_TOOLS | INSTRUCT_TOOLS | SCHEDULE_TOOLS,
    ),
}

# User-defined agent overrides from config, if any.
# e.g. {"my-custom-agent": AgentInfo(name="my-custom-agent", ...)}
_user_agents: Dict[str, AgentInfo] = {}


# ---------------------------------------------------------------------------
# Registry operations
# ---------------------------------------------------------------------------

def get_agent(name: str) -> Optional[AgentInfo]:
    """Get agent by name. Checks user overrides first, then builtins."""
    merged = _load_merged()
    return merged.get(name)


def list_agents(include_hidden: bool = False) -> List[AgentInfo]:
    """List all agents. Primary agents come first, then subagents."""
    merged = _load_merged()
    agents = [a for a in merged.values() if include_hidden or not a.hidden]
    agents.sort(key=lambda a: (0 if a.mode != "subagent" else 1, a.name))
    return agents


def default_agent() -> AgentInfo:
    """Get the default agent (from config or 'build')."""
    from floodmind.config.settings import get_config
    cfg = get_config()
    default_name = cfg.get("agent", {}).get("default", "build")
    agent = get_agent(default_name)
    if agent and agent.mode != "subagent" and not agent.hidden:
        return agent
    # Fallback to build
    return BUILTIN_AGENTS["build"]


def register_user_agent(info: AgentInfo) -> None:
    """Register or override a user-defined agent."""
    _user_agents[info.name] = info


def remove_user_agent(name: str) -> None:
    """Remove a user-defined agent."""
    _user_agents.pop(name, None)


def _load_merged() -> Dict[str, AgentInfo]:
    """Merge builtin + config overrides + user agents."""
    from floodmind.config.settings import get_config
    cfg = get_config()

    # Start with builtins
    merged = dict(BUILTIN_AGENTS)

    # Apply config overrides
    agent_cfg = cfg.get("agent", {})
    for name, override in agent_cfg.get("agents", {}).items():
        if isinstance(override, dict):
            if override.get("disable"):
                merged.pop(name, None)
                continue
            existing = merged.get(name)
            if not existing:
                existing = AgentInfo(name=name, mode="all")
                merged[name] = existing
            if "description" in override:
                existing.description = override["description"]
            if "mode" in override:
                existing.mode = override["mode"]
            if "color" in override:
                existing.color = override["color"]
            if "model" in override:
                existing.model = override["model"]
            if "temperature" in override:
                existing.temperature = override["temperature"]
            if "steps" in override:
                existing.steps = override["steps"]
            if "prompt" in override:
                existing.prompt = override["prompt"]
            if "hidden" in override:
                existing.hidden = override["hidden"]

    # Apply runtime registrations
    for name, agent in _user_agents.items():
        merged[name] = agent

    return merged


def build_tool_descriptions_for_agent(agent: AgentInfo, registry) -> str:
    """Build tool description list filtered by agent permissions."""
    if not registry:
        return "- (无工具注册)"
    lines = []
    for tool in registry.all():
        name = tool.name
        if agent.allow and name not in agent.allow and name not in agent.deny:
            # Implicitly denied (whitelist mode)
            if agent.allow.issuperset(READONLY_TOOLS | WRITE_TOOLS | EXEC_TOOLS):
                pass  # Agent has broad access, show all
            else:
                continue
        if name in agent.deny:
            continue
        desc = getattr(tool, "description", "") or ""
        short = desc.split("。")[0].split(".")[0][:80] if desc else ""
        if short:
            lines.append(f"- `{name}`：{short}")
        else:
            lines.append(f"- `{name}`")
    return "\n".join(lines)
