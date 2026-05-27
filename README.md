# 📌 Calendar Post-It

A macOS background app that pops up a yellow sticky-note **10 minutes before every Google Calendar meeting**, with a live countdown timer and a dismiss button.

---

## How it works

- Runs silently in the background as a macOS LaunchAgent (auto-starts on login)
- Polls your Google Calendar every 60 seconds
- Shows a floating sticky-note popup 10 minutes before any upcoming meeting
- Each note shows the meeting title, start time, location, and a video link (Google Meet / Zoom) if present
- Click **"Got it ✓"** to dismiss
- A 📋 icon in your menu bar lets you test it or quit

---

## Requirements

- macOS
- Python 3.9 or later (ships with macOS; check with `python3 --version`)
- A Google account with Google Calendar

---

## Step 1 — Get Google Calendar API credentials

You need to create a free API credential from Google Cloud. This is a one-time step.

### 1.1 Create a Google Cloud project

1. Go to [https://console.cloud.google.com](https://console.cloud.google.com)
2. Click the project dropdown at the top → **New Project**
3. Give it any name (e.g. `Calendar Post-It`) → click **Create**

### 1.2 Enable the Google Calendar API

1. In the left sidebar go to **APIs & Services → Library**
2. Search for **Google Calendar API**
3. Click it → click **Enable**

### 1.3 Configure the OAuth consent screen

1. Go to **APIs & Services → OAuth consent screen**
2. Select **External** → click **Create**
3. Fill in:
   - **App name**: `Calendar Post-It` (or anything you like)
   - **User support email**: your Gmail address
   - **Developer contact email**: your Gmail address
4. Click **Save and Continue** through the remaining steps (Scopes and Test users can be left as defaults)
5. On the **Test users** page, click **+ Add users** and add your own Gmail address
6. Click **Save and Continue** → **Back to Dashboard**

### 1.4 Create OAuth credentials

1. Go to **APIs & Services → Credentials**
2. Click **+ Create Credentials → OAuth 2.0 Client ID**
3. Set **Application type** to **Desktop app**
4. Give it any name → click **Create**
5. Click **Download JSON** on the confirmation dialog
6. Rename the downloaded file to **`credentials.json`**
7. Move `credentials.json` into the `Post_it_calendard` folder (the same folder as this README)

---

## Step 2 — Install

Open Terminal, navigate to the project folder, and run:

```bash
cd ~/Desktop/Post_it_calendard
bash install.sh
```

`install.sh` will:

1. Check that `credentials.json` is present
2. Install Python dependencies (`PyQt6`, `google-auth-oauthlib`, etc.)
3. Open a browser window for **one-time Google sign-in** — sign in with your Google account and click **Allow**
4. Save an auth token locally (`token.json`) so you never need to sign in again
5. Register the app as a macOS LaunchAgent so it starts automatically on every login

> **Note:** During the browser sign-in you may see a warning that says *"Google hasn't verified this app"*. This is expected because the app is running under your own personal Google Cloud project. Click **Advanced → Go to Calendar Post-It (unsafe)** to proceed.

---

## Step 3 — You're done

After install you'll see a 📋 icon in your macOS menu bar. The app is now running.

**Right-click the menu bar icon** to access:

| Menu item | What it does |
|---|---|
| 🧪 Test notification | Shows a sample sticky-note immediately so you can preview the look |
| Quit Calendar Post-It | Stops the app (it will restart on next login) |

---

## What the sticky-note shows

| Field | Notes |
|---|---|
| Meeting title | Falls back to "Untitled Meeting" if not set |
| Start time | Shown in your local timezone |
| Location | Only shown if set on the event |
| Video link | Clickable "Join video call" for Google Meet / Zoom |
| Countdown | Live timer — turns red when the meeting starts |

You can **drag** any note to reposition it on screen.

---

## Configuration

To change the alert timing or poll frequency, open `calendar_postit.py` and edit the constants near the top:

```python
ALERT_MINUTES    = 10       # show popup this many minutes before a meeting
POLL_INTERVAL_MS = 60_000   # how often to check Google Calendar (milliseconds)
```

After saving, redeploy with:

```bash
bash install.sh
```

---

## Logs

To watch the app in real time:

```bash
tail -f /tmp/calendar-postit-stdout.log
```

Each poll prints the time window being checked and any events found. Errors go to:

```bash
tail -f /tmp/calendar-postit-stderr.log
```

---

## Stopping and starting manually

```bash
# Stop
launchctl unload ~/Library/LaunchAgents/com.user.calendar-postit.plist

# Start
launchctl load ~/Library/LaunchAgents/com.user.calendar-postit.plist
```

---

## Uninstall

```bash
bash uninstall.sh
```

This removes the LaunchAgent and stops the app. Your `credentials.json` and `token.json` files are left in place — delete them manually if you no longer want them.

---

## Troubleshooting

**The menu bar icon doesn't appear**

Check if the process is running:
```bash
pgrep -fl calendar_postit
```
If nothing is returned, check the error log:
```bash
cat /tmp/calendar-postit-stderr.log
```

**Meetings aren't showing up**

Watch the live log while you have a meeting coming up:
```bash
tail -f /tmp/calendar-postit-stdout.log
```
Each poll line shows the time window and how many events were found. Make sure the meeting is a regular **Event** (not a Reminder or Task) and is on your `dexter.c.cooke@gmail.com` calendar.

**"Google hasn't verified this app" warning**

This is expected. Click **Advanced → Go to [app name] (unsafe)** during the browser sign-in. The app only requests read-only access to your calendar.

**Token expired / auth errors**

Delete the token and re-authorise:
```bash
rm ~/.calendar-postit/token.json
bash install.sh
```

---

## File reference

```
Post_it_calendard/
├── README.md            ← this file
├── calendar_postit.py   ← main application
├── credentials.json     ← you provide this (Google OAuth client secrets)
├── install.sh           ← setup and deployment script
└── uninstall.sh         ← removes the LaunchAgent
```

After install, the app is copied to `~/.calendar-postit/` — that's what actually runs. Re-run `install.sh` any time you edit `calendar_postit.py` to redeploy changes.
