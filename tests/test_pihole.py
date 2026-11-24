"""Pi-hole setup (pihole/): the fail-safe settings, and pihole.sh run against
fake system commands (nmcli, ss, docker, dig...) so no Pi or network is needed."""
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIHOLE = os.path.join(ROOT, "pihole")
SCRIPT = os.path.join(PIHOLE, "pihole.sh")

# Each fake logs its arguments, then answers from FAKE_* environment variables.
FAKES = {
    "sudo": 'exec "$@"',
    "findmnt": 'echo "${FAKE_ROOT_DEV:-/dev/mmcblk0p2}"',
    "ss": 'case "$*" in *":53"*) printf "%s" "${FAKE_SS_53:-}" ;; *":8080"*) printf "%s" "${FAKE_SS_8080:-}" ;; esac',
    "ip": 'case "$*" in *"route get"*) echo "1.1.1.1 via 192.168.50.1 dev eth0 src 192.168.50.20 uid 1000" ;; esac',
    "systemctl": 'case "$*" in *NetworkManager*) exit 0 ;; *) exit 3 ;; esac',
    "dpkg": "echo arm64",
    "getent": 'echo "93.184.215.14 example.com"',
    "sleep": "exit 0",
    "iw": "exit 0",
    "rm": "exit 0",  # never the real rm: on the Pi this repo is $HOME
    "nmcli": """case "$*" in
  "-g UUID,TYPE,DEVICE connection show --active") printf "%b" "$FAKE_CONNS" ;;
  "-g ipv6.method connection show"*) echo "${FAKE_IPV6_METHOD:-auto}" ;;
  "-g connection.id connection show"*) echo "Wired connection 1" ;;
  "-g IP4.DNS device show"*) echo "192.168.50.1" ;;
esac""",
    "docker": """case "$*" in
  "exec pihole pihole-FTL --config misc.privacylevel") echo "${FAKE_PRIVACYLEVEL:-1}" ;;
  "exec pihole pihole-FTL --config dns.queryLogging") echo "${FAKE_QUERYLOGGING:-false}" ;;
  "--version") echo "Docker version 27.0.0" ;;
  "inspect -f {{.State.Running}} pihole") [ "${FAKE_RUNNING:-0}" = 1 ] && echo true || exit 1 ;;
  inspect*) echo healthy ;;
  *gravity.db*"number = 0"*) printf "%s" "${FAKE_EMPTY_LIST:-}" ;;
  *gravity.db*"FROM adlist ORDER BY id"*) echo "yes|123|https://example/list" ;;
  *gravity.db*) echo 1000 ;;
esac""",
    "dig": """d="${@: -2:1}"
case "$d" in
  pagead2.googlesyndication.com|ad.doubleclick.net) echo "${FAKE_AD_ANSWER-0.0.0.0}" ;;
  *) echo 93.184.215.14 ;;
esac""",
}


class PiholeScript(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bin = os.path.join(self.tmp, "bin")
        os.mkdir(self.bin)
        self.log = os.path.join(self.tmp, "calls.log")
        for name, body in FAKES.items():
            path = os.path.join(self.bin, name)
            with open(path, "w") as f:
                f.write(f'#!/usr/bin/env bash\necho "{name} $*" >> "$FAKE_LOG"\n{body}\n')
            os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        self.resolv = os.path.join(self.tmp, "resolv.conf")
        self.set_resolv("1.1.1.1", "9.9.9.9")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def set_resolv(self, *servers):
        with open(self.resolv, "w") as f:
            f.write("".join(f"nameserver {s}\n" for s in servers))

    def run_script(self, *args, answer="", **fake):
        env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}",
                   FAKE_LOG=self.log, PIHOLE_RESOLV_CONF=self.resolv,
                   FAKE_CONNS="uuid-eth:802-3-ethernet:eth0\n")
        env.update({f"FAKE_{k.upper()}": v for k, v in fake.items()})
        return subprocess.run(["bash", SCRIPT, *args], input=answer, env=env,
                              capture_output=True, text=True, timeout=30)

    def log_text(self):
        if not os.path.exists(self.log):
            return ""
        with open(self.log) as f:
            return f.read()

    def calls(self, prefix):
        return [line.strip() for line in self.log_text().splitlines() if line.startswith(prefix)]

    # ── check ──
    def test_check_reports_microsd_and_free_port(self):
        r = self.run_script("check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("microSD card (/dev/mmcblk0p2)", r.stdout)
        self.assertIn("Port 53 is free", r.stdout)
        self.assertIn("7 days", r.stdout)

    def test_check_reports_nvme(self):
        r = self.run_script("check", root_dev="/dev/nvme0n1p2")
        self.assertIn("NVMe SSD", r.stdout)

    def test_check_fails_and_names_whatever_holds_port_53(self):
        r = self.run_script("check", ss_53='udp UNCONN 0 0 0.0.0.0:53 0.0.0.0:* users:(("dnsmasq",pid=612,fd=4))')
        self.assertEqual(r.returncode, 1)
        self.assertIn("Port 53 is in use", r.stdout)
        self.assertIn("dnsmasq", r.stdout)

    # ── host-dns ──
    def test_host_dns_changes_nothing_without_a_yes(self):
        r = self.run_script("host-dns", answer="\n")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Nothing changed", r.stdout)
        self.assertFalse([c for c in self.calls("nmcli") if "modify" in c or "reapply" in c])

    def test_host_dns_sets_public_resolvers_and_ignores_dhcp_dns(self):
        r = self.run_script("host-dns", answer="y\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        mods = [c for c in self.calls("nmcli") if "modify" in c]
        self.assertIn("nmcli connection modify uuid-eth ipv4.dns 1.1.1.1 9.9.9.9 ipv4.ignore-auto-dns yes", mods)
        self.assertTrue(any("ipv6.dns" in c and "ipv6.ignore-auto-dns yes" in c for c in mods))
        self.assertIn("nmcli device reapply eth0", self.calls("nmcli"))
        self.assertFalse([c for c in mods if "powersave" in c])

    def test_host_dns_turns_off_wifi_power_saving_without_reconnecting(self):
        r = self.run_script("host-dns", answer="y\n", conns="uuid-wifi:802-11-wireless:wlan0\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lines = self.log_text().splitlines()
        # saved only after the reapply, so the reapply never has to reconnect Wi-Fi
        self.assertLess(lines.index("nmcli device reapply wlan0"),
                        lines.index("nmcli connection modify uuid-wifi 802-11-wireless.powersave 2"))
        self.assertIn("iw dev wlan0 set power_save off", lines)
        self.assertFalse([line for line in lines if "connection up" in line])
        self.assertIn("Wi-Fi power saving is off", r.stdout)

    def test_host_dns_skips_ipv6_when_ipv6_is_off(self):
        r = self.run_script("host-dns", answer="y\n", ipv6_method="disabled")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse([c for c in self.calls("nmcli") if "ipv6.dns" in c])

    def test_host_dns_leaves_other_connection_types_alone(self):
        conns = "uuid-eth:802-3-ethernet:eth0\nuuid-wifi:802-11-wireless:wlan0\nuuid-d:bridge:docker0\n"
        self.run_script("host-dns", answer="y\n", conns=conns)
        mods = " ".join(c for c in self.calls("nmcli") if "ipv4.dns" in c)
        self.assertIn("uuid-eth", mods)
        self.assertIn("uuid-wifi", mods)
        self.assertNotIn("uuid-d", mods)

    # ── start / lists ──
    def test_start_refuses_when_port_53_is_taken(self):
        r = self.run_script("start", ss_53="udp UNCONN 0 0 127.0.0.53%lo:53 users:((\"systemd-resolve\"))")
        self.assertEqual(r.returncode, 1)
        self.assertIn("systemd-resolve", r.stdout)
        self.assertFalse(self.calls("docker compose"))

    def test_start_brings_pihole_up(self):
        r = self.run_script("start")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("docker compose up -d", self.calls("docker compose"))
        self.assertIn(":8080/admin", r.stdout)

    def test_lists_rejects_unknown_level(self):
        r = self.run_script("lists", "ultimate", running="1")
        self.assertEqual(r.returncode, 2)

    def test_lists_adds_the_chosen_hagezi_level_and_switches_others_off(self):
        r = self.run_script("lists", "normal", running="1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        sql = self.log_text()  # the SQL spans several lines
        self.assertIn("UPDATE adlist SET enabled = 0 WHERE address LIKE '%hagezi/dns-blocklists%'", sql)
        self.assertIn("INSERT OR IGNORE INTO adlist", sql)
        self.assertIn("/adblock/multi.txt", sql)
        self.assertIn("docker exec pihole pihole -g", self.calls("docker exec pihole pihole -g"))

    def test_lists_flags_a_list_that_came_back_empty(self):
        r = self.run_script("lists", "pro", running="1", empty_list="1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("0 domains", r.stdout)

    # ── privacy ──
    def test_privacy_on_hides_website_names(self):
        r = self.run_script("privacy", "on", answer="n\n", running="1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        calls = self.calls("docker")
        self.assertIn("docker exec pihole pihole-FTL --config misc.privacylevel 1", calls)
        self.assertIn("docker exec pihole pihole-FTL --config dns.queryLogging false", calls)
        self.assertIn("docker restart pihole", calls)
        self.assertFalse(self.calls("rm"))
        self.assertIn("Privacy on", r.stdout)

    def test_privacy_on_can_delete_the_stored_history(self):
        r = self.run_script("privacy", "on", answer="y\n", running="1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lines = self.log_text().splitlines()
        down = lines.index("docker compose down")
        wipe = lines.index("rm -f etc-pihole/pihole-FTL.db etc-pihole/pihole-FTL.db-wal etc-pihole/pihole-FTL.db-shm")
        up = lines.index("docker compose up -d")
        self.assertLess(down, wipe)  # never under a running Pi-hole
        self.assertLess(wipe, up)

    def test_privacy_off_records_names_again_and_says_so(self):
        r = self.run_script("privacy", "off", running="1", privacylevel="0", querylogging="true")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("docker exec pihole pihole-FTL --config misc.privacylevel 0", self.calls("docker"))
        self.assertIn("./pihole.sh privacy on", r.stdout)

    def test_privacy_fails_when_the_setting_did_not_take(self):
        r = self.run_script("privacy", "on", answer="n\n", running="1", privacylevel="0")
        self.assertEqual(r.returncode, 1)
        self.assertIn("didn't switch on", r.stdout)

    def test_health_warns_while_privacy_is_off(self):
        r = self.run_script("health", running="1", privacylevel="0", querylogging="true")
        self.assertIn("Privacy is off", r.stdout)

    # ── test ──
    def test_test_passes_when_ads_are_blocked_and_sites_resolve(self):
        r = self.run_script("test")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("pagead2.googlesyndication.com -> 0.0.0.0 (blocked)  [via 192.168.50.20]", r.stdout)
        self.assertIn("nslookup pagead2.googlesyndication.com 192.168.50.20", r.stdout)

    def test_test_fails_when_an_ad_domain_is_not_blocked(self):
        r = self.run_script("test", ad_answer="142.250.72.2")
        self.assertEqual(r.returncode, 1)
        self.assertIn("expected 0.0.0.0", r.stdout)

    def test_test_fails_when_the_pi_uses_pihole_for_itself(self):
        self.set_resolv("192.168.50.20", "1.1.1.1")
        r = self.run_script("test")
        self.assertEqual(r.returncode, 1)
        self.assertIn("run ./pihole.sh host-dns", r.stdout)


class PiholeConfig(unittest.TestCase):
    """The settings the fail-safe design depends on."""

    def setUp(self):
        with open(os.path.join(PIHOLE, "docker-compose.yml")) as f:
            self.compose = f.read()

    def test_restarts_on_its_own_and_keeps_its_data(self):
        self.assertIn("restart: unless-stopped", self.compose)
        self.assertIn('"./etc-pihole:/etc/pihole"', self.compose)

    def test_web_ui_on_8080_and_port_80_left_free(self):
        ports = re.findall(r'^\s*- "(\d+):(\d+)/(tcp|udp)"', self.compose, re.M)
        self.assertIn(("8080", "80", "tcp"), ports)
        self.assertIn(("53", "53", "udp"), ports)
        self.assertIn(("53", "53", "tcp"), ports)
        self.assertNotIn("80", [host for host, _, _ in ports])

    def test_short_query_history_for_the_microsd_card(self):
        days = re.search(r'FTLCONF_database_maxDBdays: "(\d+)"', self.compose)
        self.assertTrue(days and int(days.group(1)) <= 14)

    def test_no_password_in_the_compose_file(self):
        self.assertNotIn("FTLCONF_webserver_api_password", self.compose)

    def test_pihole_data_is_not_committed(self):
        if not shutil.which("git"):
            self.skipTest("git not installed")
        r = subprocess.run(["git", "-C", ROOT, "check-ignore", "-q", "pihole/etc-pihole/pihole.toml"])
        self.assertEqual(r.returncode, 0, "pihole/etc-pihole/ must be in .gitignore")

    def test_readme_has_the_rollback(self):
        with open(os.path.join(PIHOLE, "README.md")) as f:
            readme = f.read()
        self.assertIn("Roll back", readme)
        self.assertIn("Auto", readme)


if __name__ == "__main__":
    unittest.main()
