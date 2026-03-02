import time
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional

from plugins.base_plugin.base_plugin import BasePlugin
from utils.http_client import get_http_session

logger = logging.getLogger(__name__)

ND_TEAM_ID = 87
TEAM_URL = f"https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams/{ND_TEAM_ID}"
TEAM_DETAIL_URL_BASE = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams/"
RANKINGS_URL = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/rankings"
LEAGUE_CORE_URL = "https://sports.core.api.espn.com/v2/sports/football/leagues/college-football?lang=en&region=us"
ND_LOGO_URL = "https://a.espncdn.com/i/teamlogos/ncaa/500/87.png"

BASE_W = 800
BASE_H = 480

def _ensure_icon_file():
    try:
        here = Path(__file__).resolve().parent
        icon_path = here / "icon.png"
        if icon_path.exists() and icon_path.stat().st_size > 2000:
            return
        session = get_http_session()
        resp = session.get(ND_LOGO_URL, timeout=15)
        if resp.status_code == 200 and resp.content and len(resp.content) > 2000:
            icon_path.write_bytes(resp.content)
    except Exception:
        return

_ensure_icon_file()

class NdSchedule(BasePlugin):
    _cache: Dict[str, Any] = {"ts": {}, "data": {}}

    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = True
        try:
            current_year = self._detect_current_season_year(ttl=0)
        except Exception:
            from datetime import datetime
            current_year = datetime.now().year
        params["current_year"] = int(current_year)
        params["years"] = [int(current_year) - i for i in range(0, 10)]
        return params

    def generate_image(self, settings: Dict[str, Any], device_config):
        font_size = (settings.get("font_size") or "normal").strip().lower()
        if font_size not in ("normal", "large", "larger", "largest"):
            font_size = "normal"

        compact_mode = self._to_bool(settings.get("compact_mode", False))
        show_time = self._to_bool(settings.get("show_time", True))
        show_rank_setting = self._to_bool(settings.get("show_rank", True))
        hide_rank = self._to_bool(settings.get("hide_rank", False))
        hide_nickname = self._to_bool(settings.get("hide_nickname", False))
        hide_logo = self._to_bool(settings.get("hide_logo", False))

        cache_minutes = max(0, min(1440, int(settings.get("cache_minutes") or 30)))
        ttl = cache_minutes * 60

        dims = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dims = dims[::-1]
        try:
            w = int(dims[0]); h = int(dims[1])
        except Exception:
            w, h = BASE_W, BASE_H

        output_scale = min(w / BASE_W, h / BASE_H) if BASE_W and BASE_H else 1.0
        output_scale = max(0.10, min(5.00, output_scale))

        short_edge = min(w, h)
        squeeze = 0.0 if short_edge >= 700 else max(0.0, min(0.35, (700 - short_edge) / 700.0 * 0.35))

        current_year = self._detect_current_season_year(ttl)
        selected = settings.get("season_year")
        try:
            season_year = int(str(selected)) if str(selected).strip() else current_year
        except Exception:
            season_year = current_year

        sched = self._fetch_schedule_for_year(ND_TEAM_ID, season_year, ttl)
        nd_logo = self._fetch_team_logo(ttl)

        rank_map: Dict[str, int] = {}
        rank_label = ""; rank_updated = ""
        if bool(show_rank_setting and season_year == current_year and not hide_rank):
            rank_map, rank_label, rank_updated = self._get_rank_map(ttl)

        rows = self._build_rows(sched, rank_map, bool(show_rank_setting and season_year == current_year and not hide_rank), season_year, ttl, show_time=show_time)

        update_line = f"Updated {rank_updated} • Rank source: {rank_label}" if rank_label else f"Season {season_year}"

        template_params = {
            "title": f"Notre Dame Football Schedule for {season_year}",
            "nd_logo": nd_logo,
            "update_line": update_line,
            "rows": rows,
            "font_size": font_size,
            "compact_mode": bool(compact_mode),
            "show_time": bool(show_time),
            "hide_rank": bool(hide_rank),
            "hide_nickname": bool(hide_nickname),
            "hide_logo": bool(hide_logo),
            "base_w": BASE_W,
            "base_h": BASE_H,
            "output_scale": f"{output_scale:.4f}",
            "auto_squeeze": f"{squeeze:.4f}",
            "plugin_settings": settings,
        }
        return self.render_image(dims, "ndschedule.html", "ndschedule.css", template_params)

    # (helpers omitted in fallback; this file is only used if sources exist)
