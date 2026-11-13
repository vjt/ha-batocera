"""The box's TV, live, for as long as someone looks at it.

An MJPEG stream (what an <img> plays by itself): the card signs the path,
since an <img> cannot send the auth header. While a stream is open the box
keeps the daemon sending big frames (Box.watch); the frames go from MQTT to
the open streams and nowhere else.
"""

from __future__ import annotations

from aiohttp import web

from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.config_entries import ConfigEntryState

from .const import DOMAIN

# Seconds without a new frame before the last one is sent again: a still
# screen sends nothing, and only a write tells a viewer who left.
KEEPALIVE = 5

BOUNDARY = "frame"


class LiveView(HomeAssistantView):
    """GET /api/batocera/<entry id>/live: multipart JPEG frames."""

    url = "/api/batocera/{entry_id}/live"
    name = "api:batocera:live"
    requires_auth = True

    async def get(self, request: web.Request, entry_id: str) -> web.StreamResponse:
        hass = request.app[KEY_HASS]
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED:
            raise web.HTTPNotFound
        box = entry.runtime_data
        response = web.StreamResponse(
            headers={
                "Content-Type": f"multipart/x-mixed-replace;boundary={BOUNDARY}",
                "Cache-Control": "no-store",
            }
        )
        await response.prepare(request)
        end = box.watch()
        try:
            frame = None
            while request.transport is not None and not request.transport.is_closing():
                frame = await box.async_next_live(frame, KEEPALIVE)
                if frame is None:
                    continue
                await response.write(
                    f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                    f"Content-Length: {len(frame)}\r\n\r\n".encode()
                    + frame
                    + b"\r\n"
                )
        except ConnectionError:
            pass  # the viewer left
        finally:
            end()
        return response
