"""AI Chat service — builds context, calls LLM via OpenAI-compatible API, streams responses."""

import json
from uuid import UUID
from typing import AsyncIterator

from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.models.db_models import ChatConversation, ChatMessage, Anomaly
from app.services.code_context_service import CodeContextService

SYSTEM_PROMPT = """You are CodityAI, a senior SRE and observability assistant. You help engineers understand metric anomalies, correlate them with code changes, and suggest actionable fixes.

Your responses must:
1. Reference specific metrics, timestamps, and values from the provided context
2. Reference specific code changes, deployments, or config changes when available
3. Provide concrete, actionable technical suggestions (not generic advice)
4. Explain your reasoning step-by-step
5. Be concise but thorough
6. Use markdown formatting for clarity

When analyzing anomalies:
- Explain WHY the metric is anomalous (statistical reasoning)
- Explain WHAT likely caused it (code/config correlation)
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

        # Add anomaly context if available
        context = await self._build_context(anomaly_id, user_message)
        if context:
            messages.append({
                "role": "system",
                "content": f"Here is the relevant anomaly and system context:\n\n```json\n{json.dumps(context, indent=2)}\n```",
            })

        # Add conversation history
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
                max_tokens=2048,
            )

            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            yield f"\n\n⚠️ Error communicating with the AI model: {str(e)}"

    async def _build_context(self, anomaly_id: UUID | None, user_message: str) -> dict | None:
        """Build contextual information for the LLM."""
        if anomaly_id:
            return await self.ctx_service.get_full_context_for_anomaly(anomaly_id)

        # If no specific anomaly, try to find recent anomalies to provide context
        result = await self.db.execute(
            select(Anomaly).order_by(Anomaly.detected_at.desc()).limit(5)
        )
        recent = result.scalars().all()
        if recent:
            return {
                "recent_anomalies": [
                    {
                        "id": str(a.id),
                        "service_name": a.service_name,
                        "metric_name": a.metric_name,
                        "detected_at": a.detected_at.isoformat(),
                        "severity": a.severity,
                        "metric_value": a.metric_value,
                        "anomaly_type": a.anomaly_type,
                        "explanation": a.explanation,
                    }
                    for a in recent
                ]
            }
        return None

    async def _get_conversation_history(self, conversation_id: UUID) -> list[ChatMessage]:
        """Get previous messages in the conversation (excluding current)."""
        result = await self.db.execute(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.created_at.asc())
        )
        return list(result.scalars().all())
