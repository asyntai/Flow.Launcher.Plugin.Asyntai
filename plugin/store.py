# Copyright (c) Asyntai. Licensed under the MIT license.
"""Where the plugin finds Flow's folders, and the small state it keeps.

Flow starts a new Python process for every query and every action, so the
"Ask your chatbot" conversation lives in a JSON file in the plugin's cache
folder: the session id, the last answers, and the question the user just
confirmed with Enter. The API key is never written here; Flow keeps it in
its own settings file and hands it to every query.
"""

import json
import os
import time
from pathlib import Path

PLUGIN_ID = "C06CEA5711924894B228BB12694A6D44"
PLUGIN_NAME = "Asyntai AI Chatbot"
DEFAULT_KEYWORD = "asy"

# A conversation that sees no question for this long starts fresh.
SESSION_IDLE_SECONDS = 30 * 60
KEEP_ANSWERS = 20

PLUGIN_DIR = Path(__file__).resolve().parent.parent


def data_dir():
    # Flow installs a plugin into <data>/Plugins/<folder>. That gives the data
    # folder in both the normal and the portable install.
    if PLUGIN_DIR.parent.name.lower() == "plugins":
        return PLUGIN_DIR.parent.parent
    return Path(os.environ.get("APPDATA", str(Path.home()))) / "FlowLauncher"


def settings_dir():
    return data_dir() / "Settings" / "Plugins" / PLUGIN_NAME


def cache_dir():
    return data_dir() / "Cache" / "Plugins" / PLUGIN_NAME


def base_url(live):
    # A test install can point the plugin at a local Asyntai server: put its
    # address in dev_base_url.txt in the plugin's settings folder. Only
    # 127.0.0.1 and localhost are accepted, so a key can never be sent
    # anywhere else by this file.
    path = settings_dir() / "dev_base_url.txt"
    try:
        url = path.read_text(encoding="utf-8").strip().rstrip("/")
    except OSError:
        return live
    if url.startswith("http://127.0.0.1:") or url.startswith("http://localhost:"):
        return url
    return live


def flow_settings():
    try:
        with open(data_dir() / "Settings" / "Settings.json", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def line_width():
    """How many characters fit on one result line in this user's Flow.

    Measured on Flow 2.1 with Segoe UI: a result title gets the window width
    minus about 165 px (icon, margins, Alt+N badge), and one character takes
    about 0.46 of the font size.
    """
    settings = flow_settings()
    try:
        width = float(settings.get("WindowSize") or 580)
        font = float(settings.get("ResultItemFontSize") or 16)
    except (TypeError, ValueError):
        width, font = 580.0, 16.0
    chars = int((width - 165) / (0.46 * max(font, 8)))
    return max(30, min(chars, 140))


def query_prefix():
    """The text that starts a query for this plugin, e.g. "asy "."""
    try:
        words = flow_settings()["PluginSettings"]["Plugins"][PLUGIN_ID]["ActionKeywords"]
    except (KeyError, TypeError):
        return DEFAULT_KEYWORD + " "
    if not isinstance(words, list) or not words:
        return DEFAULT_KEYWORD + " "
    for word in words:
        if isinstance(word, str) and word and word != "*":
            return word + " "
    # Only the global keyword: the plugin answers any query.
    return ""


class State:
    def __init__(self):
        self.path = cache_dir() / "state.json"
        self.data = {"session": "", "last": 0, "answers": [], "pending": None}
        try:
            with open(self.path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.data.update(loaded)
        except (OSError, ValueError):
            pass
        if not isinstance(self.data.get("answers"), list):
            self.data["answers"] = []

    def session(self, new_session):
        idle = time.time() - float(self.data.get("last") or 0)
        if not self.data.get("session") or idle > SESSION_IDLE_SECONDS:
            self.reset(new_session)
        return self.data["session"]

    def reset(self, new_session):
        self.data.update({"session": new_session(), "last": time.time(), "answers": [], "pending": None})

    def answer_for(self, question):
        for q, a in reversed(self.data["answers"]):
            if q == question:
                return a
        return None

    def add_answer(self, question, answer):
        answers = [pair for pair in self.data["answers"] if pair[0] != question]
        answers.append([question, answer])
        self.data["answers"] = answers[-KEEP_ANSWERS:]
        self.data["last"] = time.time()

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f)
            os.replace(tmp, self.path)
        except OSError:
            pass
