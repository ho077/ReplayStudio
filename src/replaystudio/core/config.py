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
    # Clips whose source file no longer exists stay in the index but are hidden
    # from every listing unless the user asks to see them.
    "show_missing_files": 0,
    # Phase 3: recording
    "record_dir": "",                 # empty = obs_paths.default_record_dir()
    "record_kind": "monitor_capture", # monitor_capture | window_capture | game_capture
    "record_window": "",
    "record_cursor": 1,
    "record_desktop_audio": 1,
    "record_mic": 0,
    "record_format": "hybrid_mp4",  # SimpleOutput/RecFormat2; see recording.CONTAINERS
}


class Settings(dict):
    def __init__(self, db):
        # Seed from DEFAULTS through dict's own constructor, NOT via self[k] = v:
        # __setitem__ saves on every assignment, so filling the defaults one by
        # one would overwrite the stored user settings with a half-built dict —
        # and the read-back below would then find only defaults. (Measured: every
        # setting appeared to be forgotten on the next start.)
        self.db = db
        super().__init__(DEFAULTS)
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
