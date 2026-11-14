"""Turning Renovate's JSON log into a summary.

With `--report-type=logging`, Renovate emits one "Printing report" record holding the
package files it extracted (with the available updates) and the branches it would create.
Everything shown to the user comes from that record, plus the warnings and errors logged
along the way.
"""

from __future__ import annotations

import json

LEVELS = {10: "trace", 20: "debug", 30: "info", 40: "warn", 50: "error", 60: "fatal"}

# With --print-config (renovate-preview --show-config), Renovate logs the repository's
# config, presets resolved, defaults included, in this record's `config`. We leave out
# its `hostRules`. https://docs.renovatebot.com/self-hosted-configuration/#printconfig
RESOLVED_CONFIG = "Full resolved config and hostRules including presets"

# What a record says about how far the run has got, for the progress hints.
PHASES = {
    "Repository started": "reading configuration",
    "extract()": "extracting dependencies",
    "Dependency extraction complete": "dependencies extracted",
    "branchifyUpgrades": "computing branches",
}


def parse_record(line: str) -> dict | None:
    """Return a JSON log record, or None for the plain text Renovate interleaves."""
    if not line.startswith("{"):
        return None
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return None
    return record if isinstance(record, dict) else None


def level(record: dict) -> str:
    return LEVELS.get(record.get("level"), str(record.get("level")))


def raw_report(records: list[dict]) -> dict | None:
    """Return Renovate's report, as it printed it."""
    for record in records:
        if record.get("msg") == "Printing report" and isinstance(record.get("report"), dict):
            return record["report"]
    return None


def summarize(target: str, records: list[dict]) -> dict:
    """Return the document every output format renders."""
    report, version, stats, problems, config = None, None, None, [], None
    for record in records:
        msg = record.get("msg")
        if msg == "Printing report" and isinstance(record.get("report"), dict):
            report = record["report"]
        elif msg == RESOLVED_CONFIG:
            config = record.get("config")
        elif msg == "Repository started":
            version = record.get("renovateVersion")
        elif msg == "Dependency extraction complete":
            stats = (record.get("stats") or {}).get("total")
        if record.get("level", 0) >= 40:
            problem = f"{level(record).upper()}: {msg}"
            # An invalid repository config stops the run: "Repository has invalid config",
            # and why, in the error.
            err = record.get("err") or {}
            if err.get("validationMessage"):
                problem += f": {err.get('validationSource')}: {err['validationMessage'].strip()}"
            if problem not in problems:
                problems.append(problem)

    updates: list[dict] = []
    branches: list[dict] = []
    for repo in ((report or {}).get("repositories") or {}).values():
        for manager, package_files in (repo.get("packageFiles") or {}).items():
            for package_file in package_files:
                updates += dependency_rows(manager, package_file)
        for branch in repo.get("branches") or []:
            branches.append(
                {
                    "branch": branch.get("branchName"),
                    "pr_title": branch.get("prTitle"),
                    "upgrades": [u.get("depName") for u in branch.get("upgrades") or []],
                }
            )

    titles = {b["branch"]: b["pr_title"] for b in branches}
    for row in updates:
        row["pr_title"] = titles.get(row["branch"])

    return {
        "target": target,
        "renovate_version": version,
        "files_scanned": (stats or {}).get("fileCount"),
        "updates": sorted(updates, key=sort_key),
        "branches": branches,
        "problems": problems,
        "config": config,
    }


def sort_key(row: dict) -> tuple:
    """Sort by dependency, then the new version's age, then the file.

    Youngest first, unknown age last. The sort is stable: rows still tied stay in
    Renovate's order.
    """
    age = row["age_days"]
    return (str(row["dependency"] or "").lower(), age is None, age or 0, row["file"] or "")


UPDATE_KEYS = (
    "new",
    "type",
    "branch",
    "breaking",
    "age_days",
    "release_timestamp",
    "pending_checks",
    "pending_versions",
)


def dependency_rows(manager: str, package_file: dict) -> list[dict]:
    """Return one row per dependency, or one per available update when there are some."""
    rows = []
    for dep in package_file.get("deps") or []:
        current = dep.get("currentValue") or dep.get("currentVersion")
        base = {
            "dependency": dep.get("depName") or dep.get("packageName"),
            "current": current,
            "manager": manager,
            "datasource": dep.get("datasource"),
            "versioning": dep.get("versioning"),
            "file": package_file.get("packageFile"),
            "skip_reason": dep.get("skipReason"),
        }
        updates = dep.get("updates") or []
        if not updates:
            rows.append({**base, **dict.fromkeys(UPDATE_KEYS)})
        for update in updates:
            new = update.get("newValue") or update.get("newVersion")
            digest = update.get("newDigest")
            if digest and (new == current or update.get("updateType") == "digest"):
                # Same tag, new digest: show the digest, or it reads as a no-op.
                new = f"{new or ''}@{digest.removeprefix('sha256:')[:12]}"
            rows.append(
                {
                    **base,
                    "new": new,
                    "type": update.get("updateType"),
                    "branch": update.get("branchName"),
                    # As the versioning scheme says: under semver, any 0.x change is.
                    "breaking": update.get("isBreaking"),
                    # Absent when the datasource gives no release timestamp.
                    "age_days": update.get("newVersionAgeInDays"),
                    "release_timestamp": update.get("releaseTimestamp"),
                    # minimumReleaseAge (or minimumConfidence) not met yet: either this
                    # update itself, or newer versions held back in favour of this one.
                    "pending_checks": bool(update.get("pendingChecks")),
                    "pending_versions": update.get("pendingVersions") or [],
                }
            )
    return rows
