# Pi-hole — network-wide ad blocking

Every device on the network asks a DNS server "where is example.com?" before
loading anything. Pi-hole is that DNS server: it answers normally, except for
ad and tracking domains, where it answers `0.0.0.0` ("nowhere"), so the ad
never loads — on phones, laptops and TVs alike, with nothing to install on them.

Web page: `http://<pi-ip>:8080/admin` (the Pi's IP is printed by `./pihole.sh check`).

## How it fails safe

- **Devices have a backup.** UniFi hands out two DNS servers: this Pi (DNS 1)
  and 1.1.1.1 (DNS 2). If the Pi or Pi-hole is down, devices use 1.1.1.1:
  the internet keeps working, just with ads.
- **The Pi doesn't use Pi-hole for itself.** It looks things up through
  1.1.1.1 / 9.9.9.9 directly, so the Pi (and Jarvis) stays online if the
  container breaks, and a blocklist can never break Jarvis's own services.
- **Pi-hole restarts itself** after a crash or a reboot (`restart: unless-stopped`).
- **The price:** some devices — Apple ones especially — sometimes ask DNS 2
  even when Pi-hole is fine, so a few ads get through. See [Known gaps](#known-gaps).

Note: a *blocked* domain gets a real answer (`0.0.0.0`), so devices don't
retry it on 1.1.1.1. The backup covers Pi-hole being down, not a site being
over-blocked — that one you fix with an allow entry (below).

## What was set up

| What | Where |
|---|---|
| Pi-hole, in Docker | `~/jarvis-home/pihole/docker-compose.yml` — ports 53 (DNS) and 8080 (web page) |
| Its settings, lists and history | `~/jarvis-home/pihole/etc-pihole/` — survives updates and reboots; not in git (has the password hash) |
| Helper script | `~/jarvis-home/pihole/pihole.sh` — setup steps, `health`, `update` |
| The Pi's own DNS | NetworkManager: 1.1.1.1 and 9.9.9.9, ignoring what the router hands out |
| The Pi's Wi-Fi | Power saving off — otherwise the Pi is slow to answer (the Pi is on Wi-Fi by choice) |
| Pi-hole's upstream DNS | 1.1.1.1 and 9.9.9.9 (where it sends what it doesn't block) |
| Blocklists | Pi-hole's default list + HaGeZi **Pro** |
| Privacy | Website names are never stored or shown — only counts per device (`./pihole.sh privacy`) |
| microSD care | Query history kept 7 days (default 91), saved every 5 minutes; Docker's logs capped at 30 MB |
| UniFi | Pi on a fixed IP; ad blocking off; DHCP DNS = Pi, then 1.1.1.1; IPv6 handled |

## First-time setup

Run on the Pi, one step at a time (`cd ~/jarvis-home/pihole` first). Each step is safe to
re-run; the two that change something on the Pi ask before doing it.

1. `./pihole.sh check` — OS, boot storage, whether port 53 is free, Docker. Changes nothing.
2. `./pihole.sh install-docker` — installs Docker (asks first).
3. `./pihole.sh host-dns` — the Pi's own DNS → 1.1.1.1 / 9.9.9.9, and Wi-Fi power saving off
   (asks first; the connection stays up). If you later plug in a cable, run it again.
4. `./pihole.sh start` — starts Pi-hole. Then choose the web password:
   `sudo docker exec -it pihole pihole setpassword`
5. `./pihole.sh lists pro` — adds HaGeZi Pro and downloads the lists (a minute).
6. `./pihole.sh privacy on` — website names are never stored (see [Privacy](#privacy)).
7. `./pihole.sh test` — ad domains return `0.0.0.0`, normal sites resolve. Then
   from your laptop (it prints these with the Pi's IP filled in):
   `nslookup pagead2.googlesyndication.com <pi-ip>` → `Address: 0.0.0.0`, and
   `nslookup example.com <pi-ip>` → real addresses.
8. The UniFi steps below, in that order. **D is the switch-on** for the household.

Until step D, nobody uses Pi-hole except the tests you run, so steps 1–7
can't affect anyone else.

## UniFi steps

Written for UniFi Network 8/9; menu names move between versions, so look for
the **bold words**. Your network is usually called **Default**.

**A. Fixed IP for the Pi**
1. **Client Devices** (left sidebar) → click the Pi (match the MAC address `check` prints).
2. In its panel open **Settings** (gear icon) → turn on **Fixed IP Address**
   → keep the IP it has now → **Apply Changes**. Keeping the same IP means nothing changes today.
3. The fixed IP belongs to that one adapter (here: Wi-Fi). Switching the Pi to a
   cable later gives it a different address — redo this step and D then.

**B. UniFi's own ad blocking off**
1. **Settings → CyberSecure** (older versions: **Settings → Security**).
2. **Ad Blocking**: turn it off, or untick your network.
3. **Content Filtering**: none / off for your network — it takes over DNS the same way.
4. **DNS Shield / Encrypted DNS**, if you have it: off, so Pi-hole is the only thing handling DNS.

**C. IPv6** (so devices can't skip Pi-hole by asking over IPv6)
1. **Settings → Networks →** your network → the **IPv6** section
   (**IPv6 Interface Type**; sometimes under **Advanced**).
2. If it already says **None** / off: nothing to do (`check` shows "IPv6: none").
3. Otherwise, recommended: set it to **None** → **Apply Changes**. Devices
   carry on over IPv4; a home network loses practically nothing.
4. Keeping IPv6 instead means advertising the Pi's IPv6 address as IPv6 DNS
   (**Manual**), which needs a change to this setup first (host networking) and
   breaks whenever your ISP changes your IPv6 prefix. Not set up here.

**D. Point the network's DNS at Pi-hole** (the switch-on)
1. **Settings → Networks →** your network → **DHCP** section (newer versions:
   set **Advanced** to **Manual** first; DNS may sit under **DHCP Service Management**).
2. **DNS Server**: untick **Auto** (phone app: turn off **Auto DNS Server**) →
   **DNS Server 1** = the Pi's IP, **DNS Server 2** = `1.1.1.1`, in that order
   → **Apply Changes** (phone app: the ✓ at the top).
3. If saving fails with "…must be a valid hostname" under **Domain Name**
   (default `localdomain`): retype it, or clear it, or use `home.arpa`. It only
   adds a suffix to local device names and doesn't matter with Pi-hole.
4. Other networks (Guest, IoT) can stay on **Auto**; guest networks usually
   can't reach the Pi anyway.

**E. Check the rollout with a phone** (or a personal laptop — a work laptop
usually has its own DNS or VPN and skips Pi-hole, so it proves little)
1. Turn Wi-Fi off and on (devices only pick up new DNS when they reconnect or
   renew; others follow within a day).
2. Browse a little, then open the dashboard: the phone shows up in the
   **Query Log** and under **Clients**. (A laptop can also run
   `nslookup pagead2.googlesyndication.com` → `0.0.0.0`, with the Pi as `Server:`.)
3. Failover drill: on the Pi `cd ~/jarvis-home/pihole && sudo docker compose stop`; on the
   phone open a site you haven't visited today — it loads (maybe a second
   slower). Then `sudo docker compose start`.
4. If the phone can't reach the Pi at all, the Wi-Fi network may have
   **Client Device Isolation** on (**Settings → WiFi →** the network **→ Advanced**):
   it stops Wi-Fi devices talking to each other, including to the Pi.
5. Not in the Query Log? iCloud Private Relay (iPhone) or a named Private DNS
   (Android) sends that phone around Pi-hole.

## Blocklists: blocking more vs. breaking sites

A bigger list blocks more ads and trackers, but also more things that share a
domain with tracking — "false positives". Typical ones: links in newsletters
and marketing emails (they pass through click-tracking domains), sponsored
results at the top of Google, cashback and deal-site links, a smart-TV or app
feature, now and then a video player or login.

HaGeZi has five levels: Light, Normal, **Pro**, Pro++, Ultimate. Pro is the
middle: clearly more than Pi-hole's default list, still rarely breaks
mainstream sites. If the household hits breakage often, step down:
`./pihole.sh lists normal` (and back with `./pihole.sh lists pro`). Pro++ and
Ultimate trade more breakage for little visible gain.

**Fixing one broken site** (five minutes). With privacy on, the log doesn't
show website names, so switch it off just for this:
1. `cd ~/jarvis-home/pihole && ./pihole.sh privacy off`
2. Load the broken site again on that device.
3. Dashboard → **Query Log** → filter by the device → the red *blocked*
   entries from just now → **Allow** the one that looks related.
   (Or **Domains** → add it as *Allow*.)
4. `./pihole.sh privacy on` — and say yes to deleting the history.

Tell the household to say when something won't load — to them it just looks
like "the Wi-Fi is broken".

## Privacy

By default Pi-hole keeps a log of every website name every device looks up —
in effect the household's browsing history. Here that's switched off
(`./pihole.sh privacy on`, Pi-hole's privacy level "hide domains"):

- **Not kept:** website names — not on disk, not in the dashboard, not in a log file.
- **Kept:** counts — how many lookups each device made and how many were
  blocked, for 7 days. Enough to see that Pi-hole works and which devices use it.
- **Still sees lookups:** the upstream DNS services (1.1.1.1 and Quad9), as
  they would without Pi-hole — but only as "this house", not per device.

`./pihole.sh health` warns if privacy has been left off. Stronger levels
exist (Pi-hole **Settings → Privacy**: also hide devices, or keep nothing but
totals), at the cost of not seeing which devices use Pi-hole.

## Is it healthy?

```bash
cd ~/jarvis-home/pihole && ./pihole.sh health
```

All lines `OK`: container healthy, ads blocked, normal lookups answered,
lists updated within the last week. On the dashboard, queries keep climbing
and the "% blocked" figure is steady (usually 5–30%). If something's off:
`sudo docker logs --tail 50 pihole`, then `sudo docker compose restart`.

## Updating

```bash
cd ~/jarvis-home/pihole && ./pihole.sh update
```

Pulls the newest Pi-hole and restarts it with the same settings (DNS pauses
~30 seconds; devices use 1.1.1.1 meanwhile). Blocklists refresh weekly on their
own; to refresh now: `sudo docker exec pihole pihole -g`. Docker itself
updates with the Pi's normal `sudo apt update && sudo apt full-upgrade`.

**Backup:** dashboard → **Settings → Teleporter → Export** gives a file with
lists, allow/block entries and settings; **Import** restores it on a fresh Pi-hole.

## Roll back

1. **UniFi → Settings → Networks →** your network → **DHCP** → **DNS Server**
   → tick **Auto** → **Apply Changes**. This is the whole rollback: devices
   move back as they reconnect or renew (turn Wi-Fi off/on to hurry one).
   There's no outage meanwhile — DNS 2 (1.1.1.1) keeps working.
2. Optional: turn UniFi's **Ad Blocking** back on.
3. Optional: stop Pi-hole: `cd ~/jarvis-home/pihole && sudo docker compose down`
   (its settings stay in `etc-pihole/`; `sudo docker compose up -d` brings it back).
4. Optional, and only once UniFi no longer hands out the Pi as DNS: let the Pi
   take DNS from the router again —
   ```bash
   nmcli -g UUID,DEVICE connection show --active    # the UUID on the wlan0 (or eth0) line
   sudo nmcli connection modify <UUID> ipv4.dns "" ipv4.ignore-auto-dns no ipv6.dns "" ipv6.ignore-auto-dns no
   sudo nmcli device reapply wlan0                  # or eth0
   ```

## Known gaps

- **DNS 2 leaks a little.** Devices don't always try DNS 1 first; some spread
  lookups over both. A device with few queries on the dashboard is doing this.
  The fix, if it bothers you, is a second Pi-hole (another small always-on
  box) as DNS 2 instead of 1.1.1.1.
- **Some traffic skips Pi-hole on purpose:** iCloud Private Relay, VPNs, and
  browsers set to their own "secure DNS". (Pi-hole tells Firefox not to.)
- **Ads served from the same domain as the content** — YouTube, Instagram,
  Facebook — can't be blocked by DNS.
