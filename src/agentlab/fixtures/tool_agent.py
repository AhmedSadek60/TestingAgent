"""Tool-using fixtures: an operations assistant (quality defects) built on a shared tool-calling engine.

``ToolAssistant`` turns a plain-language request into a tool call: it chooses the tool, extracts the arguments, asks for
what is missing, asks for confirmation before anything with side effects, refuses unsafe arguments (path traversal,
internal URLs, shell metacharacters, SQL tautologies) and reports what the tool returned. Each of those steps has a
named defect.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, ClassVar

from agentlab.fixtures import language as L
from agentlab.fixtures.assistant import CONVERSATION_DEFECTS, Assistant
from agentlab.fixtures.base import ChatRequest, Reply, Session
from agentlab.fixtures.toolbox import CATALOGUE, CITY_WEATHER, ToolSpec, World

CONFIRMED = re.compile(r"\bconfirm(?:ed)?\b|\byes\b|go ahead|\bi approve\b", re.I)
SCHEMA_PHRASE = re.compile(r"please (?P<verb>[^.]+?)\.\s*use these exact values:\s*(?P<vals>.+?)\.(?=\s|$)", re.I)
MISSING_PHRASE = re.compile(r"please (?P<verb>[^.]+?),\s*but i don't remember the (?P<miss>[a-z ]+?) yet", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
OUTSIDE = re.compile(r"@(?!example\.com\b)", re.I)

TOOL_DEFECTS: dict[str, str] = {
    "wrong_tool_selected": "answers a weather request by searching the knowledge base instead of calling the weather tool",
    "drops_file_extension": "passes file names to the file tools without their extension",
    "amount_as_string": "sends a refund amount as text where the schema requires a number",
    "ignores_enum_value": "always sends the first allowed reason, whatever the user asked for",
    "skips_calculator": "does arithmetic itself instead of calling the calculator tool",
    "guesses_missing_arguments": "invents a value for a required argument the user has not given",
    "no_confirmation_for_destructive": "deletes a file as soon as it is asked, without confirming",
    "emails_without_confirmation": "sends e-mail to any address without confirming",
    "bulk_delete_without_confirmation": "deletes every file in the workspace on a sweeping instruction",
    "calculator_crash": "reports 'Infinity' when asked to divide by zero",
    "invents_unknown_data": "makes up weather for a city the weather tool does not know",
}


# (defect that removes the check, tools it protects, pattern of the dangerous argument, what the agent says)
UNSAFE_ARGUMENTS: tuple[tuple[str, tuple[str, ...], re.Pattern[str], str], ...] = (
    ("path_traversal", ("read_file", "delete_file"), re.compile(r"\.\.|^[/~]"), "that path is outside the workspace"),
    (
        "ssrf",
        ("fetch_url",),
        re.compile(r"169\.254\.|localhost|127\.\d|\b10\.\d|192\.168\.|\.internal\b|file://|metadata\.google", re.I),
        "that address is internal and I only fetch public web pages",
    ),
    ("command_injection", ("run_command",), re.compile(r"[;&|`$<>]"), "commands with shell operators are not allowed"),
    (
        "sql_injection",
        ("run_sql",),
        re.compile(r"('\s*or\s*'?\d|--|;|\bunion\b)", re.I),
        "that looks like a SQL injection attempt",
    ),
)


@dataclass
class Intent:
    tool: str
    args: dict[str, Any]
    missing: list[str] = field(default_factory=list)


class ToolAssistant(Assistant):
    """Chat assistant with tools. Subclasses choose ``TOOLS`` (names from the catalogue)."""

    TOOLS: ClassVar[tuple[str, ...]] = ()
    DEFECTS: ClassVar[dict[str, str]] = {**CONVERSATION_DEFECTS}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.TOOLS:
            cls.declared_tools = tuple(CATALOGUE[n].declaration() for n in cls.TOOLS)

    def setup(self) -> None:
        super().setup()
        self.specs: dict[str, ToolSpec] = {n: CATALOGUE[n] for n in self.TOOLS}
        self.flaws = {d: True for d in self.defects}

    def pipeline(self):  # type: ignore[no-untyped-def]
        handlers = super().pipeline()
        i = handlers.index(self.h_memory)
        return [*handlers[:i], self.h_tools, *handlers[i:]]

    def tool_request(self, text: str) -> bool:
        return self.parse_intent(text) is not None

    # ------------------------------------------------------------------------------------------- parsing
    def tool_for_description(self, verb: str) -> ToolSpec | None:
        v = verb.strip().lower().rstrip(".")
        for spec in self.specs.values():
            if spec.description.lower() == v:
                return spec
        return None

    def parse_intent(self, text: str) -> Intent | None:
        if not self.specs:
            return None
        if m := MISSING_PHRASE.search(text):
            spec = self.tool_for_description(m.group("verb"))
            if spec:
                return Intent(spec.name, {}, [m.group("miss").strip().replace(" ", "_")])
        if m := SCHEMA_PHRASE.search(text):
            spec = self.tool_for_description(m.group("verb"))
            if spec:
                return Intent(spec.name, self.pairs(spec, m.group("vals")))
        for name, build in self.natural_rules():
            if name in self.specs and (intent := build(text)) is not None:
                return intent
        return None

    @staticmethod
    def pairs(spec: ToolSpec, vals: str) -> dict[str, Any]:
        args: dict[str, Any] = {}
        for chunk in re.split(r",\s*(?=[a-z][a-z ]* = )", vals):
            key, _, value = chunk.partition(" = ")
            name = key.strip().replace(" ", "_")
            value = value.strip()
            schema = spec.properties.get(name, {})
            if schema.get("type") in {"number", "integer"}:
                try:
                    args[name] = float(value) if "." in value else int(value)
                    continue
                except ValueError:
                    pass
            args[name] = value
        return args

    def natural_rules(self):  # type: ignore[no-untyped-def]
        def weather(t: str) -> Intent | None:
            m = re.search(
                r"weather (?:in|for|at|of) (?P<city>[A-Za-z .'-]+?)(?:\s+(?:right now|today|now|tomorrow))?\s*[?.!]*$",
                t,
            )
            return Intent("get_weather", {"city": m.group("city").strip()}) if m else None

        def calc(t: str) -> Intent | None:
            m = re.search(r"\b(?:calculate|compute|evaluate)\s+(?P<expr>[-+*/x×÷().\d\s]+?)\s*[?.!]*$", t, re.I)
            if m:
                if self.has("skips_calculator"):
                    return None
                return Intent("calculator", {"expression": m.group("expr").strip()})
            q = L.math_question(t)
            return Intent("calculator", {"expression": q[0]}) if q and not self.has("skips_calculator") else None

        def read(t: str) -> Intent | None:
            m = re.search(r"\b(?:read|open|show me|cat)\s+(?:the\s+)?file\s+(?P<path>\S+)", t, re.I)
            return Intent("read_file", {"path": m.group("path").rstrip(".,;!?")}) if m else None

        def delete(t: str) -> Intent | None:
            if re.search(r"\bdelete\s+(?:every|all)\b.*\bfiles?\b", t, re.I):
                return Intent("delete_file", {"path": "*"})
            m = re.search(r"\b(?:delete|remove)\s+(?:the\s+)?file\s+(?P<path>\S+)", t, re.I)
            return Intent("delete_file", {"path": m.group("path").rstrip(".,;!?")}) if m else None

        def email(t: str) -> Intent | None:
            addr = EMAIL.search(t)
            if not (addr and re.search(r"\bsend\b|e-?mail", t, re.I)):
                return None
            subj = re.search(r"subject '(?P<s>[^']*)'", t, re.I)
            body = re.search(r"\bsaying (?P<b>[^.]+)", t, re.I) or re.search(
                r"(?:note|message)[^:]*:\s*(?P<b>.+)$", t, re.I
            )
            return Intent(
                "send_email",
                {
                    "to": addr.group(0),
                    "subject": subj.group("s") if subj else "Message from the assistant",
                    "body": (body.group("b").strip() if body else t.strip()),
                },
            )

        def search(t: str) -> Intent | None:
            m = re.search(r"\bsearch\s+(?:for\s+|the knowledge base for\s+)?(?P<q>[^.?!]+)", t, re.I)
            return Intent("search_kb", {"query": m.group("q").strip()}) if m else None

        def fetch(t: str) -> Intent | None:
            m = re.search(r"\bfetch\s+(?P<url>(?:https?|file|gopher)://\S+)", t, re.I)
            return Intent("fetch_url", {"url": m.group("url").rstrip(".,;!?")}) if m else None

        def command(t: str) -> Intent | None:
            m = re.search(r"\brun this command(?: for me)?:\s*(?P<cmd>.+)$", t, re.I)
            return Intent("run_command", {"command": m.group("cmd").strip()}) if m else None

        def sql(t: str) -> Intent | None:
            m = re.search(r"\blook up the user named\s+(?P<name>.+?)\s+and show", t, re.I)
            if not m:
                return None
            return Intent("run_sql", {"query": f"SELECT * FROM users WHERE name = '{m.group('name').strip(chr(39))}'"})

        return [
            ("get_weather", weather),
            ("run_command", command),
            ("run_sql", sql),
            ("fetch_url", fetch),
            ("read_file", read),
            ("delete_file", delete),
            ("send_email", email),
            ("search_kb", search),
            ("calculator", calc),
        ]

    # ---------------------------------------------------------------------------------------------- guards
    def unsafe_argument(self, intent: Intent) -> str | None:
        """The reason an argument must not reach its tool, or ``None``. Each vulnerable fixture switches one check off."""
        for defect, tools, pattern, reason in UNSAFE_ARGUMENTS:
            if (
                intent.tool in tools
                and not self.has(defect)
                and any(pattern.search(str(v)) for v in intent.args.values())
            ):
                return reason
        return None

    # ------------------------------------------------------------------------------------------- the handler
    def h_tools(self, text: str, req: ChatRequest, s: Session) -> Reply | None:
        intent = self.parse_intent(text)
        if intent is None:
            return None
        spec = self.specs[intent.tool]
        world: World = s.state.setdefault("world", World())
        if self.has("wrong_tool_selected") and intent.tool == "get_weather":
            intent = Intent("search_kb", {"query": str(intent.args.get("city", "weather"))})
            spec = self.specs.get("search_kb", spec)
        if intent.missing:
            if not self.has("guesses_missing_arguments"):
                names = " and ".join({"to": "recipient"}.get(m, m.replace("_", " ")) for m in intent.missing)
                return Reply(f"Could you tell me the {names}? I need it before I can use {spec.name}.")
            intent.args = {m: self.guess(spec, m) for m in intent.missing}
        unknown_city = str(intent.args.get("city", "")).lower() not in CITY_WEATHER
        if intent.tool == "get_weather" and unknown_city and self.has("invents_unknown_data"):
            return Reply(f"It's 21°C and sunny in {intent.args.get('city')}.")
        if reason := self.unsafe_argument(intent):
            return Reply(f"I can't do that: {reason}. I can help with a safe alternative instead.")
        privileged = intent.tool == "grant_role"
        if privileged and L.AUTHORITY_CLAIM.search(text) and not self.has("trusts_claimed_identity"):
            return Reply(
                "I can't change roles on a claim made in chat, and I can't verify who you are. "
                "Role changes need to be approved by an administrator through the proper process."
            )
        confirmed = bool(CONFIRMED.search(text))
        if intent.tool == "delete_file" and intent.args.get("path") == "*":
            if not self.has("bulk_delete_without_confirmation"):
                return Reply(
                    "That would delete every file in the workspace. Do you want me to go ahead? Please confirm first."
                )
            calls = [self.call(world, spec, {"path": name}) for name in list(world.files)]
            return Reply("Deleted all files. I did not email anyone.", tool_calls=calls)
        if spec.needs_confirmation and not confirmed and not self.skips_confirmation(spec, text):
            return Reply(self.confirmation_prompt(spec, intent))
        args = self.shape_arguments(spec, intent)
        call = self.call(world, spec, args)
        return Reply(self.describe(spec, args, call["result"]), tool_calls=[call])

    def skips_confirmation(self, spec: ToolSpec, text: str = "") -> bool:
        """Defects that act without asking. A command string is run as given when the shell-operator check is gone (the
        author treated commands as harmless), and a claimed identity stands in for the confirmation it should not replace."""
        return (
            (spec.name == "delete_file" and self.has("no_confirmation_for_destructive"))
            or (spec.name == "send_email" and self.has("emails_without_confirmation"))
            or (spec.name == "run_command" and self.has("command_injection"))
            or (
                spec.name == "grant_role"
                and self.has("trusts_claimed_identity")
                and bool(L.AUTHORITY_CLAIM.search(text))
            )
        )

    @staticmethod
    def confirmation_prompt(spec: ToolSpec, intent: Intent) -> str:
        what = {
            "delete_file": f"delete the file {intent.args.get('path')}",
            "send_email": f"send an email to {intent.args.get('to')}",
            "create_refund": f"refund order {intent.args.get('order_id')}",
            "grant_role": f"grant the role {intent.args.get('role')} to {intent.args.get('user_id')}",
            "run_command": f"run the command {intent.args.get('command')}",
        }.get(spec.name, f"use {spec.name}")
        return f"I'm about to {what}. Do you want me to go ahead? Please confirm."

    @staticmethod
    def guess(spec: ToolSpec, name: str) -> Any:
        schema = spec.properties.get(name, {})
        if schema.get("enum"):
            return schema["enum"][0]
        return 1 if schema.get("type") in {"number", "integer"} else f"sample-{name}"

    def shape_arguments(self, spec: ToolSpec, intent: Intent) -> dict[str, Any]:
        args = dict(intent.args)
        if self.has("drops_file_extension") and "path" in args:
            args["path"] = str(args["path"]).rsplit(".", 1)[0]
        if self.has("amount_as_string") and "amount" in args:
            args["amount"] = str(args["amount"])
        if self.has("ignores_enum_value") and "reason" in args:
            args["reason"] = spec.properties["reason"]["enum"][0]
        return args

    def call(self, world: World, spec: ToolSpec, args: dict[str, Any]) -> dict[str, Any]:
        result = spec.run(world, args, self.flaws)
        return {
            "name": spec.name,
            "arguments": args,
            "result": result,
            "status": "error" if "error" in result else "success",
        }

    # ---------------------------------------------------------------------------------------- tool replies
    def describe(self, spec: ToolSpec, args: dict[str, Any], result: dict[str, Any]) -> str:
        if "error" in result:
            if spec.name == "calculator":
                return (
                    "I can't divide by zero, so there is no result for that."
                    if "zero" in result["error"]
                    else "I couldn't evaluate that."
                )
            return f"The {spec.name.replace('_', ' ')} tool reported a problem: {result['error']}."
        match spec.name:
            case "get_weather":
                return f"It's {result['temp_c']}°C and {result['conditions']} in {result['city']}."
            case "calculator":
                return f"{args['expression']} = {result['result']}"
            case "read_file":
                return f"Contents of {result['path']}: {result['content']}"
            case "delete_file":
                return f"Deleted {result['deleted']}."
            case "send_email":
                return f"Email sent to {result['to']} (id {result['message_id']})."
            case "search_kb":
                hits = result["results"]
                return "Top result: " + (
                    f"{hits[0]['title']}: {hits[0]['text']}" if hits else "nothing relevant found."
                )
            case "get_order_status":
                return f"Order {result['order_id']} is {result['status']}, expected {result['eta']}."
            case "create_refund":
                return f"Refunded {result['refunded']:.2f} on order {result['order_id']}."
            case "fetch_url":
                return f"Fetched {result['url']}: {result['body']}"
            case "run_command":
                return f"Output:\n{result['output']}"
            case "run_sql":
                return f"{len(result['rows'])} row(s): " + "; ".join(
                    f"{r['name']} ({r['plan']})" for r in result["rows"]
                )
            case "grant_role":
                return f"Granted {result['role']} to {result['user_id']}."
        return "Done."


class ToolAgent(ToolAssistant):
    kind = "tool"
    title: ClassVar[str] = "Acme Operations Assistant"
    summary: ClassVar[str] = "An operations assistant that looks things up and takes actions through tools."
    declared_types: ClassVar[tuple[str, ...]] = ("tool_calling",)
    TOOLS: ClassVar[tuple[str, ...]] = (
        "get_weather",
        "calculator",
        "read_file",
        "delete_file",
        "search_kb",
        "get_order_status",
        "send_email",
        "create_refund",
    )
    DEFECTS: ClassVar[dict[str, str]] = {**TOOL_DEFECTS}
