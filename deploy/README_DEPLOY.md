# CastAdhan Portable — Deployment Guide

This folder contains everything needed to ship CastAdhan Portable as a
**plug-and-play gift** to non-technical recipients anywhere in the world.

```
deploy/
├── README_DEPLOY.md             # this file
├── setup-pi.sh                  # one-shot installer (run once on a fresh Pi)
├── clone-prep.sh                # scrubs per-Pi state before SD imaging
├── wifi-prebake.sh              # bakes recipient's WiFi into the SD card (Path 1)
├── castadhan-hotspot.sh         # self-setup WiFi hotspot / captive portal (Path 2)
├── castadhan-hotspot.service    # runs the hotspot orchestrator on boot
├── castadhan-captive.py         # port-80 responder that auto-opens the setup page
├── captive-dnsmasq-shared.conf  # DNS hijack, loaded only while the setup AP is up
├── castadhan-portable.service   # main systemd unit (auto-start, auto-restart)
├── castadhan-update.service     # one-shot updater unit
├── castadhan-update.timer       # daily 04:00 + boot+10min check
├── castadhan-update.sh          # the actual updater (atomic + rollback)
├── castadhan-update.defaults    # config for updater (channel, repo)
└── castadhan-sudoers            # narrow sudoers for the web "Update Now" button
```

---

## The gift workflow at a glance

```
┌─────────────────────────────────────────────────────────────┐
│  ONE TIME (your kitchen)                                    │
│                                                             │
│  1. Flash Raspberry Pi OS Lite to an SD card                │
│  2. Boot the Pi, copy CastAdhan Portable onto it            │
│  3. Run  sudo bash deploy/setup-pi.sh                       │
│  4. Verify it works (test adhan plays)                      │
│  5. Power off, image the SD card → golden.img               │
└─────────────────────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────┐
│  PER GIFT (5 minutes)                                       │
│                                                             │
│  1. Flash golden.img to a new SD card                       │
│  2. Boot the Pi briefly with the new card                   │
│  3. Run  sudo bash deploy/clone-prep.sh                     │
│  4. Run  sudo bash deploy/wifi-prebake.sh "<SSID>" "<pw>"   │
│  5. Power off, drop SD into the gift Pi, pack the box       │
└─────────────────────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────┐
│  RECIPIENT (3 minutes)                                      │
│                                                             │
│  If you PRE-BAKED their WiFi (or they use an Ethernet cable):│
│    1. Plug Pi into power → it joins their network by itself  │
│    2. Wait 60 s                                              │
│    3. Open http://castadhan.local:8786, do the 5-step wizard │
│                                                             │
│  If you did NOT pre-bake and there's no cable:              │
│    1. Plug Pi into power, wait ~90 s                         │
│    2. On their phone, join WiFi "CastAdhan Setup"           │
│    3. A page pops up → pick home WiFi, type password         │
│    4. Pi joins their WiFi; open http://castadhan.local:8786  │
│                                                             │
│  5. Done forever                                            │
└─────────────────────────────────────────────────────────────┘
```

---

## Step 1 — Build the golden image (one time)

### Hardware
- Raspberry Pi (Zero 2 W, 3 A+, or 4 — see hardware guide)
- 32 GB microSD card (SanDisk Ultra recommended)
- USB-C or micro-USB power supply (≥2.5 A for Pi 3 A+; 3 A for Pi 4)
- Your laptop, with an SD card reader

### Steps
1. Download **Raspberry Pi Imager** from raspberrypi.com (free)
2. Flash **Raspberry Pi OS Lite (64-bit, Bookworm)** to the SD card
   - **Enable SSH** in Imager's "advanced options"
   - **Set username/password** (note both — you'll need them)
   - **Pre-configure your WiFi** for the build phase
3. Boot the Pi with the new SD card; wait ~60 s
4. SSH in from your laptop:
   ```bash
   ssh <username>@raspberrypi.local
   ```
5. Copy the CastAdhan Portable folder to the Pi (`scp -r`, USB stick, or `git clone`):
   ```bash
   # on the Pi
   mkdir ~/castadhan
   # copy files via SCP, USB, or git
   ```
6. Run the installer:
   ```bash
   cd ~/castadhan
   sudo bash deploy/setup-pi.sh
   ```
   This takes ~3 minutes. When done, you'll see access URLs printed.
7. From your laptop browser, open `http://castadhan.local:8786`. Walk through the wizard, play a test adhan, confirm everything works.
8. Let it run for 24 h on your own network. Observe a Fajr, Dhuhr, Asr, Maghrib, Isha cycle.

---

## Step 2 — Configure auto-updates (one time)

Edit `/etc/default/castadhan-update` on the Pi:

```bash
sudo nano /etc/default/castadhan-update
```

Set:

```ini
GITHUB_REPO="yourname/castadhan-portable"   # <-- your GitHub repo
UPDATE_CHANNEL="stable"
UPDATE_ENABLED="true"
```

You'll need a GitHub repo with **versioned releases** (tags like `v1.0.0`, `v1.0.1`). The updater pulls the **tarball** of the latest release. Make releases via:

```bash
# Tag and push
git tag v1.0.1
git push origin v1.0.1
# Then on GitHub.com:  Releases → Draft a new release → pick the tag → Publish
```

The Pi will check daily at 04:00 local time + on every boot.

---

## Step 3 — Image the golden SD card

Once the Pi is configured and verified:

```bash
sudo poweroff   # on the Pi
```

Pull the SD card, put it in your laptop. Find its device path:

```bash
# macOS
diskutil list
# Linux
lsblk
```

Image the card to a file:

```bash
sudo dd if=/dev/diskN of=~/castadhan-golden.img bs=4M status=progress
```

(~5 minutes for a 32 GB card.) This `.img` is your master image — flash it to as many fresh SD cards as you want.

---

## Step 4 — Per-gift preparation (5 minutes each)

For each gift:

1. **Flash the golden image** to a fresh SD card using Raspberry Pi Imager:
   - "Use custom" → pick `castadhan-golden.img`
   - Pick the destination card
   - Write (~5 min)
2. **Drop the SD card into the gift Pi**, plug in power, boot briefly
3. **SSH in** and run the per-gift scripts:
   ```bash
   ssh <username>@castadhan.local

   # Wipe per-Pi state (logs, location, wizard flag, SSH host keys, machine-id)
   sudo bash /opt/castadhan-portable/deploy/clone-prep.sh

   # Bake in the recipient's WiFi
   sudo bash /opt/castadhan-portable/deploy/wifi-prebake.sh "Auntie's WiFi" "p@ssw0rd!" BE
   #                                                       └ SSID         └ password   └ country code

   # Clean shutdown
   sudo poweroff
   ```
4. Pull the SD card out and put it back into the **gift Pi** (or keep it in the same Pi if you're not also cloning the hardware).
5. Pack the box.

### Country codes for `wifi-prebake.sh`

| Country | Code |
|---|---|
| United Kingdom | `GB` |
| Belgium | `BE` |
| Netherlands | `NL` |
| Germany | `DE` |
| France | `FR` |
| Ireland | `IE` |
| United States | `US` |
| Pakistan | `PK` |
| Turkey | `TR` |
| Saudi Arabia | `SA` |
| UAE | `AE` |

---

## Step 4b — How each gift gets online (WiFi)

Every gift unit now has **two** ways onto the recipient's WiFi. Use whichever fits.

### Path 1 — Pre-bake (recommended, zero-touch for the recipient)

If you know the recipient's home WiFi name + password, bake it in during Step 4
(`wifi-prebake.sh`). The unit auto-joins on first boot — the recipient literally
just plugs in power. **This is the best experience for a non-technical recipient
and should be the default whenever you can get their WiFi details.**

> Do **not** ship an "Ethernet-only" golden image any more. `setup-pi.sh` unblocks
> the WiFi radio and sets the regulatory domain, so leave `wlan0` usable. Ethernet
> still works if they have a cable — it's just no longer required.

### Path 2 — Self-setup hotspot (fallback, no prior info needed)

If you **don't** know their WiFi (e.g. a generic unit for a stranger, or you
forgot to pre-bake), the unit handles it itself. On first boot, if it finds no
network within ~90 s and it's a fresh unit, it broadcasts an **open access point
called `CastAdhan Setup`**. The recipient:

1. joins `CastAdhan Setup` from their phone,
2. a setup page pops up automatically (captive portal),
3. picks their home WiFi and types the password,
4. the unit joins their WiFi and the hotspot disappears.

Built from: `castadhan-hotspot.sh` (orchestrator + AP), `castadhan-captive.py`
(port-80 responder that auto-opens the page), `captive-dnsmasq-shared.conf` (DNS
hijack while the AP is up), and the `/wifi-setup` page + `/api/wifi/*` endpoints
in the app. Installed + enabled by `setup-pi.sh`.

It is **fail-safe and tightly gated**: it only ever broadcasts on a *fresh*
unit (no saved WiFi profile, first-run wizard not completed) that has *no*
network. A unit that's already set up and merely offline for a moment (router
reboot) never drops into setup mode.

### ⚠️ Bench-test the hotspot before you ship a batch (one-time, ~10 min)

The hotspot path uses WiFi AP mode, which depends on the exact Pi + kernel, so
**verify it once on a spare Pi before mass-cloning** (do NOT trust it untested on
a unit going to someone's house):

- [ ] On a fresh unit with **no Ethernet and no pre-baked WiFi**, power on and wait ~90 s
- [ ] On a phone, confirm the **`CastAdhan Setup`** network appears and you can join it
- [ ] Confirm the setup page **auto-opens** (captive sheet) — if not, browse to `http://10.42.0.1:8786/wifi-setup`
- [ ] Confirm the network dropdown lists your WiFi (served from the pre-scan cache)
- [ ] Enter your WiFi password → the AP drops and the Pi joins your WiFi
- [ ] Confirm the Pi is reachable at `http://castadhan.local:8786` on your normal WiFi afterwards
- [ ] Reboot the now-configured unit with WiFi in range → it rejoins WiFi and does **not** re-broadcast `CastAdhan Setup`
- [ ] Wrong-password test: enter a bad password → page shows an error / lets you retry

If any step fails, check `journalctl -u castadhan-hotspot -b` on the Pi.

---

## Step 5 — What the recipient sees

1. They open the box, plug in the Pi
2. Wait ~60 seconds
3. On any phone/laptop/smart-TV browser on their WiFi: `http://castadhan.local:8786`
4. Wizard appears:
   - 🕌 Welcome
   - 📍 Location (auto-detect from their IP — they confirm/edit)
   - 🧮 Calculation method (default ISNA)
   - 🔊 Speakers (their Cast devices are auto-discovered)
   - ✅ Test adhan + finish

Done. Adhan plays at the next prayer time.

---

## Step 6 — Pushing updates after they've taken delivery

When you change the code:

1. Test on your pilot Pi (which is on the `beta` channel)
2. When happy, tag and push a new GitHub release:
   ```bash
   git tag v1.0.2
   git push origin v1.0.2
   ```
   Then on GitHub: Releases → publish a new release from that tag
3. Within 24 h, every gift Pi running the `stable` channel will:
   - Download the tarball at 04:00 local
   - Stop the service
   - Atomically replace the install
   - Restart and verify
   - Roll back automatically if the new version fails to start
   - Log everything to `/var/log/castadhan-update.log` and journald

Recipients see nothing unusual — the adhan just keeps playing.

---

## Step 7 — Troubleshooting from a distance

If a recipient reports a problem, you can ask them to:

1. Open `http://castadhan.local:8786` → Settings → 🔄 Updates
2. Read out the version + last 10 log lines (shown right there in the UI)
3. Click **"🔄 Check for updates now"** if you've just pushed a fix

For deeper diagnosis, you'd need either:
- SSH access (e.g. via Tailscale joined to their Pi — out of scope here)
- The recipient to read `journalctl -u castadhan-portable -n 50` over the phone

---

## Pre-flight checklist before mass-cloning

Before you commit to the golden image:

- [ ] `systemctl is-active castadhan-portable` returns `active`
- [ ] `systemctl is-active castadhan-update.timer` returns `active`
- [ ] `http://castadhan.local:8786` redirects to `/setup` after `clone-prep.sh`
- [ ] Test adhan plays on a real Cast device
- [ ] `journalctl -u castadhan-portable -n 30` shows no errors
- [ ] Reboot the Pi — service comes back automatically
- [ ] Power-cycle the router — Pi rejoins WiFi cleanly
- [ ] `castadhan-update.sh` runs without errors:  
      `sudo systemctl start castadhan-update.service && journalctl -u castadhan-update -n 20`
- [ ] **WiFi self-setup hotspot** works end-to-end — run the bench test in **Step 4b** on a spare Pi

---

## Per-gift box insert (printable)

See `/deploy/insert-card.md` — a one-page slip to drop in the box with the Pi.

---

## License & attribution checklist

- [ ] Choose a code license (MIT recommended)
- [ ] Credit Surah Al-Kahf recitation source (Sheikh Noreen Muhammad Siddiqui)
- [ ] Credit Al-Mathurat morning/evening dhikr source
- [ ] Confirm rights for "Dua of the Soul" (Barry's Dua)
- [ ] Credit the adhan recitation source
- [ ] Acknowledge `aladhan.com` (prayer times API)
- [ ] Acknowledge `ip-api.com` (location)
- [ ] Acknowledge `sunrise-sunset.org` (twilight)
- [ ] Acknowledge OpenStreetMap (location search via Nominatim)
