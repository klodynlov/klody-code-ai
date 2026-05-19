import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import MAX_MESSAGES, MEMORY_DIR

logger = logging.getLogger(__name__)


class ConversationMemory:
    def __init__(self, session_id: Optional[str] = None):
        self.session_id: str = session_id or str(uuid.uuid4())[:8]
        self.memory_file: Path = MEMORY_DIR / f"memory_{self.session_id}.json"
        self.messages: list[dict] = []
        self._created_at: str = datetime.now().isoformat()
        self.title: str = ""

    # ------------------------------------------------------------------ #
    # Ajout de messages                                                    #
    # ------------------------------------------------------------------ #

    def add_message(self, role: str, content: str, **extra) -> None:
        """Ajoute un message et applique la fenêtre glissante."""
        msg = {"role": role, "content": content, "timestamp": datetime.now().isoformat()}
        msg.update(extra)
        self.messages.append(msg)
        # Auto-title from first user message
        if role == "user" and not self.title:
            prefix = datetime.now().strftime("%d/%m %H:%M")
            excerpt = (content[:50] + "…") if len(content) > 53 else content
            self.title = f"{prefix} — {excerpt}"
        self._apply_sliding_window()
        self.save()

    def add_tool_call_message(self, tool_calls: list[dict]) -> None:
        """Ajoute un message assistant contenant des tool calls."""
        self.messages.append({
            "role": "assistant",
            "content": None,
            "tool_calls": tool_calls,
            "timestamp": datetime.now().isoformat(),
        })
        self.save()

    def add_tool_result(self, tool_call_id: str, name: str, content: str) -> None:
        """Ajoute le résultat d'un outil."""
        self.messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": name,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        })
        self.save()

    # ------------------------------------------------------------------ #
    # Format API OpenAI                                                    #
    # ------------------------------------------------------------------ #

    def get_messages_for_api(self) -> list[dict]:
        """Retourne les messages dans le format exact attendu par l'API OpenAI/Ollama."""
        api_messages = []
        for msg in self.messages:
            role = msg["role"]
            if role == "system":
                api_messages.append({"role": "system", "content": msg["content"]})
            elif role == "user":
                api_messages.append({"role": "user", "content": msg["content"]})
            elif role == "assistant":
                if msg.get("tool_calls"):
                    api_messages.append({
                        "role": "assistant",
                        "content": msg.get("content"),
                        "tool_calls": msg["tool_calls"],
                    })
                else:
                    api_messages.append({"role": "assistant", "content": msg["content"]})
            elif role == "tool":
                api_messages.append({
                    "role": "tool",
                    "tool_call_id": msg["tool_call_id"],
                    "name": msg["name"],
                    "content": msg["content"],
                })
        return api_messages

    # ------------------------------------------------------------------ #
    # Persistance JSON                                                     #
    # ------------------------------------------------------------------ #

    def save(self) -> None:
        data = {
            "session_id": self.session_id,
            "title": self.title,
            "created_at": self._created_at,
            "updated_at": datetime.now().isoformat(),
            "messages": self.messages,
        }
        try:
            self.memory_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            logger.error("Impossible de sauvegarder la mémoire: %s", e)

    @classmethod
    def load_latest(cls) -> Optional["ConversationMemory"]:
        """Charge la session la plus récente."""
        files = sorted(
            MEMORY_DIR.glob("memory_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not files:
            return None
        return cls.load_from_file(files[0])

    @classmethod
    def load_from_file(cls, path: Path) -> "ConversationMemory":
        data = json.loads(path.read_text(encoding="utf-8"))
        instance = cls(session_id=data["session_id"])
        instance.messages = data["messages"]
        instance._created_at = data["created_at"]
        instance.title = data.get("title", "")
        logger.info("Session chargée: %s (%d messages)", instance.session_id, len(instance.messages))
        return instance

    # ------------------------------------------------------------------ #
    # Utilitaires                                                          #
    # ------------------------------------------------------------------ #

    def clear(self) -> None:
        """Efface l'historique en conservant le system prompt."""
        system_msgs = [m for m in self.messages if m["role"] == "system"]
        self.messages = system_msgs
        self.save()

    def stats(self) -> dict:
        return {
            "session_id": self.session_id,
            "total_messages": len(self.messages),
            "messages_user": sum(1 for m in self.messages if m["role"] == "user"),
            "messages_assistant": sum(1 for m in self.messages if m["role"] == "assistant"),
            "messages_tool": sum(1 for m in self.messages if m["role"] == "tool"),
            "fichier": str(self.memory_file),
        }

    def _apply_sliding_window(self) -> None:
        """Maintient au maximum MAX_MESSAGES messages non-system."""
        non_system = [m for m in self.messages if m["role"] != "system"]
        if len(non_system) <= MAX_MESSAGES:
            return
        for i, m in enumerate(self.messages):
            if m["role"] != "system":
                self.messages.pop(i)
                break
