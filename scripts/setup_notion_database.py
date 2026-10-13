#!/usr/bin/env python3
"""Configures the Notion CRM database schema and properties."""

import os
import sys
import json
import time
import urllib.request
import urllib.error
from collections import OrderedDict

NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
PAGE_ID = os.environ.get("NOTION_PAGE_ID", "62f8017799ad4eef8d2cf036a6fd3643")
DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "bc283116b8fc4d7d8fe40cc3db11be8b")
DEFAULT_NOTION_VERSION = "2022-06-28"
VIEWS_NOTION_VERSION = "2025-09-03"

# ordered column layout
ORDERED_COLUMNS = [
    "Company Name",
    "Company Description",
    "Sender Email",
    "Sender Name",
    "Action Required",
    "Notes",
    "Status",
    "Opportunity Type",
    "Deliverable Scope",
    "Proposed Rate",
    "Negotiated Rate",
    "Thread ID",
    "Thread Link",
    "Thread Started",
    "Date Ingested"
]

def make_request(endpoint: str, method: str = "GET", payload: dict = None, version: str = DEFAULT_NOTION_VERSION) -> dict:
    url = f"https://api.notion.com/v1/{endpoint.lstrip('/')}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": version,
        "Content-Type": "application/json"
    }
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8")
        try:
            err_json = json.loads(err_msg)
            return {"error": True, "status": e.code, "message": err_json.get("message", err_msg), "raw": err_json}
        except Exception:
            return {"error": True, "status": e.code, "message": err_msg}

def check_access(page_id: str, db_id: str):
    db_res = make_request(f"databases/{db_id}")
    if not db_res.get("error"):
        return True, "database", db_res
    page_res = make_request(f"pages/{page_id}")
    if not page_res.get("error"):
        return True, "page", page_res
    return False, "none", page_res

def sync_pipeline_schema(db_id: str):
    print(f"Applying CRM schema to Database ID: {db_id}...")
    
    # update title
    make_request(f"databases/{db_id}", method="PATCH", payload={
        "title": [{"type": "text", "text": {"content": "Pipeline"}}]
    })

    # schema properties
    properties = OrderedDict()
    properties["Company Name"] = {"title": {}}
    properties["Company Description"] = {"rich_text": {}}
    properties["Sender Email"] = {"email": {}}
    properties["Sender Name"] = {"rich_text": {}}
    properties["Action Required"] = {
        "select": {
            "options": [
                {"name": "Needs Reply", "color": "red"},
                {"name": "Waiting on Reply", "color": "yellow"},
                {"name": "No Action Needed", "color": "gray"}
            ]
        }
    }
    properties["Notes"] = {"rich_text": {}}
    properties["Status"] = {
        "status": {
            "options": [
                {"name": "Needs Review", "color": "blue"},
                {"name": "In Discussion", "color": "yellow"},
                {"name": "Contract Sent", "color": "orange"},
                {"name": "Closed / Won", "color": "green"},
                {"name": "Passed / Dead", "color": "gray"}
            ]
        }
    }
    properties["Opportunity Type"] = {
        "select": {
            "options": [
                {"name": "Paid Client", "color": "green"},
                {"name": "Paid Integration", "color": "blue"},
                {"name": "Gifting / Product", "color": "purple"},
                {"name": "Affiliate", "color": "orange"},
                {"name": "TBD / Discovery", "color": "gray"}
            ]
        }
    }
    properties["Deliverable Scope"] = {
        "select": {
            "options": [
                {"name": "TBD", "color": "gray"},
                {"name": "Dedicated YouTube Video", "color": "red"},
                {"name": "Instagram", "color": "pink"},
                {"name": "Instagram + Social Channels", "color": "purple"},
                {"name": "Integration", "color": "blue"}
            ]
        }
    }
    properties["Proposed Rate"] = {"number": {"format": "dollar"}}
    properties["Negotiated Rate"] = {"number": {"format": "dollar"}}
    properties["Thread ID"] = {"rich_text": {}}
    properties["Thread Link"] = {"url": {}}
    properties["Thread Started"] = {"date": {}}
    properties["Date Ingested"] = {"date": {}}

    res = make_request(f"databases/{db_id}", method="PATCH", payload={"properties": properties})
    return res

def reorder_database_views(db_id: str):
    print("Reordering database view columns to match exact sequence...")
    # map property names to ids
    db_res = make_request(f"databases/{db_id}")
    if db_res.get("error"):
        print(f"[!] Could not retrieve database properties: {db_res.get('message')}")
        return False

    name_to_id = {}
    for prop_name, prop_data in db_res.get("properties", {}).items():
        name_to_id[prop_name] = prop_data.get("id")

    # fetch current views
    views_res = make_request(f"views?database_id={db_id}", method="GET", version=VIEWS_NOTION_VERSION)
    if views_res.get("error"):
        print(f"[!] Could not fetch views: {views_res.get('message')}")
        return False

    views = views_res.get("results", [])
    if not views:
        print("[i] No views found to update.")
        return True

    for v in views:
        view_id = v.get("id")
        view_name = v.get("name", "Untitled View")
        view_type = v.get("type", "table")

        ordered_view_props = []
        for col_name in ORDERED_COLUMNS:
            prop_id = name_to_id.get(col_name)
            if prop_id:
                ordered_view_props.append({
                    "property_id": prop_id,
                    "visible": True
                })

        patch_payload = {
            "configuration": {
                "type": view_type,
                "properties": ordered_view_props
            }
        }

        patch_res = make_request(f"views/{view_id}", method="PATCH", payload=patch_payload, version=VIEWS_NOTION_VERSION)
        if patch_res.get("error"):
            print(f"  [!] Failed to reorder view '{view_name}' ({view_id}): {patch_res.get('message')}")
        else:
            print(f"  [✓] Successfully reordered view '{view_name}' ({view_id})!")

    return True

def test_page_write(db_id: str):
    print("\nVerifying end-to-end write permissions with a temporary row...")
    test_payload = {
        "parent": {"database_id": db_id},
        "properties": {
            "Company Name": {"title": [{"text": {"content": "[System Connectivity Test]"}}]},
            "Company Description": {"rich_text": [{"text": {"content": "Automated pipeline write test."}}]},
            "Sender Email": {"email": "probe@crm-test.local"},
            "Sender Name": {"rich_text": [{"text": {"content": "Verification Probe"}}]},
            "Action Required": {"select": {"name": "No Action Needed"}},
            "Notes": {"rich_text": []},
            "Status": {"status": {"name": "Needs Review"}},
            "Opportunity Type": {"select": {"name": "TBD / Discovery"}},
            "Deliverable Scope": {"select": {"name": "TBD"}},
            "Thread ID": {"rich_text": [{"text": {"content": "test-probe-001"}}]}
        }
    }
    create_res = make_request("pages", method="POST", payload=test_payload)
    if create_res.get("error"):
        print(f"[!] Write verification failed: {create_res.get('message')}")
        return False
    row_id = create_res.get("id")
    print(f"[✓] Write verified! Created test row: {row_id}")
    
    # cleanup
    make_request(f"blocks/{row_id}", method="DELETE")
    print(f"[✓] Test row cleaned up.")
    return True

def main():
    print(f"--- Notion Pipeline Schema & View Manager ---")
    print(f"Target Page ID:     {PAGE_ID}")
    print(f"Target Database ID: {DATABASE_ID}")
    
    watch_mode = "--watch" in sys.argv or "-w" in sys.argv

    ok, access_type, access_data = check_access(PAGE_ID, DATABASE_ID)
    if not ok:
        print("\n[!] Notion Integration cannot access this page or database yet.")
        print(f"Notion API message: {access_data.get('message')}")
        print("\nEnsure the page has been shared with the Notion integration.")
        
        if watch_mode:
            print("Watching for connection...")
            while not ok:
                time.sleep(3)
                ok, access_type, access_data = check_access(PAGE_ID, DATABASE_ID)
                if not ok:
                    print(".", end="", flush=True)
            print("\n[✓] Connection detected!")
        else:
            print("Run with '--watch' to poll until connected.")
            sys.exit(1)

    print(f"[✓] Access verified via {access_type}!")
    
    res = sync_pipeline_schema(DATABASE_ID)
    if res.get("error"):
        print(f"[!] Schema sync failed: {res.get('message')}")
        sys.exit(1)

    print("\n========================================================")
    print(f"[✓] SUCCESS! 'Pipeline' database schema synchronized!")
    print(f"Database ID: {DATABASE_ID.replace('-', '')}")
    print(f"Database URL: {res.get('url')}")
    print("========================================================")

    # reorder columns
    reorder_database_views(DATABASE_ID)

    # verify write permissions
    test_page_write(DATABASE_ID.replace('-', ''))

    print("\nRequested Column Sequence Applied:")
    for idx, col in enumerate(ORDERED_COLUMNS, start=1):
        print(f"  {idx:2d}. {col}")

if __name__ == "__main__":
    main()
