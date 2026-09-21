#!/usr/bin/env python3
"""
Seerr (Overseerr / Jellyseerr) & Radarr Request Resender
=========================================================
A CLI utility to find approved, processing requests in Overseerr or Jellyseerr
and resend/retry them to Radarr.

Supports two modes:
  1. Overseerr Retry Mode (Default): Calls POST /api/v1/request/{id}/retry in Overseerr.
  2. Direct Radarr Sync Mode: Directly queries Radarr via API, adds missing movies, 
     and triggers search commands directly in Radarr (bypasses Overseerr queue issues!).

Requirements:
    Python 3.7+ (No external pip packages required!)

Usage:
    # Mode 1: Retry approved processing requests via Overseerr API
    python3 resend_seerr_requests.py --url http://localhost:5055 --api-key OVERSEERR_KEY

    # Mode 2: Direct Radarr Sync (Injects missing approved requests directly into Radarr & triggers search)
    python3 resend_seerr_requests.py \
      --url http://localhost:5055 --api-key OVERSEERR_KEY \
      --radarr-url http://localhost:7878 --radarr-api-key RADARR_KEY

Environment Variables (Optional):
    SEERR_URL       - Overseerr / Jellyseerr base URL
    SEERR_API_KEY   - Overseerr / Jellyseerr API key
    RADARR_URL      - Radarr base URL (e.g., http://localhost:7878)
    RADARR_API_KEY  - Radarr API key
"""

import argparse
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Set, Tuple

# Terminal Color Formatting
class Colors:
    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKCYAN = "\033[96m"
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"
    DIM = "\033[2m"

    @classmethod
    def disable(cls):
        cls.HEADER = ""
        cls.OKBLUE = ""
        cls.OKCYAN = ""
        cls.OKGREEN = ""
        cls.WARNING = ""
        cls.FAIL = ""
        cls.ENDC = ""
        cls.BOLD = ""
        cls.UNDERLINE = ""
        cls.DIM = ""

if not sys.stdout.isatty():
    Colors.disable()


REQUEST_STATUS_MAP = {
    1: "PENDING_APPROVAL",
    2: "APPROVED",
    3: "DECLINED",
}

MEDIA_STATUS_MAP = {
    1: "UNKNOWN",
    2: "PENDING",
    3: "PROCESSING",
    4: "PARTIALLY_AVAILABLE",
    5: "AVAILABLE",
}


def load_env_file(filepath: str = ".env") -> None:
    """Simple parser for .env files without requiring python-dotenv."""
    if not os.path.isfile(filepath):
        return
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip("'\"")
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception:
        pass


class SeerrClient:
    """HTTP Client for Overseerr / Jellyseerr API."""

    def __init__(self, base_url: str, api_key: str, timeout: int = 15):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _request(
        self, endpoint: str, method: str = "GET", payload: Optional[Dict[str, Any]] = None, max_redirects: int = 5
    ) -> Tuple[int, Any]:
        headers = {
            "X-Api-Key": self.api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Seerr-Request-Resender/2.0",
        }

        current_url = f"{self.base_url}/api/v1{endpoint}"
        for _ in range(max_redirects):
            data = json.dumps(payload).encode("utf-8") if payload is not None else None
            req = urllib.request.Request(current_url, data=data, headers=headers, method=method)

            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    final_url = resp.geturl()
                    if "/api/v1" in final_url:
                        self.base_url = final_url.split("/api/v1")[0]
                    resp_data = resp.read().decode("utf-8")
                    json_resp = json.loads(resp_data) if resp_data else {}
                    return resp.status, json_resp
            except urllib.error.HTTPError as e:
                if e.code in (301, 302, 303, 307, 308):
                    location = e.headers.get("Location")
                    if location:
                        current_url = urllib.parse.urljoin(current_url, location)
                        if "/api/v1" in current_url:
                            self.base_url = current_url.split("/api/v1")[0]
                        continue
                try:
                    err_body = e.read().decode("utf-8")
                    err_json = json.loads(err_body)
                except Exception:
                    err_json = {"message": str(e)}
                return e.code, err_json
            except urllib.error.URLError as e:
                return 0, {"message": f"URL / Network Error: {e.reason}"}
            except Exception as e:
                return 0, {"message": f"Unexpected Error: {str(e)}"}

        return 307, {"message": f"Too many redirects ({current_url})"}

    def test_connection(self) -> Tuple[bool, str]:
        status, data = self._request("/settings/public")
        if status == 200:
            version = data.get("version", "Unknown")
            return True, f"Successfully connected to Seerr (v{version})"
        return False, data.get("message", f"HTTP Error {status}")

    def get_all_requests(self, sort: str = "added") -> List[Dict[str, Any]]:
        all_requests: List[Dict[str, Any]] = []
        page = 1
        take = 50

        print(f"{Colors.OKCYAN}Fetching requests from Seerr...{Colors.ENDC}", end="", flush=True)

        while True:
            params = urllib.parse.urlencode({"take": take, "skip": (page - 1) * take, "sort": sort})
            status, data = self._request(f"/request?{params}")

            if status != 200:
                print(f"\n{Colors.FAIL}Error fetching requests: {data.get('message')}{Colors.ENDC}")
                break

            results = data.get("results", [])
            all_requests.extend(results)

            page_info = data.get("pageInfo", {})
            total_pages = page_info.get("pages", 1)

            print(".", end="", flush=True)

            if page >= total_pages or not results:
                break
            page += 1

        print(f" Total fetched: {Colors.BOLD}{len(all_requests)}{Colors.ENDC}")
        return all_requests

    def retry_request(self, request_id: int) -> Tuple[bool, str]:
        status, data = self._request(f"/request/{request_id}/retry", method="POST")
        if status in (200, 201):
            return True, "Success"
        message = data.get("message") or data.get("error") or f"HTTP {status}"
        return False, message


class RadarrClient:
    """HTTP Client for Radarr API v3."""

    def __init__(self, base_url: str, api_key: str, timeout: int = 15):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _request(
        self, endpoint: str, method: str = "GET", payload: Optional[Dict[str, Any]] = None, max_redirects: int = 5
    ) -> Tuple[int, Any]:
        headers = {
            "X-Api-Key": self.api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Seerr-Radarr-Sync/2.0",
        }

        current_url = f"{self.base_url}/api/v3{endpoint}"
        for _ in range(max_redirects):
            data = json.dumps(payload).encode("utf-8") if payload is not None else None
            req = urllib.request.Request(current_url, data=data, headers=headers, method=method)

            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    final_url = resp.geturl()
                    if "/api/v3" in final_url:
                        self.base_url = final_url.split("/api/v3")[0]
                    resp_data = resp.read().decode("utf-8")
                    json_resp = json.loads(resp_data) if resp_data else {}
                    return resp.status, json_resp
            except urllib.error.HTTPError as e:
                if e.code in (301, 302, 303, 307, 308):
                    location = e.headers.get("Location")
                    if location:
                        current_url = urllib.parse.urljoin(current_url, location)
                        if "/api/v3" in current_url:
                            self.base_url = current_url.split("/api/v3")[0]
                        continue
                try:
                    err_body = e.read().decode("utf-8")
                    err_json = json.loads(err_body)
                except Exception:
                    err_json = {"message": str(e)}
                return e.code, err_json
            except urllib.error.URLError as e:
                return 0, {"message": f"URL / Network Error: {e.reason}"}
            except Exception as e:
                return 0, {"message": f"Unexpected Error: {str(e)}"}

        return 307, {"message": f"Too many redirects ({current_url})"}

    def test_connection(self) -> Tuple[bool, str]:
        status, data = self._request("/system/status")
        if status == 200:
            version = data.get("version", "Unknown")
            return True, f"Successfully connected to Radarr (v{version})"
        return False, data.get("message", f"HTTP Error {status}")

    def get_existing_tmdb_ids(self) -> Dict[int, int]:
        """Returns dict mapping tmdbId -> radarr_movie_id for all movies in Radarr."""
        status, data = self._request("/movie")
        movie_map = {}
        if status == 200 and isinstance(data, list):
            for m in data:
                tmdb_id = m.get("tmdbId")
                radarr_id = m.get("id")
                if tmdb_id and radarr_id:
                    movie_map[tmdb_id] = radarr_id
        return movie_map

    def get_default_quality_profile_id(self) -> int:
        status, data = self._request("/qualityprofile")
        if status == 200 and isinstance(data, list) and len(data) > 0:
            return data[0].get("id", 1)
        return 1

    def get_default_root_folder(self) -> str:
        status, data = self._request("/rootfolder")
        if status == 200 and isinstance(data, list) and len(data) > 0:
            return data[0].get("path", "")
        return ""

    def add_movie_by_tmdb(
        self,
        tmdb_id: int,
        quality_profile_id: Optional[int] = None,
        root_folder_path: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Directly adds a movie to Radarr via TMDB lookup."""
        status, lookup_resp = self._request(f"/movie/lookup/tmdb?tmdbId={tmdb_id}")
        if status != 200:
            return False, f"Radarr TMDB lookup failed (HTTP {status})"

        movie_data = None
        if isinstance(lookup_resp, dict) and lookup_resp.get("tmdbId"):
            movie_data = lookup_resp
        elif isinstance(lookup_resp, list) and len(lookup_resp) > 0:
            movie_data = lookup_resp[0]

        if not movie_data:
            return False, f"TMDB #{tmdb_id} not found in Radarr lookup"

        q_id = quality_profile_id or self.get_default_quality_profile_id()
        r_path = root_folder_path or self.get_default_root_folder()

        if not r_path:
            return False, "No valid root folder path configured in Radarr"

        movie_data["qualityProfileId"] = q_id
        movie_data["rootFolderPath"] = r_path
        movie_data["monitored"] = True
        movie_data["addOptions"] = {"searchForMovie": True}

        post_status, post_resp = self._request("/movie", method="POST", payload=movie_data)
        if post_status in (200, 201):
            return True, "Added to Radarr & search triggered"

        if isinstance(post_resp, list) and len(post_resp) > 0:
            err_msg = post_resp[0].get("errorMessage") or str(post_resp)
        else:
            err_msg = post_resp.get("message") or post_resp.get("error") or f"HTTP {post_status}"
        return False, err_msg

    def trigger_movie_search(self, movie_ids: List[int]) -> Tuple[bool, str]:
        """Triggers a MoviesSearch command in Radarr."""
        if not movie_ids:
            return True, "No movies to search"
        payload = {"name": "MoviesSearch", "movieIds": movie_ids}
        status, data = self._request("/command", method="POST", payload=payload)
        if status in (200, 201):
            return True, "Radarr search command issued successfully"
        return False, data.get("message", f"HTTP {status}")


def extract_media_title(req: Dict[str, Any], client: SeerrClient) -> str:
    """Attempts to resolve movie title."""
    media = req.get("media") or {}
    media_type = media.get("mediaType") or req.get("type") or "movie"
    tmdb_id = media.get("tmdbId") or req.get("tmdbId")

    if media.get("title"):
        return media.get("title")
    if media.get("name"):
        return media.get("name")

    if tmdb_id:
        endpoint = f"/movie/{tmdb_id}" if media_type == "movie" else f"/tv/{tmdb_id}"
        status, data = client._request(endpoint)
        if status == 200:
            title = data.get("title") or data.get("name") or data.get("originalTitle")
            if title:
                release_year = (data.get("releaseDate") or data.get("firstAirDate") or "")[:4]
                return f"{title} ({release_year})" if release_year else title

    return f"TMDB #{tmdb_id}" if tmdb_id else f"Request #{req.get('id')}"


def parse_date(date_str: Optional[str]) -> Optional[datetime.datetime]:
    if not date_str:
        return None
    try:
        clean_str = date_str.replace("Z", "+00:00")
        return datetime.datetime.fromisoformat(clean_str)
    except Exception:
        return None


def format_request_summary(req: Dict[str, Any], title: str, radarr_id: Optional[int] = None) -> str:
    req_id = req.get("id")
    media = req.get("media") or {}
    media_type = (media.get("mediaType") or req.get("type") or "movie").upper()
    req_status_id = req.get("status", 0)
    media_status_id = media.get("status", 0)

    req_status_str = REQUEST_STATUS_MAP.get(req_status_id, f"STATUS_{req_status_id}")
    media_status_str = MEDIA_STATUS_MAP.get(media_status_id, f"MEDIA_{media_status_id}")

    ext_id = radarr_id if radarr_id is not None else media.get("externalServiceId")
    ext_str = f"Radarr ID: {ext_id}" if ext_id is not None else f"{Colors.FAIL}Radarr ID: MISSING{Colors.ENDC}"

    created_at = req.get("createdAt", "")[:10]

    return (
        f"[{Colors.BOLD}#{req_id: <4}{Colors.ENDC}] "
        f"{Colors.OKCYAN}{media_type: <5}{Colors.ENDC} | "
        f"{Colors.BOLD}{title[:32]: <32}{Colors.ENDC} | "
        f"Req: {Colors.OKBLUE}{req_status_str}{Colors.ENDC} | "
        f"Media: {Colors.WARNING if media_status_str in ('PENDING', 'PROCESSING') else Colors.OKGREEN}{media_status_str}{Colors.ENDC} | "
        f"{ext_str} | Date: {created_at}"
    )


def main():
    load_env_file()

    parser = argparse.ArgumentParser(
        description="Resend / sync approved Overseerr requests to Radarr.",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    group_seerr = parser.add_argument_group("Overseerr / Jellyseerr Settings")
    group_seerr.add_argument(
        "--url",
        default=os.getenv("SEERR_URL") or os.getenv("OVERSEERR_URL") or os.getenv("JELLYSEERR_URL"),
        help="Base URL for Overseerr/Jellyseerr (e.g. http://localhost:5055). Default: $SEERR_URL",
    )
    group_seerr.add_argument(
        "--api-key",
        default=os.getenv("SEERR_API_KEY") or os.getenv("OVERSEERR_API_KEY") or os.getenv("JELLYSEERR_API_KEY"),
        help="Overseerr API Key. Default: $SEERR_API_KEY",
    )

    group_radarr = parser.add_argument_group("Radarr Direct Connection (Optional - For Direct Injection)")
    group_radarr.add_argument(
        "--radarr-url",
        default=os.getenv("RADARR_URL"),
        help="Radarr Base URL (e.g. http://localhost:7878). If provided, enables Direct Radarr Injection mode.",
    )
    group_radarr.add_argument(
        "--radarr-api-key",
        default=os.getenv("RADARR_API_KEY"),
        help="Radarr API Key.",
    )
    group_radarr.add_argument(
        "--radarr-quality-profile-id",
        type=int,
        help="Radarr Quality Profile ID for direct injection (optional).",
    )
    group_radarr.add_argument(
        "--radarr-root-folder",
        help="Radarr Root Folder Path for direct injection (e.g. /movies).",
    )

    group_filter = parser.add_argument_group("Filter Options")
    group_filter.add_argument(
        "--media-type",
        choices=["movie", "tv", "all"],
        default="movie",
        help="Filter by media type: 'movie' (Radarr), 'tv' (Sonarr), or 'all'. Default: movie",
    )
    group_filter.add_argument(
        "--status",
        choices=["approved_processing", "processing", "pending", "failed", "all"],
        default="approved_processing",
        help="Filter requests by status:\n"
        "  approved_processing : Only APPROVED requests currently in PROCESSING or PENDING state (Default)\n"
        "  processing          : Only media in PROCESSING state (status 3)\n"
        "  pending             : Only media in PENDING state (status 2)\n"
        "  failed              : Only requests missing Radarr external service ID\n"
        "  all                 : All approved non-available requests",
    )
    group_filter.add_argument(
        "--older-than-days",
        type=int,
        metavar="N",
        help="Only target requests created MORE than N days ago.",
    )
    group_filter.add_argument(
        "--newer-than-days",
        type=int,
        metavar="N",
        help="Only target requests created WITHIN the last N days.",
    )
    group_filter.add_argument(
        "--request-ids",
        help="Target specific request ID(s), comma-separated (e.g. 10,12,45). Overrides other filters.",
    )

    group_exec = parser.add_argument_group("Execution Options")
    group_exec.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview requests that WOULD be resent without actually modifying anything.",
    )
    group_exec.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Delay in seconds between API calls. Default: 0.5s",
    )
    group_exec.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Automatically confirm and execute retries without prompt.",
    )

    args = parser.parse_args()

    if not args.url or not args.api_key:
        print(f"{Colors.FAIL}Error: Overseerr --url and --api-key (or $SEERR_URL and $SEERR_API_KEY) are required.{Colors.ENDC}")
        sys.exit(1)

    print(f"\n{Colors.BOLD}{Colors.HEADER}===================================================={Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.HEADER}  Seerr & Radarr Request Resender & Direct Sync CLI  {Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.HEADER}===================================================={Colors.ENDC}\n")

    seerr = SeerrClient(args.url, args.api_key)
    ok, msg = seerr.test_connection()
    if not ok:
        print(f"{Colors.FAIL}Overseerr Connection Error: {msg}{Colors.ENDC}")
        sys.exit(1)
    print(f"{Colors.OKGREEN}✓ {msg}{Colors.ENDC}")

    radarr: Optional[RadarrClient] = None
    existing_radarr_tmdb_map: Dict[int, int] = {}

    if args.radarr_url and args.radarr_api_key:
        radarr = RadarrClient(args.radarr_url, args.radarr_api_key)
        r_ok, r_msg = radarr.test_connection()
        if not r_ok:
            print(f"{Colors.FAIL}Radarr Connection Error: {r_msg}{Colors.ENDC}")
            sys.exit(1)
        print(f"{Colors.OKGREEN}✓ Direct Radarr Mode Active: {r_msg}{Colors.ENDC}")
        existing_radarr_tmdb_map = radarr.get_existing_tmdb_ids()
        print(f"  Found {len(existing_radarr_tmdb_map)} existing movies in Radarr library.\n")
    else:
        print(f"{Colors.WARNING}* Running in Overseerr Retry Mode. (Tip: Pass --radarr-url and --radarr-api-key to inject directly into Radarr if Overseerr retry is failing!){Colors.ENDC}\n")

    # Fetch Overseerr requests
    requests_list = seerr.get_all_requests()
    if not requests_list:
        print(f"{Colors.WARNING}No requests found in Overseerr.{Colors.ENDC}")
        sys.exit(0)

    target_ids = set()
    if args.request_ids:
        try:
            target_ids = {int(i.strip()) for i in args.request_ids.split(",") if i.strip()}
        except ValueError:
            print(f"{Colors.FAIL}Error: --request-ids must be comma-separated integers.{Colors.ENDC}")
            sys.exit(1)

    now = datetime.datetime.now(datetime.timezone.utc)
    matching_requests: List[Dict[str, Any]] = []

    for req in requests_list:
        req_id = req.get("id")
        media = req.get("media") or {}
        media_type = (media.get("mediaType") or req.get("type") or "movie").lower()
        req_status = req.get("status", 0)  # 2 = APPROVED
        media_status = media.get("status", 0)  # 2 = PENDING, 3 = PROCESSING, 5 = AVAILABLE

        if target_ids:
            if req_id in target_ids:
                matching_requests.append(req)
            continue

        # Strict rule: Must be APPROVED request
        if req_status != 2:
            continue

        # Skip already available
        if media_status == 5:
            continue

        # Filter media type
        if args.media_type != "all" and media_type != args.media_type:
            continue

        # Filter status
        if args.status == "approved_processing" and media_status not in (2, 3):
            continue
        elif args.status == "processing" and media_status != 3:
            continue
        elif args.status == "pending" and media_status != 2:
            continue

        # Filter by date age
        created_at_dt = parse_date(req.get("createdAt"))
        if created_at_dt:
            age_days = (now - created_at_dt).total_seconds() / 86400.0
            if args.older_than_days is not None and age_days < args.older_than_days:
                continue
            if args.newer_than_days is not None and age_days > args.newer_than_days:
                continue

        matching_requests.append(req)

    print(f"\n{Colors.BOLD}Found {len(matching_requests)} approved request(s) matching criteria:{Colors.ENDC}\n")

    if not matching_requests:
        print(f"{Colors.OKGREEN}No matching requests found.{Colors.ENDC}")
        sys.exit(0)

    printable_items: List[Tuple[Dict[str, Any], str, Optional[int]]] = []
    for req in matching_requests:
        media = req.get("media") or {}
        tmdb_id = media.get("tmdbId") or req.get("tmdbId")
        radarr_id = existing_radarr_tmdb_map.get(tmdb_id) if radarr and tmdb_id else None
        title = extract_media_title(req, seerr)
        printable_items.append((req, title, radarr_id))
        print(format_request_summary(req, title, radarr_id))

    print("-" * 80)

    if args.dry_run:
        print(f"\n{Colors.WARNING}{Colors.BOLD}[DRY-RUN MODE] No changes made.{Colors.ENDC}")
        sys.exit(0)

    if not args.yes:
        confirm = input(f"\nProceed with resending/syncing these {len(printable_items)} request(s)? [y/N]: ")
        if confirm.lower() not in ("y", "yes"):
            print(f"{Colors.WARNING}Cancelled.{Colors.ENDC}")
            sys.exit(0)

    print(f"\n{Colors.OKCYAN}{Colors.BOLD}Processing requests...{Colors.ENDC}\n")
    success_count = 0
    fail_count = 0

    for idx, (req, title, radarr_id) in enumerate(printable_items, start=1):
        req_id = req.get("id")
        media = req.get("media") or {}
        tmdb_id = media.get("tmdbId") or req.get("tmdbId")

        print(f"[{idx}/{len(printable_items)}] Request #{req_id} ({title})... ", end="", flush=True)

        if radarr and tmdb_id:
            # DIRECT RADARR MODE
            if radarr_id:
                # Already in Radarr! Trigger search
                s_ok, s_msg = radarr.trigger_movie_search([radarr_id])
                if s_ok:
                    print(f"{Colors.OKGREEN}✓ Movie already in Radarr -> Triggered Radarr Search{Colors.ENDC}")
                    success_count += 1
                else:
                    print(f"{Colors.WARNING}In Radarr, but search failed: {s_msg}{Colors.ENDC}")
                    fail_count += 1
            else:
                # Missing from Radarr -> Inject directly
                a_ok, a_msg = radarr.add_movie_by_tmdb(
                    tmdb_id=tmdb_id,
                    quality_profile_id=args.radarr_quality_profile_id,
                    root_folder_path=args.radarr_root_folder,
                )
                if a_ok:
                    print(f"{Colors.OKGREEN}✓ INJECTED DIRECTLY INTO RADARR & SEARCH STARTED!{Colors.ENDC}")
                    success_count += 1
                else:
                    print(f"{Colors.FAIL}✗ Direct Radarr Injection Failed ({a_msg}){Colors.ENDC}")
                    fail_count += 1
        else:
            # OVERSEERR RETRY MODE
            ok, msg = seerr.retry_request(req_id)
            if ok:
                print(f"{Colors.OKGREEN}✓ RETRIED VIA OVERSEERR{Colors.ENDC}")
                success_count += 1
            else:
                print(f"{Colors.FAIL}✗ OVERSEERR RETRY FAILED ({msg}){Colors.ENDC}")
                fail_count += 1

        if args.delay > 0 and idx < len(printable_items):
            time.sleep(args.delay)

    print(f"\n{Colors.BOLD}Execution Finished!{Colors.ENDC}")
    print(f"  Processed: {len(printable_items)} | {Colors.OKGREEN}Success: {success_count}{Colors.ENDC} | {Colors.FAIL}Failed: {fail_count}{Colors.ENDC}\n")


if __name__ == "__main__":
    main()
