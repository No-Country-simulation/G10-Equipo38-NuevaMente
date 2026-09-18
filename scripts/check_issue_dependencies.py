import os
import sys
import re
import json
import urllib.request
import urllib.error

sys.stdout.reconfigure(encoding="utf-8")

token = os.environ.get("GITHUB_TOKEN")
if not token:
    print("GITHUB_TOKEN is required in environment.")
    sys.exit(1)

repo = os.environ.get("GITHUB_REPOSITORY", "No-Country-simulation/G10-Equipo38-NuevaMente")

headers = {
    "Authorization": f"Bearer {token}",
    "Accept": "application/vnd.github+json",
    "User-Agent": "NuevaMente-Dependency-Bot"
}

def api_call(url, data=None, method="GET"):
    req_data = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=req_data, headers=headers, method=method)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))

def main():
    print(f"Checking dependencies for repository: {repo}")
    
    all_issues = []
    page = 1
    while True:
        url = f"https://api.github.com/repos/{repo}/issues?state=all&per_page=100&page={page}"
        batch = api_call(url)
        if not batch:
            break
        all_issues.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    states = {it["number"]: it["state"] for it in all_issues if "pull_request" not in it}
    open_issues = [it for it in all_issues if it.get("state") == "open" and "pull_request" not in it]
    
    print(f"Total issues: {len(states)}, Open issues: {len(open_issues)}")
    
    updated_count = 0
    unblocked_count = 0

    for issue in open_issues:
        num = issue["number"]
        body = issue["body"] or ""
        labels = [l["name"] for l in issue.get("labels", [])]
        
        if "### ⛔ Bloqueado por" not in body and "### 🟢 Estado" not in body:
            continue

        dep_matches = list(re.finditer(r"^-\s*\[([ xX])\]\s*#(\d+)(.*)$", body, re.MULTILINE))
        if not dep_matches:
            # 0 dependencies (root issue) -> should be ready
            if "status:blocked" in labels or "status:ready" not in labels:
                new_labels = [l for l in labels if not l.startswith("status:")] + ["status:ready"]
                patch_url = f"https://api.github.com/repos/{repo}/issues/{num}"
                api_call(patch_url, {"labels": new_labels}, method="PATCH")
                print(f"Issue #{num} (Root) labeled as status:ready")
            continue

        all_deps_closed = True
        new_body = body
        body_changed = False
        
        for m in dep_matches:
            is_checked = (m.group(1).lower() == "x")
            dep_num = int(m.group(2))
            rest = m.group(3)
            
            dep_state = states.get(dep_num, "open")
            is_dep_closed = (dep_state == "closed")
            
            if not is_dep_closed:
                all_deps_closed = False
                
            if is_dep_closed != is_checked:
                body_changed = True
                old_line = m.group(0)
                new_check = "[x]" if is_dep_closed else "[ ]"
                new_line = f"- {new_check} #{dep_num}{rest}"
                new_body = new_body.replace(old_line, new_line, 1)

        was_blocked = "status:blocked" in labels
        was_ready = "status:ready" in labels
        label_changed = False
        new_labels = [l for l in labels if not l.startswith("status:")]
        
        if all_deps_closed:
            new_labels.append("status:ready")
            if not was_ready or was_blocked:
                label_changed = True
        else:
            new_labels.append("status:blocked")
            if not was_blocked or was_ready:
                label_changed = True

        if body_changed or label_changed:
            patch_url = f"https://api.github.com/repos/{repo}/issues/{num}"
            payload = {}
            if body_changed:
                payload["body"] = new_body
            if label_changed:
                payload["labels"] = new_labels
                
            api_call(patch_url, payload, method="PATCH")
            updated_count += 1
            print(f"Updated Issue #{num}: all_deps_closed={all_deps_closed}, labels={new_labels}")
            
            if was_blocked and all_deps_closed:
                unblocked_count += 1
                comment_url = f"https://api.github.com/repos/{repo}/issues/{num}/comments"
                comment_body = {
                    "body": "🎉 **¡Desbloqueado!** Todas las dependencias requeridas para este issue han sido completadas en GitHub. ¡Ya está disponible para que cualquier integrante del equipo lo tome!"
                }
                api_call(comment_url, comment_body, method="POST")
                print(f"  Posted unblocked comment on #{num}")

    print(f"\nDependency sync complete: {updated_count} issues updated, {unblocked_count} newly unblocked.")

if __name__ == "__main__":
    main()
