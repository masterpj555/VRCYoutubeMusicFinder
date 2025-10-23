# Spotify VRC — Local Only (Dark, Button-Only) + Icon support
# v1.6 — Adds window icon loading compatible with PyInstaller onefile.
# Uses Windows "Now Playing" (GSMTC) to read the current track, finds first YouTube result,
# and silently copies the URL to clipboard. Overlay appears at window bottom and fades out.
#
# Build to EXE (PyInstaller):
#   pyinstaller --onefile --noconsole --name "Spotify VRC" --icon TaskBarIcon.ico --collect-all winsdk --add-data "TaskBarIcon.ico;." spotify_vrc.py
#
# Requirements:
#   pip install winsdk requests pyinstaller
#
# © 2025

import re
import sys
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

import requests

APP_NAME = "Spotify VRC"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)

# --- Helpers ------------------------------------------------------------------

def resource_path(rel_path: str) -> str:
    """
    Return an absolute path to resource, works for dev and PyInstaller.
    """
    try:
        base_path = getattr(sys, "_MEIPASS")  # type: ignore[attr-defined]
    except Exception:
        base_path = Path(__file__).parent
    return str(Path(base_path) / rel_path)

# --- Logic --------------------------------------------------------------------

def youtube_first_result_url(query: str) -> str | None:
    """Return first YouTube watch URL for a query by parsing search HTML (no API key)."""
    base = "https://www.youtube.com/results"
    params = {"search_query": query}
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    r = requests.get(base, params=params, headers=headers, timeout=20)
    if r.status_code != 200:
        return None

    ids = []
    for m in re.finditer(r"watch\?v=([a-zA-Z0-9_-]{11})", r.text):
        vid = m.group(1)
        if vid not in ids:
            ids.append(vid)
    if not ids:
        return None
    return f"https://www.youtube.com/watch?v={ids[0]}"


def get_local_now_playing():
    """Windows GSMTC: read the currently playing media title and artist."""
    try:
        import asyncio  # noqa: F401
        from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as MediaManager
    except Exception as e:
        raise RuntimeError("Windows 'Now Playing' requires the 'winsdk' package. Install: pip install winsdk") from e

    async def _get():
        mgr = await MediaManager.request_async()
        sessions = mgr.get_sessions()
        # Prefer Spotify if present
        spotify_session = None
        for s in sessions:
            try:
                appid = s.source_app_user_model_id or ""
            except Exception:
                appid = ""
            if "Spotify" in appid:
                spotify_session = s
                break
        session = spotify_session or mgr.get_current_session()
        if not session:
            return None, None
        props = await session.try_get_media_properties_async()
        title = (getattr(props, "title", "") or "").strip()
        artist = (getattr(props, "artist", "") or "").strip()
        # Normalize separators like "Artist1; Artist2"
        artist = ", ".join([a.strip() for a in re.split(r"[;/]", artist) if a.strip()]) or artist
        return title, artist

    import asyncio
    return asyncio.run(_get())

# --- UI -----------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.resizable(False, False)
        self.attributes("-topmost", True)  # Always on top

        # Try to set window icon (works in dev and PyInstaller, when icon bundled via --add-data)
        try:
            ico_path = resource_path("TaskBarIcon.ico")
            if Path(ico_path).exists():
                self.iconbitmap(ico_path)
        except Exception:
            pass

        # state for overlay timers
        self._overlay = None
        self._overlay_after_id = None
        self._fade_after_id = None

        self._init_dark_theme()
        self._build_ui()

        # Let Tk auto-size window to content (just the big button)
        self.update_idletasks()
        self.geometry("")  # pick the natural size

    def _init_dark_theme(self):
        # Colors
        self.bg = "#0f1115"
        self.fg = "#e6e6e6"
        self.btn_bg = "#1f2937"
        self.btn_bg_active = "#273244"
        self.btn_bg_pressed = "#111827"
        self.overlay_bg = "#111827"
        self.overlay_fg = "#e6e6e6"

        self.configure(bg=self.bg)
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure("Dark.TFrame", background=self.bg)
        style.configure(
            "Giant.TButton",
            background=self.btn_bg,
            foreground=self.fg,
            padding=(56, 36),              # big padding
            font=("Segoe UI", 36, "bold"), # big font
            borderwidth=0
        )
        style.map(
            "Giant.TButton",
            background=[("active", self.btn_bg_active), ("pressed", self.btn_bg_pressed)],
            foreground=[("disabled", "#666666")]
        )
        style.layout("Giant.TButton", [
            ("Button.border", {"sticky": "nswe", "children": [
                ("Button.padding", {"sticky": "nswe", "children": [
                    ("Button.label", {"sticky": "nswe"})
                ]})
            ]})
        ])

    def _build_ui(self):
        self.container = ttk.Frame(self, style="Dark.TFrame", padding=0)
        self.container.pack(fill="both", expand=True)

        # Only one giant Share button, centered
        self.share_btn = ttk.Button(
            self.container, style="Giant.TButton", text="Share", command=self.share_song_url, takefocus=True
        )
        self.share_btn.pack(padx=0, pady=0)

    # --- Overlay helpers ---
    def _hide_overlay(self):
        # Cancel timers and destroy overlay toplevel if present
        if self._overlay_after_id is not None:
            try:
                self.after_cancel(self._overlay_after_id)
            except Exception:
                pass
            self._overlay_after_id = None
        if self._fade_after_id is not None:
            try:
                self.after_cancel(self._fade_after_id)
            except Exception:
                pass
            self._fade_after_id = None
        if self._overlay is not None:
            try:
                self._overlay.destroy()
            except Exception:
                pass
            self._overlay = None

    def _show_overlay(self, text: str, duration_ms: int = 2000, fade_ms: int = 500):
        # Remove existing overlay
        self._hide_overlay()

        # Ensure geometry is up to date
        self.update_idletasks()

        # Create borderless, topmost toplevel overlay
        overlay = tk.Toplevel(self)
        overlay.overrideredirect(True)
        overlay.attributes("-topmost", True)
        try:
            overlay.attributes("-alpha", 0.98)
        except Exception:
            pass  # alpha may not be supported in rare environments
        overlay.configure(bg=self.overlay_bg)

        # Content label
        lbl = tk.Label(
            overlay,
            text=text,
            bg=self.overlay_bg,
            fg=self.overlay_fg,
            font=("Segoe UI", 12, "bold"),
            padx=12,
            pady=6,
            bd=1,
            relief="ridge"
        )
        lbl.pack()

        # Position: bottom flush with window bottom, centered to the Share button
        try:
            self.update_idletasks()
            bx, by = self.share_btn.winfo_rootx(), self.share_btn.winfo_rooty()
            bw, bh = self.share_btn.winfo_width(), self.share_btn.winfo_height()

            overlay.update_idletasks()
            ow, oh = overlay.winfo_reqwidth(), overlay.winfo_reqheight()

            root_bottom = self.winfo_rooty() + self.winfo_height()
            x = int(bx + (bw - ow) / 2)
            y = int(root_bottom - oh)
            overlay.geometry(f"{ow}x{oh}+{x}+{y}")
        except Exception:
            # Fallback: center of root
            rx = self.winfo_rootx()
            ry = self.winfo_rooty()
            rw = self.winfo_width()
            rh = self.winfo_height()
            overlay.update_idletasks()
            ow, oh = overlay.winfo_reqwidth(), overlay.winfo_reqheight()
            x = int(rx + (rw - ow) / 2)
            y = int(ry + rh - oh)
            overlay.geometry(f"{ow}x{oh}+{x}+{y}")

        self._overlay = overlay

        # Fade parameters
        steps = 10
        step_ms = max(10, int(fade_ms / steps))

        def _fade_step(i: int):
            try:
                alpha = max(0.0, 0.98 * (1 - i / steps))
                try:
                    overlay.attributes("-alpha", alpha)
                except Exception:
                    pass
                if i < steps:
                    self._fade_after_id = self.after(step_ms, lambda: _fade_step(i + 1))
                else:
                    self._hide_overlay()
            except Exception:
                self._hide_overlay()

        # Schedule fade after hold
        self._overlay_after_id = self.after(max(0, duration_ms - fade_ms), lambda: _fade_step(1))

    # --- Actions ---
    def share_song_url(self):
        try:
            track, artist = get_local_now_playing()
            if not track:
                # Transient overlay instead of a message box
                self._show_overlay("no track is playing", duration_ms=2000, fade_ms=500)
                return

            query = f"{track} {artist}".strip()
            url = youtube_first_result_url(query)
            if not url:
                messagebox.showerror(APP_NAME, "Couldn't find a YouTube result.")
                return

            # Copy silently to clipboard
            self.clipboard_clear()
            self.clipboard_append(url)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Error: {e}")


if __name__ == "__main__":
    try:
        app = App()
        app.mainloop()
    except Exception as ex:
        try:
            messagebox.showerror(APP_NAME, f"Fatal error: {ex}")
        except Exception:
            print(f"Fatal error: {ex}")
