"""Bound the REST metadata preflight; the pinned Pages action remains the deploy gate."""

import argparse
import json
import os
import time
import urllib.error
import urllib.request


def select_artifact(data, name, artifact_id, head):
    matches = [artifact for artifact in data["artifacts"] if artifact["name"] == name]
    if not matches:
        return None
    if len(matches) != 1:
        raise RuntimeError("Expected exactly one artifact with the attempt-specific name")
    artifact = matches[0]
    if str(artifact["id"]) != str(artifact_id):
        raise RuntimeError("Artifact ID differs from the completed upload")
    if artifact.get("expired") is not False:
        raise RuntimeError("Artifact is expired or expiry state is unavailable")
    if artifact.get("workflow_run", {}).get("head_sha") != head:
        raise RuntimeError("Artifact source revision differs from this workflow")
    return artifact


def wait_ready(fetch, name, artifact_id, head, now=time.monotonic, pause=time.sleep):
    deadline = now() + 60
    last = "expected artifact not visible"
    while now() < deadline:
        remaining = deadline - now()
        if remaining <= 0:
            break
        try:
            artifact = select_artifact(fetch(min(5, remaining)), name, artifact_id, head)
            if artifact is not None:
                return artifact
            last = "expected artifact not visible"
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504):
                raise
            last = "transient metadata HTTP " + str(error.code)
            error.close()
        except (urllib.error.URLError, TimeoutError):
            last = "metadata transport unavailable"
        pause(min(2, max(0, deadline - now())))
    raise TimeoutError("Pages artifact " + name + " (upload " + str(artifact_id)
                       + ") not ready within 60 seconds: " + last)


def list_run_artifacts(endpoint, token, timeout, opener=urllib.request.urlopen, now=time.monotonic):
    deadline = now() + timeout
    artifacts = []
    page = 1
    while True:
        remaining = deadline - now()
        if remaining <= 0:
            raise TimeoutError("Artifact metadata pagination exceeded its request budget")
        request = urllib.request.Request(endpoint + "?per_page=100&page=" + str(page), headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
        })
        with opener(request, timeout=remaining) as response:
            batch = json.load(response)["artifacts"]
        artifacts.extend(batch)
        if len(batch) < 100:
            return {"artifacts": artifacts}
        page += 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-access", action="store_true")
    args = parser.parse_args(argv)
    endpoint = (os.environ["GITHUB_API_URL"] + "/repos/" + os.environ["GITHUB_REPOSITORY"]
                + "/actions/runs/" + os.environ["GITHUB_RUN_ID"] + "/artifacts")

    def fetch(timeout):
        return list_run_artifacts(endpoint, os.environ["GH_TOKEN"], timeout)

    if args.probe_access:
        fetch(5)
        print("Read-only metadata access verified for this workflow run")
        return
    artifact_id = os.environ["EXPECTED_ARTIFACT_ID"]
    if not artifact_id:
        raise RuntimeError("Upload did not return an artifact ID")
    artifact = wait_ready(fetch, os.environ["PAGES_ARTIFACT_NAME"], artifact_id, os.environ["GITHUB_SHA"])
    # REST visibility is a signal; deploy-pages still performs its own artifact lookup.
    print("Expected Pages artifact metadata is visible: " + str(artifact["id"]))


if __name__ == "__main__":
    main()
