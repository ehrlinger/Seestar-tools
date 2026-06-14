# NAS Wired-Only Auto-Mount (macOS laptop)

Automatically mount an SMB NAS share **only when a wired (Ethernet) connection is
active**, and never auto-mount over Wi-Fi. If the link drops from wired to Wi-Fi,
an existing mount is left alone rather than force-unmounted.

This is handy when the NAS holds your Seestar archive and you only want the sync
pipeline to see it on a fast/stable wired link.

## Why a LaunchAgent (not `/etc/fstab`)?

`/etc/fstab` mounts at boot regardless of how the machine is connected — fine for
a stationary box that's always on Ethernet, but a laptop roams. `nsmb.conf`'s
`mc_prefer_wired` only affects SMB Multichannel *speed*; it does **not** stop a
mount from happening over Wi-Fi. So we gate the mount with a small LaunchAgent
that fires on every network change and mounts only when Ethernet is the active
default route.

## Files here

| File | Purpose |
|---|---|
| `nas-wired-mount.sh` | the gate-and-mount script (reads `nas-mount.conf`) |
| `nas-mount.conf.example` | template for your NAS host/share/user — copy to `nas-mount.conf` |
| `nas-wired-mount.plist.example` | LaunchAgent that runs the script at login + on network change |

> `nas-mount.conf` (your real values) is **gitignored** — your NAS IP, share, and
> username never get committed.

## Setup

### 1. Configure

```bash
cd setup/nas-mount
cp nas-mount.conf.example nas-mount.conf      # then edit NAS_HOST / SHARE / SMB_USER
```

### 2. Seed the NAS password into the Keychain (once)

The script mounts via the system's standard SMB mount, which reads the login
Keychain. Seed it once in Finder:

1. Finder → **Go → Connect to Server** (`⌘K`).
2. Enter `smb://<NAS_HOST>/<SHARE>` → **Connect**.
3. Sign in as your SMB user, and **check "Remember this password in my keychain."**
4. Once it mounts, you can eject it — the credential is now stored.

Verify (substitute your values):

```bash
security find-internet-password -s "<NAS_HOST>" -a "<SMB_USER>" >/dev/null 2>&1 \
  && echo "keychain credential: OK" || echo "keychain credential: MISSING"
```

### 3. Install the script + config

```bash
mkdir -p ~/bin
cp nas-wired-mount.sh nas-mount.conf ~/bin/      # keep the conf next to the script
chmod 755 ~/bin/nas-wired-mount.sh
```

### 4. Install the LaunchAgent

```bash
# edit YOUR_USERNAME in the .example first, then:
mkdir -p ~/Library/LaunchAgents
cp nas-wired-mount.plist.example \
   ~/Library/LaunchAgents/com.example.nas-wired-mount.plist

launchctl unload ~/Library/LaunchAgents/com.example.nas-wired-mount.plist 2>/dev/null
launchctl load  ~/Library/LaunchAgents/com.example.nas-wired-mount.plist
```

### 5. Test

```bash
~/bin/nas-wired-mount.sh
tail -n 5 ~/Library/Logs/nas-wired-mount.log
```

- **On Ethernet:** it mounts; log shows `mounted ... (wired: enX)`. Confirm `ls /Volumes/<SHARE>`.
- **On Wi-Fi only:** log shows `active interface ... is Wi-Fi; skip`. No new mount.
- **Real trigger:** plug in Ethernet — within a second or two the network-change
  watch fires and it mounts on its own.

## How the wired check works

- `route -n get default` → the interface carrying the current default route.
- `networksetup -listallhardwareports` → the device name of the **Wi-Fi** port.
- If the default-route interface **is** the Wi-Fi device → treat as Wi-Fi, skip.
- Otherwise (Ethernet, Thunderbolt bridge, USB-C LAN adapter, etc.) → wired, mount.

With both Ethernet and Wi-Fi connected, macOS normally ranks Ethernet first, so
the default route is wired and the mount happens. Unplug Ethernet and the default
route falls back to Wi-Fi, so the agent stops mounting.

## Behavior on wired → Wi-Fi drop

The script **does not unmount** when you fall back to Wi-Fi. Tradeoff: if your
`nsmb.conf` uses `soft=yes`, I/O on the now-disconnected share returns an error
promptly instead of hanging, but a stale `/Volumes/<SHARE>` may linger until you
eject it. Add an unmount branch if you'd rather it auto-unmount on Wi-Fi.

## Uninstall

```bash
launchctl unload ~/Library/LaunchAgents/com.example.nas-wired-mount.plist
rm ~/Library/LaunchAgents/com.example.nas-wired-mount.plist
rm ~/bin/nas-wired-mount.sh ~/bin/nas-mount.conf
# (optional) remove the stored credential:
# security delete-internet-password -s "<NAS_HOST>" -a "<SMB_USER>"
```

## Troubleshooting

- **Nothing mounts on Ethernet:** run `~/bin/nas-wired-mount.sh` by hand and read
  `~/Library/Logs/nas-wired-mount.log`. A `mount FAILED` line means the Keychain
  credential is missing/wrong — redo step 2.
- **Wrong Wi-Fi interface detected:** run `networksetup -listallhardwareports`
  and confirm the Wi-Fi device.
- **Name vs IP:** use the NAS's static IP in `nas-mount.conf` so it doesn't depend
  on mDNS (`.local`) resolving.
