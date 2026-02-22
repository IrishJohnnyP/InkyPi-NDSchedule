import time
import logging
from typing import Any, Dict, List, Tuple

from plugins.base_plugin.base_plugin import BasePlugin
from utils.http_client import get_http_session

logger = logging.getLogger(__name__)

ND_TEAM_ID = 87

TEAM_URL = f"https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams/{ND_TEAM_ID}"
SCHEDULE_BASE_URL = f"https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams/{ND_TEAM_ID}/schedule"
RANKINGS_URL = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/rankings"
LEAGUE_CORE_URL = "https://sports.core.api.espn.com/v2/sports/football/leagues/college-football?lang=en&region=us"


class NdSchedule(BasePlugin):
    """Notre Dame Football schedule.

    v8:
      - Removes opponent record column.
      - Shows result only if completed as: W 31-24 / L 21-24 (Notre Dame perspective).
      - Optimizes line spacing and column spacing.

    Still includes:
      - Season year selectable (last 5 years), default current season.
      - Rankings only for current season (CFP preferred, else AP).
      - Location column.
      - Update line below title.
    """

    _cache: Dict[str, Any] = {"ts": {}, "data": {}}

    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = True
        return params

    def generate_image(self, settings: Dict[str, Any], device_config):
        font_size = (settings.get("font_size") or "normal").strip().lower()
        if font_size not in ("normal", "large", "larger", "largest"):
            font_size = "normal"

        compact_mode = self._to_bool(settings.get("compact_mode", False))
        show_rank_setting = self._to_bool(settings.get("show_rank", True))

        cache_minutes = max(0, min(1440, int(settings.get("cache_minutes") or 30)))
        ttl = cache_minutes * 60

        dims = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dims = dims[::-1]

        current_year = self._detect_current_season_year(ttl)
        selected = settings.get("season_year")
        try:
            season_year = int(str(selected)) if str(selected).strip() else current_year
        except Exception:
            season_year = current_year

        sched = self._fetch_schedule_for_year(season_year, ttl)
        nd_logo = self._fetch_team_logo(ttl)

        effective_show_rank = bool(show_rank_setting and season_year == current_year)
        rank_map: Dict[str, int] = {}
        rank_label = ""
        rank_updated = ""
        if effective_show_rank:
            rank_map, rank_label, rank_updated = self._get_rank_map(ttl, device_config)

        rows = self._build_rows(sched, rank_map, effective_show_rank, device_config)

        update_line = ""
        if effective_show_rank and rank_label:
            if rank_updated:
                update_line = f"Updated {rank_updated} • Rank source: {rank_label}"
            else:
                update_line = f"Rank source: {rank_label}"
        else:
            sched_updated = self._format_updated(sched, device_config)
            update_line = f"Updated {sched_updated}" if sched_updated else f"Season {season_year}"

        template_params = {
            "title": f"Notre Dame Football Schedule for {season_year}",
            "nd_logo": nd_logo,
            "update_line": update_line,
            "rows": rows,
            "font_size": font_size,
            "compact_mode": compact_mode,
            "plugin_settings": settings,
        }

        return self.render_image(dims, "ndschedule.html", "ndschedule.css", template_params)

    # ----------------------------
    # HTTP + caching
    # ----------------------------

    def _fetch_json_cached(self, url: str, ttl: int) -> Dict[str, Any]:
        now = time.time()
        ts = self._cache["ts"].get(url, 0.0)
        if ttl > 0 and url in self._cache["data"] and (now - ts) < ttl:
            return self._cache["data"][url]

        session = get_http_session()
        resp = session.get(url, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        if ttl > 0:
            self._cache["ts"][url] = now
            self._cache["data"][url] = data

        return data

    def _detect_current_season_year(self, ttl: int) -> int:
        from datetime import datetime
        year_guess = datetime.now().year
        try:
            core = self._fetch_json_cached(LEAGUE_CORE_URL, ttl)
            season = core.get("season")
            if isinstance(season, dict) and season.get("year"):
                return int(season.get("year"))
        except Exception:
            pass
        return year_guess

    def _fetch_schedule_for_year(self, year: int, ttl: int) -> Dict[str, Any]:
        candidates = [
            f"{SCHEDULE_BASE_URL}?season={year}",
            f"{SCHEDULE_BASE_URL}?year={year}",
            f"{SCHEDULE_BASE_URL}?season={year}&seasontype=2",
            f"{SCHEDULE_BASE_URL}?season={year}&seasontype=3",
            f"{SCHEDULE_BASE_URL}?year={year}&seasontype=2",
            f"{SCHEDULE_BASE_URL}?year={year}&seasontype=3",
            SCHEDULE_BASE_URL,
        ]
        last = None
        for url in candidates:
            try:
                data = self._fetch_json_cached(url, ttl)
                last = data
                events = data.get("events")
                if isinstance(events, list) and events:
                    return data
            except Exception:
                continue
        return last or {"events": []}

    def _fetch_team_logo(self, ttl: int) -> str:
        try:
            data = self._fetch_json_cached(TEAM_URL, ttl)
            team = data.get("team") if isinstance(data.get("team"), dict) else data
            logos = (team.get("logos") or []) if isinstance(team, dict) else []
            if isinstance(logos, list):
                for item in logos:
                    if isinstance(item, dict) and item.get("href"):
                        return item.get("href")
        except Exception:
            pass
        return f"https://a.espncdn.com/i/teamlogos/ncaa/500/{ND_TEAM_ID}.png"

    # ----------------------------
    # Helpers
    # ----------------------------

    def _safe_int(self, v: Any):
        try:
            if v is None:
                return None
            if isinstance(v, (int, float)):
                return int(v)
            if isinstance(v, str):
                s = v.strip()
                if not s:
                    return None
                if s.isdigit() or (s.startswith('-') and s[1:].isdigit()):
                    return int(s)
                return int(float(s))
        except Exception:
            return None
        return None

    # ----------------------------
    # Rankings
    # ----------------------------

    def _get_rank_map(self, ttl: int, device_config) -> Tuple[Dict[str, int], str, str]:
        data = self._fetch_json_cached(RANKINGS_URL, ttl)
        polls = data.get("rankings")
        if isinstance(polls, dict):
            polls = polls.get("items") or polls.get("rankings")
        if not isinstance(polls, list):
            return {}, "", ""

        def norm(s: Any) -> str:
            return str(s or "").strip().lower()

        def poll_iso(p: Dict[str, Any]) -> str:
            for k in ("date", "lastUpdated", "lastUpdate", "updated", "updateDate"):
                v = p.get(k)
                if v:
                    return str(v)
            return ""

        def poll_epoch(p: Dict[str, Any]) -> float:
            import datetime
            iso = poll_iso(p)
            if not iso:
                return 0.0
            try:
                ds = iso.replace('Z', '+00:00')
                dt = datetime.datetime.fromisoformat(ds)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
                return dt.timestamp()
            except Exception:
                return 0.0

        def is_cfp(p: Dict[str, Any]) -> bool:
            n = norm(p.get("name"))
            if "playoff selection committee" in n:
                return True
            blob = " ".join([n, norm(p.get("shortName")), norm(p.get("type")), norm(p.get("headline"))])
            return "cfp" in blob or "playoff" in blob

        def is_ap(p: Dict[str, Any]) -> bool:
            t = norm(p.get("type"))
            if t == "ap":
                return True
            n = norm(p.get("name"))
            s = norm(p.get("shortName"))
            return ("ap" in s) or ("ap top" in n)

        cfp = [p for p in polls if isinstance(p, dict) and is_cfp(p)]
        ap = [p for p in polls if isinstance(p, dict) and is_ap(p)]
        cfp.sort(key=poll_epoch, reverse=True)
        ap.sort(key=poll_epoch, reverse=True)

        poll = cfp[0] if cfp else (ap[0] if ap else None)
        if not poll:
            return {}, "", ""

        label = (poll.get("shortName") or poll.get("name") or "").strip()
        updated_iso = poll_iso(poll)
        updated_fmt = self._format_iso_datetime(updated_iso, device_config) if updated_iso else ""

        ranks = poll.get("ranks")
        if isinstance(ranks, dict):
            ranks = ranks.get("items") or ranks.get("entries") or ranks.get("ranks")
        if not isinstance(ranks, list):
            ranks = poll.get("entries") or []
        if not isinstance(ranks, list):
            ranks = []

        rank_map: Dict[str, int] = {}
        for r in ranks:
            if not isinstance(r, dict):
                continue
            rk = r.get("current") or r.get("rank") or r.get("position")
            team = r.get("team") or {}
            tid = team.get("id")
            try:
                if tid is not None and rk is not None:
                    rank_map[str(tid)] = int(rk)
            except Exception:
                pass

        rank_map = {k: v for k, v in rank_map.items() if 1 <= v <= 25}
        return rank_map, label, updated_fmt

    # ----------------------------
    # Rows
    # ----------------------------

    def _build_rows(self, sched: Dict[str, Any], rank_map: Dict[str, int], show_rank: bool, device_config) -> List[Dict[str, Any]]:
        events = sched.get("events") or []
        if not isinstance(events, list):
            events = []

        rows: List[Dict[str, Any]] = []
        for ev in events:
            if not isinstance(ev, dict):
                continue

            iso_date = ev.get("date") or ""

            comps = ev.get("competitions")
            if isinstance(comps, list) and comps:
                comp = comps[0]
            elif isinstance(ev.get("competitions"), dict):
                comp = ev.get("competitions")
            else:
                comp = ev

            date_disp = self._format_game_datetime(iso_date, comp, device_config)
            location_disp = self._format_location(comp)

            competitors = (comp.get("competitors") or []) if isinstance(comp, dict) else []
            if not isinstance(competitors, list):
                competitors = []

            nd_side = None
            opp_side = None
            for c in competitors:
                if not isinstance(c, dict):
                    continue
                team = c.get("team") or {}
                if str(team.get("id")) == str(ND_TEAM_ID):
                    nd_side = c
                else:
                    opp_side = c

            if not opp_side:
                continue

            opp_team = opp_side.get("team") or {}
            opp_id = str(opp_team.get("id") or "")
            opp_name = opp_team.get("shortDisplayName") or opp_team.get("displayName") or opp_team.get("name") or "Opponent"

            logo = ""
            logos = opp_team.get("logos")
            if isinstance(logos, list) and logos:
                for item in logos:
                    if isinstance(item, dict) and item.get("href"):
                        logo = item.get("href")
                        break
            else:
                logo = opp_team.get("logo") or ""

            rk = rank_map.get(opp_id) if show_rank else None

            result = ""
            status = (comp.get("status") or {}).get("type") if isinstance(comp, dict) else {}
            completed = bool(status.get("completed")) if isinstance(status, dict) else False
            if completed and nd_side:
                nd_score = self._safe_int(nd_side.get("score"))
                opp_score = self._safe_int(opp_side.get("score"))
                if nd_score is not None and opp_score is not None:
                    wl = "W" if nd_score > opp_score else ("L" if nd_score < opp_score else "T")
                    result = f"{wl} {nd_score}-{opp_score}"

            rows.append({
                "date": date_disp,
                "opp_rank": rk,
                "logo": logo,
                "opp_name": opp_name,
                "location": location_disp,
                "result": result,
            })

        return rows

    # ----------------------------
    # Formatting
    # ----------------------------

    def _format_location(self, comp: Dict[str, Any]) -> str:
        if not isinstance(comp, dict):
            return ""
        venue = comp.get("venue")
        if not isinstance(venue, dict):
            return ""
        address = venue.get("address")
        if isinstance(address, dict) and address.get("city") and address.get("state"):
            return f"{address.get('city')}, {address.get('state')}"
        return venue.get("fullName") or venue.get("name") or ""

    def _get_tzinfo(self, device_config):
        try:
            tz_name = device_config.get_config("timezone")
        except Exception:
            tz_name = None
        if not tz_name:
            return None
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(tz_name)
        except Exception:
            return None

    def _format_iso_datetime(self, iso_str: str, device_config) -> str:
        from datetime import datetime, timezone
        if not iso_str:
            return ""
        tzinfo = self._get_tzinfo(device_config)
        try:
            ds = iso_str.replace('Z', '+00:00')
            dt = datetime.fromisoformat(ds)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt_local = dt.astimezone(tzinfo) if tzinfo else dt.astimezone()
            date_part = dt_local.strftime('%b %d, %Y')
            hour = dt_local.strftime('%I').lstrip('0') or '12'
            minute = dt_local.strftime('%M')
            ampm = dt_local.strftime('%p')
            tz_abbr = (dt_local.strftime('%Z') or '').strip()
            time_part = f"{hour}:{minute} {ampm}" + (f" {tz_abbr}" if tz_abbr else "")
            return f"{date_part} {time_part}"
        except Exception:
            return iso_str

    def _format_game_datetime(self, iso_str: str, comp: Dict[str, Any], device_config) -> str:
        from datetime import datetime, timezone
        if not iso_str:
            return "TBD"

        tzinfo = self._get_tzinfo(device_config)
        time_tbd = False
        try:
            if isinstance(comp, dict):
                if comp.get('timeTBD') is True or comp.get('timeTbd') is True:
                    time_tbd = True
                st = (comp.get('status') or {}).get('type') or {}
                detail = str(st.get('detail') or '')
                if 'TBD' in detail.upper():
                    time_tbd = True
        except Exception:
            pass

        try:
            ds = iso_str.replace('Z', '+00:00')
            dt = datetime.fromisoformat(ds)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt_local = dt.astimezone(tzinfo) if tzinfo else dt.astimezone()

            date_part = dt_local.strftime('%b %d')
            if time_tbd and dt_local.hour == 0 and dt_local.minute == 0:
                return date_part

            hour = dt_local.strftime('%I').lstrip('0') or '12'
            minute = dt_local.strftime('%M')
            ampm = dt_local.strftime('%p')
            tz_abbr = (dt_local.strftime('%Z') or '').strip()
            time_part = f"{hour}:{minute} {ampm}" + (f" {tz_abbr}" if tz_abbr else "")
            return f"{date_part} {time_part}"
        except Exception:
            return iso_str[:10]

    def _format_updated(self, data: Dict[str, Any], device_config) -> str:
        date_str = None
        for k in ("timestamp", "lastUpdated", "date", "updateDate"):
            v = data.get(k)
            if v:
                date_str = str(v)
                break
        if not date_str:
            return ""

        from datetime import datetime, timezone
        tzinfo = self._get_tzinfo(device_config)
        try:
            if date_str.isdigit() and len(date_str) >= 12:
                dt = datetime.fromtimestamp(int(date_str) / 1000, tz=timezone.utc)
            elif date_str.isdigit():
                dt = datetime.fromtimestamp(int(date_str), tz=timezone.utc)
            else:
                ds = date_str.replace('Z', '+00:00')
                dt = datetime.fromisoformat(ds)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            dt_local = dt.astimezone(tzinfo) if tzinfo else dt.astimezone()

            date_part = dt_local.strftime("%b %d, %Y")
            hour = dt_local.strftime("%I").lstrip("0") or "12"
            minute = dt_local.strftime("%M")
            ampm = dt_local.strftime("%p")
            tz_abbr = (dt_local.strftime("%Z") or "").strip()
            time_part = f"{hour}:{minute} {ampm}" + (f" {tz_abbr}" if tz_abbr else "")
            return f"{date_part} {time_part}"
        except Exception:
            return ""

    def _to_bool(self, v: Any) -> bool:
        if isinstance(v, bool):
            return v
        if v is None:
            return False
        if isinstance(v, (list, tuple)) and v:
            v = v[-1]
        if isinstance(v, str):
            s = v.strip().lower()
            if s in ("1", "true", "yes", "on", "checked"):
                return True
            if s in ("0", "false", "no", "off", ""):
                return False
            return True
        return bool(v)
