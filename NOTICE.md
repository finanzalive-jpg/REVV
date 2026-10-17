# Credits and third-party licenses

Rise itself is released under the GNU General Public License v3.0 (see `LICENSE`).
It includes or downloads the following work. Thank you to all their authors.

| Component | Use in Rise | License |
| --- | --- | --- |
| [Ubuntu](https://ubuntu.com) 26.04 LTS | Base system: kernel, drivers, firmware, tools | Various free licenses (GPL, LGPL, MIT, …); see `/usr/share/doc/*/copyright` on the system |
| [Win11 icon theme](https://github.com/yeyushengfan258/Win11-icon-theme) by yeyushengfan258 (based on Yaru and others) | Icons for apps, folders and file types | GPL-3.0 |
| [labwc](https://github.com/labwc/labwc) | Window manager | GPL-2.0 |
| [cage](https://github.com/cage-kiosk/cage) | Fallback full-screen session | MIT |
| [gtk4-layer-shell](https://github.com/wmww/gtk4-layer-shell) | Desktop and taskbar layers | MIT |
| [pywayland](https://github.com/flacjacket/pywayland) and the wlr-foreign-toplevel protocol | Window list in the taskbar | NCSA / MIT |
| [Plymouth](https://www.freedesktop.org/wiki/Software/Plymouth/) | Boot splash | GPL-2.0 |
| [Samba](https://www.samba.org), [wsdd](https://github.com/christgau/wsdd), [Avahi](https://avahi.org) | Shared folders and network discovery | GPL-3.0 / MIT / LGPL-2.1 |
| [GNOME Web](https://apps.gnome.org/Epiphany/) | Web browser | GPL-3.0 |
| [mpv](https://mpv.io) | Video player | GPL-2.0 |
| [plocate](https://plocate.sesse.net) | File search | GPL-2.0 |
| [greetd](https://git.sr.ht/~kennylevinsen/greetd) | Automatic login | GPL-3.0 |
| [WebKitGTK](https://webkitgtk.org) and [GTK](https://gtk.org) | Engine that draws the desktop | LGPL-2.1 / BSD |
| [PDF.js](https://mozilla.github.io/pdf.js/) | PDF viewer | Apache-2.0 |
| [mammoth.js](https://github.com/mwilliamson/mammoth.js) | Word (.docx) viewer | BSD-2-Clause |
| [SheetJS Community Edition](https://sheetjs.com) | Excel (.xlsx) viewer | Apache-2.0 |
| [Onest](https://fonts.google.com/specimen/Onest), [Unbounded](https://fonts.google.com/specimen/Unbounded), [JetBrains Mono](https://www.jetbrains.com/lp/mono/), [Source Serif 4](https://fonts.google.com/specimen/Source+Serif+4) via [Fontsource](https://fontsource.org) | Typefaces | SIL Open Font License 1.1 |

Office suite, downloaded at build time from the official releases: [WordCraft](https://github.com/storytold/wordcraft), [GridCraft](https://github.com/storytold/gridcraft) and [DeckCraft](https://github.com/storytold/deckcraft) by Brandon Thomas and the ArtCraft team (MIT OR Apache-2.0).

Rise Drop implements the open [LocalSend protocol](https://github.com/localsend/protocol) (LocalSend is Apache-2.0); no LocalSend code is included.

Windows is a trademark of Microsoft Corporation. Rise is an independent project and is not affiliated with or endorsed by Microsoft.

## Assistant voice and web

- whisper.cpp (MIT) with OpenAI Whisper models (MIT): speech recognition, models downloaded on first use.
- Kokoro-82M (Apache 2.0) via kokoro-onnx (MIT) with the Italian voices Sara and Nicola: text to speech, model downloaded on first use. Piper (MIT) as fallback.
- Web search through DuckDuckGo's public HTML page; pages are fetched directly by the user's computer.
- fpdf2 (LGPL-3.0): PDF documents created by the Assistant.
- Flathub / Flatpak (LGPL-2.1): app store; each app keeps its own license, shown on flathub.org.
- Open-Meteo (data CC BY 4.0): weather forecasts.
- Market data from Yahoo Finance (delayed, for personal information only); news headlines from the publishers' public RSS feeds.
