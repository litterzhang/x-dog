"""Slash commands: built-in commands available in interactive and REPL modes."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from xdog.agent.skills import Skill, SkillManager
from xdog.coding.config import get_skills_dir
from xdog.coding.core.agent_session import AgentSession


@dataclass(frozen=True)
class CommandResult:
    """Result of executing a slash command."""

    output: str
    exit_requested: bool = False
    #: Text to send to the agent as if the user had typed it. Loading a skill
    #: is only useful if its instructions reach the model — printing them to
    #: the terminal would tell the human something they already asked for and
    #: leave the agent none the wiser.
    prompt: str = ""


# Registry of built-in slash commands: name → description
BUILTIN_COMMANDS: dict[str, str] = {
    "help": "Show available commands",
    "skills": "List available skills: /skills",
    "unload": "Drop an active skill: /unload [name|all]",
    "model": "Show or switch model: /model [name]",
    "thinking": "Show or set thinking level: /thinking [off|low|medium|high]",
    "compact": "Force conversation compaction",
    "clear": "Clear conversation history",
    "session": "Show session info",
    "sessions": "List recent sessions",
    "fork": "Create a conversation branch",
    "branch": "Restore a conversation branch",
    "quit": "Exit the agent",
    "exit": "Exit the agent",
}


@lru_cache(maxsize=1)
def skill_manager() -> SkillManager:
    """The skill source for slash commands.

    Cached because it is consulted on every unrecognised command, and because
    constructing one creates its directory. Discovery of packaged skills is
    left at its default, so installing a distribution that ships one is all it
    takes for a new command to appear.
    """
    return SkillManager(shared_dir=get_skills_dir())


def available_skills() -> list[Skill]:
    """Skills usable as commands right now. Never raises — a broken skills
    directory should not take the command dispatcher down with it."""
    try:
        return skill_manager().list_skills()
    except OSError:
        return []


def list_commands() -> dict[str, str]:
    """Return every command, built-in and skill-provided.

    Skills are listed alongside built-ins deliberately: to the person typing,
    `/flow` and `/model` are the same gesture, and a skill that does not appear
    in completion may as well not be installed. Built-ins win a name clash, so
    a skill can never shadow `/quit`.
    """
    commands = {s.slug: s.description or f"Skill: {s.name}" for s in available_skills()}
    commands.update(BUILTIN_COMMANDS)
    return commands


async def execute_command(
    command: str,
    args: str,
    session: AgentSession,
) -> CommandResult:
    """Execute a slash command."""
    cmd = command.lower().strip()

    if cmd == "help":
        return _cmd_help()
    elif cmd == "model":
        return _cmd_model(args, session)
    elif cmd == "thinking":
        return _cmd_thinking(args, session)
    elif cmd == "compact":
        return await _cmd_compact(session)
    elif cmd == "clear":
        return _cmd_clear(session)
    elif cmd == "session":
        return _cmd_session(session)
    elif cmd == "sessions":
        return _cmd_sessions(session)
    elif cmd == "fork":
        return _cmd_fork(args, session)
    elif cmd == "branch":
        return _cmd_branch(args, session)
    elif cmd in ("quit", "exit"):
        return CommandResult(output="Goodbye.", exit_requested=True)
    elif cmd == "skills":
        return _cmd_skills(session)
    elif cmd == "unload":
        return _cmd_unload(args, session)

    # Not a built-in: a skill of that name becomes a command. Checked last so
    # a skill can never take over `/quit`.
    skill = _load_skill(cmd)
    if skill is not None:
        return _run_skill(skill, args, session)

    return CommandResult(output=f"Unknown command: /{cmd}. Type /help for available commands.")


def parse_slash_command(text: str) -> tuple[str, str] | None:
    """Parse a slash command from user input.

    Returns (command, args) or None if not a slash command.
    """
    if not text.startswith("/"):
        return None
    parts = text[1:].split(None, 1)
    cmd = parts[0] if parts else ""
    args = parts[1] if len(parts) > 1 else ""
    return (cmd, args)


# -- Command implementations --


def _load_skill(slug: str) -> Skill | None:
    try:
        return skill_manager().load_skill(slug)
    except OSError:
        return None


def _run_skill(skill: Skill, args: str, session: AgentSession) -> CommandResult:
    """Activate a skill, and start a turn if the user gave one.

    The instructions go into the system prompt rather than the message
    history. That costs a prompt-cache miss on activation, and buys the only
    thing the message-history approach cannot offer: taking them back out.

    `/flow` on its own just activates. `/flow add a retry step` activates and
    sends the request, so it reads as one instruction — the skill says how,
    the argument says what.
    """
    session.activate_skill(skill.slug)
    origin = " (shipped with an installed package)" if skill.packaged else ""
    note = f"Activated skill: {skill.name}{origin} — /unload {skill.slug} to drop it"
    return CommandResult(output=note, prompt=args)


def _cmd_unload(args: str, session: AgentSession) -> CommandResult:
    active = session.active_skills
    if not args:
        if not active:
            return CommandResult(output="No skills are active.")
        return CommandResult(
            output="Active skills: " + ", ".join(sorted(active)) + "\nDrop one with /unload <name>."
        )

    slug = args.strip()
    if slug == "all":
        for s in sorted(active):
            session.deactivate_skill(s)
        return CommandResult(output=f"Unloaded {len(active)} skill(s)." if active else "Nothing to unload.")

    if session.deactivate_skill(slug):
        return CommandResult(output=f"Unloaded skill: {slug}")
    return CommandResult(output=f"Skill not active: {slug}")


def _cmd_skills(session: AgentSession) -> CommandResult:
    skills = available_skills()
    if not skills:
        return CommandResult(
            output=(
                "No skills found.\n\n"
                f"Add one at {get_skills_dir()}/<name>/SKILL.md, or install a package "
                "that ships one — `pip install xdog-flow` provides /flow."
            )
        )

    active = session.active_skills
    lines = ["Available skills:", ""]
    width = max(len(s.slug) for s in skills)
    for s in skills:
        mark = "*" if s.packaged else " "
        state = "  [active]" if s.slug in active else ""
        lines.append(f"  {mark} /{s.slug:<{width}s}  {s.description}{state}")
    lines += [
        "",
        "  * shipped with an installed package",
        "",
        "Activate with /<name> [request], drop with /unload <name>.",
    ]
    return CommandResult(output="\n".join(lines))


def _cmd_help() -> CommandResult:
    lines = ["Available commands:", ""]
    for name, desc in sorted(BUILTIN_COMMANDS.items()):
        if name == "exit":
            continue
        lines.append(f"  /{name:<12s} {desc}")

    skills = available_skills()
    if skills:
        lines += ["", "Skills:", ""]
        for s in skills:
            lines.append(f"  /{s.slug:<12s} {s.description}")
    return CommandResult(output="\n".join(lines))


def _cmd_model(args: str, session: AgentSession) -> CommandResult:
    """Show current model or switch to a new one."""
    current = session.model or "unknown"

    if not args:
        # List available models from the ai runtime
        try:
            import xdog.ai as ai
            runtime = ai.load()
            all_models = runtime.models()
        except Exception:
            all_models = ()

        lines = [f"Current model: {current}", "", "Available models:"]
        for m in sorted(all_models, key=lambda x: x.id):
            short = m.id.split("/", 1)[-1] if "/" in m.id else m.id
            marker = "  → " if short == current or m.id == current else "    "
            reasoning = " (reasoning)" if m.reasoning else ""
            lines.append(f"{marker}{short}{reasoning}")
        if not all_models:
            lines.append("    (no models available)")
        return CommandResult(output="\n".join(lines))

    # Switch model
    target = args.strip()
    session.set_model(target)
    return CommandResult(output=f"Switched to model: {target}")


def _cmd_thinking(args: str, session: AgentSession) -> CommandResult:
    """Show or change the thinking level."""
    if not args:
        level = session.agent.options.thinking or "off"
        return CommandResult(output=f"Current thinking level: {level}")

    requested = args.strip().lower()
    valid_levels = ("off", "minimal", "low", "medium", "high", "xhigh")
    if requested not in valid_levels:
        return CommandResult(
            output=f"Invalid thinking level: {requested}\nValid levels: {', '.join(valid_levels)}"
        )

    if level == "off":
        session.set_thinking_level(None)
    else:
        session.set_thinking_level(level)
    return CommandResult(output=f"Thinking level set to: {level}")


async def _cmd_compact(session: AgentSession) -> CommandResult:
    msg_before = len(session.messages)
    await session.compact()
    msg_after = len(session.messages)
    return CommandResult(output=f"Compacted: {msg_before} → {msg_after} messages")


def _cmd_clear(session: AgentSession) -> CommandResult:
    session.clear()
    return CommandResult(output="Conversation cleared.")


def _cmd_session(session: AgentSession) -> CommandResult:
    model_name = session.model or "unknown"
    thinking = session.agent.options.thinking or "off"
    lines = [
        f"Session ID: {session.session_id}",
        f"Messages:   {len(session.messages)}",
        f"Model:      {model_name}",
        f"Thinking:   {thinking}",
        f"Working dir: {session.working_dir}",
    ]
    return CommandResult(output="\n".join(lines))


def _cmd_sessions(session: AgentSession) -> CommandResult:
    metas = session.session_manager.list_sessions(limit=10)
    if not metas:
        return CommandResult(output="No sessions found.")

    lines = ["Recent sessions:", ""]
    for i, meta in enumerate(metas):
        current = " ← current" if meta.session_id == session.session_id else ""
        summary = meta.summary[:60] if meta.summary else "(empty)"
        lines.append(f"  {i + 1}. {meta.session_id[:12]} | {meta.model} | {summary}{current}")
    return CommandResult(output="\n".join(lines))


def _cmd_fork(args: str, session: AgentSession) -> CommandResult:
    at_index: int | None = None
    if args:
        try:
            at_index = int(args.strip())
        except ValueError:
            return CommandResult(output=f"Invalid index: {args}")

    branch_id = session.create_branch(at_index=at_index)
    return CommandResult(output=f"Branch created: {branch_id}")


def _cmd_branch(args: str, session: AgentSession) -> CommandResult:
    if not args:
        branches = session.session_data.branches
        if not branches:
            return CommandResult(output="No branches. Use /fork to create one.")
        lines = ["Branches:", ""]
        for b in branches:
            lines.append(f"  {b['branch_id']} (at message {b['branch_point']})")
        return CommandResult(output="\n".join(lines))

    branch_id = args.strip()
    if session.restore_branch(branch_id):
        return CommandResult(output=f"Restored branch: {branch_id}")
    return CommandResult(output=f"Branch not found: {branch_id}")
