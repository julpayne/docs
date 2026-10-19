"""Run the upstream cluster suite without writing into its source checkout."""
import json
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET


def summarize(report):
    counts = {"tests": 0, "passed": 0, "failed": 0, "skipped": 0, "errors": 0}
    for case in ET.parse(report).getroot().iter("testcase"):
        counts["tests"] += 1
        status = case.get("status", "")
        error_types = {error.get("type", "") for error in case.findall("error")}
        if case.find("failure") is not None or status in ("failed", "ambiguous", "undefined", "pending"):
            counts["failed"] += 1
        elif error_types - {"skipped"}:
            counts["errors"] += 1
        elif case.find("skipped") is not None or status == "skipped" or "skipped" in error_types:
            counts["skipped"] += 1
        elif status in ("", "passed"):
            counts["passed"] += 1
        else:
            counts["errors"] += 1
    return counts


def run_godog(repo, evidence, env, timeout):
    report = evidence / "junit-godog.xml"
    summary = {"tags": "@cluster", "status": "incomplete", "exit_code": None}
    try:
        with tempfile.TemporaryDirectory(prefix="evalhub-godog-") as tmp:
            root = Path(tmp)
            binary = root / "features.test"
            subprocess.run(["go", "test", "-c", "-mod=readonly", "-o", str(binary), "./tests/features"],
                           cwd=repo, env=env, check=True, timeout=900)
            # Upstream writes bin/tests.log and resolves fixtures from the repo root.
            # Give it a private working directory with the original source/fixtures.
            work = root / "work"
            work.mkdir()
            for source in repo.iterdir():
                if source.name not in ("bin", ".venv", "node_modules"):
                    (work / source.name).symlink_to(source, target_is_directory=source.is_dir())
            (work / "bin").symlink_to(evidence, target_is_directory=True)
            result = subprocess.run([
                str(binary), "-test.v", f"-test.timeout={timeout}s",
                "--godog.tags=@cluster", "--godog.concurrency=1", "--godog.strict=true",
                "--godog.stop-on-failure=false",
                f"--godog.format=pretty,junit:{report},cucumber:{evidence / 'cucumber-godog.json'}",
            ], cwd=work, env=env, timeout=timeout, check=False)
            summary["exit_code"] = result.returncode
            if result.returncode:
                raise subprocess.CalledProcessError(result.returncode, ["Godog", "@cluster"])
    finally:
        if report.exists():
            try:
                summary.update(summarize(report))
                if summary["exit_code"] == 0 and summary["tests"]:
                    summary["status"] = ("failed" if summary["failed"] or summary["errors"] else
                                         "passed_with_skips" if summary["skipped"] else "passed")
                elif summary["exit_code"]:
                    summary["status"] = "failed"
            except (OSError, ET.ParseError) as error:
                summary["report_error"] = str(error)
        (evidence / "godog-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(f"Godog @cluster: {json.dumps(summary)}\nReports: {evidence}", flush=True)
    if summary["status"] not in ("passed", "passed_with_skips"):
        raise RuntimeError("Godog did not report a successful, nonempty scenario run; inspect reports.")
