import re
from dataclasses import dataclass, field
from pathlib import Path

MAX_FILE_BYTES = 1 << 20
MAX_LINE_LEN = 5000
MAX_FINDINGS = 50


@dataclass
class Finding:
    severity: str
    rule_id: str
    file: str
    line: int | None
    message: str


@dataclass
class ScanReport:
    findings: list[Finding] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if any(f.severity == "high" for f in self.findings):
            return "blocked"
        if self.findings:
            return "warn"
        return "clean"

    def counts(self) -> dict[str, int]:
        high = sum(1 for f in self.findings if f.severity == "high")
        medium = sum(1 for f in self.findings if f.severity == "medium")
        low = sum(1 for f in self.findings if f.severity == "low")
        return {"high": high, "medium": medium, "low": low}

    def summary(self) -> str:
        c = self.counts()
        return f"{self.verdict}: {c['high']} high, {c['medium']} medium, {c['low']} low"


class SecurityBlocked(Exception):
    def __init__(self, report: ScanReport):
        self.report = report
        super().__init__(report.summary())


RULES = [
    {
        "id": "prompt-inject-ignore",
        "severity": "high",
        "pattern": re.compile(
            r"\b(ignore|disregard|forget|override)\s+(all\s+)?"
            r"(previous|prior|earlier|your|these)\s+"
            r"(instructions?|prompts?|rules?|directives?)\b",
            re.IGNORECASE,
        ),
        "message": "instructs the agent to ignore prior instructions",
    },
    {
        "id": "prompt-inject-reveal",
        "severity": "high",
        "pattern": re.compile(
            r"\b(reveal|disclose|leak|dump|print|show)\b.{0,40}"
            r"\b(system prompt|system instructions?|your instructions?|"
            r"initial prompt)\b",
            re.IGNORECASE,
        ),
        "message": "asks to reveal system instructions",
    },
    {
        "id": "prompt-inject-secrecy",
        "severity": "high",
        "pattern": re.compile(
            r"\b(do not|don't|never)\s+(tell|inform|mention|reveal|disclose)\s+"
            r"(it\s+)?to\s+the user\b",
            re.IGNORECASE,
        ),
        "message": "instructs hiding behavior from the user",
    },
    {
        "id": "prompt-inject-bypass",
        "severity": "high",
        "pattern": re.compile(
            r"\b(bypass|circumvent|disable)\b.{0,30}"
            r"\b(safety|guardrails?|policies|restrictions?|filters?)\b",
            re.IGNORECASE,
        ),
        "message": "instructs bypassing safety controls",
    },
    {
        "id": "exfil-ssh-key",
        "severity": "high",
        "pattern": re.compile(
            r"(\.ssh/id_(rsa|ed25519|dsa)|~/\.ssh/)",
            re.IGNORECASE,
        ),
        "message": "references SSH private key material",
    },
    {
        "id": "exfil-post-data",
        "severity": "high",
        "pattern": re.compile(
            r"\b(curl|wget)\b.{0,100}"
            r"(--data(-binary|--urlencode)?|--post-data|"
            r"(?<![\w-])-d[ =])",
            re.IGNORECASE,
        ),
        "message": "sends data to a URL via curl/wget",
    },
    {
        "id": "exfil-http-post",
        "severity": "high",
        "pattern": re.compile(
            r"\brequests\.(post|put|patch)\s*\(",
            re.IGNORECASE,
        ),
        "message": "sends HTTP request with a body via requests",
    },
    {
        "id": "rce-pipe-shell",
        "severity": "high",
        "pattern": re.compile(
            r"\b(curl|wget)\b.{0,150}\|\s*(ba)?sh\b",
            re.IGNORECASE,
        ),
        "message": "pipes a download into a shell",
    },
    {
        "id": "rce-eval-net",
        "severity": "high",
        "pattern": re.compile(r"\b(eval|exec)\s*\(", re.IGNORECASE),
        "also": re.compile(
            r"\b(requests\.get|urlopen|http\.get|\bfetch\s*\()",
            re.IGNORECASE,
        ),
        "message": "evaluates downloaded code",
    },
    {
        "id": "rce-os-system",
        "severity": "high",
        "pattern": re.compile(r"\bos\.system\s*\(", re.IGNORECASE),
        "message": "calls os.system",
    },
    {
        "id": "rce-shell-true",
        "severity": "high",
        "pattern": re.compile(r"shell\s*=\s*True", re.IGNORECASE),
        "message": "subprocess with shell=True",
    },
    {
        "id": "destruct-rmrf-root",
        "severity": "high",
        "pattern": re.compile(
            r"\brm\s+-[a-z]*r[a-z]*f\s+(\/\s|~|\$HOME)",
            re.IGNORECASE,
        ),
        "message": "rm -rf against / ~ or $HOME",
    },
    {
        "id": "destruct-forkbomb",
        "severity": "high",
        "pattern": re.compile(
            r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}",
            re.IGNORECASE,
        ),
        "message": "fork bomb",
    },
    {
        "id": "prompt-role-override",
        "severity": "medium",
        "pattern": re.compile(r"\byou are now\b", re.IGNORECASE),
        "message": "attempts to reassign the agent role",
    },
    {
        "id": "rce-eval-plain",
        "severity": "medium",
        "pattern": re.compile(r"\b(eval|exec)\s*\(", re.IGNORECASE),
        "message": "dynamic code evaluation",
    },
    {
        "id": "net-download",
        "severity": "medium",
        "pattern": re.compile(r"\b(curl|wget)\s+https?://", re.IGNORECASE),
        "message": "downloads remote content",
    },
    {
        "id": "secret-env-read",
        "severity": "medium",
        "pattern": re.compile(
            r"""os\.environ(\[|\.get\()\s*["'][A-Z_]*(KEY|TOKEN|SECRET|PASSWORD)[A-Z_]*["']""",
            re.IGNORECASE,
        ),
        "message": "reads a secret from the environment",
    },
    {
        "id": "secret-env-read-js",
        "severity": "medium",
        "pattern": re.compile(
            r"process\.env\.[A-Z_]*(KEY|TOKEN|SECRET|PASSWORD)[A-Z_]*",
            re.IGNORECASE,
        ),
        "message": "reads a secret from the environment",
    },
    {
        "id": "obfusc-b64",
        "severity": "medium",
        "pattern": re.compile(r"[A-Za-z0-9+/]{200,}={0,2}"),
        "message": "long base64 blob",
    },
    {
        "id": "destruct-rmrf-plain",
        "severity": "medium",
        "pattern": re.compile(r"\brm\s+-[a-z]*r[a-z]*f\b", re.IGNORECASE),
        "message": "recursive force delete",
    },
]

_RANK = {"high": 0, "medium": 1, "low": 2}


def _scan_one_file(p: Path, rel: str) -> list[Finding]:
    if p.is_symlink():
        return [Finding("low", "symlink-skipped", rel, None, "symlink skipped")]
    try:
        st = p.stat()
    except OSError:
        return []
    if st.st_size > MAX_FILE_BYTES:
        return [
            Finding("low", "oversize-skipped", rel, None, "file over 1 MiB skipped")
        ]
    out: list[Finding] = []
    if st.st_mode & 0o111:
        out.append(Finding("low", "executable-bit", rel, None, "executable bit set"))
    try:
        data = p.read_bytes()
    except OSError:
        return out
    if b"\x00" in data[:4096]:
        out.append(Finding("low", "binary-skipped", rel, None, "binary file skipped"))
        return out
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    for rule in RULES:
        pat = rule["pattern"]
        also = rule.get("also")
        for idx, line in enumerate(lines, start=1):
            if pat.search(line) and (also is None or also.search(line)):
                out.append(
                    Finding(rule["severity"], rule["id"], rel, idx, rule["message"])
                )
                break
    for idx, line in enumerate(lines, start=1):
        if len(line) > MAX_LINE_LEN:
            out.append(Finding("medium", "longline", rel, idx, "line over 5000 chars"))
    return out


def scan_bundle(bundle_dir, manifest) -> ScanReport:
    base = Path(bundle_dir)
    raw = manifest.get("files") if isinstance(manifest, dict) else []
    names: list[str] = []
    listed: set[str] = set()
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and item:
                posix = Path(item).as_posix()
                names.append(item)
                listed.add(posix)
    findings: list[Finding] = []
    for item in names:
        rel = Path(item).as_posix()
        findings.extend(_scan_one_file(base / item, rel))
    if base.is_dir():
        for p in base.rglob("*"):
            if p.is_symlink():
                continue
            if not p.is_file():
                continue
            try:
                rel = p.relative_to(base).as_posix()
            except ValueError:
                continue
            if rel not in listed:
                findings.append(
                    Finding(
                        "medium",
                        "unlisted-file",
                        rel,
                        None,
                        "file not listed in manifest",
                    )
                )
    findings.sort(
        key=lambda f: (_RANK.get(f.severity, 99), f.file, f.line or 0, f.rule_id)
    )
    return ScanReport(findings=findings[:MAX_FINDINGS])
