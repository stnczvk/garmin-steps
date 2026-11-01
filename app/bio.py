"""Publiczna strona „link w bio” (/bio): linki do YT, Instagrama i TikToka + najnowszy vlog i dzień redukcji z centrum."""
import re
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from social import _read

router = APIRouter()
WEB = Path(__file__).parent / "web"
LINKS = {"youtube": "https://www.youtube.com/@lysyzsilowni", "instagram": "https://www.instagram.com/st4nczuk_/",
         "tiktok": "https://www.tiktok.com/@lysyzsilowni"}


@router.get("/bio")
def bio_page():
    return FileResponse(WEB / "bio.html", media_type="text/html", headers={"Cache-Control": "no-cache"})


@router.get("/bio/avatar.jpg")
def bio_avatar():
    return FileResponse(WEB / "bio_avatar.jpg", media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/bio/data")
def bio_data():
    acc = _read("cache.json", {}).get("accounts") or {}
    acc = list(acc.values()) if isinstance(acc, dict) else acc
    priv = [a for a in acc if a.get("group") == "prywatne"]
    yt = next((a for a in priv if a.get("platform") == "youtube"), {})
    vids = sorted([p for p in yt.get("posts") or [] if (p.get("dur") or 0) > 60] or yt.get("posts") or [], key=lambda p: p.get("ts") or "", reverse=True)
    last = vids[0] if vids else None
    day = 0
    try:
        from planer import plan
        for x in plan():
            m = re.search(r"dzień\s+(\d+)", x.get("label", ""), re.I)
            if m and x.get("status") in ("published", "approved"):
                day = max(day, int(m.group(1)))
    except Exception:
        pass
    return JSONResponse({"links": LINKS, "day": day or None, "followers": sum(a.get("followers") or 0 for a in priv),
                         "latest": {"title": last.get("title"), "url": last.get("url"), "id": last.get("id")} if last else None},
                        headers={"Cache-Control": "public, max-age=300"})
