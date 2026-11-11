"""The notification helper's contract, run as a process (see helperproc.py)."""

import json
import os
import unittest

from helperproc import Sandbox

LIMIT = 131072
FOCUS = 'hl.dsp.focus({ window = "address:'


class HelperTest(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)

    def focus(self, data, clients, op="focus"):
        """Runs one focus job in `serve`; returns (reply, dispatch argv lists)."""
        self.box.hypr("clients", clients)
        self.box.clear_calls()
        reply = self.box.job(op, data)
        return reply, self.box.dispatches()

    def focused(self, data, clients, op="focus"):
        """The address a focus job brought forward, or 'none'."""
        reply, dispatches = self.focus(data, clients, op)
        self.assertTrue(reply["ok"], reply)
        if reply["out"].strip() == "none":
            self.assertEqual(dispatches, [])
            return "none"
        self.assertEqual(reply["out"], "")
        self.assertEqual(len(dispatches), 1, dispatches)
        self.assertTrue(dispatches[0][0].startswith(FOCUS) and dispatches[0][0].endswith('" })'), dispatches)
        return dispatches[0][0][len(FOCUS) : -len('" })')]

    def miss(self, data, clients, op="focus"):
        reply, dispatches = self.focus(data, clients, op)
        self.assertFalse(reply["ok"], reply)
        self.assertTrue(reply["error"])
        self.assertEqual(dispatches, [])
        return reply.get("reason")

    def assertOrigin(self, body, host):
        clients = [
            {"class": "vivaldi", "address": "0x10"},
            {"class": "vivaldi-" + host + "__-Profile_1", "address": "0x11"},
        ]
        self.assertEqual(self.focused({"app": "Vivaldi", "body": body}, clients), "0x11", body)

    def assertNoOrigin(self, body):
        # Without an origin a browser sender is matched by its own window class.
        self.assertEqual(
            self.focused({"app": "Vivaldi", "body": body}, [{"class": "vivaldi", "address": "0x10"}]), "0x10", body
        )

    def test_browser_identity_is_exact_and_profiles_are_not_guessed(self):
        data = {"app": "Vivaldi", "body": '<a href="https://teams.microsoft.com/">teams.microsoft.com</a>\nAlex: Hi'}
        clients = [
            {"class": "vivaldi", "address": "0x10"},
            {"class": "vivaldi-teams.microsoft.com__-Profile_1", "address": "0x11"},
        ]
        self.assertEqual(self.focused(data, clients), "0x11")
        clients.append({"class": "vivaldi-teams.microsoft.com__-Profile_2", "address": "0x12"})
        self.assertEqual(self.miss(data, clients), "browser")
        data["mappings"] = [{"origin": "teams.microsoft.com", "windowClass": "vivaldi-teams.microsoft.com__-Profile_2"}]
        self.assertEqual(self.focused(data, clients), "0x12")
        self.assertNoOrigin("Alex says visit https://evil.invalid")
        self.assertNoOrigin('<a href="javascript:alert(1)">x</a>')

    def test_link_origins_use_the_url_host_only(self):
        self.assertOrigin("https://Teams.Microsoft.com/chat?x=1", "teams.microsoft.com")
        self.assertOrigin("<A HREF='https://user@teams.microsoft.com:443/x'>Teams</A> hi", "teams.microsoft.com")
        self.assertOrigin(
            '<a class="x" href="https://first.example/" data="2" href="https://teams.microsoft.com/">t</a>',
            "teams.microsoft.com",
        )
        self.assertOrigin("  \n https://teams.microsoft.com", "teams.microsoft.com")
        for edge in ["chrome", "msedge", "brave"]:
            clients = [{"class": edge + "-teams.microsoft.com__-Default", "address": "0x21"}]
            self.assertEqual(self.focused({"app": "Chromium", "body": "teams.microsoft.com"}, clients), "0x21")
        self.assertNoOrigin('<abbr href="https://teams.microsoft.com/">x</abbr>')
        self.assertNoOrigin('<a href="https://teams.microsoft.com/"')
        self.assertNoOrigin("ftp://teams.microsoft.com/")

    def test_sender_is_not_a_regex_or_dispatch_expression(self):
        self.assertEqual(self.focused({"app": ".*"}, [{"class": "anything", "address": "0x1"}]), "none")
        reply, dispatches = self.focus({"app": "Chat"}, [{"class": "Chat", "address": "0x1;evil"}])
        self.assertFalse(reply["ok"])
        self.assertIn("Invalid window address", reply["error"])
        self.assertNotIn("reason", reply)
        self.assertEqual(dispatches, [])

    def test_bare_browser_origin_selects_teams_instead_of_browser(self):
        data = {"app": "Vivaldi", "desktopEntry": "vivaldi-stable", "body": "teams.microsoft.com\nAlex: Hi"}
        clients = [
            {"class": "vivaldi-stable", "address": "0x10"},
            {"class": "vivaldi-teams.microsoft.com__-Profile_1", "address": "0x11"},
        ]
        self.assertEqual(self.focused(data, clients), "0x11")
        # A recognized web app must not fall back to the general browser if absent.
        self.assertEqual(self.miss(data, clients[:1]), "browser")
        clients.append({"class": "vivaldi-teams.microsoft.com__-Profile_2", "address": "0x12"})
        self.assertEqual(self.miss(data, clients), "browser")
        data["mappings"] = [{"origin": "teams.microsoft.com", "windowClass": "vivaldi-teams.microsoft.com__-Profile_2"}]
        self.assertEqual(self.focused(data, clients), "0x12")

    def test_bare_origin_requires_a_complete_hostname_line(self):
        for body in ["teams.microsoft.com", "  TEAMS.MICROSOFT.COM\r\nAlex: Hi", "teams.cloud.microsoft\nMessage"]:
            with self.subTest(body=body):
                self.assertOrigin(body, body.strip().splitlines()[0].lower())
        for body in [
            "Alex says visit teams.microsoft.com",
            "teams.microsoft.com is mentioned here",
            "Message\nteams.microsoft.com",
            "teams.microsoft.com/path",
            "teams.microsoft.com@evil.invalid",
            "-teams.microsoft.com",
            "teams..microsoft.com",
            "localhost",
            "a" * 64 + ".com",
            "teams.microsoft.c0m-",
            "x." * 126 + "com",
        ]:
            with self.subTest(body=body):
                self.assertNoOrigin(body)

    def test_non_browser_message_hostname_is_not_app_identity(self):
        clients = [
            {"class": "Chat", "address": "0x10"},
            {"class": "vivaldi-teams.microsoft.com__-Profile_1", "address": "0x11"},
        ]
        self.assertEqual(self.focused({"app": "Chat", "body": "teams.microsoft.com\nMessage"}, clients), "0x10")

    def test_app_matching_desktop_entry_substring_and_agent_windows(self):
        clients = [
            {"class": "org.gnome.Nautilus", "address": "0xa"},
            {"class": "Slack", "address": "0xb"},
            {
                "class": "org.omarchy.agent",
                "initialClass": "org.omarchy.agent",
                "initialTitle": "Claude Code",
                "address": "0xc",
            },
        ]
        self.assertEqual(self.focused({"app": "Files", "desktopEntry": "org.gnome.Nautilus.desktop"}, clients), "0xa")
        self.assertEqual(self.focused({"app": "SLACK"}, clients), "0xb")
        self.assertEqual(self.focused({"app": "nautilus"}, clients), "0xa")
        self.assertEqual(self.focused({"app": "claude"}, clients), "0xc")
        self.assertEqual(self.focused({"app": "", "desktopEntry": ""}, clients), "none")
        self.assertEqual(self.focused({}, clients), "none")

    def test_focus_miss_reasons_separate_windowless_senders_from_mapping_problems(self):
        clients = [
            {"class": "vivaldi-stable", "address": "0x10"},
            {"class": "Chat", "address": "0x11"},
            {"class": "Chat", "address": "0x12"},
        ]
        self.assertEqual(self.focused({"app": "omasnap"}, clients), "none")
        self.assertEqual(self.miss({"app": "Chat"}, clients), "ambiguous")
        self.assertEqual(self.miss({"app": "Vivaldi", "body": "teams.microsoft.com\nHi"}, clients), "browser")
        mapped = {
            "app": "Vivaldi",
            "body": "teams.microsoft.com",
            "mappings": [{"origin": "teams.microsoft.com", "windowClass": "gone"}],
        }
        self.assertEqual(self.miss(mapped, clients), "browser")
        self.assertIsNone(self.miss({"app": "Vivaldi", "body": "teams.microsoft.com", "mappings": None}, clients))

    def test_focus_reports_a_windowless_sender_without_failing(self):
        self.box.hypr("clients", [{"class": "Other", "address": "0x1"}])
        self.assertEqual(self.box.ok("focus", {"app": "omasnap"}), "none\n")
        self.assertEqual(self.box.dispatches(), [])
        # A browser app without its window is still an error: a mapping can fix it.
        result = self.box.run("focus", {"app": "Vivaldi", "body": "teams.microsoft.com"})
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stderr.decode().startswith("foamy.notifications: "), result.stderr)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(self.box.dispatches(), [])

    def test_focus_dispatch_uses_lua_syntax_then_the_legacy_dispatcher(self):
        self.box.hypr("clients", [{"class": "Chat", "address": "0xAbC1"}])
        self.assertEqual(self.box.ok("focus", {"app": "chat"}), "")
        self.assertEqual(self.box.calls(), [["-j", "clients"], ["dispatch", FOCUS + '0xAbC1" })']])
        (self.box.bin / "dispatch-fail-lua").touch()
        self.box.clear_calls()
        self.assertEqual(self.box.ok("focus", {"app": "chat"}), "")
        self.assertEqual(self.box.dispatches(), [[FOCUS + '0xAbC1" })'], ["focuswindow", "address:0xAbC1"]])
        (self.box.bin / "dispatch-fail-legacy").touch()
        self.assertEqual(self.box.run("focus", {"app": "chat"}).returncode, 1)

    def test_hyprland_failures_fail_the_job(self):
        (self.box.bin / "clients.json").write_text("not json")
        self.assertFalse(self.box.job("focus", {"app": "Chat"})["ok"])
        (self.box.bin / "clients.json").write_text("[" + " " * (4 * 1024 * 1024) + "]")
        self.assertFalse(self.box.job("focus", {"app": "Chat"})["ok"])
        self.assertEqual(self.box.dispatches(), [])
        (self.box.bin / "hyprctl").unlink()
        self.assertFalse(self.box.job("focus", {"app": "Chat"})["ok"])

    def test_serve_reports_the_focus_miss_reason(self):
        self.box.hypr("clients", [])
        jobs = [
            {"id": 1, "op": "focus", "data": {"app": "omasnap"}},
            {"id": 2, "op": "focus", "data": {"app": "Vivaldi", "body": "teams.microsoft.com"}},
        ]
        first, second = self.box.serve(jobs)
        self.assertEqual((first["ok"], first["out"], first.get("reason")), (True, "none\n", None))
        self.assertEqual((second["ok"], second["reason"]), (False, "browser"))
        self.assertEqual(list(first), ["id", "ok", "out", "error"])
        self.assertEqual(list(second), ["id", "ok", "out", "error", "reason"])

    def test_atomic_private_persistence_and_handled_removal(self):
        row = {"timestamp": 1000, "originalId": 3, "summary": "Hello", "body": "$(touch nope)"}
        self.box.write(dict(row, actionsJson='[{"identifier":"reply"}]'))
        path = self.box.popups / "1000-3.json"
        self.assertEqual(json.loads(path.read_text()), dict(row, appIcon="", image="", imageFormat=1))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.box.ok("forget", {"keys": ["1000-3"]})
        self.assertFalse(path.exists())
        # The center reads this journal; Foamy never runs the center's scripts.
        self.assertEqual((self.box.base / "handled").read_text().split(), ["1000-3"])
        self.box.ok("forget", {"keys": ["1000-4", "1000-3"]})
        self.assertEqual((self.box.base / "handled").read_text(), "1000-4\n1000-3\n")
        self.assertEqual((self.box.base / "handled").stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.box.run("forget", {"keys": ["../secrets"]}).returncode, 1)
        self.assertEqual(self.box.run("forget", {"keys": ["1-1"] * 101}).returncode, 1)
        self.assertEqual(self.box.run("forget", {}).returncode, 1)
        self.assertFalse((self.box.root / "nope").exists())

    def test_handled_journal_keeps_the_newest_thousand(self):
        self.box.base.mkdir(parents=True)
        (self.box.base / "handled").write_text(" ".join("%d-1" % i for i in range(1100)) + "\nbad ../x 5-5\n")
        self.box.ok("forget", {"keys": ["5-5", "9999-9"]})
        lines = (self.box.base / "handled").read_text().splitlines()
        self.assertEqual(len(lines), 1000)
        self.assertEqual(lines[-2:], ["5-5", "9999-9"])
        self.assertEqual(lines[0], "102-1")
        self.assertNotIn("bad", lines)

    def test_written_files_are_byte_compatible(self):
        entry = {
            "timestamp": 1000,
            "originalId": 3,
            "summary": "Hei på deg ☃ \U0001f600",
            "body": 'a\tb\n"q"\\ \x7f\x01/',
            "urgency": 2,
            "ratio": 0.5,
            "big": 1e16,
            "small": 1e-05,
            "neg": -0.0,
            "whole": 3.0,
            "flag": True,
            "none": None,
            "list": [1, [2, {}], []],
            "nested": {"k": "v", "n": -12345678901},
            "image": 3,
        }
        self.box.write(entry)
        expected = json.dumps(dict(entry, appIcon="", image="", imageFormat=1)) + "\n"
        self.assertEqual((self.box.popups / "1000-3.json").read_text(), expected)
        self.assertEqual(self.box.ok("read", {}), expected)

    def test_write_reports_sources_and_rejects_bad_identities(self):
        out = self.box.write(
            {"timestamp": 7, "originalId": 8, "summary": "S", "appIcon": "foot"}, sources={"image": "x"}, imageVersion=4
        )
        self.assertEqual(
            out,
            json.dumps({"key": "7-8", "version": 4, "sources": {"image": "x"}, "appIcon": "foot", "image": ""}) + "\n",
        )
        self.assertEqual(
            json.loads(self.box.write({"timestamp": 7, "originalId": 8, "summary": "S"}, sources=None))["version"], 0
        )
        self.assertEqual(self.box.write({"timestamp": "7", "originalId": "9", "summary": "S"}), "")
        self.assertTrue((self.box.popups / "7-9.json").exists())
        for entry in [
            {"timestamp": 1.5, "originalId": 1},
            {"timestamp": -1, "originalId": 1},
            {"timestamp": "x/../1", "originalId": 1},
            {"timestamp": 1},
            {"originalId": 1},
            {"timestamp": True, "originalId": 1},
        ]:
            with self.subTest(entry=entry):
                result = self.box.run("write", {"entry": dict(entry, summary="S")})
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertTrue(result.stderr.decode().startswith("foamy.notifications: "))
        self.assertEqual(self.box.run("write", {}).returncode, 1)
        self.assertEqual(sorted(p.name for p in self.box.popups.iterdir()), ["7-8.json", "7-9.json"])

    def test_one_shot_limits_and_errors(self):
        self.assertEqual(self.box.run("read", raw=b'{"x":"' + b"a" * LIMIT + b'"}').returncode, 1)
        self.assertEqual(self.box.run("read", raw=b'{"x":"' + b"a" * (LIMIT - 8) + b'"}').returncode, 0)
        self.assertEqual(self.box.run("read", raw=b"[]").returncode, 1)
        self.assertEqual(self.box.run("read", raw=b"not json").returncode, 1)
        result = self.box.run("no-such-op", {})
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stderr.decode().startswith("foamy.notifications: "))
        self.assertEqual(self.box.run(None, {}, args=[]).returncode, 1)
        self.assertEqual(self.box.ok("read", raw=b"{}"), "")

    def test_fifo_image_cannot_block_storage(self):
        fifo = self.box.root / "fifo"
        os.mkfifo(fifo)
        destination = self.box.images / "1000-3-appIcon"
        row = {"timestamp": 1000, "originalId": 3, "summary": "Hello", "appIcon": "file://" + str(destination)}
        result = self.box.run(
            "write", {"entry": row, "copies": [{"from": str(fifo), "to": str(destination)}]}, timeout=2
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.box.saved("1000-3")["appIcon"], "")

    def test_copies_must_target_the_entry_image_names(self):
        for to in [
            str(self.box.images / "1000-4-appIcon"),
            str(self.box.root / "1000-3-appIcon"),
            "images/1000-3-image",
        ]:
            with self.subTest(to=to):
                row = {"timestamp": 1000, "originalId": 3, "summary": "Hello"}
                self.assertEqual(
                    self.box.run("write", {"entry": row, "copies": [{"from": "/dev/null", "to": to}]}).returncode, 1
                )
        self.assertFalse((self.box.popups / "1000-3.json").exists())

    def test_corrupt_and_oversized_restores_are_skipped(self):
        root = self.box.popups
        root.mkdir(parents=True)
        (root / "1000-1.json").write_text("")
        (root / "1000-2.json").write_text("x" * (LIMIT + 1))
        (root / "1000-4.json").write_text(json.dumps({"timestamp": 1000, "originalId": 5, "summary": "Wrong name"}))
        (root / "1000-6.json").write_text(json.dumps({"timestamp": 1000, "originalId": 6}))
        (root / "0-7.json").write_text(json.dumps({"timestamp": 0, "originalId": 7, "summary": "Zero"}))
        (root / "1000-10.json").write_text("[1]")
        (root / "notes.json").write_text(json.dumps({"timestamp": 1, "originalId": 1, "summary": "x"}))
        (root / "1000-9.json").write_text(json.dumps({"timestamp": 1000, "originalId": 8, "summary": "Via link"}))
        (root / "1000-8.json").symlink_to(root / "1000-9.json")
        self.box.write({"timestamp": 1000, "originalId": 3, "summary": "Good"})
        result = self.box.run("read", {}, timeout=2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertEqual(json.loads(result.stdout)["summary"], "Good")

    def test_read_orders_newest_first_and_normalizes_old_rows(self):
        self.box.popups.mkdir(parents=True)
        for stamp, ident in [(5, 1), (40, 2), (40, 10), (300, 1)]:
            (self.box.popups / ("%d-%d.json" % (stamp, ident))).write_text(
                json.dumps(
                    {
                        "timestamp": stamp,
                        "originalId": ident,
                        "summary": "S",
                        "imageFormat": 1,
                        "appIcon": "",
                        "image": "",
                    }
                )
            )
        (self.box.popups / "7-7.json").write_text(
            json.dumps({"summary": "Old", "timestamp": 7, "originalId": 7, "image": "image://raw"})
        )
        out = self.box.ok("read", {})
        rows = [json.loads(line) for line in out.splitlines()]
        self.assertEqual(
            [(r["timestamp"], r["originalId"]) for r in rows], [(300, 1), (40, 2), (40, 10), (7, 7), (5, 1)]
        )
        self.assertEqual(
            out.splitlines()[3],
            json.dumps(
                {"summary": "Old", "timestamp": 7, "originalId": 7, "image": "", "appIcon": "", "imageFormat": 1}
            ),
        )
        self.assertEqual((self.box.popups / "7-7.json").read_text(), out.splitlines()[3] + "\n")
        self.assertEqual(self.box.ok("read", {"history": True}), "")

    def test_read_returns_at_most_a_thousand_rows(self):
        self.box.popups.mkdir(parents=True)
        for stamp in range(1, 1003):
            (self.box.popups / ("%d-1.json" % stamp)).write_text(
                json.dumps(
                    {"timestamp": stamp, "originalId": 1, "summary": "S", "appIcon": "", "image": "", "imageFormat": 1}
                )
            )
        rows = [json.loads(line) for line in self.box.ok("read", {}).splitlines()]
        self.assertEqual((len(rows), rows[0]["timestamp"], rows[-1]["timestamp"]), (1000, 1002, 3))

    def test_center_focus_uses_current_browser_mapping_and_ignores_commands(self):
        folder = self.box.home / ".config/omarchy"
        folder.mkdir(parents=True)
        mapping = {"origin": "teams.microsoft.com", "windowClass": "vivaldi-teams.microsoft.com__-Profile_2"}
        (folder / "shell.json").write_text(
            json.dumps(
                {
                    "plugins": [
                        {"id": "other", "browserMappings": "x"},
                        {"id": "foamy.notifications", "browserMappings": [mapping]},
                    ]
                }
            )
        )
        clients = [
            {"class": "vivaldi-teams.microsoft.com__-Profile_1", "address": "0x11"},
            {"class": "vivaldi-teams.microsoft.com__-Profile_2", "address": "0x12"},
        ]
        marker = self.box.root / "marker"
        data = {"app": "Vivaldi", "body": "teams.microsoft.com", "execArgv": '["touch", "%s"]' % marker, "mappings": []}
        self.assertEqual(self.focused(data, clients, op="focus-configured"), "0x12")
        self.assertFalse(marker.exists())
        (folder / "shell.json").write_text(
            json.dumps({"plugins": [{"id": "foamy.notifications", "browserMappings": [{"origin": 1}]}]})
        )
        self.assertIsNone(self.miss(data, clients, op="focus-configured"))
        (folder / "shell.json").write_text(
            json.dumps({"plugins": [{"id": "foamy.notifications", "browserMappings": [mapping] * 33}]})
        )
        self.assertIsNone(self.miss(data, clients, op="focus-configured"))
        (folder / "shell.json").unlink()
        self.assertEqual(self.miss(data, clients, op="focus-configured"), "browser")

    def test_context_reports_fullscreen_and_pointer_monitors(self):
        self.box.hypr(
            "monitors",
            [
                {
                    "name": "eDP-1",
                    "x": 0,
                    "y": 0,
                    "width": 2880,
                    "height": 1800,
                    "scale": 2,
                    "transform": 0,
                    "activeWorkspace": {"id": 1},
                    "specialWorkspace": {"id": 0},
                },
                {
                    "name": "DP-1",
                    "x": 1440,
                    "y": 0,
                    "width": 1920,
                    "height": 1080,
                    "scale": 1.0,
                    "transform": 1,
                    "activeWorkspace": {"id": 2},
                    "specialWorkspace": {"id": -98},
                },
            ],
        )
        self.box.hypr(
            "clients", [{"workspace": {"id": 1}, "fullscreen": 1}, {"workspace": {"id": -98}, "fullscreen": 2}]
        )
        self.box.hypr("cursorpos", {"x": 2000, "y": 1500})
        self.assertEqual(
            self.box.ok("context", {"fullscreen": True, "pointer": True}),
            json.dumps({"fullscreen": ["DP-1"], "pointer": "DP-1"}) + "\n",
        )
        self.assertEqual(self.box.calls(), [["-j", "monitors"], ["-j", "clients"], ["-j", "cursorpos"]])
        self.box.clear_calls()
        self.assertEqual(self.box.ok("context", {}), json.dumps({"fullscreen": [], "pointer": ""}) + "\n")
        self.assertEqual(self.box.calls(), [["-j", "monitors"]])
        self.box.hypr("cursorpos", {"x": 100, "y": 100})
        self.assertEqual(json.loads(self.box.ok("context", {"pointer": 1}))["pointer"], "eDP-1")
        self.box.hypr("monitors", [{"name": "X"}])
        self.assertEqual(self.box.run("context", {}).returncode, 1)

    def test_one_time_migration_from_the_4_0_layout(self):
        old = self.box.state / "omarchy/notifications"
        (old / "history").mkdir(parents=True)
        (old / "images").mkdir()
        image = "1000-1-image-" + "a" * 64 + ".png"
        (old / "images" / image).write_bytes(b"png")
        (old / "images/notes.txt").write_bytes(b"stock")
        (old / "1000-1.json").write_text(
            json.dumps(
                {"timestamp": 1000, "originalId": 1, "summary": "Live", "image": (old / "images" / image).as_uri()}
            )
        )
        (old / "history/900-2.json").write_text(json.dumps({"timestamp": 900, "originalId": 2, "summary": "Old"}))
        for stock in ("live.json", "history.json", "quiet.json"):
            (old / stock).write_text("[]")
        (self.box.state / "omarchy/notifications.json").write_text(json.dumps({"dnd": True, "snoozes": {"Chat": 5}}))
        self.assertEqual(self.box.ok("migrate", {}), json.dumps({"migrated": True, "moved": 2}) + "\n")
        live = self.box.saved("1000-1")
        self.assertEqual(live["image"], (self.box.images / image).as_uri())
        self.assertEqual((self.box.images / image).read_bytes(), b"png")
        self.assertEqual((self.box.images / image).stat().st_mode & 0o777, 0o600)
        self.assertTrue((self.box.history / "900-2.json").exists())
        self.assertFalse((old / "1000-1.json").exists())
        self.assertFalse((old / "images" / image).exists())
        self.assertTrue((old / "images/notes.txt").exists())
        for stock in ("live.json", "history.json", "quiet.json"):
            self.assertTrue((old / stock).exists())
        state = (self.box.base / "state.json").read_text()
        self.assertEqual(state, json.dumps({"version": 5, "dnd": True, "snoozes": {"Chat": 5}}, indent=2) + "\n")
        for folder in (self.box.base, self.box.popups, self.box.history, self.box.images):
            self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.box.base / ".migrated-from-omarchy-notifications").read_text(), "1\n")
        # Once only: later 4.0-layout files belong to whoever wrote them (stock 4.0).
        (old / "1100-3.json").write_text(json.dumps({"timestamp": 1100, "originalId": 3, "summary": "Stock"}))
        self.assertEqual(self.box.ok("migrate", {}), json.dumps({"migrated": False}) + "\n")
        self.assertTrue((old / "1100-3.json").exists())

    def test_migration_reads_dnd_from_stock_quiet_after_the_4_5_migration(self):
        old = self.box.state / "omarchy/notifications"
        old.mkdir(parents=True)
        (old / "quiet.json").write_text(json.dumps({"dnd": True, "snoozes": {}}))
        self.box.ok("migrate", {})
        self.assertTrue(json.loads((self.box.base / "state.json").read_text())["dnd"])

    def test_migration_without_old_state_and_with_existing_state(self):
        self.assertEqual(json.loads(self.box.ok("migrate", {})), {"migrated": True, "moved": 0})
        self.assertEqual(
            (self.box.base / "state.json").read_text(), '{\n  "version": 5,\n  "dnd": false,\n  "snoozes": {}\n}\n'
        )
        other = Sandbox()
        self.addCleanup(other.cleanup)
        other.base.mkdir(parents=True)
        os.chmod(other.base, 0o755)
        (other.base / "state.json").write_text('{"mine": 1}')
        other.ok("migrate", {})
        self.assertEqual((other.base / "state.json").read_text(), '{"mine": 1}')
        self.assertEqual(other.base.stat().st_mode & 0o777, 0o700)

    def test_serve_answers_each_job_and_drop_removes_own_copies(self):
        jobs = [
            {
                "id": 1,
                "op": "write",
                "data": {"entry": {"timestamp": 10, "originalId": 1, "summary": "A"}, "history": True},
            },
            {"id": 2, "op": "write", "data": {"entry": {"timestamp": 20, "originalId": 2, "summary": "B"}}},
            {"id": 3, "op": "drop", "data": {"before": 15}},
            {"id": 4, "op": "archive", "data": {"key": "20-2"}},
            {"id": 5, "op": "read", "data": {"history": True}},
            {"id": 6, "op": "unknown", "data": {}},
        ]
        replies = self.box.serve(jobs)
        self.assertEqual([r["id"] for r in replies], [1, 2, 3, 4, 5, 6])
        self.assertEqual([r["ok"] for r in replies], [True, True, True, True, True, False])
        self.assertEqual([json.loads(line)["summary"] for line in replies[4]["out"].splitlines()], ["B"])

    def test_serve_survives_bad_lines_and_creates_private_dirs(self):
        lines = [
            b"not json\n",
            b"\n",
            b"[1]\n",
            b'{"id": "x", "op": "read", "data": 5}\n',
            b'{"id": [1], "op": "read", "data": null}\n',
            b'{"id": 9, "op": "read", "data": {"pad": "' + b"a" * (LIMIT + 5000) + b'"}}\n',
            b'{"id": 10, "op": ["read"]}\n',
            b'{"id": 11, "op": "read"}\n',
            b'{"id": 12, "op": "read"}',
        ]
        replies = self.box.serve(None, raw=b"".join(lines))
        self.assertEqual([r["id"] for r in replies], [None, None, None, "x", [1], None, 10, 11, None])
        self.assertEqual([r["ok"] for r in replies], [False, False, False, False, True, False, False, True, False])
        self.assertEqual(replies[5]["error"], "Notification exceeds the storage limit")
        self.assertEqual(replies[8]["error"], "Notification exceeds the storage limit")
        self.assertEqual(replies[3]["error"], "Expected an object")
        for reply in replies:
            self.assertEqual(list(reply), ["id", "ok", "out", "error"])
            self.assertTrue(reply["ok"] or reply["error"])
        for folder in (self.box.base, self.box.popups, self.box.history, self.box.images):
            self.assertEqual(folder.stat().st_mode & 0o777, 0o700)

    def test_serve_accepts_a_line_up_to_the_limit(self):
        pad = LIMIT + 4096 - len(b'{"id": 1, "op": "read", "data": {"p": ""}}\n')
        line = b'{"id": 1, "op": "read", "data": {"p": "' + b"a" * pad + b'"}}\n'
        self.assertEqual(len(line), LIMIT + 4096)
        self.assertTrue(self.box.serve(None, raw=line + line)[1]["ok"])
        line = b'{"id": 1, "op": "read", "data": {"p": "' + b"a" * (pad + 1) + b'"}}\n'
        replies = self.box.serve(None, raw=line + b'{"id": 2, "op": "read"}\n')
        self.assertEqual([(r["id"], r["ok"]) for r in replies], [(None, False), (2, True)])

    def test_history_write_removes_the_stale_popup_copy(self):
        self.box.ok("migrate", {})
        image = self.box.images / ("5-5-image-" + "a" * 64 + ".png")
        image.write_bytes(b"png")
        self.box.write({"timestamp": 5, "originalId": 5, "summary": "Shown"})
        self.assertTrue((self.box.popups / "5-5.json").exists())
        # A queued update converted into a history write after the popup was dismissed.
        self.box.write({"timestamp": 5, "originalId": 5, "summary": "Updated"}, history=True)
        self.assertFalse((self.box.popups / "5-5.json").exists())
        self.assertEqual(self.box.saved("5-5", history=True)["summary"], "Updated")
        self.assertTrue(image.exists())

    def test_history_keeps_the_newest_hundred_with_their_images(self):
        self.box.ok("migrate", {})
        (self.box.images / "1-1-image").write_bytes(b"x")
        (self.box.images / ("1-1-appIcon-" + "b" * 64 + ".png")).write_bytes(b"x")
        (self.box.images / "1-1-notes").write_bytes(b"x")
        jobs = [
            {
                "id": i,
                "op": "write",
                "data": {"entry": {"timestamp": i, "originalId": 1, "summary": "S"}, "history": True},
            }
            for i in range(1, 106)
        ]
        self.assertTrue(all(r["ok"] for r in self.box.serve(jobs)))
        stamps = sorted(int(p.name.split("-")[0]) for p in self.box.history.iterdir())
        self.assertEqual(stamps, list(range(6, 106)))
        self.assertEqual(sorted(p.name for p in self.box.images.iterdir()), ["1-1-notes"])

    def test_archive_delete_clear_drop_and_sweep(self):
        self.box.ok("migrate", {})
        for key in ["1-1", "2-2", "3-3", "4-4"]:
            stamp, ident = key.split("-")
            self.box.write({"timestamp": int(stamp), "originalId": int(ident), "summary": key})
            (self.box.images / (key + "-image")).write_bytes(b"x")
            (self.box.images / (key + "-extra")).write_bytes(b"x")
        self.box.ok("archive", {"keys": ["1-1", "2-2", "9-9"]})
        self.assertEqual(sorted(p.name for p in self.box.history.iterdir()), ["1-1.json", "2-2.json"])
        self.assertEqual(sorted(p.name for p in self.box.popups.iterdir()), ["3-3.json", "4-4.json"])
        self.box.ok("delete", {"key": "3-3"})
        self.assertFalse((self.box.popups / "3-3.json").exists())
        self.assertFalse(any(p.name.startswith("3-3-") for p in self.box.images.iterdir()))
        self.box.ok("clear", {})
        self.assertEqual(list(self.box.history.iterdir()), [])
        self.assertEqual(
            sorted(p.name for p in self.box.images.iterdir()), ["1-1-extra", "2-2-extra", "4-4-extra", "4-4-image"]
        )
        self.box.write({"timestamp": 30, "originalId": 1, "summary": "H"}, history=True)
        self.box.ok("drop", {"keys": ["4-4"]})
        self.assertFalse((self.box.popups / "4-4.json").exists())
        self.box.ok("drop", {"before": 30})
        self.assertEqual(list(self.box.history.iterdir()), [])
        for bad in [{"before": -1}, {"before": 1.0}, {"before": True}, {"before": "3"}, {"keys": "x"}, {}]:
            with self.subTest(bad=bad):
                self.assertEqual(self.box.run("drop", bad).returncode, 1)
        for bad in [{"keys": ["x"]}, {"keys": ["1-1"] * 2049}, {"key": None}, {}]:
            with self.subTest(bad=bad):
                self.assertEqual(self.box.run("archive", bad).returncode, 1)
                self.assertEqual(self.box.run("delete", bad).returncode, 1)
        self.box.write({"timestamp": 50, "originalId": 5, "summary": "Live"})
        for name in [
            "50-5-image",
            "50-5-appIcon-ab.png",
            "60-6-image",
            "60-6-appIcon-" + "c" * 64 + ".png",
            "leftover.tmp",
            ".foamy-x",
            "keep.txt",
        ]:
            (self.box.images / name).write_bytes(b"x")
        (self.box.images / ".foamy-image-dir").mkdir()
        (self.box.images / ".foamy-image-dir/input").write_bytes(b"x")
        self.box.ok("sweep", {})
        self.assertEqual(
            sorted(p.name for p in self.box.images.iterdir()),
            ["1-1-extra", "2-2-extra", "4-4-extra", "50-5-appIcon-ab.png", "50-5-image", "keep.txt"],
        )

    def test_migration_moves_legacy_unhashed_image_copies(self):
        old = self.box.state / "omarchy/notifications"
        (old / "history").mkdir(parents=True)
        (old / "images").mkdir()
        (old / "images/900-2-image").write_bytes(b"legacy")
        (old / "history/900-2.json").write_text(
            json.dumps(
                {"timestamp": 900, "originalId": 2, "summary": "Old", "image": (old / "images/900-2-image").as_uri()}
            )
        )
        self.box.ok("migrate", {})
        moved = self.box.images / "900-2-image"
        self.assertTrue(moved.exists())
        self.assertEqual(self.box.saved("900-2", history=True)["image"], moved.as_uri())


if __name__ == "__main__":
    unittest.main()
