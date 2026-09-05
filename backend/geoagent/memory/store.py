from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4


def _json_default(obj: Any) -> Any:
    """JSONL 持久化的兜底序列化：Decimal/时间转基础类型，避免工具结果落库崩溃。"""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def summarize_title(content: str, limit: int = 20) -> str:
    """根据首条用户消息生成会话标题（去空白、超长截断加省略号）。"""
    text = " ".join(str(content).split())
    if not text:
        return "未命名会话"
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


class ConversationStore:
    """基于 JSONL 的会话存储（多会话，暂未做认证）。

    data_dir/conversations/
    ├── _meta.json      # 会话索引
    └── {id}.jsonl      # 每行一条 JSON 消息
    """

    def __init__(self, data_dir: Path) -> None:
        self.conv_dir = Path(data_dir) / "conversations"
        self.conv_dir.mkdir(parents=True, exist_ok=True)
        self._meta_path = self.conv_dir / "_meta.json"
        self._meta: dict[str, dict[str, Any]] = {}
        self._load_meta()

    def _load_meta(self) -> None:
        if not self._meta_path.exists():
            return
        try:
            data = json.loads(self._meta_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._meta = data
        except (json.JSONDecodeError, OSError):
            self._meta = {}

    def _save_meta(self) -> None:
        tmp = self._meta_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self._meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self._meta_path)

    def _conv_path(self, conversation_id: str) -> Path:
        return self.conv_dir / f"{conversation_id}.jsonl"

    def create(self, title: Optional[str] = None, model: str = "") -> dict[str, Any]:
        conversation_id = uuid4().hex[:12]
        now = _utc_now_iso()
        conv = {
            "id": conversation_id,
            "title": title or f"会话 {conversation_id[:6]}",
            "model": model,
            "created_at": now,
            "updated_at": now,
        }
        self._meta[conversation_id] = conv
        self._save_meta()
        return dict(conv)

    def get(self, conversation_id: str) -> Optional[dict[str, Any]]:
        conv = self._meta.get(conversation_id)
        return dict(conv) if conv else None

    def list(self) -> list[dict[str, Any]]:
        # 按创建时间倒序：最新会话排在最前。
        return [
            dict(c)
            for c in sorted(
                self._meta.values(),
                key=lambda x: x["created_at"],
                reverse=True,
            )
        ]

    def set_model(self, conversation_id: str, model: str) -> Optional[dict[str, Any]]:
        conv = self._meta.get(conversation_id)
        if conv is None:
            return None
        conv["model"] = model
        conv["updated_at"] = _utc_now_iso()
        self._save_meta()
        return dict(conv)

    @staticmethod
    def _is_placeholder_title(title: str) -> bool:
        """判断标题是否还是默认占位（新建会话尚未起标题）。"""
        text = (title or "").strip()
        if not text:
            return True
        if text in ("新会话", "新对话"):
            return True
        if text.startswith(("新会话 ", "新对话 ", "会话 ")):
            return True
        return False

    def update_title_if_placeholder(
        self, conversation_id: str, content: str
    ) -> Optional[dict[str, Any]]:
        """若会话标题仍是占位，则用首条用户消息生成标题。"""
        conv = self._meta.get(conversation_id)
        if conv is None:
            return None
        if self._is_placeholder_title(conv.get("title", "")):
            conv["title"] = summarize_title(content)
            conv["updated_at"] = _utc_now_iso()
            self._save_meta()
        return dict(conv)

    def delete(self, conversation_id: str) -> None:
        """删除会话（元数据与消息文件）。"""
        self._meta.pop(conversation_id, None)
        self._save_meta()
        path = self._conv_path(conversation_id)
        if path.exists():
            path.unlink()

    def add_message(self, conversation_id: str, message: dict[str, Any]) -> None:
        conv = self._meta.get(conversation_id)
        if conv is None:
            raise KeyError(f"Conversation not found: {conversation_id}")
        entry = dict(message)
        entry.setdefault("ts", _utc_now_iso())
        path = self._conv_path(conversation_id)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=_json_default) + "\n")
        conv["updated_at"] = _utc_now_iso()
        self._save_meta()

    def messages(self, conversation_id: str) -> list[dict[str, Any]]:
        path = self._conv_path(conversation_id)
        if not path.exists():
            return []
        messages = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return messages

    def rollback_last_user_turn(self, conversation_id: str) -> Optional[dict[str, Any]]:
        """撤回最后一条用户消息及其回答（用于“修改后重新生成”）。

        前端修改文字后会先调用本方法清掉旧问题与旧回答，
        再以新内容正常发起一轮，避免服务端历史出现重复的用户消息。
        返回被撤回的原用户消息；若会话不存在或没有用户消息则返回 None。
        """
        conv = self._meta.get(conversation_id)
        if conv is None:
            return None
        path = self._conv_path(conversation_id)
        if not path.exists():
            return None
        lines = path.read_text(encoding="utf-8").splitlines()
        # 从尾部向前找最后一条用户消息（用户消息后的内容都是它的回答）
        target_index = -1
        for i in range(len(lines) - 1, -1, -1):
            line = lines[i].strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("role") == "user":
                target_index = i
                break
        if target_index < 0:
            return None
        old_message = json.loads(lines[target_index])
        # 只保留最后一条用户消息之前的内容；该条消息与其后的旧回答一并移除
        kept_lines = lines[:target_index]
        tmp = path.with_suffix(".tmp")
        tmp.write_text("\n".join(kept_lines) + "\n", encoding="utf-8")
        tmp.replace(path)
        conv["updated_at"] = _utc_now_iso()
        self._save_meta()
        return dict(old_message)
