#!/usr/bin/env python3

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote
import html
import json
import os
import signal
import subprocess
import threading
import time

PROFILE_DIRECTORY = Path(
    "/home/joel/Documents/prefs/audio/camilladsp"
)

CAMILLA_BINARY = "camilladsp"

STATE_DIRECTORY = Path(
    "/home/joel/.local/state/camilladsp-webremote"
)

PID_FILE = STATE_DIRECTORY / "camilladsp.pid"
ACTIVE_PROFILE_FILE = STATE_DIRECTORY / "active-profile"
LOG_FILE = STATE_DIRECTORY / "camilladsp.log"

LISTEN_ADDRESS = "0.0.0.0"
LISTEN_PORT = 8766

SWITCH_LOCK = threading.Lock()

SERVER_SCRIPT = Path(__file__).resolve()
SERVER_PID_FILE = STATE_DIRECTORY / "web-server.pid"


def kill_previous_web_servers():
    # Stop any existing CamillaDSP instance.
    subprocess.run(
        [
            "pkill",
            "-x",
            "camilladsp",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )

    # Kill whichever process currently owns port 8766.
    subprocess.run(
        [
            "fuser",
            "-k",
            f"{LISTEN_PORT}/tcp",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )

    # Allow the previous listener to release the port.
    time.sleep(0.5)


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">

<meta
  name="viewport"
  content="width=device-width,initial-scale=1,viewport-fit=cover"
>

<meta name="theme-color" content="#090a0f">
<meta name="apple-mobile-web-app-capable" content="yes">

<meta
  name="apple-mobile-web-app-status-bar-style"
  content="black-translucent"
>

<title>CamillaDSP Remote</title>

<style>
:root {
  color-scheme: dark;

  font-family:
    -apple-system,
    BlinkMacSystemFont,
    "SF Pro Display",
    sans-serif;
}

* {
  box-sizing: border-box;
  -webkit-tap-highlight-color: transparent;
}

body {
  margin: 0;

  min-height: 100svh;

  padding:
    max(22px, env(safe-area-inset-top))
    18px
    max(30px, env(safe-area-inset-bottom));

  color: white;

  background:
    radial-gradient(
      circle at top,
      #422568 0,
      #17151f 43%,
      #07080b 100%
    );
}

main {
  width: min(100%, 520px);
  margin: 0 auto;
}

h1 {
  margin: 7px 0 7px;

  font-size: 30px;
  text-align: center;
}

.subtitle {
  margin-bottom: 22px;

  color: #aaa7b4;

  text-align: center;
  font-size: 14px;
}

.now-playing {
  min-height: 100px;
  margin-bottom: 25px;
  padding: 19px;

  border-radius: 23px;

  background: rgba(255, 255, 255, .11);

  box-shadow:
    inset 0 1px rgba(255, 255, 255, .17),
    0 14px 38px rgba(0, 0, 0, .27);

  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
}

.now-label {
  margin-bottom: 8px;

  color: #aaa7b5;

  font-size: 12px;
  font-weight: 750;
  letter-spacing: .11em;
  text-transform: uppercase;
}

#active-profile {
  overflow-wrap: anywhere;

  font-size: 18px;
  font-weight: 750;
  line-height: 1.35;
}

#active-state {
  margin-top: 6px;

  color: #bbb7c4;

  font-size: 14px;
}

.section-title {
  margin: 29px 3px 12px;

  color: #aaa6b5;

  font-size: 13px;
  font-weight: 750;
  letter-spacing: .08em;
  text-transform: uppercase;
}

.profile-list {
  display: grid;
  gap: 11px;
}

.profile-button {
  width: 100%;
  min-height: 70px;

  padding: 14px 17px;

  border: 0;
  border-radius: 19px;

  color: white;
  background: rgba(255, 255, 255, .10);

  box-shadow:
    inset 0 1px rgba(255, 255, 255, .17),
    0 10px 28px rgba(0, 0, 0, .25);

  text-align: left;
  font: inherit;

  cursor: pointer;
}

.profile-button:active {
  transform: scale(.975);
  background: rgba(255, 255, 255, .18);
}

.profile-button.active {
  background:
    linear-gradient(
      135deg,
      #724cff,
      #a264ff
    );
}

.profile-button.busy {
  opacity: .55;
  pointer-events: none;
}

.profile-name {
  display: block;

  font-size: 16px;
  font-weight: 720;
  line-height: 1.3;
}

.profile-device {
  display: block;

  margin-top: 5px;

  color: #c0bdca;

  font-size: 13px;
}

#status {
  min-height: 26px;
  margin-top: 17px;

  color: #bbb7c4;

  text-align: center;
  font-size: 14px;
}

.error {
  color: #ff8c9e !important;
}
</style>
</head>

<body>
<main>
  <h1>CamillaDSP</h1>

  <div class="subtitle">
    AirPlay convolution profile remote
  </div>

  <div class="now-playing">
    <div class="now-label">
      Active profile
    </div>

    <div id="active-profile">
      Loading…
    </div>

    <div id="active-state"></div>
  </div>

  <div class="section-title">
    Profiles
  </div>

  <div
    id="profile-list"
    class="profile-list"
  ></div>

  <div id="status"></div>
</main>

<script>
const profileList =
  document.querySelector("#profile-list");

const activeProfile =
  document.querySelector("#active-profile");

const activeState =
  document.querySelector("#active-state");

const statusBox =
  document.querySelector("#status");

let switching = false;
let statusTimer = null;


function setStatus(message, isError = false) {
  statusBox.textContent = message;

  statusBox.classList.toggle(
    "error",
    isError
  );

  if (statusTimer !== null) {
    clearTimeout(statusTimer);
  }

  if (message) {
    statusTimer = setTimeout(() => {
      if (statusBox.textContent === message) {
        statusBox.textContent = "";
        statusBox.classList.remove("error");
      }
    }, 4000);
  }
}


async function requestJSON(url, options = {}) {
  const response = await fetch(
    url,
    {
      cache: "no-store",
      ...options
    }
  );

  let result;

  try {
    result = await response.json();
  } catch {
    throw new Error(
      "Server returned invalid data"
    );
  }

  if (!response.ok || result.ok === false) {
    throw new Error(
      result.error || "Request failed"
    );
  }

  return result;
}


function friendlyName(filename) {
  return filename
    .replace(/\.ya?ml$/i, "")
    .replace(/^\d+-/, "")
    .replace(/^earpods-/, "")
    .replace(/^cloud3-/, "")
    .replace(/-/g, " ")
    .replace(/\b\w/g, value => value.toUpperCase());
}


function deviceName(filename) {
  if (filename.includes("-cloud3-")) {
    return "HyperX Cloud III";
  }

  return "Apple EarPods";
}


function setButtonsBusy(value) {
  for (
    const button
    of document.querySelectorAll(
      ".profile-button"
    )
  ) {
    button.classList.toggle(
      "busy",
      value
    );
  }
}


async function switchProfile(filename) {
  if (switching) {
    return;
  }

  switching = true;
  setButtonsBusy(true);

  setStatus(
    "Switching profile…"
  );

  try {
    const result = await requestJSON(
      "/api/select",
      {
        method: "POST",

        headers: {
          "Content-Type":
            "application/json"
        },

        body: JSON.stringify({
          profile: filename
        })
      }
    );

    setStatus(
      "Started " +
      friendlyName(result.profile)
    );

    await loadProfiles();

  } catch (error) {
    setStatus(
      error.message,
      true
    );

  } finally {
    switching = false;
    setButtonsBusy(false);
  }
}


async function loadProfiles() {
  try {
    const result =
      await requestJSON("/api/profiles");

    profileList.innerHTML = "";

    if (result.running && result.active) {
      activeProfile.textContent =
        friendlyName(result.active);

      activeState.textContent =
        deviceName(result.active) +
        " • Running";

    } else if (result.active) {
      activeProfile.textContent =
        friendlyName(result.active);

      activeState.textContent =
        "Process is not running";

    } else {
      activeProfile.textContent =
        "No profile running";

      activeState.textContent = "";
    }

    for (const filename of result.profiles) {
      const button =
        document.createElement("button");

      button.className =
        "profile-button";

      if (
        result.running &&
        filename === result.active
      ) {
        button.classList.add("active");
      }

      const name =
        document.createElement("span");

      name.className =
        "profile-name";

      name.textContent =
        friendlyName(filename);

      const device =
        document.createElement("span");

      device.className =
        "profile-device";

      device.textContent =
        deviceName(filename);

      button.append(name, device);

      button.addEventListener(
        "click",
        () => switchProfile(filename)
      );

      profileList.appendChild(button);
    }

  } catch (error) {
    setStatus(
      error.message,
      true
    );
  }
}


loadProfiles();

setInterval(
  loadProfiles,
  3000
);
</script>
</body>
</html>
"""


def ensure_state_directory():
    STATE_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )


def available_profiles():
    if not PROFILE_DIRECTORY.is_dir():
        raise RuntimeError(
            f"Profile directory does not exist: "
            f"{PROFILE_DIRECTORY}"
        )

    profiles = []

    for pattern in ("*.yml", "*.yaml"):
        profiles.extend(
            PROFILE_DIRECTORY.glob(pattern)
        )

    return sorted(
        {
            profile.name: profile
            for profile in profiles
            if profile.is_file()
        }.values(),
        key=lambda profile: profile.name.lower(),
    )


def resolve_profile(filename):
    if not isinstance(filename, str):
        raise ValueError("Invalid profile name")

    if Path(filename).name != filename:
        raise ValueError("Invalid profile path")

    profile = PROFILE_DIRECTORY / filename

    profile = profile.resolve()
    directory = PROFILE_DIRECTORY.resolve()

    if profile.parent != directory:
        raise ValueError("Invalid profile path")

    if (
        not profile.is_file()
        or profile.suffix.lower()
        not in {".yml", ".yaml"}
    ):
        raise FileNotFoundError(
            f"Profile not found: {filename}"
        )

    return profile


def read_pid():
    try:
        value = PID_FILE.read_text().strip()
        return int(value)
    except (
        FileNotFoundError,
        ValueError,
        OSError,
    ):
        return None


def process_exists(pid):
    if pid is None:
        return False

    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def read_active_profile():
    try:
        return ACTIVE_PROFILE_FILE.read_text().strip()
    except OSError:
        return ""


def run_command(arguments, check=True):
    return subprocess.run(
        arguments,
        text=True,
        capture_output=True,
        timeout=15,
        check=check,
    )


def stop_existing_camilladsp():
    pid = read_pid()

    if process_exists(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

        for _ in range(20):
            if not process_exists(pid):
                break
            time.sleep(0.1)

        if process_exists(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    # Also remove CamillaDSP instances started manually,
    # not only instances launched by this web app.
    subprocess.run(
        [
            "pkill",
            "-x",
            "camilladsp",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )

    PID_FILE.unlink(missing_ok=True)

def stop_system_audio():
    run_command(
        [
            "systemctl",
            "--user",
            "stop",
            "pipewire-pulse.service",
            "pipewire-pulse.socket",
            "wireplumber.service",
            "pipewire.service",
            "pipewire.socket",
        ],
        check=False,
    )


def set_alsa_loopback_to_100():
    result = run_command(
        [
            "amixer",
            "-c",
            "Loopback",
            "sset",
            "PCM",
            "100%",
        ],
        check=False,
    )

    if result.returncode != 0:
        error = (
            result.stderr.strip()
            or result.stdout.strip()
            or "Unknown amixer error"
        )

        raise RuntimeError(
            "Could not set ALSA Loopback PCM "
            f"to 100%: {error}"
        )


def switch_profile(filename):
    with SWITCH_LOCK:
        profile = resolve_profile(filename)

        stop_system_audio()
        stop_existing_camilladsp()
        set_alsa_loopback_to_100()

        try:
            pid = launch_camilladsp(profile)

        except Exception:
            ACTIVE_PROFILE_FILE.unlink(
                missing_ok=True
            )
            raise

        return {
            "profile": profile.name,
            "pid": pid,
        }


def launch_camilladsp(profile):
    ensure_state_directory()

    log_handle = LOG_FILE.open(
        "ab",
        buffering=0,
    )

    process = subprocess.Popen(
        [
            CAMILLA_BINARY,
            str(profile),
        ],
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )

    log_handle.close()

    PID_FILE.write_text(
        f"{process.pid}\n"
    )

    ACTIVE_PROFILE_FILE.write_text(
        f"{profile.name}\n"
    )

    time.sleep(1)

    return_code = process.poll()

    if return_code is not None:
        PID_FILE.unlink(
            missing_ok=True
        )

        try:
            log_tail = LOG_FILE.read_text(
                errors="replace"
            )[-4000:]
        except OSError:
            log_tail = ""

        raise RuntimeError(
            "CamillaDSP failed to start.\n"
            + log_tail
        )

    return process.pid


class Handler(BaseHTTPRequestHandler):
    def send_data(
        self,
        status,
        content_type,
        data,
    ):
        self.send_response(status)

        self.send_header(
            "Content-Type",
            content_type,
        )

        self.send_header(
            "Content-Length",
            str(len(data)),
        )

        self.send_header(
            "Cache-Control",
            "no-store, no-cache, must-revalidate",
        )

        self.send_header(
            "Pragma",
            "no-cache",
        )

        self.send_header(
            "Expires",
            "0",
        )

        self.end_headers()
        self.wfile.write(data)

    def send_json(
        self,
        status,
        payload,
    ):
        self.send_data(
            status,
            "application/json; charset=utf-8",
            json.dumps(payload).encode(),
        )

    def read_json_body(self):
        length = int(
            self.headers.get(
                "Content-Length",
                "0",
            )
        )

        if length <= 0:
            return {}

        return json.loads(
            self.rfile.read(length)
        )

    def do_GET(self):
        if self.path.split("?", 1)[0] == "/":
            self.send_data(
                200,
                "text/html; charset=utf-8",
                PAGE.encode(),
            )
            return

        if (
            self.path.split("?", 1)[0]
            == "/api/profiles"
        ):
            try:
                profiles = [
                    profile.name
                    for profile in available_profiles()
                ]

                pid = read_pid()

                self.send_json(
                    200,
                    {
                        "ok": True,
                        "profiles": profiles,
                        "active":
                            read_active_profile(),
                        "running":
                            process_exists(pid),
                        "pid": pid,
                    },
                )

            except Exception as error:
                self.send_json(
                    500,
                    {
                        "ok": False,
                        "error": str(error),
                    },
                )

            return

        self.send_json(
            404,
            {
                "ok": False,
                "error": "Not found",
            },
        )

    def do_POST(self):
        path = self.path.split("?", 1)[0]

        if path != "/api/select":
            self.send_json(
                404,
                {
                    "ok": False,
                    "error": "Not found",
                },
            )
            return

        try:
            payload = self.read_json_body()
            filename = payload["profile"]

            result = switch_profile(filename)

            self.send_json(
                200,
                {
                    "ok": True,
                    **result,
                },
            )

        except Exception as error:
            self.send_json(
                500,
                {
                    "ok": False,
                    "error": str(error),
                },
            )

    def log_message(self, format, *args):
        pass


class ReusableThreadingHTTPServer(
    ThreadingHTTPServer
):
    allow_reuse_address = True
    daemon_threads = True


ensure_state_directory()
kill_previous_web_servers()

server = ReusableThreadingHTTPServer(
    (LISTEN_ADDRESS, LISTEN_PORT),
    Handler,
)

SERVER_PID_FILE.write_text(
    f"{os.getpid()}\\n",
    encoding="utf-8",
)

print(
    f"CamillaDSP web remote listening on "
    f"{LISTEN_ADDRESS}:{LISTEN_PORT}",
    flush=True,
)

try:
    server.serve_forever()

finally:
    server.server_close()

    try:
        saved_pid = int(
            SERVER_PID_FILE
            .read_text(encoding="utf-8")
            .strip()
        )
    except (
        FileNotFoundError,
        ValueError,
        OSError,
    ):
        saved_pid = None

    if saved_pid == os.getpid():
        SERVER_PID_FILE.unlink(
            missing_ok=True
        )
