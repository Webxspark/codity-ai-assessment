"""AI Chat endpoints with SSE streaming."""

import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.db_models import ChatConversation, ChatMessage
from app.models.schemas import ChatMessageIn, ChatConversationOut
from app.services.ai_chat_service import AIChatService

router = APIRouter()


@router.post("")
async def send_message(
    payload: ChatMessageIn,
    db: AsyncSession = Depends(get_db),
):
    """Send a chat message and receive an SSE streamed AI response."""
    chat_service = AIChatService(db)

    # Get or create conversation
    conversation = await chat_service.get_or_create_conversation(
        conversation_id=payload.conversation_id,
        anomaly_id=payload.anomaly_id,
    )

    # Save user message
    await chat_service.save_message(
        conversation_id=conversation.id,
        role="user",
        content=payload.message,
    )

    # Build context and stream response
    async def event_stream():
        full_response = ""
        try:
            async for chunk in chat_service.generate_response(
                conversation_id=conversation.id,
                user_message=payload.message,
                anomaly_id=payload.anomaly_id,
            ):
                full_response += chunk
                data = json.dumps({
                    "type": "chunk",
                    "content": chunk,
                    "conversation_id": str(conversation.id),
                })
                yield f"data: {data}\n\n"

            # Save assistant message after streaming completes
            await chat_service.save_message(
                conversation_id=conversation.id,
                role="assistant",
                content=full_response,
            )

            # Send done signal
            done_data = json.dumps({
                "type": "done",
                "conversation_id": str(conversation.id),
            })
            yield f"data: {done_data}\n\n"
        except Exception as e:
            error_data = json.dumps({
                "type": "error",
                "content": str(e),
            })
            yield f"data: {error_data}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{conversation_id}", response_model=ChatConversationOut)
async def get_conversation(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get conversation history."""
    stmt = (
        select(ChatConversation)
        .options(selectinload(ChatConversation.messages))
        .where(ChatConversation.id == conversation_id)
    )
    result = await db.execute(stmt)
    conversation = result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@router.get("", response_model=list[ChatConversationOut])
async def list_conversations(
    db: AsyncSession = Depends(get_db),
):
    """List all conversations."""
    stmt = (
        select(ChatConversation)
        .options(selectinload(ChatConversation.messages))
        .order_by(ChatConversation.updated_at.desc())
        .limit(50)
    )
    result = await db.execute(stmt)
    return result.scalars().all()
