import time
import logging
import requests
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class NotionClient:
    def __init__(
        self,
        api_key: str,
        page_or_db_id: str,
        notion_version: str = "2022-06-28",
        max_retries: int = 3,
    ):
        self.api_key = api_key
        # strip uuid hyphens
        self.raw_id = page_or_db_id.replace("-", "").strip()
        self.notion_version = notion_version
        self.max_retries = max_retries
        self.database_id: Optional[str] = None
        self.base_url = "https://api.notion.com/v1"

    @property
    def headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Notion-Version": self.notion_version,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """Performs HTTP request with exponential backoff for rate limits (429) and transient errors."""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        delay = 1.0

        for attempt in range(self.max_retries):
            try:
                response = requests.request(method, url, headers=self.headers, **kwargs)
                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", delay))
                    logger.warning("Notion rate limited (429). Retrying after %.1fs...", retry_after)
                    time.sleep(retry_after)
                    delay *= 2
                    continue

                if response.status_code in (500, 502, 503, 504):
                    logger.warning("Notion server error (%d). Attempt %d/%d...", response.status_code, attempt + 1, self.max_retries)
                    time.sleep(delay)
                    delay *= 2
                    continue

                data = response.json() if response.content else {}
                if not response.ok:
                    code = data.get("code", "unknown_error")
                    message = data.get("message", response.text)
                    raise RuntimeError(f"Notion API error ({response.status_code} {code}): {message}")

                return data
            except requests.RequestException as e:
                if attempt == self.max_retries - 1:
                    raise
                time.sleep(delay)
                delay *= 2

        raise RuntimeError(f"Failed Notion request to {endpoint} after {self.max_retries} attempts.")

    def resolve_target_database_id(self) -> str:
        """
        Determines whether the provided ID is directly a database,
        a page containing an inline database, or locates accessible databases.
        """
        if self.database_id:
            return self.database_id

        # probe direct database
        try:
            db_res = self._request("GET", f"/databases/{self.raw_id}")
            if db_res.get("object") == "database":
                self.database_id = self.raw_id
                return self.database_id
        except Exception:
            pass

        # check child blocks
        try:
            page_res = self._request("GET", f"/pages/{self.raw_id}")
            if page_res.get("object") == "page":
                # child database found
                blocks_res = self._request("GET", f"/blocks/{self.raw_id}/children")
                for block in blocks_res.get("results", []):
                    if block.get("type") == "child_database":
                        self.database_id = block["id"].replace("-", "")
                        return self.database_id
        except Exception:
            pass

        # search accessible databases
        try:
            search_res = self._request(
                "POST",
                "/search",
                json={"filter": {"property": "object", "value": "database"}},
            )
            results = search_res.get("results", [])
            if results:
                # use first match
                self.database_id = results[0]["id"].replace("-", "")
                return self.database_id
        except Exception:
            pass

        # fallback
        self.database_id = self.raw_id
        return self.database_id

    def extract_asset_link(self, page_properties: Dict[str, Any]) -> Optional[str]:
        # match assets column
        prop_key = None
        for key in page_properties.keys():
            if key.strip().lower() == "assets":
                prop_key = key
                break

        if not prop_key:
            return None

        prop = page_properties[prop_key]
        prop_type = prop.get("type")

        # handle url property
        if prop_type == "url":
            val = prop.get("url")
            return val.strip() if val else None

        # handle files property
        if prop_type == "files":
            files = prop.get("files", [])
            if files:
                first = files[0]
                if first.get("type") == "file":
                    return first.get("file", {}).get("url")
                elif first.get("type") == "external":
                    return first.get("external", {}).get("url")
            return None

        # handle rich text
        if prop_type == "rich_text":
            rt_list = prop.get("rich_text", [])
            for rt in rt_list:
                href = rt.get("href")
                if href:
                    return href.strip()
                text = rt.get("plain_text", "").strip()
                if text.startswith("http://") or text.startswith("https://"):
                    return text
            return None

        return None

    def extract_page_title(self, page_properties: Dict[str, Any]) -> str:
        """Extracts the title property of the page."""
        for prop in page_properties.values():
            if prop.get("type") == "title":
                title_list = prop.get("title", [])
                full_title = "".join(t.get("plain_text", "") for t in title_list).strip()
                if full_title:
                    return full_title
        return "Untitled Content"

    def extract_status(self, page_properties: Dict[str, Any]) -> str:
        # extract status property
        for key, prop in page_properties.items():
            if key.strip().lower() == "status":
                ptype = prop.get("type")
                if ptype == "select":
                    sel = prop.get("select")
                    return sel.get("name", "").strip() if sel else ""
                elif ptype == "status":
                    st = prop.get("status")
                    return st.get("name", "").strip() if st else ""
        return ""

    def extract_names_list(self, page_properties: Dict[str, Any], prop_name: str) -> List[str]:
        # extract person property values
        target = prop_name.strip().lower()
        targets = {target, target + "s", target.rstrip("s")}
        for key, prop in page_properties.items():
            if key.strip().lower() in targets:
                ptype = prop.get("type")
                if ptype == "multi_select":
                    return [o.get("name", "").strip() for o in prop.get("multi_select", []) if o.get("name")]
                elif ptype == "select":
                    sel = prop.get("select")
                    return [sel["name"].strip()] if sel and sel.get("name") else []
                elif ptype == "people":
                    return [p.get("name", "").strip() for p in prop.get("people", []) if p.get("name")]
                elif ptype == "rich_text":
                    raw = "".join(t.get("plain_text", "") for t in prop.get("rich_text", [])).strip()
                    return [n.strip() for n in raw.split(",") if n.strip()] if raw else []
        return []

    def query_assets_database(self) -> List[Dict[str, Any]]:
        target_db = self.resolve_target_database_id()
        endpoint = f"/databases/{target_db}/query"

        results = []
        has_more = True
        start_cursor = None

        while has_more:
            payload: Dict[str, Any] = {"page_size": 100}
            if start_cursor:
                payload["start_cursor"] = start_cursor

            response = self._request("POST", endpoint, json=payload)
            pages = response.get("results", [])
            for page in pages:
                page_id = page["id"]
                props = page.get("properties", {})
                asset_link = self.extract_asset_link(props)
                if asset_link:
                    title = self.extract_page_title(props)
                    page_url = page.get("url", f"https://www.notion.so/{page_id.replace('-', '')}")
                    status = self.extract_status(props)
                    reviewers = self.extract_names_list(props, "reviewer")
                    editors = self.extract_names_list(props, "editor")
                    uploaders = self.extract_names_list(props, "uploader")

                    results.append(
                        {
                            "notion_page_id": page_id,
                            "page_title": title,
                            "asset_url": asset_link,
                            "page_url": page_url,
                            "status": status,
                            "reviewers": reviewers,
                            "editors": editors,
                            "uploaders": uploaders,
                        }
                    )

            has_more = response.get("has_more", False)
            start_cursor = response.get("next_cursor")

        return results

    def update_page_status(self, page_id: str, new_status: str) -> bool:
        # update notion page status
        clean_id = page_id.replace("-", "").strip()
        status_variants = [new_status]
        if new_status == "Ready to Post":
            status_variants.append("Ready to post")
        elif new_status == "Ready to post":
            status_variants.append("Ready to Post")

        for variant in status_variants:
            for ptype in ("status", "select"):
                try:
                    self._request(
                        "PATCH",
                        f"/pages/{clean_id}",
                        json={"properties": {"Status": {ptype: {"name": variant}}}},
                    )
                    return True
                except Exception:
                    continue

        logger.error("Failed to update status for page %s to '%s'", page_id, new_status)
        return False
