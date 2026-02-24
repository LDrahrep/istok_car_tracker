from __future__ import annotations

import traceback
from dataclasses import dataclass
from typing import Optional


@dataclass
class AdminLogEvent:
    title: str
    details: str = ""
    user_id: Optional[int] = None
    username: Optional[str] = None

    def format(self) -> str:
        header = f"🧾 {self.title}"
        meta = []
        if self.user_id:
            meta.append(f"uid={self.user_id}")
        if self.username:
            meta.append(f"@{self.username}")
        meta_line = f"\n({' | '.join(meta)})" if meta else ""
        body = f"\n{self.details}" if self.details else ""
        return header + meta_line + body


def format_exception(e: BaseException) -> str:
    return "".join(
        traceback.format_exception(type(e), e, e.__traceback__)
    )[-3000:]