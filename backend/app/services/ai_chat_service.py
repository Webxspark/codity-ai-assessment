"""AI Chat service — builds context, calls LLM via OpenAI-compatible API, streams responses.

Production considerations:
- Token budgeting: conversation history is trimmed to MAX_HISTORY_MSGS
  (most recent) so the combined prompt stays within model limits.
- Context formatting: JSON context is compact (no indent) and
  heavy fields (detection_details) are excluded to save tokens.
- Fallback context: when no anomaly_id is provided, recent anomalies
  plus recent deployments/config changes are included.
"""

import json
from uuid import UUID
from typing import AsyncIterator

from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.models.db_models import (
    ChatConversation,
    ChatMessage,
    Anomaly,
    DeploymentLog,
    ConfigChangeLog,
)
from app.services.code_context_service import CodeContextService

# Maximum conversation history messages to include (user+assistant pairs).
# With ~2 KB per turn, 30 turns ≈ 60 KB ≈ ~15K tokens — safe for 128K models.
MAX_HISTORY_MSGS = 30

SYSTEM_PROMPT = """You are CodityAI, a senior SRE and observability assistant. You help engineers understand metric anomalies, correlate them with code changes, deployments, and configuration changes, and suggest actionable fixes.

CRITICAL INSTRUCTIONS:
1. **Always check the provided context carefully** — look at nearby_deployments, nearby_config_changes, related_anomalies, and metric_trend_around_anomaly sections.
2. If config changes or deployments are present in the context, you MUST reference them in your analysis. Do NOT say "no changes found" if the context contains changes.
3. Reference specific metrics, timestamps, and values from the provided context.
4. Provide concrete, actionable technical suggestions (not generic advice).
5. Explain your reasoning step-by-step.
6. Use markdown formatting for clarity.

When analyzing anomalies:
- Explain WHY the metric is anomalous (statistical reasoning from z_score, baseline_mean, baseline_std)
- Explain WHAT likely caused it — correlate with nearby deployments, config changes, or related anomalies listed in the context
- Suggest HOW to fix or mitigate it (actionable steps)
- Rate your confidence in the root cause assessment

Always ground your answers in the data provided. If you don't have enough context, say so explicitly rather than guessing."""


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
        """Generate a streaming AI response with full context."""

        # Build messages list
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        # Add anomaly context — compact JSON (no indent) to save tokens
        context = await self._build_context(anomaly_id, user_message)
        if context:
            compact = json.dumps(context, default=str, separators=(",", ":"))
            messages.append({
                "role": "system",
                "content": f"ANOMALY & SYSTEM CONTEXT (reference this data in your answer):\n{compact}",
            })

        # Add conversation history (trimmed to MAX_HISTORY_MSGS most-recent)
        history = await self._get_conversation_history(conversation_id)
        for msg in history:
            messages.append({"role": msg.role, "content": msg.content})

        # Add current user message
        messages.append({"role": "user", "content": user_message})

        # Stream from LLM
        try:
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

    async def _build_context(self, anomaly_id: UUID | None, user_message: str) -> dict | None:
        """Build contextual information for the LLM.

        When an anomaly_id is given, returns full context from CodeContextService.
        Otherwise, returns recent anomalies *plus* recent deployments and config
        changes so the LLM can still reason about what happened.
        """
        if anomaly_id:
            return await self.ctx_service.get_full_context_for_anomaly(anomaly_id)

        # ---- Fallback: no specific anomaly ----
        ctx: dict = {}

        # Recent anomalies
        result = await self.db.execute(
            select(Anomaly).order_by(Anomaly.detected_at.desc()).limit(5)
        )
        recent = result.scalars().all()
        if recent:
            ctx["recent_anomalies"] = [
                {
                    "id": str(a.id),
                    "service_name": a.service_name,
                    "metric_name": a.metric_name,
                    "detected_at": a.detected_at.isoformat(),
                    "severity": a.severity,
                    "metric_value": a.metric_value,
                    "anomaly_type": a.anomaly_type,
                    "explanation": (a.explanation or "")[:300],
                }
                for a in recent
            ]

        # Recent deployments (last 10)
        dep_result = await self.db.execute(
            select(DeploymentLog).order_by(DeploymentLog.timestamp.desc()).limit(10)
        )
        deps = dep_result.scalars().all()
        if deps:
            ctx["recent_deployments"] = [
                {
                    "service_name": d.service_name,
                    "deployed_at": d.timestamp.isoformat(),
                    "commit_sha": d.commit_sha,
                    "commit_message": (d.commit_message or "")[:200],
                }
                for d in deps
            ]

        # Recent config changes (last 10)
        cfg_result = await self.db.execute(
            select(ConfigChangeLog).order_by(ConfigChangeLog.timestamp.desc()).limit(10)
        )
        cfgs = cfg_result.scalars().all()
        if cfgs:
            ctx["recent_config_changes"] = [
                {
                    "service_name": c.service_name,
                    "parameter": c.parameter,
                    "old_value": c.old_value,
                    "new_value": c.new_value,
                    "changed_at": c.timestamp.isoformat(),
                }
                for c in cfgs
            ]

        return ctx or None

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
