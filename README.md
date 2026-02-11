# Interview Snapshot Relay

Hotkey → screenshot → OpenAI feedback → instant push to your phone.  
Components:

| Folder | Purpose |
| ------ | ------- |
| `server/` | Go HTTP server: stores screenshots, exposes `/api/feedback`, `/api/stream`, and serves the phone UI |
| `desktop-agent/` | Windows tray/CLI Python agent that listens for the global hotkey, captures the screen, calls OpenAI, and posts to the relay |

## 0. Quick launcher (Windows)

Prefer a single command? From the repo root run:

```powershell
python startup.py            # start both components in their own PowerShell windows
python startup.py --stop     # terminate whichever components are tracked
python startup.py --server-only
python startup.py --agent-only
python startup.py --minimized
```

The launcher mirrors the old `startup.ps1`, keeps message logs visible, and tracks process IDs in `.startup-state.json` so `--stop` knows what to kill.

## 1. Run the Go relay server

```bash
cd server
copy env.sample .env   # edit if you need custom PORT or CORS origin
go build ./...
go run .
```

The server hosts:

- `POST /api/feedback` – agent uploads `{feedback, image:dataUrl, timestamp, meta}`
- `GET /api/latest` – last payload (used to hydrate after reconnects)
- `GET /api/stream` – Server‑Sent Events feed that phones subscribe to
- `GET /api/info` – shows detected LAN base URLs (used for the QR helper)
- `GET /api/qr` – renders a PNG QR for any `http(s)` URL so you can scan it
- Static UI at `/` – leave this page open on your phone’s browser to see updates

Screenshots land in `server/uploads/` with short cache headers; clean them up as needed.

> **Network tip:** keep phone and laptop on the same Wi‑Fi so `http://<laptop-ip>:4000` loads without tunneling. When you load the page on your laptop it now shows a QR card with all detected LAN URLs—scan it once on your phone and bookmark the resulting address.

## 2. Configure the Windows hotkey agent

```powershell
cd desktop-agent
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy env.sample .env   # fill in OPENAI_API_KEY etc.
python main.py
```

Environment variables (`.env`):

- `OPENAI_API_KEY` – your project key (never reuse the sample string)
- `SERVER_URL` – e.g. `http://192.168.1.42:4000`
- `OPENAI_MODEL` – defaults to `gpt-4o-mini`
- `HOTKEY` – any `keyboard`-compatible combo, e.g. `ctrl+alt+space`
- `PROMPT` – optional custom instruction for the AI critique
- `PRIMARY_*` / `SECONDARY_*` – override hotkeys/models/prompts per mode (see below)

How it works:

1. `keyboard` registers the global hotkey (requires the script to run with enough privileges).
2. On trigger it uses `mss` to capture the full desktop, converts to PNG, then base64.
3. Sends the screenshot to OpenAI Responses API with a concise critique prompt.
4. Posts the resulting feedback + screenshot to the Go relay, which instantly streams to any connected phone browsers.
5. UI plays a short ping and swaps in the new screenshot/feedback.

You’ll see logs in the console (`feedback delivered ✅`). The callback runs inside a daemon thread so you can mash the hotkey without blocking, though overlapping runs are throttled.

### Dual hotkeys / models

Set `PRIMARY_*` and `SECONDARY_*` in `.env` to bind different combos to different models/prompts—e.g. `PRIMARY_HOTKEY=ctrl+alt+space` + `PRIMARY_MODEL=gpt-4.1`, and `SECONDARY_HOTKEY=ctrl+alt+c` + `SECONDARY_MODEL=gpt-5.1`. Each hotkey spins its own OpenAI request, and the agent attaches the mode metadata (name, hotkey, model) to the payload so you can see which model answered on the phone UI. If `SECONDARY_*` isn’t set, only the primary hotkey is registered.

### Autostart (optional)

- Create a shortcut to `python main.py` and drop it into `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`.
- Or wrap it with Task Scheduler, “Run with highest privileges” so the global hotkey works even on secure desktops.

## 3. Interview workflow

1. Start the Go server and keep the phone UI open (home-screen it for quick access).
2. Launch the desktop agent; verify the log shows “Interview agent armed”.
3. During the interview, press the hotkey whenever you want instant critique.
4. Glance at the phone—new feedback appears within ~2 seconds, complete with screenshot and metadata.

## Troubleshooting

- **No phone updates** – ensure phone/browser stays awake; SSE reconnect logic shows status chips. Check that `SERVER_URL` is reachable from Windows and that firewall allows port 4000.
- **Keyboard hook denied** – run the agent as admin once to grant permissions; `keyboard` needs low-level access.
- **Slow OpenAI responses** – switch to a lighter model (e.g., `gpt-4o-mini`) or reduce prompt size.
- **Security hygiene** – screenshots are saved locally; purge `server/uploads` regularly if you’re recording sensitive material. Never commit `.env` files.

With this setup the hotkey → AI feedback loop is local-first, low-latency, and doesn’t require touching your phone mid-interview.

