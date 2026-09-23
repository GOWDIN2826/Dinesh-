"""
security_scan.py - diagram box "Security Scan" + decision "Virus Detected?"

YES -> raise INFECTED_FILE_DETECTED   |   NO -> continue to File Format Validation

Two layers:
  1. Built-in scanner (always on, no install needed)
       - EICAR standard test virus
       - Windows/Linux programs disguised as documents
       - PDF active content   (/JavaScript, /Launch, embedded files)
       - DOCX macros / embedded programs / zip-bombs
  2. ClamAV antivirus server (optional - set CLAMAV_ENABLED=true in .env)

Be honest about limits: the built-in scanner catches the common tricks but it is
NOT a full antivirus.  For real banking use, turn ClamAV on.
If ClamAV is enabled but unreachable we REJECT the file (fail closed).
"""
import re
import socket
import struct
import zipfile
from pathlib import Path

from app.config import settings
from schemas import ErrorCode, PipelineError

# The EICAR test string is split in two so that YOUR antivirus does not
# quarantine this source file.  Joined at run time it is the real test signature.
EICAR = (
    "X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + "EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
).encode()

# First bytes of programs (Windows .exe / Linux) - never valid in a job letter
EXECUTABLE_SIGNATURES = {
    b"MZ": "Windows executable disguised as a document",
    b"\x7fELF": "Linux executable disguised as a document",
}

# Risky names inside a PDF
PDF_ACTIVE_CONTENT = re.compile(
    rb"/(JavaScript|JS|Launch|EmbeddedFiles?|RichMedia)(?![A-Za-z0-9])"
)

# Risky files inside a DOCX (a DOCX is a zip archive)
DOCX_BAD_SUFFIXES = (".exe", ".dll", ".bat", ".cmd", ".js", ".vbs", ".ps1", ".scr", ".jar")
MAX_UNZIPPED_BYTES = 200 * 1024 * 1024   # 200 MB
MAX_COMPRESSION_RATIO = 200              # zip-bomb guard


def _builtin_findings(data: bytes, file_path: Path) -> list[str]:
    findings: list[str] = []

    if EICAR in data:
        findings.append("EICAR test virus signature")

    for signature, text in EXECUTABLE_SIGNATURES.items():
        if data.startswith(signature):
            findings.append(text)

    # PDF active content
    if b"%PDF-" in data[:1024]:
        found = {m.group(1).decode() for m in PDF_ACTIVE_CONTENT.finditer(data)}
        if found:
            findings.append("PDF contains active content: " + ", ".join(sorted(found)))

    # DOCX (zip) checks - never extracts anything to disk
    if zipfile.is_zipfile(file_path):
        try:
            with zipfile.ZipFile(file_path) as zf:
                infos = zf.infolist()
                total_unzipped = sum(i.file_size for i in infos)
                total_zipped = max(1, sum(i.compress_size for i in infos))
                if total_unzipped > MAX_UNZIPPED_BYTES:
                    findings.append("Archive expands to an unsafe size (zip bomb)")
                elif total_unzipped / total_zipped > MAX_COMPRESSION_RATIO:
                    findings.append("Suspicious compression ratio (zip bomb)")
                for info in infos:
                    name = info.filename.lower()
                    if name.endswith("vbaproject.bin"):
                        findings.append("Word document contains macros (VBA)")
                    elif "activex" in name:
                        findings.append("Word document contains ActiveX controls")
                    elif name.startswith("word/embeddings/"):
                        findings.append("Word document contains embedded objects")
                    elif name.endswith(DOCX_BAD_SUFFIXES):
                        findings.append(f"Archive contains a program file ({name})")
        except zipfile.BadZipFile:
            pass  # not a valid zip - File Format Validation will deal with it

    return findings


def _clamav_scan(data: bytes) -> tuple[bool, str]:
    """Send the file to a ClamAV server (clamd) using its INSTREAM command."""
    try:
        with socket.create_connection(
            (settings.clamav_host, settings.clamav_port), timeout=20
        ) as sock:
            sock.sendall(b"zINSTREAM\0")
            for i in range(0, len(data), 8192):
                chunk = data[i : i + 8192]
                sock.sendall(struct.pack(">I", len(chunk)) + chunk)
            sock.sendall(struct.pack(">I", 0))
            reply = sock.recv(4096).decode(errors="replace").strip("\0\r\n ")
    except OSError as exc:
        raise PipelineError(
            ErrorCode.INTERNAL_PROCESSING_ERROR,
            details="Antivirus service unavailable - file rejected for safety",
        ) from exc

    if reply.endswith("FOUND"):
        return False, reply
    if reply.endswith("OK"):
        return True, reply
    raise PipelineError(
        ErrorCode.INTERNAL_PROCESSING_ERROR,
        details=f"Unexpected antivirus response: {reply[:100]}",
    )


def run_security_scan(file_path: Path) -> dict:
    """
    Returns a small report (saved to the audit log) if the file is clean.
    Raises PipelineError(INFECTED_FILE_DETECTED) if a virus / risk is found.
    """
    data = file_path.read_bytes()
    engines = ["builtin"]

    findings = _builtin_findings(data, file_path)

    if settings.clamav_enabled:
        engines.append("clamav")
        clean, reply = _clamav_scan(data)
        if not clean:
            findings.append(f"ClamAV: {reply}")

    if findings:
        raise PipelineError(
            ErrorCode.INFECTED_FILE_DETECTED,
            details="Security scan failed: " + "; ".join(findings),
        )

    return {"engine": "+".join(engines), "clean": True}