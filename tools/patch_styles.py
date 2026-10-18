"""Batch-convert inline f-string stylesheets to registered $KEY templates."""
import pathlib

EDITS = {
    "src/replaystudio/ui/settings_page.py": [
        ('h.setStyleSheet(f"color: {T.TEXT}; font-size: 14px; font-weight: 600;")',
         'T.style(h, "color: $TEXT; font-size: 14px; font-weight: 600;")'),
        ('hint.setStyleSheet(f"color: {T.TEXT_FAINT}; font-size: 11.5px;")',
         'T.style(hint, "color: $TEXT_FAINT; font-size: 11.5px;")'),
        ('self.lbl_engine.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 11.5px;")',
         'T.style(self.lbl_engine, "color: $TEXT_DIM; font-size: 11.5px;")'),
        ('self.lbl_stats.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 12px;")',
         'T.style(self.lbl_stats, "color: $TEXT_DIM; font-size: 12px;")'),
        ('about.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 12px; line-height: 160%;")',
         'T.style(about, "color: $TEXT_DIM; font-size: 12px; line-height: 160%;")'),
    ],
    "src/replaystudio/ui/player_page.py": [
        ('self.video_host.setStyleSheet("background: #000; border-radius: 10px;")',
         'T.style(self.video_host, "background: #000; border-radius: ${RADIUS}px;")'),
        ('self.title_label.setStyleSheet(f"font-size: 15px; font-weight: 600; color: {T.TEXT};")',
         'T.style(self.title_label, "font-size: 15px; font-weight: 600; color: $TEXT;")'),
        ('self.info_label.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 12px;")',
         'T.style(self.info_label, "color: $TEXT_DIM; font-size: 12px;")'),
        ('self.meta_label.setStyleSheet(f"color: {T.TEXT}; font-weight: 600; font-size: 13px;")',
         'T.style(self.meta_label, "color: $TEXT; font-weight: 600; font-size: 13px;")'),
        ('self.meta_body.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 12px; line-height: 150%;")',
         'T.style(self.meta_body, "color: $TEXT_DIM; font-size: 12px; line-height: 150%;")'),
        ('ev_title.setStyleSheet(f"color: {T.TEXT}; font-weight: 600; font-size: 13px;")',
         'T.style(ev_title, "color: $TEXT; font-weight: 600; font-size: 13px;")'),
        ('''bar.setStyleSheet(f"background: {T.SURFACE}; border-top: 1px solid {T.BORDER};")''',
         'T.style(bar, "background: $SURFACE; border-top: 1px solid $BORDER;")'),
        ('self.time_label.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 12px;")',
         'T.style(self.time_label, "color: $TEXT_DIM; font-size: 12px;")'),
        ('''            b.setStyleSheet(f"""
                QPushButton {{ background: transparent; border: none; text-align: left;
                    color: {T.TEXT_DIM}; padding: 5px 8px; border-radius: 6px; font-size: 12px; }}
                QPushButton:hover {{ background: {T.SURFACE_2}; color: {T.TEXT}; }}
            """)''',
         '''            T.style(b, """
                QPushButton { background: transparent; border: none; text-align: left;
                    color: $TEXT_DIM; padding: 5px 8px; border-radius: 6px; font-size: 12px; }
                QPushButton:hover { background: $SURFACE_2; color: $TEXT; }
            """)'''),
        ('''        self.info_label.setStyleSheet(f"color: {T.AMBER}; font-size: 12px;")''',
         '''        T.style(self.info_label, "color: $AMBER; font-size: 12px;")'''),
        ('''        self.info_label.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 12px;")''',
         '''        T.style(self.info_label, "color: $TEXT_DIM; font-size: 12px;")'''),
    ],
    "src/replaystudio/ui/main_window.py": [
        ('''        status.setStyleSheet(f"background: {T.SURFACE}; border-top: 1px solid {T.BORDER};")''',
         '''        T.style(status, "background: $SURFACE; border-top: 1px solid $BORDER;")'''),
        ('''        self.status_label.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 12px;")''',
         '''        T.style(self.status_label, "color: $TEXT_DIM; font-size: 12px;")'''),
    ],
}

for path, pairs in EDITS.items():
    p = pathlib.Path(path)
    s = p.read_text(encoding="utf-8")
    for old, new in pairs:
        if old not in s:
            print("MISS:", path, old[:50].replace("\n", "\\n"))
            continue
        s = s.replace(old, new, 1)
    p.write_text(s, encoding="utf-8")
    print("ok", path)
