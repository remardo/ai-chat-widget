"""Telegram alerts service."""

import httpx
import logging
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class TelegramService:
    """Send alerts to Telegram."""

    def __init__(self, bot_token: Optional[str] = None, chat_id: Optional[str] = None):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._enabled = None  # Lazy evaluation

    @property
    def enabled(self) -> bool:
        """Check if Telegram is enabled (lazy load from settings)."""
        if self._enabled is None:
            if self.bot_token and self.chat_id:
                self._enabled = True
            else:
                # Try to load from settings
                try:
                    from ..config import settings
                    self.bot_token = settings.TELEGRAM_BOT_TOKEN
                    self.chat_id = settings.TELEGRAM_CHAT_ID
                    self._enabled = bool(self.bot_token and self.chat_id)
                except Exception:
                    self._enabled = False
        return self._enabled

    def _escape_markdown(self, text: str) -> str:
        """Escape special Markdown characters."""
        # Characters that need escaping in Telegram MarkdownV2
        special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
        for char in special_chars:
            text = text.replace(char, f'\\{char}')
        return text

    async def send_message(self, text: str, parse_mode: str = None) -> bool:
        """
        Send raw message to Telegram.

        Args:
            text: Message text
            parse_mode: Optional parse mode (Markdown, MarkdownV2, HTML)

        Returns:
            True if sent successfully, False otherwise
        """
        if not self.enabled:
            logger.debug("Telegram not enabled, skipping message")
            return False

        try:
            payload = {
                "chat_id": self.chat_id,
                "text": text,
            }
            if parse_mode:
                payload["parse_mode"] = parse_mode

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                    json=payload,
                )

                if response.status_code == 200:
                    logger.info(f"Telegram message sent successfully")
                    return True
                else:
                    error_data = response.json()
                    logger.error(f"Telegram API error: {response.status_code} - {error_data}")
                    return False

        except httpx.TimeoutException:
            logger.error("Telegram request timeout")
            return False
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False

    async def send_alert(
        self,
        message: str,
        alert_type: str = "info",
        session_id: Optional[str] = None,
        page_url: Optional[str] = None,
        user_email: Optional[str] = None,
    ) -> bool:
        """
        Send formatted alert to Telegram.

        Args:
            message: Alert message
            alert_type: Type of alert (bug, escalation, suggestion, feedback, info)
            session_id: Optional session ID
            page_url: Optional page URL where alert originated
            user_email: Optional user email

        Returns:
            True if sent successfully, False otherwise
        """
        if not self.enabled:
            logger.debug("Telegram not enabled, skipping alert")
            return False

        emoji_map = {
            "bug": "🐛",
            "escalation": "🚨",
            "suggestion": "💡",
            "feedback": "💬",
            "info": "ℹ️",
            "error": "❌",
            "success": "✅",
        }

        emoji = emoji_map.get(alert_type, "ℹ️")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Build message parts
        parts = [
            f"{emoji} {alert_type.upper()}",
            f"⏰ {timestamp}",
            "",
            message,
        ]

        # Add optional context
        if session_id:
            parts.append(f"\n📍 Session: {session_id[:20]}...")
        if page_url:
            parts.append(f"🔗 Page: {page_url}")
        if user_email:
            parts.append(f"👤 User: {user_email}")

        formatted_message = "\n".join(parts)

        # Send without Markdown to avoid escaping issues
        return await self.send_message(formatted_message)

    async def send_bug_report(
        self,
        description: str,
        severity: str = "medium",
        session_id: Optional[str] = None,
        page_url: Optional[str] = None,
        user_email: Optional[str] = None,
        screenshot_url: Optional[str] = None,
    ) -> bool:
        """Send bug report alert."""
        severity_emoji = {
            "low": "🟢",
            "medium": "🟡",
            "high": "🟠",
            "critical": "🔴",
        }

        message = f"{severity_emoji.get(severity, '🟡')} Severity: {severity.upper()}\n\n{description}"

        if screenshot_url:
            message += f"\n\n📸 Screenshot: {screenshot_url}"

        return await self.send_alert(
            message=message,
            alert_type="bug",
            session_id=session_id,
            page_url=page_url,
            user_email=user_email,
        )

    async def send_escalation(
        self,
        reason: str,
        conversation_summary: Optional[str] = None,
        session_id: Optional[str] = None,
        page_url: Optional[str] = None,
        user_email: Optional[str] = None,
    ) -> bool:
        """Send escalation alert."""
        message = f"Reason: {reason}"

        if conversation_summary:
            # Truncate if too long
            summary = conversation_summary[:500]
            if len(conversation_summary) > 500:
                summary += "..."
            message += f"\n\n💬 Conversation:\n{summary}"

        return await self.send_alert(
            message=message,
            alert_type="escalation",
            session_id=session_id,
            page_url=page_url,
            user_email=user_email,
        )

    async def send_feedback(
        self,
        text: str,
        sentiment: str = "neutral",
        session_id: Optional[str] = None,
        page_url: Optional[str] = None,
        user_email: Optional[str] = None,
    ) -> bool:
        """Send feedback alert."""
        sentiment_emoji = {
            "positive": "😊",
            "negative": "😞",
            "neutral": "😐",
        }

        message = f"{sentiment_emoji.get(sentiment, '😐')} Sentiment: {sentiment}\n\n{text}"

        return await self.send_alert(
            message=message,
            alert_type="feedback",
            session_id=session_id,
            page_url=page_url,
            user_email=user_email,
        )

    async def send_chat_transcript_turn(
        self,
        *,
        session_id: str,
        page_url: Optional[str],
        user_message: str,
        assistant_message: str,
    ) -> bool:
        """Send one full chat turn (user + assistant) to Telegram group."""
        visitor = session_id or "unknown"
        user_text = (user_message or "").strip()[:2000]
        assistant_text = (assistant_message or "").strip()[:2000]

        text = (
            "🧾 CHAT TRANSCRIPT\n"
            f"👤 Visitor: {visitor}\n"
            f"🔗 Page: {page_url or 'unknown'}\n\n"
            f"🙋 Клиент:\n{user_text}\n\n"
            f"🤖 Бот:\n{assistant_text}"
        )
        return await self.send_message(text)

    async def send_lead(
        self,
        lead_text: str,
        session_id: Optional[str] = None,
        page_url: Optional[str] = None,
        user_email: Optional[str] = None,
    ) -> bool:
        """Send lead capture notification."""
        return await self.send_alert(
            message=f"Новый лид:\n\n{lead_text}",
            alert_type="success",
            session_id=session_id,
            page_url=page_url,
            user_email=user_email,
        )

    async def test_connection(self) -> dict:
        """
        Test Telegram connection.

        Returns:
            dict with status and bot info
        """
        # Trigger lazy loading of credentials
        if not self.enabled or not self.bot_token:
            return {"ok": False, "error": "TELEGRAM_BOT_TOKEN not configured"}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"https://api.telegram.org/bot{self.bot_token}/getMe"
                )

                data = response.json()

                if data.get("ok"):
                    bot_info = data.get("result", {})
                    return {
                        "ok": True,
                        "bot_username": bot_info.get("username"),
                        "bot_name": bot_info.get("first_name"),
                        "chat_id_configured": bool(self.chat_id),
                    }
                else:
                    return {"ok": False, "error": data.get("description", "Unknown error")}

        except Exception as e:
            return {"ok": False, "error": str(e)}


# Global instance (lazy initialization)
telegram_service = TelegramService()
