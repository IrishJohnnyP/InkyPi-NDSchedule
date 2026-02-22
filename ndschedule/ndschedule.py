import time
import logging
from typing import Any, Dict, List, Optional, Tuple

from plugins.base_plugin.base_plugin import BasePlugin
from utils.http_client import get_http_session

logger = logging.getLogger(__name__)

ND_TEAM_ID = 87

SCHEDULE_URL = f"https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams/{ND_TEAM_ID}/schedule"
RANKINGS_URL = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/rankings"


class NdSchedule(BasePlugin):
    """Notre Dame schedule plugin.

    Displays:
      - Game date
      - Opponent rank (CFP preferred, otherwise AP Top 25)
      - Opponent logo
      - Opponent name
      - Opponent record (as provided by ESPN schedule feed)
      - Score/result if game is completed

    Uses ESPN's unofficial site API endpoints.
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
        show_rank = self._to_bool(settings.get("show_rank", True))

        cache_minutes = max(0, min(1440, int(settings.get("cache_minutes") or 30)))
        ttl = cache_minutes * 60

        dims = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dims = dims[::-1]

        sched = self._fetch_json_cached(SCHEDULE_URL, ttl)
        rank_map, rank_label = self._get_rank_map(ttl)

        rows = self._build_rows(sched, rank_map, show_rank)

        updated = self._format_updated(sched, device_config)

        template_params = {
            "title": "Notre Dame Schedule",
            "poll_date": updated,
            "meta": rank_label if show_rank else "",
            "rows": rows,
            "font_size": font_size,
            "compact_mode": compact_mode,
        }

        return self.render_image(dims, "ndschedule.html", "ndschedule.css", template_params)

    # ----------------------------
    # Data
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

    def _get_rank_map(self, ttl: int) -> Tuple[Dict[str, int], str]:
        """Return a map of teamId->rank using CFP poll if present else AP poll."""
        data = self._fetch_json_cached(RANKINGS_URL, ttl)
        polls = data.get("rankings")
        if isinstance(polls, dict):
            polls = polls.get("items") or polls.get("rankings")
        if not isinstance(polls, list):
            return {}, ""

        def norm(s: Any) -> str:
            return str(s or "").strip().lower()

        def parse_date(p: Dict[str, Any]) -> float:
            import datetime
            for k in ("date", "lastUpdated", "lastUpdate", "updated", "updateDate"):
                v = p.get(k)
                if not v:
                    continue
                try:
                    ds = str(v).replace("Z", "+00:00")
                    dt = datetime.datetime.fromisoformat(ds)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=datetime.timezone.utc)
                    return dt.timestamp()
                except Exception:
                    pass
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
        cfp.sort(key=parse_date, reverse=True)
        ap.sort(key=parse_date, reverse=True)

        poll = cfp[0] if cfp else (ap[0] if ap else None)
        if not poll:
            return {}, ""

        label = (poll.get("shortName") or poll.get("name") or "").strip()

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

        # We only care about Top 25
        rank_map = {k: v for k, v in rank_map.items() if 1 <= v <= 25}
        return rank_map, label

    # ----------------------------
    # Build rows
    # ----------------------------

    def _build_rows(self, sched: Dict[str, Any], rank_map: Dict[str, int], show_rank: bool) -> List[Dict[str, Any]]:
        events = sched.get("events") or []
        if not isinstance(events, list):
            events = []

        rows = []
        for ev in events:
            if not isinstance(ev, dict):
                continue

            iso_date = ev.get("date") or ""
            date_disp = self._format_game_date(iso_date)

            comps = ev.get("competitions")
            if isinstance(comps, list) and comps:
                comp = comps[0]
            elif isinstance(ev.get("competitions"), dict):
                comp = ev.get("competitions")
            else:
                comp = ev

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
                    if team.get("id") is not None:
                        opp_side = c

            if not opp_side:
                continue

            opp_team = opp_side.get("team") or {}
            opp_id = str(opp_team.get("id") or "")
            opp_name = opp_team.get("shortDisplayName") or opp_team.get("displayName") or opp_team.get("name") or "Opponent"

            # logo
            logo = ""
            logos = opp_team.get("logos")
            if isinstance(logos, list) and logos:
                href = None
                for item in logos:
                    if isinstance(item, dict) and item.get("href"):
                        href = item.get("href")
                        break
                logo = href or ""
            else:
                logo = opp_team.get("logo") or ""

            # opponent record
            opp_record = ""
            recs = opp_side.get("records")
            if isinstance(recs, list) and recs:
                for r in recs:
                    if isinstance(r, dict) and r.get("type") in ("total", "overall"):
                        opp_record = r.get("summary") or r.get("displayValue") or ""
                        break
                if not opp_record:
                    r0 = recs[0]
                    if isinstance(r0, dict):
                        opp_record = r0.get("summary") or r0.get("displayValue") or ""
            if not opp_record:
                opp_record = opp_side.get("record") or ""

            # rank
            rk = rank_map.get(opp_id) if show_rank else None

            # result/score
            result = ""
            status = (comp.get("status") or {}).get("type") if isinstance(comp, dict) else {}
            completed = bool(status.get("completed")) if isinstance(status, dict) else False
            if completed and nd_side:
                try:
                    nd_score = int(float(nd_side.get("score") or 0))
                    opp_score = int(float(opp_side.get("score") or 0))
                    wl = "W" if nd_score > opp_score else ("L" if nd_score < opp_score else "T")
                    result = f"{wl} {nd_score}-{opp_score}"
                except Exception:
                    pass

            rows.append({
                "date": date_disp,
                "opp_rank": rk,
                "logo": logo,
                "opp_name": opp_name,
                "opp_record": opp_record,
                "result": result,
            })

        return rows

    # ----------------------------
    # Formatting
    # ----------------------------

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

    def _format_updated(self, data: Dict[str, Any], device_config) -> str:
        """Show date/time only."""
        from datetime import datetime, timezone
        # Try top-level timestamp keys
        date_str = None
        for k in ("timestamp", "lastUpdated", "date", "updateDate"):
            v = data.get(k)
            if v:
                date_str = str(v)
                break
        if not date_str:
            return ""

        tzinfo = self._get_tzinfo(device_config)
        try:
            # timestamp might be millis
            if date_str.isdigit() and len(date_str) >= 12:
                dt = datetime.fromtimestamp(int(date_str)/1000, tz=timezone.utc)
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

    def _format_game_date(self, iso_str: str) -> str:
        from datetime import datetime, timezone
        if not iso_str:
            return "TBD"
        tzinfo = None
        try:
            # BasePlugin may render without device tz here; keep local system time
            tzinfo = None
        except Exception:
            tzinfo = None
        try:
            ds = iso_str.replace('Z', '+00:00')
            dt = datetime.fromisoformat(ds)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt_local = dt.astimezone(tzinfo) if tzinfo else dt.astimezone()
            return dt_local.strftime('%b %d')
        except Exception:
            return iso_str[:10]

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
