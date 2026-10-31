#!/usr/bin/env python3
"""Deploy the GainFrame sales page (site/) to Vercel via the REST API.

Uses the stored custom.vercel-account credential (full-account token) through
the dynamic-credentials surrogate flow — no raw token ever touches this script.
Codified after the get-ballot-help deploy-upload.py pattern.

Usage:
  python3 deploy-site.py            # create project if needed, upload, deploy to production
  python3 deploy-site.py --domains  # also (re)add gainframe.cc + www.gainframe.cc to the project

Prints JSON status lines; the final line is the production deployment.
"""
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
from dynamic_credentials import add_surrogate_to_request, read_json_response

CREDENTIAL = "custom.vercel-account"
ALLOWED_HOSTS = ("api.vercel.com",)
TEAM_ID = "team_J2rJMMn9uYdvJ41IH286aG4K"
PROJECT_NAME = "gainframe"
SITE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site")
API = "https://api.vercel.com"


def call(method, path, body=None, raw=None, headers=None):
    url = API + path
    data = raw
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    add_surrogate_to_request(req, CREDENTIAL, allowed_hosts=ALLOWED_HOSTS)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, read_json_response(resp)
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode())
        except Exception:
            payload = {"error": {"message": str(exc)}}
        return exc.code, payload


def find_project():
    status, payload = call("GET", f"/v9/projects?teamId={TEAM_ID}&limit=100")
    for p in payload.get("projects", []):
        if p.get("name") == PROJECT_NAME:
            return p
    return None


def main():
    with_domains = "--domains" in sys.argv

    project = find_project()
    if project is None:
        status, payload = call("POST", f"/v10/projects?teamId={TEAM_ID}",
                               body={"name": PROJECT_NAME})
        if status not in (200, 201):
            print(json.dumps({"step": "create_project", "status": status, "payload": payload}))
            return 1
        project = payload
        print(json.dumps({"step": "create_project", "id": project["id"]}))
    else:
        print(json.dumps({"step": "project_exists", "id": project["id"]}))
    pid = project["id"]

    # Upload every file in site/
    files = []
    for name in sorted(os.listdir(SITE_DIR)):
        fpath = os.path.join(SITE_DIR, name)
        if not os.path.isfile(fpath):
            continue
        blob = open(fpath, "rb").read()
        sha = hashlib.sha1(blob).hexdigest()
        status, payload = call("POST", f"/v2/now/files?teamId={TEAM_ID}", raw=blob,
                               headers={"Content-Type": "application/octet-stream",
                                        "x-vercel-digest": sha})
        if status not in (200, 201):
            print(json.dumps({"step": "upload", "file": name, "status": status, "payload": payload}))
            return 1
        files.append({"file": name, "sha": sha, "size": len(blob)})
        print(json.dumps({"step": "uploaded", "file": name, "sha": sha, "size": len(blob)}))

    status, payload = call("POST", f"/v13/deployments?teamId={TEAM_ID}", body={
        "name": PROJECT_NAME,
        "project": pid,
        "target": "production",
        "files": files,
        "projectSettings": {"framework": None},
    })
    if status not in (200, 201):
        print(json.dumps({"step": "create_deployment", "status": status, "payload": payload}))
        return 1
    dep = payload
    print(json.dumps({"step": "deployment_created", "id": dep["id"], "url": dep.get("url")}))

    # Poll until ready
    for _ in range(40):
        time.sleep(5)
        status, payload = call("GET", f"/v13/deployments/{dep['id']}?teamId={TEAM_ID}")
        state = payload.get("readyState")
        if state in ("READY", "ERROR", "CANCELED"):
            dep = payload
            break
    print(json.dumps({"step": "deployment_final", "id": dep["id"],
                      "readyState": dep.get("readyState"), "url": dep.get("url"),
                      "alias": dep.get("alias")}))

    if with_domains:
        for domain in ("gainframe.cc", "www.gainframe.cc"):
            status, payload = call("POST", f"/v10/projects/{pid}/domains?teamId={TEAM_ID}",
                                   body={"name": domain})
            print(json.dumps({"step": "add_domain", "domain": domain,
                              "status": status, "payload": payload}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
