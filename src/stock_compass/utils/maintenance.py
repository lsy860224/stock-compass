"""주기적 유지보수 — craft_export `.bak` 누적 정리 등. `daily` 잡에서 호출.

DB 백업 정리는 backup.py 가 담당. 여기서는 그 외 디스크 누적 정리.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from stock_compass.config import settings
from stock_compass.utils.logging import get_logger

_logger = get_logger(__name__)

# 예: 2026-05-26.md.20260526010801.bak
_BAK_RE = re.compile(r"^(?P<base>.+\.md)\.\d+\.bak$")


def prune_craft_export_backups(*, keep: int | None = None) -> int:
    """craft_export 의 `*.md.<ts>.bak` 를 원본 파일별 최신 `keep`개만 유지.

    CraftExporter.export_to_file 가 덮어쓸 때마다 timestamped .bak 를 쌓아
    누적되는 문제 정리. 삭제 개수 반환.
    """
    keep_n = keep if keep is not None else settings.craft_export_backup_keep
    export_dir = settings.craft_export_dir
    if not export_dir.exists():
        return 0

    groups: dict[str, list[Path]] = defaultdict(list)
    for bak in export_dir.glob("*.bak"):
        m = _BAK_RE.match(bak.name)
        if m:
            groups[m.group("base")].append(bak)

    deleted = 0
    for baks in groups.values():
        # 파일명에 timestamp 포함 → 이름 역순 = 최신순
        for old in sorted(baks, key=lambda p: p.name, reverse=True)[max(keep_n, 0):]:
            try:
                old.unlink()
                deleted += 1
            except OSError as e:
                _logger.warning("craft_export bak 삭제 실패: %s (%s)", old.name, e)
    if deleted:
        _logger.info("craft_export .bak 정리: %d개 삭제", deleted)
    return deleted
