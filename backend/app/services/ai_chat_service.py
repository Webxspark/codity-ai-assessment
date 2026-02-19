"""AI Chat service — LLM with autonomous tool-calling for data retrieval.

The LLM is given a set of tools (functions) that let it autonomously query
the database for anomalies, metrics, deployments, and config changes.
When the user asks "Why did p95 latency spike at 14:32?" the LLM will:
  1. Call search_anomalies to find matching anomalies
  2. Call get_anomaly_context to get full context (correlations, metric trend)
  3. Synthesize an answer grounded in real data

Production considerations:
- Tool-calling loop capped at MAX_TOOL_ROUNDS to prevent infinite loops
- Conversation history trimmed to MAX_HISTORY_MSGS
- Tool results are compact JSON
"""

import json
from datetime import datetime, timedelta
from uuid import UUID
from typing import AsyncIterator

from openai import AsyncOpenAI
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.db_models import (
    ChatConversation,
    ChatMessage,
    Anomaly,
    DeploymentLog,
    ConfigChangeLog,
    MetricDataPoint,
)
from app.services.code_context_service import CodeContextService

MAX_HISTORY_MSGS = 30
MAX_TOOL_ROUNDS = 5  # max autonomous data-fetching rounds before forcing a reply

SYSTEM_PROMPT = """You are CodityAI, a senior SRE and observability assistant. You help engineers understand metric anomalies, correlate them with code changes, deployments, and configuration changes, and suggest actionable fixes.

You have access to tools that let you query the system's database. USE THEM PROACTIVELY:
- When the user asks about a specific metric, time, or service — call search_anomalies or query_metric_data to find relevant data.
- When you find an anomaly — call get_anomaly_context to get its full context (correlations with deployments, config changes, metric trends).
- When the user asks about deployments or config changes — call the relevant tool.
- When the user asks about system health — call get_metrics_summary.

CRITICAL INSTRUCTIONS:
1. NEVER say "I don't have data" without first trying to fetch it using your tools.
2. If context is already provided, check it first — but still use tools if you need more detail.
3. Reference specific metrics, timestamps, and values.
4. Provide concrete, actionable technical suggestions (not generic advice).
5. Explain your reasoning step-by-step.
6. Use markdown formatting for clarity.

When analyzing anomalies:
- Explain WHY the metric is anomalous (statistical reasoning from z_score, baseline_mean, baseline_std)
- Explain WHAT likely caused it — correlate with nearby deployments, config changes, or related anomalies
- Suggest HOW to fix or mitigate it (actionable steps)
- Rate your confidence in the root cause assessment

Always ground your answers in the data provided. If after using tools you still don't have enough context, say so explicitly rather than guessing."""

# ── Tool definitions (OpenAI function-calling format) ────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_anomalies",
            "description": (
                "Search for detected anomalies. Use this when the user asks about "
                "a specific metric, service, time range, or anomaly event. "
                "Returns a list of matching anomalies with their details."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "service_name": {
                        "type": "string",
                        "description": "Filter by service name (e.g. 'api-gateway', 'payment-service')",
                    },
                    "metric_name": {
                        "type": "string",
                        "description": "Filter by metric name (e.g. 'latency_p95', 'error_rate', 'cpu_percent')",
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "warning", "info"],
                        "description": "Filter by severity level",
                    },
                    "hours_back": {
                        "type": "number",
                        "description": "How many hours back to search (default: 24)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results to return (default: 10)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_anomaly_context",
            "description": (
                "Get FULL context for a specific anomaly by its ID. Returns correlated "
                "deployments, config changes, related anomalies across services, "
                "and the metric trend around the anomaly. Use this after finding an "
                "anomaly via search_anomalies to get deep analysis data."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "anomaly_id": {
                        "type": "string",
                        "description": "The UUID of the anomaly to get context for",
                    },
                },
                "required": ["anomaly_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_deployments",
            "description": (
                "Get recent deployments (code releases). Returns commit SHA, message, "
                "author, changed files, and timestamp."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "service_name": {
                        "type": "string",
                        "description": "Filter by service name",
                    },
                    "hours_back": {
                        "type": "number",
                        "description": "How many hours back to search (default: 48)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default: 10)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_config_changes",
            "description": (
                "Get recent configuration parameter changes. Shows parameter name, "
                "old value, new value, who changed it, and when."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "service_name": {
                        "type": "string",
                        "description": "Filter by service name",
                    },
                    "hours_back": {
                        "type": "number",
                        "description": "How many hours back to search (default: 48)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default: 10)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_metrics_summary",
            "description": (
                "Get a high-level summary of all services and their metrics: "
                "count, min, max, avg values, and latest timestamp. "
                "Use this for system health overviews."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "service_name": {
                        "type": "string",
                        "description": "Filter by service name (optional)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_metric_data",
            "description": (
                "Query raw metric time-series data points. Use when you need to "
                "see actual metric values at specific times. Returns timestamp+value pairs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "service_name": {
                        "type": "string",
                        "description": "Service name (required)",
                    },
                    "metric_name": {
                        "type": "string",
                        "description": "Metric name (required)",
                    },
                    "hours_back": {
                        "type": "number",
                        "description": "How many hours back (default: 2)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max data points (default: 60)",
                    },
                },
                "required": ["service_name", "metric_name"],
            },
        },
    },
]


class AIChatService:
    """Service for AI-powered chat with anomaly context."""

    def __init__(self, db: AsyncSession):
        self.db = db
        settings = get_settings()
        self.client = AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_API_BASE,
        )
        self.model = settings.OPENAI_MODEL
        self.ctx_service = CodeContextService(db)

    async def get_or_create_conversation(
        self,
        conversation_id: UUID | None = None,
        anomaly_id: UUID | None = None,
    ) -> ChatConversation:
        """Get existing or create new conversation."""
        if conversation_id:
            result = await self.db.execute(
                select(ChatConversation).where(ChatConversation.id == conversation_id)
            )
            conv = result.scalar_one_or_none()
            if conv:
                return conv

        conv = ChatConversation(anomaly_id=anomaly_id)
        self.db.add(conv)
        await self.db.flush()
        return conv

    async def save_message(
        self,
        conversation_id: UUID,
        role: str,
        content: str,
        metadata: dict | None = None,
    ) -> ChatMessage:
        """Save a chat message."""
        msg = ChatMessage(
            conversation_id=conversation_id,
            role=role,
            content=content,
            metadata_=metadata,
        )
        self.db.add(msg)
        await self.db.flush()
        return msg

    async def generate_response(
        self,
        conversation_id: UUID,
        user_message: str,
        anomaly_id: UUID | None = None,
    ) -> AsyncIterator[str]:
        """Generate an AI response with autonomous tool-calling.

        Flow:
        1. Build messages (system prompt + optional pre-attached context + history)
        2. Call LLM with tools — if it returns tool_calls, execute them and loop
        3. When the LLM produces a final text response, yield it
        """

        # Build messages list
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

        # If an anomaly_id is explicitly attached, inject its context directly
        # so the LLM has it immediately (avoids one tool-call round-trip)
        if anomaly_id:
            context = await self.ctx_service.get_full_context_for_anomaly(anomaly_id)
            if context:
                compact = json.dumps(context, default=str, separators=(",", ":"))
                messages.append({
                    "role": "system",
                    "content": f"PRE-ATTACHED ANOMALY CONTEXT:\n{compact}",
                })

        # Add conversation history (trimmed)
        history = await self._get_conversation_history(conversation_id)
        for msg in history:
            messages.append({"role": msg.role, "content": msg.content})

        # Add current user message
        messages.append({"role": "user", "content": user_message})

        # ── Tool-calling loop (streaming throughout) ────────────────
        try:
            for _round in range(MAX_TOOL_ROUNDS):
                stream = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=TOOLS,
                    tool_choice="auto",
                    stream=True,
                    temperature=0.3,
                    max_tokens=4096,
                )

                # Accumulate streamed response — content is yielded live,
                # tool-call deltas are collected for execution.
                content_parts: list[str] = []
                tool_calls_acc: dict[int, dict] = {}  # index → {id, name, arguments}

                async for chunk in stream:
                    delta = chunk.choices[0].delta

                    # Stream text tokens to the user immediately
                    if delta.content:
                        yield delta.content
                        content_parts.append(delta.content)

                    # Accumulate tool-call fragments
                    if delta.tool_calls:
                        for tc_delta in delta.tool_calls:
                            idx = tc_delta.index
                            if idx not in tool_calls_acc:
                                tool_calls_acc[idx] = {"id": "", "name": "", "arguments": ""}
                            if tc_delta.id:
                                tool_calls_acc[idx]["id"] = tc_delta.id
                            if tc_delta.function:
                                if tc_delta.function.name:
                                    tool_calls_acc[idx]["name"] += tc_delta.function.name
                                if tc_delta.function.arguments:
                                    tool_calls_acc[idx]["arguments"] += tc_delta.function.arguments

                # If content was streamed and no tool calls → final answer, done.
                if not tool_calls_acc:
                    return

                # ── Execute tool calls ───────────────────────────────
                # Append the assistant message (with tool_calls) to message history
                assistant_tool_calls = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"]},
                    }
                    for tc in tool_calls_acc.values()
                ]
                messages.append({
                    "role": "assistant",
                    "content": "".join(content_parts) or None,
                    "tool_calls": assistant_tool_calls,
                })

                # Show progress indicator
                tool_names = [tc["name"] for tc in tool_calls_acc.values()]
                friendly = ", ".join(n.replace("_", " ") for n in tool_names)
                yield f"🔍 *Fetching data: {friendly}...*\n\n"

                for tc in tool_calls_acc.values():
                    try:
                        fn_args = json.loads(tc["arguments"])
                    except json.JSONDecodeError:
                        fn_args = {}
                    result = await self._execute_tool(tc["name"], fn_args)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(result, default=str, separators=(",", ":")),
                    })

            # Exhausted MAX_TOOL_ROUNDS — final streaming call (no tools)
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                stream=True,
                temperature=0.3,
                max_tokens=4096,
            )
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

        except Exception as e:
            yield f"\n\n⚠️ Error communicating with the AI model: {str(e)}"

    # ── Tool execution ───────────────────────────────────────────────

    async def _execute_tool(self, name: str, args: dict) -> dict | list:
        """Execute a tool function and return the result."""
        handlers = {
            "search_anomalies": self._tool_search_anomalies,
            "get_anomaly_context": self._tool_get_anomaly_context,
            "get_recent_deployments": self._tool_get_deployments,
            "get_recent_config_changes": self._tool_get_config_changes,
            "get_metrics_summary": self._tool_get_metrics_summary,
            "query_metric_data": self._tool_query_metric_data,
        }
        handler = handlers.get(name)
        if not handler:
            return {"error": f"Unknown tool: {name}"}
        try:
            return await handler(**args)
        except Exception as e:
            return {"error": str(e)}

    async def _tool_search_anomalies(
        self,
        service_name: str | None = None,
        metric_name: str | None = None,
        severity: str | None = None,
        hours_back: float = 24,
        limit: int = 10,
    ) -> list[dict]:
        """Search for anomalies matching criteria."""
        stmt = select(Anomaly)
        conditions = []
        if service_name:
            conditions.append(Anomaly.service_name == service_name)
        if metric_name:
            conditions.append(Anomaly.metric_name == metric_name)
        if severity:
            conditions.append(Anomaly.severity == severity)
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)
        conditions.append(Anomaly.detected_at >= cutoff)
        if conditions:
            stmt = stmt.where(and_(*conditions))
        stmt = stmt.order_by(Anomaly.detected_at.desc()).limit(min(limit, 20))

        result = await self.db.execute(stmt)
        anomalies = result.scalars().all()
        return [
            {
                "id": str(a.id),
                "service_name": a.service_name,
                "metric_name": a.metric_name,
                "detected_at": a.detected_at.isoformat(),
                "severity": a.severity,
                "confidence_score": a.confidence_score,
                "anomaly_type": a.anomaly_type,
                "metric_value": round(a.metric_value, 4),
                "baseline_mean": round(a.baseline_mean, 4) if a.baseline_mean else None,
                "z_score": round(a.z_score, 2) if a.z_score else None,
                "explanation": (a.explanation or "")[:300],
            }
            for a in anomalies
        ]

    async def _tool_get_anomaly_context(self, anomaly_id: str) -> dict:
        """Get full context for one anomaly (correlations, trends)."""
        try:
            uid = UUID(anomaly_id)
        except ValueError:
            return {"error": f"Invalid anomaly ID: {anomaly_id}"}
        ctx = await self.ctx_service.get_full_context_for_anomaly(uid)
        return ctx or {"error": "Anomaly not found"}

    async def _tool_get_deployments(
        self,
        service_name: str | None = None,
        hours_back: float = 48,
        limit: int = 10,
    ) -> list[dict]:
        """Get recent deployments."""
        stmt = select(DeploymentLog)
        conditions = []
        if service_name:
            conditions.append(DeploymentLog.service_name == service_name)
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)
        conditions.append(DeploymentLog.timestamp >= cutoff)
        stmt = stmt.where(and_(*conditions))
        stmt = stmt.order_by(DeploymentLog.timestamp.desc()).limit(min(limit, 20))

        result = await self.db.execute(stmt)
        deps = result.scalars().all()
        return [
            {
                "id": str(d.id),
                "service_name": d.service_name,
                "deployed_at": d.timestamp.isoformat(),
                "commit_sha": d.commit_sha,
                "commit_message": (d.commit_message or "")[:200],
                "author": d.author,
                "changed_files": d.changed_files or [],
            }
            for d in deps
        ]

    async def _tool_get_config_changes(
        self,
        service_name: str | None = None,
        hours_back: float = 48,
        limit: int = 10,
    ) -> list[dict]:
        """Get recent config changes."""
        stmt = select(ConfigChangeLog)
        conditions = []
        if service_name:
            conditions.append(ConfigChangeLog.service_name == service_name)
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)
        conditions.append(ConfigChangeLog.timestamp >= cutoff)
        stmt = stmt.where(and_(*conditions))
        stmt = stmt.order_by(ConfigChangeLog.timestamp.desc()).limit(min(limit, 20))

        result = await self.db.execute(stmt)
        cfgs = result.scalars().all()
        return [
            {
                "id": str(c.id),
                "service_name": c.service_name,
                "parameter": c.parameter,
                "old_value": c.old_value,
                "new_value": c.new_value,
                "changed_by": c.changed_by,
                "changed_at": c.timestamp.isoformat(),
            }
            for c in cfgs
        ]

    async def _tool_get_metrics_summary(
        self,
        service_name: str | None = None,
    ) -> list[dict]:
        """Get aggregated metrics summary."""
        stmt = select(
            MetricDataPoint.service_name,
            MetricDataPoint.metric_name,
            func.count(MetricDataPoint.id).label("count"),
            func.min(MetricDataPoint.value).label("min_value"),
            func.max(MetricDataPoint.value).label("max_value"),
            func.avg(MetricDataPoint.value).label("avg_value"),
            func.max(MetricDataPoint.timestamp).label("latest_timestamp"),
        ).group_by(MetricDataPoint.service_name, MetricDataPoint.metric_name)

        if service_name:
            stmt = stmt.where(MetricDataPoint.service_name == service_name)
        stmt = stmt.limit(50)

        result = await self.db.execute(stmt)
        return [
            {
                "service_name": r.service_name,
                "metric_name": r.metric_name,
                "count": r.count,
                "min": round(float(r.min_value), 4),
                "max": round(float(r.max_value), 4),
                "avg": round(float(r.avg_value), 4),
                "latest_at": r.latest_timestamp.isoformat() if r.latest_timestamp else None,
            }
            for r in result.all()
        ]

    async def _tool_query_metric_data(
        self,
        service_name: str,
        metric_name: str,
        hours_back: float = 2,
        limit: int = 60,
    ) -> list[dict]:
        """Query raw metric data points."""
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)
        stmt = (
            select(MetricDataPoint)
            .where(
                and_(
                    MetricDataPoint.service_name == service_name,
                    MetricDataPoint.metric_name == metric_name,
                    MetricDataPoint.timestamp >= cutoff,
                )
            )
            .order_by(MetricDataPoint.timestamp.asc())
            .limit(min(limit, 200))
        )
        result = await self.db.execute(stmt)
        points = result.scalars().all()
        return [
            {
                "timestamp": p.timestamp.isoformat(),
                "value": round(p.value, 4),
            }
            for p in points
        ]

    async def _get_conversation_history(self, conversation_id: UUID) -> list[ChatMessage]:
        """Return conversation history trimmed to MAX_HISTORY_MSGS most-recent.

        This prevents unbounded growth from blowing the model's context window.
        """
        result = await self.db.execute(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(MAX_HISTORY_MSGS)
        )
        # Reverse so chronological order is preserved
        return list(reversed(result.scalars().all()))
