"""Application settings: a plain dict persisted into SQLite."""
from __future__ import annotations

import json

DEFAULTS = {
    "aclos_dir": "",
    "hwdec": "auto",
    "volume": 80,
    "speed": 1.0,
    "remember_position": 1,
    "thumb_width": 300,
    # Phase 2: playback behaviour
    "seek_step": 5,          # ← / →  single jump, seconds
    "seek_step_long": 30,    # Shift + ← / →
    "autoplay_next": 1,      # continue to the next item in the context list
    "sub_delay": 0.0,        # applied to every newly loaded file
    "theme": "valorant",
}


class Settings(dict):
    def __init__(self, db):
        super().__init__()
        self.db = db
        for k, v in DEFAULTS.items():
            self[k] = v
        raw = db.get_setting("settings_json", "")
        if raw:
            try:
                self.update(json.loads(raw))
            except json.JSONDecodeError:
                pass

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self.save()

    def update(self, *a, **kw):  # noqa: D102
        super().update(*a, **kw)
        self.save()

    def save(self):
        self.db.set_setting("settings_json", json.dumps(self, ensure_ascii=False))

    def get(self, key, default=None):
        return super().get(key, DEFAULTS.get(key, default))
