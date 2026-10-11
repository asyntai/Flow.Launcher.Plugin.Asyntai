"""Runs the Flow Launcher plugin the way Flow runs it, against a fake Asyntai.

Every call starts `python -c <Flow's bootstrap> <json>`, exactly like
Flow.Launcher.Core/Plugin/PythonPlugin.cs, inside a temporary Flow data
folder (Plugins/, Settings/, Cache/). The fake server records every request,
so the tests can prove what the plugin sends and when.

Run: python tests/test_plugin.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SRC = Path(__file__).resolve().parent.parent
PLUGIN_ID = "C06CEA5711924894B228BB12694A6D44"
GOOD_KEY = "test_key_ok"
FREE_KEY = "test_key_free"

ANSWER = (
    "**Shipping** is free over 50 EUR. See [our shipping page](https://shop.example/shipping) "
    "for details.\n- Czech Republic: 1 day\n- Slovakia: 2 days\nPrice: 120 Kč ✓"
)


class Fake(BaseHTTPRequestHandler):
    log = []

    def log_message(self, *a):
        pass

    def _send(self, status, body):
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle(self, method):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        url = urlparse(self.path)
        Fake.log.append({
            "method": method, "path": url.path, "query": parse_qs(url.query),
            "auth": self.headers.get("Authorization"), "ua": self.headers.get("User-Agent"), "body": body,
        })
        auth = self.headers.get("Authorization") or ""
        if auth == "Bearer " + FREE_KEY:
            return self._send(403, {"success": False, "error": "API access requires a paid plan (Starter, Standard, or Pro)."})
        if auth != "Bearer " + GOOD_KEY:
            return self._send(401, {"success": False, "error": "Invalid API key"})

        if url.path == "/api/v1/sessions/":
            return self._send(200, {"success": True, "sessions": [
                {"session_id": "abc123", "first_message": "Do you ship\nto Slovakia?", "last_message_at": "2026-10-11T08:00:00Z",
                 "message_count": 3, "website_domain": "shop.example", "country": "SK", "page_url": "https://shop.example/p/1"},
                {"session_id": "api_flow_ask_1_aaaaaa", "first_message": "my own question", "message_count": 2},
                {"session_id": "cmdpal_ask_2_bbbbbb", "first_message": "palette question", "message_count": 2},
                {"session_id": "def456", "first_message": "Opening hours?", "message_count": 1, "website_domain": "shop.example"},
            ]})
        if url.path == "/api/v1/leads/":
            return self._send(200, {"success": True, "leads": [
                {"session_id": "abc123", "email": "lead@example.test", "phone": "+420 111 222 333",
                 "page_url": "https://shop.example/p/1", "started_at": "2026-10-10T08:00:00Z"},
            ]})
        if url.path == "/api/v1/tickets/":
            return self._send(200, {"success": True, "tickets": [
                {"ticket_number": "1042", "session_id": "abc123", "status": "open", "subject": "Broken parcel",
                 "visitor_email": "lead@example.test", "created_at": "2026-10-09T08:00:00Z"},
            ]})
        if url.path == "/api/v1/conversations/":
            return self._send(200, {"success": True, "session_id": "abc123", "messages": [
                {"role": "user", "content": "Do you ship to Slovakia?", "timestamp": "2026-10-11T08:00:00Z"},
                {"role": "assistant", "content": "Yes, in **2 days**.", "sender_type": "ai", "timestamp": "2026-10-11T08:00:02Z"},
                {"role": "assistant", "content": "Hi, Jana here.", "sender_type": "human", "agent_name": "Jana"},
            ]})
        if url.path == "/api/v1/chat/" and method == "POST":
            return self._send(200, {"success": True, "response": ANSWER, "session_id": body.get("session_id")})
        return self._send(404, {"success": False, "error": "not found"})

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")


class FlowPluginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        Fake.log.clear()
        self.data = Path(tempfile.mkdtemp(prefix="flowtest_"))
        self.plugin = self.data / "Plugins" / "Asyntai AI Chatbot-1.0.0"
        shutil.copytree(SRC, self.plugin, ignore=shutil.ignore_patterns("tests", ".git", ".github", "__pycache__"))
        self.settings = self.data / "Settings" / "Plugins" / "Asyntai AI Chatbot"
        self.settings.mkdir(parents=True)
        (self.settings / "dev_base_url.txt").write_text("http://127.0.0.1:%d" % self.port, encoding="utf-8")
        self.base = "http://127.0.0.1:%d" % self.port
        self.key = GOOD_KEY

    def tearDown(self):
        shutil.rmtree(self.data, ignore_errors=True)

    # -- running the plugin like Flow -----------------------------------

    def rpc(self, method, *params, settings=True):
        request = {"id": 1, "method": method, "parameters": list(params)}
        if settings:
            request["settings"] = {"apiKey": self.key}
        plugindir = str(self.plugin)
        bootstrap = (
            "import sys\n"
            "sys.path.append(r'%s')\n"
            "sys.path.append(r'%s')\n"
            "sys.path.append(r'%s')\n"
            "import runpy\n"
            "runpy.run_path(r'%s', None, '__main__')\n"
        ) % (plugindir, os.path.join(plugindir, "lib"), os.path.join(plugindir, "plugin"),
             os.path.join(plugindir, "main.py"))
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        p = subprocess.run([sys.executable, "-c", bootstrap, json.dumps(request)],
                           cwd=plugindir, capture_output=True, env=env, timeout=60)
        self.assertEqual(p.stderr, b"", "stderr must stay empty, Flow treats it as an error")
        out = p.stdout.decode("ascii")  # ensure_ascii output: survives any console code page
        return json.loads(out) if out.strip() else None

    def query(self, text):
        return self.rpc("query", text)["result"]

    def run_action(self, row):
        action = row["JsonRPCAction"]
        self.assertFalse(action["method"].startswith("Flow.Launcher."), "this action is run by Flow itself")
        return self.rpc(action["method"], *action["parameters"], settings=False)

    def posts(self):
        return [r for r in Fake.log if r["method"] == "POST"]

    def titles(self, rows):
        return [r["Title"] for r in rows]

    # -- tests ----------------------------------------------------------

    def test_menu_without_key_asks_for_it(self):
        self.key = ""
        rows = self.query("")
        self.assertEqual(rows[0]["Title"], "Add your Asyntai API key")
        self.assertEqual(rows[0]["JsonRPCAction"]["method"], "Flow.Launcher.OpenSettingDialog")
        self.assertEqual(Fake.log, [], "the menu never calls the API")

    def test_menu_order_and_keyword(self):
        rows = self.query("")
        self.assertEqual(self.titles(rows), ["Recent chats", "Leads", "Tickets", "Ask your chatbot", "Open the Asyntai dashboard"])
        scores = [r["Score"] for r in rows]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(rows[0]["JsonRPCAction"], {"method": "Flow.Launcher.ChangeQuery",
                                                    "parameters": ["asy chats ", True], "dontHideAfterAction": True})
        self.assertEqual(rows[-1]["JsonRPCAction"]["parameters"], [self.base + "/dashboard/"])

    def test_menu_follows_a_changed_keyword(self):
        (self.data / "Settings" / "Settings.json").write_text(json.dumps(
            {"PluginSettings": {"Plugins": {PLUGIN_ID: {"ActionKeywords": ["ai"]}}}}), encoding="utf-8")
        self.assertEqual(self.query("")[0]["JsonRPCAction"]["parameters"][0], "ai chats ")
        (self.data / "Settings" / "Settings.json").write_text(json.dumps(
            {"PluginSettings": {"Plugins": {PLUGIN_ID: {"ActionKeywords": ["*"]}}}}), encoding="utf-8")
        self.assertEqual(self.query("")[0]["JsonRPCAction"]["parameters"][0], "chats ")

    def test_chats_hide_own_questions(self):
        rows = self.query("chats")
        self.assertEqual(self.titles(rows), ["Do you ship to Slovakia?", "Opening hours?"])
        self.assertIn("3 messages", rows[0]["SubTitle"])
        self.assertIn("shop.example", rows[0]["SubTitle"])
        # Enter reads the chat right in Flow.
        self.assertEqual(rows[0]["JsonRPCAction"], {"method": "Flow.Launcher.ChangeQuery",
                                                    "parameters": ["asy transcript abc123", True], "dontHideAfterAction": True})
        req = Fake.log[0]
        self.assertEqual(req["path"], "/api/v1/sessions/")
        self.assertEqual(req["query"], {"limit": ["50"]})
        self.assertEqual(req["auth"], "Bearer " + GOOD_KEY)
        self.assertTrue(req["ua"].startswith("AsyntaiFlowLauncher/"))

    def test_chats_filter(self):
        self.assertEqual(self.titles(self.query("chats slovakia")), ["Do you ship to Slovakia?"])
        self.assertEqual(self.titles(self.query("chats zzz")), ["No chats found"])

    def test_chat_context_menu(self):
        row = self.query("chats")[0]
        menu = self.rpc("context_menu", row["ContextData"], settings=False)["result"]
        self.assertEqual(self.titles(menu), ["Open in Asyntai", "Open the page"])
        self.assertEqual(menu[0]["JsonRPCAction"], {"method": "open_url", "parameters": [self.base + "/chat/abc123/"],
                                                    "dontHideAfterAction": False})

    def test_leads(self):
        rows = self.query("leads")
        self.assertEqual(rows[0]["Title"], "lead@example.test")
        self.assertIn("+420 111 222 333", rows[0]["SubTitle"])
        self.assertEqual(rows[0]["CopyText"], "lead@example.test")
        menu = self.rpc("context_menu", rows[0]["ContextData"], settings=False)["result"]
        self.assertEqual(self.titles(menu), ["Open in Asyntai", "Open the page", "Copy email", "Copy phone"])
        self.assertEqual(menu[2]["JsonRPCAction"], {"method": "Flow.Launcher.CopyToClipboard",
                                                    "parameters": ["lead@example.test", False, True], "dontHideAfterAction": False})

    def test_tickets(self):
        rows = self.query("tickets")
        self.assertEqual(rows[0]["Title"], "#1042 Broken parcel")
        self.assertTrue(rows[0]["SubTitle"].startswith("Open"))
        menu = self.rpc("context_menu", rows[0]["ContextData"], settings=False)["result"]
        self.assertIn("Copy ticket number", self.titles(menu))

    def test_transcript(self):
        rows = self.query("transcript abc123")
        self.assertEqual(rows[0]["Title"], "Open this chat in Asyntai")
        self.assertEqual(self.titles(rows[1:]), ["Do you ship to Slovakia?", "Yes, in 2 days.", "Hi, Jana here."])
        self.assertTrue(rows[1]["SubTitle"].startswith("Visitor"))
        self.assertTrue(rows[2]["SubTitle"].startswith("Chatbot"))
        self.assertTrue(rows[3]["SubTitle"].startswith("Jana"))
        self.assertEqual(Fake.log[0]["query"], {"session_id": ["abc123"], "limit": ["100"]})

    def test_ask_needs_enter_before_any_paid_call(self):
        for partial in ("ask W", "ask What", "ask What is shipping", "ask What is shipping?"):
            rows = self.query(partial)
        self.assertEqual(rows[0]["Title"], "Ask your chatbot: What is shipping?")
        self.assertEqual(rows[0]["JsonRPCAction"], {"method": "ask_now", "parameters": ["What is shipping?"],
                                                    "dontHideAfterAction": True})
        self.assertEqual(self.posts(), [], "typing alone must never call the chatbot")

    def test_ask_flow(self):
        prompt = self.query("ask What is shipping?")[0]
        out = self.run_action(prompt)
        self.assertEqual(out, {"method": "Flow.Launcher.ReQuery", "parameters": [True], "dontHideAfterAction": False})
        self.assertEqual(self.posts(), [])

        rows = self.query("ask What is shipping?")
        posts = self.posts()
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["path"], "/api/v1/chat/")
        self.assertEqual(posts[0]["body"]["message"], "What is shipping?")
        session = posts[0]["body"]["session_id"]
        self.assertTrue(session.startswith("flow_ask_"))

        self.assertEqual(rows[0]["Title"], "What is shipping?")
        body = " ".join(r["Title"] for r in rows[1:])
        self.assertIn("Shipping is free over 50 EUR", body)
        self.assertNotIn("**", body)
        self.assertIn("our shipping page (https://shop.example/shipping)", body)
        self.assertIn("• Czech Republic: 1 day", body)
        self.assertIn("120 Kč ✓", body)
        self.assertEqual(rows[1]["JsonRPCAction"]["method"], "Flow.Launcher.CopyToClipboard")
        self.assertEqual(rows[0]["CopyText"], rows[1]["JsonRPCAction"]["parameters"][0])

        # Flow runs the same query again (window reopened, requery): no new call.
        self.query("ask What is shipping?")
        self.assertEqual(len(self.posts()), 1)

        # A follow-up keeps the conversation.
        self.run_action(self.query("ask And to Austria?")[0])
        self.query("ask And to Austria?")
        self.assertEqual(len(self.posts()), 2)
        self.assertEqual(self.posts()[1]["body"]["session_id"], session)

        # The answer's context menu: copy and links.
        menu = self.rpc("context_menu", rows[0]["ContextData"], settings=False)["result"]
        self.assertEqual(self.titles(menu), ["Copy the answer", "Open link"])
        self.assertEqual(menu[1]["JsonRPCAction"]["parameters"], ["https://shop.example/shipping"])
        self.assertIn("Shift+Enter for links", rows[0]["SubTitle"])
        # The last answer row starts a new conversation.
        self.assertEqual(rows[-1]["Title"], "Start a new conversation")
        out = self.run_action(rows[-1])
        self.assertEqual(out["method"], "Flow.Launcher.ChangeQuery")
        self.assertEqual(out["parameters"], ["asy ask ", True])
        self.run_action(self.query("ask Hello")[0])
        self.query("ask Hello")
        self.assertNotEqual(self.posts()[2]["body"]["session_id"], session)

        state = (self.data / "Cache" / "Plugins" / "Asyntai AI Chatbot" / "state.json").read_text(encoding="utf-8")
        self.assertNotIn(GOOD_KEY, state, "the key is never written to the cache")

    def test_no_context_menu_changes_the_query(self):
        # Inside a context menu Flow uses a new query text to filter that
        # menu, so a ChangeQuery there would show an empty list.
        datas = [r["ContextData"] for q in ("chats", "leads", "tickets", "transcript abc123")
                 for r in self.query(q) if r.get("ContextData")]
        self.run_action(self.query("ask What is shipping?")[0])
        datas += [r["ContextData"] for r in self.query("ask What is shipping?") if r.get("ContextData")]
        self.assertTrue(datas)
        for data in datas:
            for row in self.rpc("context_menu", data, settings=False)["result"]:
                self.assertNotIn(row["JsonRPCAction"]["method"], ("Flow.Launcher.ChangeQuery", "Flow.Launcher.ReQuery"))
                if not row["JsonRPCAction"]["method"].startswith("Flow.Launcher."):
                    out = None if row["JsonRPCAction"]["method"] == "open_url" else self.run_action(row)
                    self.assertIsNone(out)

    def test_answer_lines_fit_the_window(self):
        self.run_action(self.query("ask What is shipping?")[0])
        narrow = [r["Title"] for r in self.query("ask What is shipping?")[1:-1]]
        self.assertTrue(all(len(t) <= 56 for t in narrow), narrow)
        (self.data / "Settings" / "Settings.json").write_text(json.dumps({"WindowSize": 900}), encoding="utf-8")
        wide = [r["Title"] for r in self.query("ask What is shipping?")[1:-1]]
        self.assertLess(len(wide), len(narrow))
        self.assertTrue(any(len(t) > 56 for t in wide), wide)
        self.assertEqual(len(self.posts()), 1, "re-wrapping uses the stored answer")

    def test_free_text_is_a_question(self):
        rows = self.query("how much is shipping")
        self.assertEqual(self.titles(rows), ["Ask your chatbot: how much is shipping"])
        rows = self.query("le")
        self.assertEqual(self.titles(rows), ["Leads", "Ask your chatbot: le"])
        self.assertEqual(self.posts(), [])

    def test_bad_key(self):
        self.key = "wrong"
        rows = self.query("chats")
        self.assertEqual(rows[0]["Title"], "Asyntai no longer accepts this key. Open the plugin settings and paste a new key.")
        self.assertEqual(rows[0]["JsonRPCAction"]["method"], "Flow.Launcher.OpenSettingDialog")

    def test_free_plan(self):
        self.key = FREE_KEY
        rows = self.query("leads")
        self.assertIn("paid plan", rows[0]["Title"])
        self.assertEqual(rows[0]["JsonRPCAction"]["method"], "Flow.Launcher.ReQuery")

    def test_server_down(self):
        (self.settings / "dev_base_url.txt").write_text("http://127.0.0.1:9", encoding="utf-8")
        rows = self.query("tickets")
        self.assertEqual(rows[0]["Title"], "Asyntai did not answer. Check your connection.")

    def test_dev_url_only_for_localhost(self):
        (self.settings / "dev_base_url.txt").write_text("https://evil.example", encoding="utf-8")
        rows = self.query("")
        self.assertEqual(rows[-1]["JsonRPCAction"]["parameters"], ["https://asyntai.com/dashboard/"])

    def test_release_files(self):
        manifest = json.loads((SRC / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["ID"], PLUGIN_ID)
        self.assertEqual(manifest["Language"], "python")
        self.assertTrue((SRC / manifest["ExecuteFileName"]).is_file())
        self.assertTrue((SRC / manifest["IcoPath"].replace("\\", "/")).is_file())
        template = (SRC / "SettingsTemplate.yaml").read_text(encoding="utf-8")
        self.assertIn("type: passwordBox", template)
        self.assertIn("name: apiKey", template)
        # Flow 2.1 drops any settings item without a name (JsonRPCPluginSettings.CreateSettingPanel).
        items = template.split("  - type:")[1:]
        self.assertEqual(len(items), 3)
        for item in items:
            self.assertIn("      name:", item, item)


if __name__ == "__main__":
    unittest.main(verbosity=2)
