# CastAdhan Portable

Self-contained Islamic prayer-time system that runs on a Raspberry Pi (or any Linux SBC) and broadcasts the adhan and supporting audio to Google Cast / Google Nest speakers on your local network.

**Current release:** see [`VERSION`](VERSION) — auto-update timer pulls the latest GitHub release nightly at 04:00 local time.

## What it does, fully unattended

- ✅ Plays adhan at all 5 daily prayers, calculated for your latitude with high-latitude rules (Fiqh Council `combine_prayers` and friends)
- ✅ Plays warnings: 5 min before sunrise (end of Fajr), 10 min before Asr (end of Dhuhr), 10 min before Maghrib (end of Asr)
- ✅ Morning dhikr at a configurable time (Surah Kahf on Fridays)
- ✅ Evening dhikr 30 min after Maghrib (with cutoff so it never plays past your bedtime)
- ✅ Optional wakeup alarm with audio-file picker
- ✅ Suhoor alarm during Ramadan
- ✅ Eid takbeeraat
- ✅ Web dashboard at `http://<pi-ip>:8786` — English / Dutch / French / Arabic (RTL)
- ✅ Auto-update from GitHub releases — bugs get fixed remotely without you visiting the Pi

## What you need

| Item | Notes |
|---|---|
| Raspberry Pi 3B+ / 4 / Zero 2W | Pi 3 A+ works in `LITE_MODE` (set in systemd unit) |
| SanDisk Ultra 16-32 GB SD card | Friction-fit slot on Pi 4 — tape it for shipping |
| Genuine Pi PSU (5V 3A USB-C) | Phone chargers under-volt; check `vcgencmd get_throttled` after install |
| 1+ Google Cast / Nest speaker on same WiFi | Amazon Alexa does NOT work (different protocol) |
| Pi OS Lite 64-bit (bookworm or trixie) | Headless, no desktop |

## Install (fresh Pi)

1. Flash Pi OS Lite via Raspberry Pi Imager. Set hostname `castadhan`, enable SSH, pre-configure your WiFi.
2. Boot Pi, find its IP, SSH in.
3. From your Mac/laptop:
   ```
   scp -r castadhan-portable/ pi@castadhan.local:~/
   ssh pi@castadhan.local
   sudo ~/castadhan-portable/deploy/setup-pi.sh
   ```
4. After install completes (≈2-7 minutes depending on Pi model), open `http://castadhan.local:8786` in your browser.
5. The first-run **welcome wizard** walks through location, calculation method, and speaker selection.

The setup script:
- Creates a `castadhan` system user
- Installs the systemd service (`castadhan-portable.service`)
- Installs the auto-update timer (`castadhan-update.timer`, fires daily 04:00 local time)
- Configures `/etc/default/castadhan-update` with this repo's URL
- Disables WiFi power-save (improves stability under Cast mDNS bursts)
- Disables IPv6 sysctl (avoids brcmfmac instability on some routers)

## Auto-update

The Pi checks GitHub nightly at 04:00 for a new release tag. If the latest tag is newer than `/opt/castadhan-portable/VERSION`, it:

1. Stops the service
2. Moves the install dir to `*.previous`
3. Downloads + extracts the new tarball
4. Restores `venv/`, `config.yaml`, and `audio/` from the previous install
5. Refreshes Python deps if `requirements.txt` changed
6. Starts the service
7. If the new service fails to come up in 8s → automatic rollback to `*.previous`

**To opt out** on any specific Pi: edit `/etc/default/castadhan-update` and set `UPDATE_ENABLED="false"`.

**To force an update now**: `sudo systemctl start castadhan-update.service`

## Two users on the Pi (don't confuse them)

After install the Pi has two separate user accounts:

| User | Purpose | Has shell? | Has SSH? | Password |
|---|---|---|---|---|
| The **login user** you set in Pi Imager (e.g. `farley`, `pi`, `admin`) | What you SSH in as. Owns `/home/<user>/`. Member of `sudo`. | yes | yes | what you set |
| **`castadhan`** (system user) | Runs the `castadhan-portable.service` daemon. Owns `/opt/castadhan-portable/`. | no | no | none (`*` in `/etc/shadow`) |

This split exists because the service shouldn't run as a human user (security) and your human user shouldn't own `/opt/castadhan-portable/` (avoids file-permission confusion after updates). Remember: `ssh <login-user>@<pi>` to log in; the service file `User=castadhan` is correct as-is.

## Remote support (built in from v1.3.0)

Every fresh CastAdhan Portable install can self-enrol on your Tailscale tailnet during `setup-pi.sh`, so you can SSH in from anywhere on the internet without setting up VPNs, port-forwards, or NAT-traversal yourself.

**To bake Tailscale into a fresh gift Pi:**

1. Sign up at https://tailscale.com (free for personal use, up to 100 devices).
2. Generate a one-shot auth key: https://login.tailscale.com/admin/settings/keys → Generate auth key → Reusable: OFF, Ephemeral: OFF, Pre-approved: ON, expiration: 30 days.
3. Copy `deploy/castadhan-tailscale.defaults.template` → `deploy/castadhan-tailscale.defaults` and fill in `TS_AUTHKEY` + `TS_HOSTNAME` (give each gift Pi a distinctive hostname like `aunt-pi-ghent` or `son-pi-haverfordwest`).
4. Run `sudo bash setup-pi.sh` on the Pi. The script picks up the key, installs Tailscale, enrols the Pi, and **shreds the key file** so it can't be recovered from the SD card later. The Pi appears in your Tailscale console within seconds.
5. After enrolment: disable key expiry on the Tailscale console (Machines page → ⋯ → Disable key expiry) so the Pi stays connected indefinitely without re-authentication.

**Alternative — pass the key inline:**
```
sudo TS_AUTHKEY=tskey-auth-... TS_HOSTNAME=aunt-pi-ghent bash setup-pi.sh
```

**Alternative — SD-card pre-bake (zero-touch flash workflow):**

Drop the filled-in `castadhan-tailscale.env` onto the boot partition of the SD card (mount the SD on your Mac/Windows, copy the file to `/boot/` or `/boot/firmware/` on the FAT partition). The Pi's first-boot `setup-pi.sh` will find it, enrol, then shred. Perfect for "flash and post the SD card" gift workflows.

Once enrolled, SSH from any other tailnet device:
```
ssh <login-user>@<tailscale-100-ip>
```

This is the operational difference between "gift product that needs maintenance visits" and "gift product that maintains itself remotely". See [`deploy/castadhan-tailscale.defaults.template`](deploy/castadhan-tailscale.defaults.template) for full options.

## Project history

A detailed war diary (every bug, every fix, every lesson) is maintained privately because it contains real deployment-site IPs, family names, and credentials. Contact the maintainer if you need access for diagnostic context.

## License

Personal-use, gift-product. Not currently published for commercial reuse — open an issue if you want to discuss.
