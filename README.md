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

## Remote support (optional but recommended for unattended deployments)

Install [Tailscale](https://tailscale.com) on the Pi during setup. Once the Pi is on your tailnet, you can SSH from any other tailnet device (your laptop anywhere in the world):
```
ssh <login-user>@<tailscale-100-ip>
```

This removes the need to visit the Pi physically when bugs surface. See `INCIDENT_REPORT_2026-05-22.md` for the field-tested rationale.

## Project history

A detailed war diary (every bug, every fix, every lesson) is maintained privately because it contains real deployment-site IPs, family names, and credentials. Contact the maintainer if you need access for diagnostic context.

## License

Personal-use, gift-product. Not currently published for commercial reuse — open an issue if you want to discuss.
