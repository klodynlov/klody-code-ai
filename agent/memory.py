import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import config

from agent.dbc import invariant
from agent.tokens import count_tokens

logger = logging.getLogger(__name__)

# Budget de contexte des MESSAGES = CONTEXT_WINDOW − réserve outils − réserve
# réponse. Le prompt réel envoyé au modèle inclut, EN PLUS des messages, les
# schémas d'outils (~8k, passés hors `messages`) et doit laisser de quoi générer
# la réponse. Borner les messages sur CONTEXT_WINDOW seul (ancien ratio 0.8)
# ignorait ces deux postes → la fenêtre se saturait (jauge ~32k/32.8k) et la
# génération n'avait plus de place. Lu via `config` car réglable à chaud.
def _message_budget() -> int:
    return max(
        2048,
        config.CONTEXT_WINDOW - config.CONTEXT_TOOLS_RESERVE - config.CONTEXT_RESPONSE_RESERVE,
    )


class ConversationMemory:
    def __init__(self, session_id: str | None = None):
        self.session_id: str = session_id or str(uuid.uuid4())[:8]
        self.memory_file: Path = config.MEMORY_DIR / f"memory_{self.session_id}.json"
        self.messages: list[dict] = []
        self._created_at: str = datetime.now().isoformat()
        self.title: str = ""
        # Archivée = rangée hors de la liste active mais conservée pour être
        # rechargée/réutilisée. Sticky : préservée à travers save() pour qu'un
        # tour de chat sur une session réutilisée ne la désarchive pas en douce.
        self.archived: bool = False

    # ------------------------------------------------------------------ #
    # Ajout de messages                                                    #
    # ------------------------------------------------------------------ #

    def add_message(self, role: str, content: str, **extra: object) -> None:
        """Ajoute un message et applique la fenêtre glissante."""
        msg: dict[str, object] = {"role": role, "content": content, "timestamp": datetime.now().isoformat()}
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
        # Une boucle ReAct enchaîne jusqu'à MAX_ITERATIONS tours d'outils SANS
        # repasser par add_message : sans borne ici, le contexte gonfle librement
        # pendant un seul tour utilisateur (cf. session molécule 3D saturée).
        self._apply_sliding_window()
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
        self._apply_sliding_window()
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
            "archived": self.archived,
            "created_at": self._created_at,
            "updated_at": datetime.now().isoformat(),
            # On retire les clés privées éphémères (cache de tokens `_tok*`) : elles
            # n'ont pas à être persistées et seront recalculées au besoin.
            "messages": [
                {k: v for k, v in m.items() if not k.startswith("_")}
                for m in self.messages
            ],
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
            config.MEMORY_DIR.glob("memory_*.json"),
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
        instance.archived = bool(data.get("archived", False))
        # Assainit les sessions héritées : un tool result orphelin viole
        # l'invariant ET casse l'API OpenAI/Ollama au prochain appel.
        dropped = instance._drop_orphan_tool_results()
        if dropped:
            logger.warning(
                "Session %s : %d tool result(s) orphelin(s) purgé(s) au chargement",
                instance.session_id, dropped,
            )
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
        """Borne le contexte non-system par DEUX plafonds, en retirant les
        plus anciens groupes cohérents (jamais un tool result sans son call) :

        1. nombre de messages (MAX_MESSAGES) — garde-fou simple ;
        2. budget de tokens estimés (CONTEXT_WINDOW) — la fenêtre du modèle se
           sature selon la TAILLE, pas le nombre de messages : un seul gros
           dump d'outil peut suffire. On garde toujours ≥1 groupe (le tour
           courant doit passer même s'il est volumineux).

        Appelé sur CHAQUE ajout, y compris les tool calls/results intermédiaires
        d'un tour ReAct. Contrat exprimé en relatif (« ne CRÉE pas d'orphelin »)
        plutôt qu'en absolu : un message tool peut être ajouté avant son call
        (entrée malformée, séquence en cours de construction) ; le rognage ne doit
        pas faire échouer la génération sur cet état transitoire — il doit juste
        ne jamais aggraver les choses.
        """
        orphans_before = len(self._orphan_tool_results())

        while self._count_non_system() > config.MAX_MESSAGES:
            if not self._pop_oldest_group():
                break

        budget = _message_budget()
        while self._total_estimated_tokens() > budget and self._count_non_system() > 1:
            if not self._pop_oldest_group():
                break

        # Invariant de sortie : le rognage (par groupes assistant+tools cohérents)
        # ne crée jamais de NOUVEL orphelin. Détecte une régression de
        # _pop_oldest_group (call retiré sans ses results) sans planter sur un
        # orphelin préexistant.
        invariant(
            len(self._orphan_tool_results()) <= orphans_before,
            "la fenêtre glissante ne doit jamais créer de tool result orphelin",
        )

    def _count_non_system(self) -> int:
        return sum(1 for m in self.messages if m["role"] != "system")

    def _pop_oldest_group(self) -> bool:
        """Retire le plus ancien groupe non-system cohérent : un message user
        et tout ce qui le suit jusqu'au prochain user, ou un assistant à
        tool_calls et ses tool results. Retourne False si rien à retirer."""
        idx = next((i for i, m in enumerate(self.messages) if m["role"] != "system"), None)
        if idx is None:
            return False
        role = self.messages[idx]["role"]
        if role == "user":
            self.messages.pop(idx)
            while idx < len(self.messages) and self.messages[idx]["role"] not in ("system", "user"):
                self.messages.pop(idx)
        elif role == "assistant" and self.messages[idx].get("tool_calls"):
            tc_ids = {tc["id"] for tc in self.messages[idx].get("tool_calls", [])}
            self.messages.pop(idx)
            while (idx < len(self.messages) and self.messages[idx]["role"] == "tool"
                   and self.messages[idx].get("tool_call_id") in tc_ids):
                self.messages.pop(idx)
        else:
            self.messages.pop(idx)
        return True

    @staticmethod
    def _estimate_tokens(message: dict) -> int:
        """Coût en tokens : contenu + nom/arguments des tool_calls + surcoût de
        structure (+4 : rôle et délimiteurs). Comptage exact via le tokenizer du
        modèle si disponible (cf. agent/tokens), sinon repli ~chars/4.

        Le résultat est mis en cache dans le message (clé privée `_tok`, invalidée
        si la taille du contenu change). La fenêtre glissante somme ce coût à
        CHAQUE ajout, sur ≤ MAX_MESSAGES messages : sans cache, autant d'encodages
        par ajout → O(n²) par tour. Les clés `_tok*` sont retirées à la
        sauvegarde (cf. save) et jamais transmises à l'API."""
        text = message.get("content") or ""
        parts = [text]
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function", {})
            parts.append(fn.get("name", ""))
            parts.append(fn.get("arguments", ""))
        blob = "".join(parts)
        sig = len(blob)
        if message.get("_tok_sig") == sig and "_tok" in message:
            return message["_tok"]
        n = count_tokens(blob) + 4
        message["_tok"] = n
        message["_tok_sig"] = sig
        return n

    def _total_estimated_tokens(self) -> int:
        return sum(self._estimate_tokens(m) for m in self.messages)

    # ------------------------------------------------------------------ #
    # Invariant « pas de tool result orphelin » (Design by Contract)      #
    # ------------------------------------------------------------------ #

    def _tool_call_ids(self) -> set[str]:
        """Ids de tous les tool_calls émis par les messages assistant."""
        ids: set[str] = set()
        for m in self.messages:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                ids.update(tc.get("id") for tc in m["tool_calls"])
        return ids

    def _orphan_tool_results(self) -> list[dict]:
        """Messages 'tool' dont le tool_call_id ne correspond à aucun tool_call."""
        valid = self._tool_call_ids()
        return [
            m for m in self.messages
            if m.get("role") == "tool" and m.get("tool_call_id") not in valid
        ]

    def _drop_orphan_tool_results(self) -> int:
        """Retire les tool results orphelins. Retourne le nombre purgé."""
        valid = self._tool_call_ids()
        before = len(self.messages)
        self.messages = [
            m for m in self.messages
            if not (m.get("role") == "tool" and m.get("tool_call_id") not in valid)
        ]
        return before - len(self.messages)
