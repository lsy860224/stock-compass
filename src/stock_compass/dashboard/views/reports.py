"""Reports 페이지 — launchd 자동화가 Obsidian 볼트에 기록한 보고 뷰어."""

from __future__ import annotations

import streamlit as st


def render_reports() -> None:
    import re
    from pathlib import Path

    from stock_compass.config import settings

    st.header("Reports — 자동 생성 보고")
    st.caption("launchd 자동화가 Obsidian 볼트에 기록한 보고 (일일·배치·주간·알림).")

    root: Path | None = None
    if settings.obsidian_vault_dir is not None:
        root = settings.obsidian_vault_dir / settings.obsidian_reports_subdir
    if root is None or not root.exists():
        st.warning(
            "Obsidian Reports 폴더가 아직 없습니다 — 자동 보고 미생성 또는 볼트 미동기."
        )
        st.caption(f"기대 경로: `{root}`")
        return

    subfolders = sorted(
        p for p in root.iterdir() if p.is_dir() and not p.name.startswith("_")
    )
    if not subfolders:
        st.info("생성된 보고가 없습니다.")
        return

    col1, col2 = st.columns([1, 3])
    with col1:
        kind = st.selectbox("종류", [p.name for p in subfolders])
        kind_dir = root / kind
        files = sorted(kind_dir.glob("*.md"), key=lambda p: p.name, reverse=True)
        if not files:
            st.info("이 종류의 보고가 없습니다.")
            return
        chosen = st.selectbox("보고", [p.stem for p in files])
    with col2:
        path = kind_dir / f"{chosen}.md"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            st.error(f"읽기 실패: {e}")
            return
        body = _strip_frontmatter(text)
        # Obsidian 임베드 ![[x.png]] → st.image (첨부는 _attachments/)
        attach_dir = root / "_attachments"
        embeds = re.findall(r"!\[\[([^\]]+\.png)\]\]", body)
        body = re.sub(r"!\[\[[^\]]+\.png\]\]", "", body)
        st.markdown(body)
        for img in embeds:
            img_path = attach_dir / img
            if img_path.exists():
                st.image(str(img_path))


def _strip_frontmatter(text: str) -> str:
    """YAML frontmatter(--- ... ---) 제거 후 본문 반환."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4 :].lstrip("\n")
    return text
