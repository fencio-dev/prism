import logging
import pathlib
import re
import time
import uuid
import yaml
from typing import Any, Callable, Dict, List
from uuid import UUID

import httpx
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict

_PROMPTS_DIR = pathlib.Path(__file__).parent / "prompts"

logger = logging.getLogger(__name__)


class _ToolIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    resource: str


class PrismCallback(BaseCallbackHandler):
    """LangChain callback handler that enforces Prism policies on LLM and agent events."""

    def __init__(
        self,
        base_url: str,
        tenant_id: str,
        model=None,
        api_key: str | None = None,
        agent_id: str = "unknown",
        principal_id: str = "unknown",
        enforcement_mode: str = "block",
        timeout: float = 2.0,
        fail_open: bool = True,
        action_mapper: Callable[[str, dict], str] | None = None,
        resource_mapper: Callable[[str], str] | None = None,
    ) -> None:
        """Initialize the callback with Prism connection and enforcement settings."""
        self.base_url = base_url.rstrip("/")
        self.tenant_id = tenant_id
        self.api_key = api_key
        self.agent_id = agent_id
        self.principal_id = principal_id
        self.enforcement_mode = enforcement_mode
        self.timeout = timeout
        self.fail_open = fail_open
        self._model = model
        self._action_mapper = action_mapper
        self._resource_mapper = resource_mapper
        if self._model is None and not (self._action_mapper and self._resource_mapper):
            raise ValueError(
                "model is required unless both action_mapper and resource_mapper are provided"
            )
        self.raise_error = True
        self._intent_cache: dict[str, dict] = {}
        self._pending_agent_actions: set[str] = set()
        self._infer_prompt = yaml.safe_load(
            (_PROMPTS_DIR / "infer_tool_intent.yaml").read_text()
        )

    def _enforce(self, intent: dict) -> tuple[str, dict]:
        """POST intent to Prism /api/v2/enforce and return (decision, full response dict)."""
        headers = {
            "Content-Type": "application/json",
            "X-Tenant-Id": self.tenant_id,
        }

        try:
            response = httpx.post(
                f"{self.base_url}/api/v2/enforce",
                json=intent,
                headers=headers,
                timeout=self.timeout,
            )
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            if self.fail_open:
                logger.warning("Prism unreachable (%s); failing open with ALLOW", exc)
                return "ALLOW", {}
            raise

        if not response.is_success:
            raise RuntimeError(
                f"Prism enforcement request failed with status {response.status_code}"
            )

        data = response.json()
        return data["decision"], data

    def _parse_intent_from_text(self, raw: Any) -> dict:
        import json as _json

        if raw is None:
            raise ValueError("empty model response")

        text = raw if isinstance(raw, str) else str(raw)
        candidate = text.strip()

        fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", candidate, flags=re.DOTALL)
        if fenced:
            candidate = fenced.group(1).strip()

        try:
            parsed = _json.loads(candidate)
        except _json.JSONDecodeError:
            match = re.search(r"\{.*\}", candidate, flags=re.DOTALL)
            if not match:
                raise
            parsed = _json.loads(match.group(0))

        if not isinstance(parsed, dict):
            raise ValueError("intent response must be a JSON object")
        return {
            "action": parsed["action"],
            "resource": parsed["resource"],
        }

    def _infer_intent(self, tool_name: str, tool_input: dict) -> dict:
        """Infer action and resource for a tool call. Returns {"action": ..., "resource": ...}."""
        if self._model is None:
            raise RuntimeError(
                f"Prism: no model configured for intent inference on tool '{tool_name}'"
            )
        if tool_name in self._intent_cache:
            return self._intent_cache[tool_name]

        try:
            input_keys = list(tool_input.keys())
            system_msg = self._infer_prompt["system"]
            user_msg = self._infer_prompt["user"].format(
                tool_name=tool_name,
                input_keys=input_keys,
            )
            model_obj: Any = self._model

            if callable(model_obj) and not hasattr(model_obj, "invoke"):
                raw = model_obj(system_msg, user_msg)
                result = self._parse_intent_from_text(raw)
            elif isinstance(model_obj, BaseChatModel):
                with_structured_output = getattr(model_obj, "with_structured_output")
                structured = with_structured_output(_ToolIntent)
                parsed = structured.invoke([
                    SystemMessage(content=system_msg),
                    HumanMessage(content=user_msg),
                ])
                if isinstance(parsed, _ToolIntent):
                    result = parsed.model_dump()
                elif isinstance(parsed, dict):
                    result = {
                        "action": parsed["action"],
                        "resource": parsed["resource"],
                    }
                else:
                    result = self._parse_intent_from_text(parsed)
            else:
                invoke = getattr(model_obj, "invoke", None)
                if invoke is None:
                    raise ValueError("model must be callable or implement invoke()")
                response = invoke([
                    SystemMessage(content=system_msg),
                    HumanMessage(content=user_msg),
                ])
                result = self._parse_intent_from_text(response.content)
        except Exception as exc:
            raise RuntimeError(f"Prism: LLM intent inference failed for tool '{tool_name}'") from exc

        self._intent_cache[tool_name] = result
        return result

    def _infer_action(self, tool_name: str, tool_input: dict | None = None) -> str:
        if self._action_mapper:
            return self._action_mapper(tool_name, tool_input or {})
        return self._infer_intent(tool_name, tool_input or {})["action"]

    def _infer_resource(self, tool_name: str, tool_input: dict | None = None) -> str:
        if self._resource_mapper:
            return self._resource_mapper(tool_name)
        return self._infer_intent(tool_name, tool_input or {})["resource"]

    def on_agent_action(
        self,
        action: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: List[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when an agent selects an action to execute."""
        intent = {
            "event_type": "tool_call",
            "id": f"tool_input_{uuid.uuid4()}",
            "ts": time.time(),
            "tenant_id": self.tenant_id,
            "identity": {
                "agent_id": self.agent_id,
                "principal_id": self.principal_id,
                "actor_type": "agent",
                "service_account": None,
                "role_scope": None,
            },
            "t": f"{self._infer_resource(action.tool, action.tool_input)}/{action.tool}",
            "op": self._infer_action(action.tool, action.tool_input),
            "p": f"Tool inputs: {list(action.tool_input.keys())}",
            "params": action.tool_input,
            "ctx": None,
        }
        decision, response_data = self._enforce(intent)
        if decision == "DENY":
            if self.enforcement_mode == "block":
                raise PermissionError(f"Prism blocked tool call: {action.tool}")
            else:
                logger.warning("Prism DENY (warn mode) — tool call: %s", action.tool)
        elif decision == "MODIFY":
            modified = response_data.get("modified_params") or {}
            action.tool_input.update(modified)
            logger.info("Prism MODIFY applied to tool inputs: %s", list(modified.keys()))
        else:
            logger.debug("Prism decision %s for tool: %s", decision, action.tool)
        self._pending_agent_actions.add(action.tool)

    def on_tool_start(
        self,
        serialized: Dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: List[str] | None = None,
        metadata: Dict[str, Any] | None = None,
        inputs: Dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when a tool starts execution. Used by LangGraph (on_agent_action is not fired there)."""
        tool_name = serialized.get("name", "unknown")

        # Classic AgentExecutor fires on_agent_action before this — skip to avoid double enforcement.
        if tool_name in self._pending_agent_actions:
            self._pending_agent_actions.discard(tool_name)
            return

        tool_input: dict = inputs or {}
        if not tool_input and input_str:
            import json as _json
            try:
                tool_input = _json.loads(input_str)
                if not isinstance(tool_input, dict):
                    tool_input = {"input": input_str}
            except (_json.JSONDecodeError, ValueError):
                tool_input = {"input": input_str}

        intent = {
            "event_type": "tool_call",
            "id": f"tool_input_{uuid.uuid4()}",
            "ts": time.time(),
            "tenant_id": self.tenant_id,
            "identity": {
                "agent_id": self.agent_id,
                "principal_id": self.principal_id,
                "actor_type": "agent",
                "service_account": None,
                "role_scope": None,
            },
            "t": f"{self._infer_resource(tool_name, tool_input)}/{tool_name}",
            "op": self._infer_action(tool_name, tool_input),
            "p": f"Tool inputs: {list(tool_input.keys())}",
            "params": tool_input,
            "ctx": None,
        }
        decision, response_data = self._enforce(intent)
        if decision == "DENY":
            if self.enforcement_mode == "block":
                raise PermissionError(f"Prism blocked tool call: {tool_name}")
            else:
                logger.warning("Prism DENY (warn mode) — tool call: %s", tool_name)
        elif decision == "MODIFY":
            logger.warning("Prism MODIFY on tool start — cannot rewrite input_str in-place: %s", tool_name)
        else:
            logger.debug("Prism decision %s for tool: %s", decision, tool_name)

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: List[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when a tool encounters an error."""
        pass


class PrismModel(BaseChatModel):
    """A BaseChatModel wrapper that enforces Prism policies before delegating to an underlying LLM."""

    def __init__(self, llm: BaseChatModel, callback: PrismCallback) -> None:
        super().__init__()
        object.__setattr__(self, "_llm", llm)
        object.__setattr__(self, "_callback", callback)

    def __getattr__(self, name: str) -> Any:
        # Delegate unknown attribute lookups to the inner LLM.
        # __getattr__ is only called when normal attribute lookup fails,
        # so _llm, _callback, and explicitly defined methods are unaffected.
        return getattr(object.__getattribute__(self, "_llm"), name)

    def _generate(self, messages: List, stop=None, run_manager=None, **kwargs: Any):
        intent = {
            "event_type": "reasoning",
            "id": f"llm_input_{uuid.uuid4()}",
            "ts": time.time(),
            "tenant_id": self._callback.tenant_id,
            "identity": {
                "agent_id": self._callback.agent_id,
                "principal_id": self._callback.principal_id,
                "actor_type": "llm",
                "service_account": None,
                "role_scope": None,
            },
            "t": f"llm://{type(self._llm).__name__}",
            "op": "generate_response",
            "p": f"Prompt ({len(messages)} message(s))",
            "params": {},
            "ctx": None,
        }
        decision, _ = self._callback._enforce(intent)
        if decision == "DENY":
            raise PermissionError(f"Prism blocked LLM call: {intent['id']}")
        return self._llm._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    async def _agenerate(self, messages: List, stop=None, run_manager=None, **kwargs: Any):
        intent = {
            "event_type": "reasoning",
            "id": f"llm_input_{uuid.uuid4()}",
            "ts": time.time(),
            "tenant_id": self._callback.tenant_id,
            "identity": {
                "agent_id": self._callback.agent_id,
                "principal_id": self._callback.principal_id,
                "actor_type": "llm",
                "service_account": None,
                "role_scope": None,
            },
            "t": f"llm://{type(self._llm).__name__}",
            "op": "generate_response",
            "p": f"Prompt ({len(messages)} message(s))",
            "params": {},
            "ctx": None,
        }
        decision, _ = self._callback._enforce(intent)
        if decision == "DENY":
            raise PermissionError(f"Prism blocked LLM call: {intent['id']}")
        return await self._llm._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)

    def bind_tools(self, tools, **kwargs):
        return self._llm.bind_tools(tools, **kwargs)

    @property
    def _llm_type(self) -> str:
        return self._llm._llm_type
