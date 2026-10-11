# Copyright (c) Asyntai. Licensed under the MIT license.
"""Turns Flow Launcher queries into Asyntai results.

Query grammar, after the action keyword (default "asy"):

    (empty)              the menu
    chats [filter]       recent website chats
    leads [filter]       visitors who left an email or phone number
    tickets [filter]     chats escalated to your team
    ask <question>       ask your own chatbot
    transcript <id>      every message of one chat
    <anything else>      treated as a question for your chatbot

Flow runs a new process for every call. A query gets the plugin settings
(the API key); an action does not, so every action here either calls a Flow
API directly or works without the key.
"""

import json
import re
import sys
import textwrap
import webbrowser
from datetime import datetime, timezone

from . import api, store

ICON = "Images\\app.png"
ANSWER_MAX_LINES = 14

MENU = [
    ("chats", "Recent chats", "Read the latest conversations on your website"),
    ("leads", "Leads", "Visitors who left an email address or a phone number"),
    ("tickets", "Tickets", "Chats your chatbot handed over to your team"),
    ("ask", "Ask your chatbot", "Type a question and see the answer your visitors get"),
]


def run(argv):
    request = {"method": "query", "parameters": [""]}
    if len(argv) > 1 and argv[1]:
        try:
            request = json.loads(argv[1])
        except ValueError:
            pass

    method = request.get("method") or "query"
    params = request.get("parameters") or []
    plugin = Plugin(request.get("settings") or {})

    try:
        if method == "query":
            out = {"result": plugin.query(params[0] if params else "")}
        elif method == "context_menu":
            out = {"result": plugin.context_menu(params[0] if params else None)}
        elif method in ACTIONS:
            out = getattr(plugin, method)(*params)
        else:
            out = None
    except Exception as e:  # never let a traceback reach Flow's stderr check
        if method in ("query", "context_menu"):
            out = {"result": [result("Something went wrong", str(e) or e.__class__.__name__)]}
        else:
            out = None

    if out is not None:
        sys.stdout.write(json.dumps(out))
        sys.stdout.flush()


ACTIONS = ("open_url", "ask_now", "new_conversation")


# -- result helpers -------------------------------------------------------

def result(title, subtitle="", action=None, context=None, copy=None, score=0):
    r = {"Title": title, "SubTitle": subtitle, "IcoPath": ICON, "Score": score}
    if action is not None:
        r["JsonRPCAction"] = action
    if context is not None:
        r["ContextData"] = context
    if copy:
        r["CopyText"] = copy
    return r


def flow(method, *params, keep=False):
    """An action Flow runs itself, without starting the plugin."""
    return {"method": "Flow.Launcher." + method, "parameters": list(params), "dontHideAfterAction": keep}


def call(method, *params, keep=False):
    """An action that starts the plugin again with this method."""
    return {"method": method, "parameters": list(params), "dontHideAfterAction": keep}


def ranked(rows):
    # Flow sorts by score. Keep the order the plugin chose.
    total = len(rows)
    for i, r in enumerate(rows):
        r["Score"] = 1000 + (total - i) * 10
    return rows


def one_line(text, limit=110):
    line = re.sub(r"\s+", " ", text or "").strip()
    return line if len(line) <= limit else line[: limit - 1] + "…"


def ago(iso):
    if not iso:
        return ""
    try:
        when = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    seconds = max(0, (datetime.now(timezone.utc) - when).total_seconds())
    if seconds < 60:
        return "now"
    if seconds < 3600:
        return "%d min ago" % (seconds // 60)
    if seconds < 86400:
        return "%d h ago" % (seconds // 3600)
    if seconds < 30 * 86400:
        return "%d d ago" % (seconds // 86400)
    return when.astimezone().strftime("%Y-%m-%d")


def messages_label(count):
    if not count or count == "0":
        return ""
    return "1 message" if count == "1" else count + " messages"


def joined(*parts):
    return " · ".join(p for p in parts if p)


def plain(text):
    """Chatbot answers are markdown. Flow shows plain text."""
    text = text or ""
    text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"\1 (\2)", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
    text = re.sub(r"(\*\*|__)(.+?)\1", r"\2", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"^\s*[-*]\s+", "• ", text, flags=re.M)
    return text.strip()


def links(text):
    seen = []
    for url in re.findall(r"https?://[^\s)\]>\"']+", text or ""):
        url = url.rstrip(".,;:!?")
        if url not in seen:
            seen.append(url)
    return seen[:5]


def matches(needle, *haystack):
    if not needle:
        return True
    needle = needle.lower()
    return any(needle in (h or "").lower() for h in haystack)


# -- the plugin -----------------------------------------------------------

class Plugin:
    def __init__(self, settings):
        key = settings.get("apiKey") if isinstance(settings, dict) else ""
        self.client = api.Client(key if isinstance(key, str) else "", store.base_url(api.LIVE_BASE))

    # queries ------------------------------------------------------------

    def query(self, search):
        text = (search or "").strip()
        if not text:
            return self.menu()

        word, _, rest = text.partition(" ")
        word = word.lower()
        rest = rest.strip()
        if word == "chats":
            return self.listing(self.chats, rest)
        if word == "leads":
            return self.listing(self.leads, rest)
        if word == "tickets":
            return self.listing(self.tickets, rest)
        if word == "transcript":
            return self.listing(self.transcript, rest)
        if word == "ask":
            return self.ask(rest)

        # Free text: the menu entries it starts, then the question itself.
        rows = []
        if " " not in text:
            rows = [self.menu_row(*m) for m in MENU if m[0].startswith(word)]
        return ranked(rows + self.ask(text, with_hint=False))

    def menu(self):
        rows = []
        if not self.client.has_key:
            rows.append(self.key_needed())
        rows += [self.menu_row(*m) for m in MENU]
        rows.append(result(
            "Open the Asyntai dashboard",
            "Settings, knowledge base and every chat in your browser",
            call("open_url", self.client.base + "/dashboard/"),
        ))
        return ranked(rows)

    def menu_row(self, word, title, subtitle):
        return result(title, subtitle, flow("ChangeQuery", store.query_prefix() + word + " ", True, keep=True))

    def key_needed(self):
        return result(
            "Add your Asyntai API key",
            "Enter opens Flow settings. Find the key in Asyntai under Settings, API.",
            flow("OpenSettingDialog"),
        )

    def failed(self, error):
        if not self.client.has_key or error.status == 401:
            row = self.key_needed()
            row["Title"] = error.message
            return [row]
        return [result(error.message, "Press Enter to try again", flow("ReQuery", True, keep=True))]

    def listing(self, build, arg):
        if not self.client.has_key:
            return [self.key_needed()]
        try:
            return ranked(build(arg))
        except api.AsyntaiError as e:
            return self.failed(e)

    def read_chat(self, session_id):
        # A search-text change only works from the result list; inside a
        # context menu Flow uses the new text to filter that menu. So reading
        # a chat is the Enter action, never a context-menu entry.
        return flow("ChangeQuery", store.query_prefix() + "transcript " + session_id, True, keep=True)

    def session_context(self, session_id, page_url=None, **extra):
        context = {"kind": "session", "sid": session_id, "dash": self.client.dashboard_url(session_id), "page": page_url or ""}
        context.update(extra)
        return context

    def chats(self, needle):
        rows = []
        for s in self.client.sessions():
            sid = api._s(s, "session_id") or ""
            first = api._s(s, "first_message")
            domain = api._s(s, "website_domain")
            country = api._s(s, "country")
            if not matches(needle, first, domain, country, api._s(s, "page_url")):
                continue
            count = api._s(s, "message_count")
            rows.append(result(
                one_line(first) or "Chat without a message",
                joined(ago(api._s(s, "last_message_at")), messages_label(count), domain, country),
                self.read_chat(sid),
                self.session_context(sid, api._s(s, "page_url")),
                copy=first,
            ))
        if not rows:
            rows.append(result(
                "No chats found" if needle else "No chats yet",
                "A chat appears here as soon as a visitor writes to your chatbot.",
            ))
        return rows

    def leads(self, needle):
        rows = []
        for lead in self.client.leads():
            sid = api._s(lead, "session_id") or ""
            email = api._s(lead, "email")
            phone = api._s(lead, "phone")
            page = api._s(lead, "page_url")
            if not matches(needle, email, phone, page):
                continue
            rows.append(result(
                email or phone or "Lead",
                joined(ago(api._s(lead, "started_at")), phone if email else "", page),
                self.read_chat(sid),
                self.session_context(sid, page, email=email or "", phone=phone or ""),
                copy=email or phone,
            ))
        if not rows:
            rows.append(result(
                "No leads found" if needle else "No leads yet",
                "A lead appears when a visitor leaves an email or phone number.",
            ))
        return rows

    def tickets(self, needle):
        rows = []
        for t in self.client.tickets():
            sid = api._s(t, "session_id") or ""
            number = api._s(t, "ticket_number") or ""
            subject = api._s(t, "subject") or "No subject"
            email = api._s(t, "visitor_email")
            if not matches(needle, number, subject, email, api._s(t, "status")):
                continue
            rows.append(result(
                one_line((("#" if number.isdigit() else "") + number + " " if number else "") + subject),
                joined((api._s(t, "status") or "").capitalize(), email, ago(api._s(t, "created_at"))),
                self.read_chat(sid) if sid else None,
                self.session_context(sid, api._s(t, "page_url"), email=email or "", ticket=number) if sid else None,
                copy=number,
            ))
        if not rows:
            rows.append(result(
                "No tickets found" if needle else "No tickets yet",
                "A ticket appears when your chatbot hands a chat over to your team.",
            ))
        return rows

    def transcript(self, session_id):
        if not session_id:
            return [result("Pick a chat first", "Open Recent chats and press Enter on a chat")]
        rows = [result(
            "Open this chat in Asyntai",
            "Reply, take over the chat, or see the visitor's details",
            call("open_url", self.client.dashboard_url(session_id)),
        )]
        for m in self.client.messages(session_id):
            content = api._s(m, "content") or ""
            if api._s(m, "role") == "user":
                who = "Visitor"
            elif api._s(m, "sender_type") == "human":
                who = api._s(m, "agent_name") or "Your team"
            else:
                who = "Chatbot"
            rows.append(result(
                one_line(plain(content)) or "(empty message)",
                joined(who, ago(api._s(m, "timestamp")), "Enter copies the message"),
                flow("CopyToClipboard", plain(content), False, True),
                {"kind": "message", "text": plain(content)},
                copy=plain(content),
            ))
        if len(rows) == 1:
            rows.append(result("This chat has no messages", ""))
        return rows

    def ask(self, question, with_hint=True):
        if not self.client.has_key:
            return [self.key_needed()]
        if not question:
            return ranked([result(
                "Type a question a visitor would ask",
                "Your chatbot answers from your website, like it answers visitors.",
            )])

        state = store.State()
        session_id = state.session(api.new_ask_session)
        answer = state.answer_for(question)

        if answer is None and state.data.get("pending") == question:
            # The user pressed Enter on this question. Clear the flag before
            # the call, so a cancelled query never asks the same thing twice.
            state.data["pending"] = None
            state.save()
            try:
                answer = self.client.ask(question, session_id)
            except api.AsyntaiError as e:
                return self.failed(e)
            state.add_answer(question, answer)
            state.save()

        if answer is not None:
            return ranked(self.answer_rows(question, answer))

        return ranked([result(
            "Ask your chatbot: " + one_line(question, 90),
            "Press Enter to ask. Your chatbot answers from your website.",
            call("ask_now", question, keep=True),
        )])

    def answer_rows(self, question, answer):
        text = plain(answer) or "(your chatbot sent an empty answer)"
        copy = flow("CopyToClipboard", text, False, True)
        context = {"kind": "answer", "text": text, "links": links(text)}

        width = store.line_width()
        lines = []
        for paragraph in text.splitlines():
            lines += textwrap.wrap(paragraph, width) or []
        if len(lines) > ANSWER_MAX_LINES:
            lines = lines[: ANSWER_MAX_LINES - 1] + [lines[ANSWER_MAX_LINES - 1].rstrip() + " …"]

        hint = "Your chatbot's answer · Enter copies it"
        if context["links"]:
            hint += " · Shift+Enter for links"
        rows = [result(one_line(question, 100), hint, copy, context, copy=text)]
        for line in lines:
            rows.append(result(line, "", copy, context, copy=text))
        rows.append(result(
            "Start a new conversation",
            "Enter starts fresh. Type a new question to keep the context.",
            call("new_conversation", keep=True),
        ))
        return rows

    # context menus ------------------------------------------------------

    def context_menu(self, data):
        if not isinstance(data, dict):
            return []
        kind = data.get("kind")
        rows = []

        if kind == "session" and data.get("sid"):
            rows.append(result("Open in Asyntai", "Reply or take over the chat in your browser",
                               call("open_url", data.get("dash", ""))))
            if data.get("page"):
                rows.append(result("Open the page", data["page"], call("open_url", data["page"])))
            for field, label in (("email", "Copy email"), ("phone", "Copy phone"), ("ticket", "Copy ticket number")):
                if data.get(field):
                    rows.append(result(label, data[field], flow("CopyToClipboard", data[field], False, True)))

        elif kind == "answer":
            if data.get("text"):
                rows.append(result("Copy the answer", one_line(data["text"], 90),
                                   flow("CopyToClipboard", data["text"], False, True)))
            for url in data.get("links") or []:
                rows.append(result("Open link", url, call("open_url", url)))

        elif kind == "message" and data.get("text"):
            rows.append(result("Copy the message", one_line(data["text"], 90),
                               flow("CopyToClipboard", data["text"], False, True)))

        return ranked(rows)

    # actions --------------------------------------------------------------

    def open_url(self, url):
        if isinstance(url, str) and url.startswith(("https://", "http://")):
            webbrowser.open(url)
        return None

    def ask_now(self, question):
        state = store.State()
        state.session(api.new_ask_session)
        state.data["pending"] = question
        state.save()
        # Run the same query again; it sees the flag and asks the chatbot.
        return flow("ReQuery", True)

    def new_conversation(self):
        state = store.State()
        state.reset(api.new_ask_session)
        state.save()
        return flow("ChangeQuery", store.query_prefix() + "ask ", True)
