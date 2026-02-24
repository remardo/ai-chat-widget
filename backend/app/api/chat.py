"""Chat API endpoints."""

import asyncio
import logging
import re
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Optional, List
from ..services.storage.base import Message
from ..services.ai_service import ai_service
from ..services.supabase_catalog import supabase_catalog_service
from ..services.telegram import telegram_service
from ..services.security import security_service
from ..config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

# Manager takeover mode: when active for a session, bot does not answer as AI.
manager_takeover_sessions = set()

# Prevent duplicate lead notifications per session.
lead_notified_sessions = set()

PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\-\s\(\)]{8,}\d)")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
CONTACT_INTENT_RE = re.compile(
    r"(адрес|шоурум|шоу-рум|офис|где\s+наход|где\s+располож|контакт|телефон)",
    re.IGNORECASE,
)

UNCERTAIN_REPLY_PATTERNS = [
    "у меня нет информации",
    "у меня нет доступа",
    "не могу ответить",
    "не знаю",
    "не располагаю информацией",
]


def _extract_lead(text: str) -> Dict[str, List[str]]:
    phones = [p.strip() for p in PHONE_RE.findall(text or "")]
    emails = [e.strip() for e in EMAIL_RE.findall(text or "")]
    return {"phones": phones, "emails": emails}


def _build_safe_contact_reply(knowledge_text: str) -> Optional[str]:
    """Build deterministic contact reply from knowledge markdown."""
    if not knowledge_text:
        return None

    phone_match = re.search(r"Телефон:\s*([^\n\r]+)", knowledge_text, re.IGNORECASE)
    sales_match = re.search(r"Адрес\s+салона\s+продаж.*?:\s*([^\n\r]+)", knowledge_text, re.IGNORECASE)
    factory_match = re.search(r"Адрес\s+шоурума\s+на\s+фабрике:\s*([^\n\r]+)", knowledge_text, re.IGNORECASE)
    legacy_match = re.search(r"Адрес\s+шоурума:\s*([^\n\r]+)", knowledge_text, re.IGNORECASE)

    if not phone_match and not sales_match and not factory_match and not legacy_match:
        return None

    phone = phone_match.group(1).strip() if phone_match else "уточняйте у менеджера"
    sales_address = sales_match.group(1).strip() if sales_match else ""
    factory_address = factory_match.group(1).strip() if factory_match else ""

    # Backward compatibility: split old merged address into two known points.
    if (not sales_address or not factory_address) and legacy_match:
        merged = legacy_match.group(1).strip()
        if "Дорожный проезд" in merged:
            sales_address = sales_address or "Чебоксары, Московский проспект, 40Б, павильон 20"
            factory_address = factory_address or "Чебоксары, Дорожный проезд, 6Б"
        else:
            sales_address = sales_address or merged
            factory_address = factory_address or ""

    schedule_lines = re.findall(
        r"^\s*-\s*(Понедельник[^\n\r]*|Суббота[^\n\r]*|Воскресенье[^\n\r]*)",
        knowledge_text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    schedule = ", ".join(line.strip() for line in schedule_lines[:3]) if schedule_lines else ""

    points = []
    if sales_address:
        points.append(f"Салон продаж (Северная Ярмарка): {sales_address}")
    if factory_address:
        points.append(f"Шоурум на фабрике: {factory_address}")

    if not points:
        points.append("Адреса уточняйте у менеджера")

    reply = "Можно посмотреть двери в двух точках: " + " ".join(
        [f"{idx + 1}) {p}." for idx, p in enumerate(points)]
    )
    reply += f" Телефон: {phone}."
    if schedule:
        reply += f" График: {schedule}."
    return reply


def _needs_manager_handoff(ai_reply: str) -> bool:
    reply = (ai_reply or "").lower()
    return any(pattern in reply for pattern in UNCERTAIN_REPLY_PATTERNS)


def _run_in_background(coro, label: str):
    """Fire-and-forget helper for non-critical side effects."""
    task = asyncio.create_task(coro)

    def _done(t: asyncio.Task):
        try:
            t.result()
        except Exception as e:
            logger.warning(f"Background task failed ({label}): {e}")

    task.add_done_callback(_done)


class PageContext(BaseModel):
    """Page context from frontend."""

    url: str = ""
    title: str = ""
    meta_description: Optional[str] = ""
    headings: Optional[Dict[str, List[str]]] = {}
    selected_text: Optional[str] = ""
    main_content: Optional[str] = ""


class ChatRequest(BaseModel):
    """Chat message request."""

    session_id: str
    message: str
    page_context: Optional[PageContext] = None


class ChatResponse(BaseModel):
    """Chat message response."""

    reply: str
    session_id: str
    blocked: bool = False
    attack_detected: Optional[str] = None
    manager_handoff: bool = False


@router.post("/message", response_model=ChatResponse)
async def send_message(request: ChatRequest):
    """
    Send a message and get AI response.

    This endpoint:
    1. Validates request (security checks)
    2. Saves user message with page context
    3. Loads conversation history
    4. Builds system prompt with page context and knowledge base
    5. Sends to AI
    6. Saves AI response
    7. Returns reply
    """
    from ..main import storage, knowledge_base

    # ==========================================
    # SECURITY CHECKS
    # ==========================================
    is_valid, error_message, attack_info = security_service.validate_request(
        message=request.message,
        session_id=request.session_id
    )

    # If completely blocked (banned or rate limited)
    if not is_valid and not attack_info:
        return ChatResponse(
            reply=error_message,
            session_id=request.session_id,
            blocked=True
        )

    # If attack detected
    if attack_info:
        page_url = request.page_context.url if request.page_context else "unknown"

        # Log attack
        logger.warning(
            f"Attack detected: {attack_info['type']} | "
            f"Session: {request.session_id[:20]}... | "
            f"Page: {page_url} | "
            f"Severity: {attack_info['severity']}"
        )

        # Send Telegram alert for high/critical attacks
        if attack_info["severity"] in ["high", "critical"]:
            await telegram_service.send_alert(
                message=f"Тип: {attack_info['type']}\n"
                        f"Severity: {attack_info['severity']}\n"
                        f"Описание: {attack_info['description']}\n"
                        f"Strikes: {attack_info.get('strikes', 0)}/{attack_info.get('max_strikes', 3)}",
                alert_type="escalation",
                session_id=request.session_id,
                page_url=page_url,
            )

        # If banned after this attack
        if not is_valid:
            return ChatResponse(
                reply=error_message,
                session_id=request.session_id,
                blocked=True,
                attack_detected=attack_info["type"]
            )

        # Return blocked response (but don't ban yet)
        blocked_response = security_service.get_blocked_response(attack_info)
        return ChatResponse(
            reply=blocked_response,
            session_id=request.session_id,
            blocked=False,
            attack_detected=attack_info["type"]
        )

    # ==========================================
    # DETECT ESCALATION / FEEDBACK
    # ==========================================
    message_lower = request.message.lower()
    page_url = request.page_context.url if request.page_context else "unknown"

    # Escalation keywords (user wants human help) - use word stems for flexibility
    escalation_keywords = [
        "человек", "оператор", "менеджер", "поддержк",  # want human
        "не работа", "сломал", "баг", "ошибк",  # something broken
        "не могу", "не получ", "помоги", "срочно",  # need help
        "talk to human", "real person", "support", "help me"
    ]

    # Positive feedback keywords
    positive_keywords = [
        "спасибо", "благодар",  # thanks
        "отлично", "супер", "класс", "молодец", "круто", "здорово", "классн",  # great
        "помогл", "получил", "понял", "разобрал",  # it worked
        "thank", "great", "awesome", "helpful", "works", "nice", "cool"
    ]

    # Negative feedback keywords
    negative_keywords = [
        "плохо", "ужасн", "отстой", "фигн", "хрен",  # bad
        "не помог", "бесполезн", "не понима", "тупой", "глуп", "идиот",  # useless
        "не работа", "сломал",  # broken (also triggers escalation)
        "useless", "stupid", "bad", "terrible", "suck", "hate"
    ]

    # Check for escalation (broken things, need human)
    is_escalation = any(kw in message_lower for kw in escalation_keywords)
    is_negative = any(kw in message_lower for kw in negative_keywords)
    is_positive = any(kw in message_lower for kw in positive_keywords)

    # Don't send positive if also negative (sarcasm protection)
    if is_positive and is_negative:
        is_positive = False

    if is_escalation:
        print(f"🚨 Escalation detected: {request.message[:50]}...")
        try:
            _run_in_background(
                telegram_service.send_escalation(
                    reason="Пользователь запрашивает помощь или сообщает о проблеме",
                    conversation_summary=request.message[:300],
                    session_id=request.session_id,
                    page_url=page_url,
                ),
                "escalation_keywords",
            )
        except Exception as e:
            print(f"Telegram escalation ERROR: {e}")

    elif is_negative:
        print(f"😞 Negative feedback detected: {request.message[:50]}...")
        try:
            _run_in_background(
                telegram_service.send_feedback(
                    text=request.message[:300],
                    sentiment="negative",
                    session_id=request.session_id,
                    page_url=page_url,
                ),
                "negative_feedback",
            )
        except Exception as e:
            print(f"Telegram negative feedback ERROR: {e}")

    elif is_positive:
        print(f"😊 Positive feedback detected: {request.message[:50]}...")
        try:
            _run_in_background(
                telegram_service.send_feedback(
                    text=request.message[:300],
                    sentiment="positive",
                    session_id=request.session_id,
                    page_url=page_url,
                ),
                "positive_feedback",
            )
        except Exception as e:
            print(f"Telegram positive feedback ERROR: {e}")

    # ==========================================
    # NORMAL MESSAGE PROCESSING
    # ==========================================
    try:
        # Save user message with page context
        page_context_dict = request.page_context.dict() if request.page_context else {}

        user_message = Message(
            session_id=request.session_id,
            role="user",
            content=request.message,
            page_context=page_context_dict,
        )
        await storage.save_message(user_message)

        # Lead capture from user message (phone/email)
        lead = _extract_lead(request.message)
        if request.session_id not in lead_notified_sessions and (lead["phones"] or lead["emails"]):
            lead_parts = []
            if lead["phones"]:
                lead_parts.append("Телефон: " + ", ".join(lead["phones"]))
            if lead["emails"]:
                lead_parts.append("Email: " + ", ".join(lead["emails"]))
            lead_parts.append(f"Сообщение: {request.message[:500]}")

            _run_in_background(
                telegram_service.send_lead(
                    lead_text="\n".join(lead_parts),
                    session_id=request.session_id,
                    page_url=page_url,
                    user_email=lead["emails"][0] if lead["emails"] else None,
                ),
                "lead_capture",
            )
            lead_notified_sessions.add(request.session_id)

        # If manager takeover is active, skip AI and wait for manager response.
        if request.session_id in manager_takeover_sessions:
            manager_reply = "Подключаю менеджера. Он ответит вам в этом чате в ближайшее время."
            _run_in_background(
                telegram_service.send_alert(
                    message=f"Новое сообщение в takeover-сессии:\n\n{request.message[:700]}",
                    alert_type="info",
                    session_id=request.session_id,
                    page_url=page_url,
                ),
                "takeover_new_user_message",
            )

            if settings.TELEGRAM_TRANSCRIPT_ENABLED:
                _run_in_background(
                    telegram_service.send_chat_transcript_turn(
                        session_id=request.session_id,
                        page_url=page_url,
                        user_message=request.message,
                        assistant_message=manager_reply,
                    ),
                    "takeover_transcript_turn",
                )

            return ChatResponse(
                reply=manager_reply,
                session_id=request.session_id,
                manager_handoff=True,
            )

        # Load conversation history
        history = await storage.get_messages(request.session_id, limit=20)

        # Build system prompt with retrieved knowledge context
        knowledge_context = knowledge_base.get_context_for_query(request.message)
        live_data_context = await supabase_catalog_service.get_live_context(request.message)

        # Deterministic contacts/addresses response to avoid LLM hallucinations.
        if CONTACT_INTENT_RE.search(request.message):
            safe_reply = _build_safe_contact_reply(knowledge_base.get_content())
            if safe_reply:
                assistant_message = Message(
                    session_id=request.session_id, role="assistant", content=safe_reply, page_context=page_context_dict
                )
                await storage.save_message(assistant_message)
                if settings.TELEGRAM_TRANSCRIPT_ENABLED:
                    _run_in_background(
                        telegram_service.send_chat_transcript_turn(
                            session_id=request.session_id,
                            page_url=page_url,
                            user_message=request.message,
                            assistant_message=safe_reply,
                        ),
                        "chat_transcript_turn_contacts",
                    )
                return ChatResponse(reply=safe_reply, session_id=request.session_id, manager_handoff=False)

        system_prompt = ai_service.build_system_prompt(
            page_context=page_context_dict,
            knowledge_base=knowledge_context,
            live_data=live_data_context,
        )

        # Prepare messages for AI
        messages = [{"role": "system", "content": system_prompt}]

        # Add conversation history
        for msg in history:
            messages.append({"role": msg.role, "content": msg.content})

        # Get AI response (guard against provider returning empty content)
        ai_reply = await ai_service.chat_completion(messages)
        if not (ai_reply or "").strip():
            logger.warning("AI provider returned empty content, retrying once")
            ai_reply = await ai_service.chat_completion(messages)
        if not (ai_reply or "").strip():
            ai_reply = (
                "Не удалось получить полный ответ от AI-сервиса. "
                "Скажите, пожалуйста, какой именно диапазон цены вам нужен, "
                "и я сразу дам конкретные варианты."
            )

        # Save AI response
        assistant_message = Message(
            session_id=request.session_id, role="assistant", content=ai_reply, page_context=page_context_dict
        )
        await storage.save_message(assistant_message)

        if settings.TELEGRAM_TRANSCRIPT_ENABLED:
            _run_in_background(
                telegram_service.send_chat_transcript_turn(
                    session_id=request.session_id,
                    page_url=page_url,
                    user_message=request.message,
                    assistant_message=ai_reply,
                ),
                "chat_transcript_turn",
            )

        # Auto handoff if assistant signals uncertainty
        manager_handoff = False
        if _needs_manager_handoff(ai_reply):
            manager_handoff = True
            manager_takeover_sessions.add(request.session_id)
            _run_in_background(
                telegram_service.send_escalation(
                    reason="Бот неуверен в ответе / вне компетенции. Нужен менеджер.",
                    conversation_summary=f"User: {request.message[:500]}\nAssistant: {ai_reply[:500]}",
                    session_id=request.session_id,
                    page_url=page_url,
                ),
                "auto_handoff_escalation",
            )

        return ChatResponse(
            reply=ai_reply,
            session_id=request.session_id,
            manager_handoff=manager_handoff,
        )

    except Exception as e:
        logger.error(f"Chat error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Chat failed: {str(e)}")


@router.delete("/session/{session_id}")
async def delete_session(session_id: str):
    """Delete a chat session."""
    from ..main import storage

    try:
        await storage.delete_session(session_id)
        return {"message": "Session deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions")
async def get_sessions():
    """Get all session IDs."""
    from ..main import storage

    try:
        sessions = await storage.get_all_sessions()
        return {"sessions": sessions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/alert")
async def send_alert(alert_type: str, message: str):
    """
    Send alert to Telegram.

    Alert types: bug, escalation, suggestion, feedback
    """
    try:
        await telegram_service.send_alert(message, alert_type)
        return {"message": "Alert sent"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history/{session_id}")
async def get_history(session_id: str, limit: int = 50):
    """Get chat history for a session."""
    from ..main import storage

    try:
        messages = await storage.get_messages(session_id, limit)
        return {
            "session_id": session_id,
            "messages": [
                {"role": msg.role, "content": msg.content, "timestamp": msg.timestamp.isoformat()} for msg in messages
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ManagerTakeoverRequest(BaseModel):
    """Manager takeover toggle request."""

    session_id: str


class ManagerReplyRequest(BaseModel):
    """Manual manager reply into chat history."""

    session_id: str
    message: str


@router.post("/manager/takeover")
async def manager_takeover(req: ManagerTakeoverRequest):
    """Enable manager takeover for a session."""
    manager_takeover_sessions.add(req.session_id)
    return {"session_id": req.session_id, "takeover": True}


@router.post("/manager/release")
async def manager_release(req: ManagerTakeoverRequest):
    """Disable manager takeover for a session."""
    manager_takeover_sessions.discard(req.session_id)
    return {"session_id": req.session_id, "takeover": False}


@router.get("/manager/takeover")
async def manager_takeover_list():
    """List sessions where manager takeover is active."""
    return {"sessions": sorted(manager_takeover_sessions)}


@router.post("/manager/reply")
async def manager_reply(req: ManagerReplyRequest):
    """Inject manager response into chat as assistant message."""
    from ..main import storage

    try:
        manager_message = Message(
            session_id=req.session_id,
            role="assistant",
            content=f"Менеджер: {req.message}",
            page_context={},
        )
        await storage.save_message(manager_message)

        await telegram_service.send_alert(
            message=f"Менеджер ответил клиенту:\n\n{req.message[:700]}",
            alert_type="info",
            session_id=req.session_id,
        )

        return {"message": "Manager reply saved", "session_id": req.session_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/telegram/test")
async def test_telegram():
    """
    Test Telegram connection.

    Returns bot info if configured correctly.
    """
    result = await telegram_service.test_connection()

    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Telegram not configured"))

    return result


@router.post("/telegram/send-test")
async def send_test_message():
    """
    Send a test message to Telegram.

    Use this to verify alerts are working.
    """
    if not telegram_service.enabled:
        raise HTTPException(status_code=400, detail="Telegram not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")

    success = await telegram_service.send_alert(
        message="🧪 Test message from AI Chat Widget!\n\nIf you see this, Telegram alerts are working correctly.",
        alert_type="success",
    )

    if success:
        return {"message": "Test message sent successfully"}
    else:
        raise HTTPException(status_code=500, detail="Failed to send test message")


@router.get("/security/status")
async def security_status():
    """Get security service status (for debugging)."""
    return {
        "max_message_length": security_service.max_message_length,
        "max_requests_per_minute": security_service.max_requests_per_minute,
        "max_requests_per_hour": security_service.max_requests_per_hour,
        "ban_duration_minutes": security_service.ban_duration_minutes,
        "max_strikes": security_service.max_strikes,
        "active_bans": len(security_service._banned_sessions),
    }


@router.get("/supabase/status")
async def supabase_status():
    """Get Supabase live-data integration status."""
    try:
        return await supabase_catalog_service.check_connection()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/knowledge/reload")
async def reload_knowledge():
    """Reload markdown knowledge files and rebuild retrieval index/fallback state."""
    from ..main import knowledge_base

    try:
        knowledge_base.reload()
        return {"message": "Knowledge reloaded"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
