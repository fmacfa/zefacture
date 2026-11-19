"""Local preview of bank statements. Only rows confirmed by the user enter the ledger."""

import os
import re
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

from core.common import BusinessError
from .bank_statements import bank_date, header

MAX_PAGES = 15
MAX_LINES = 100
MAX_TEXT = 120_000
DATE = re.compile(
    r"^\s*(\d{4}-\d{2}-\d{2}|\d{1,2}[./-]\d{1,2}(?:[./-](?:\d{4}|\d{2}))?)(?=\s)"
)
AMOUNT = re.compile(
    r"(?<![\w.,])([+−–-]?\s*(?:\d{1,3}(?:[ .,'’\u00a0\u202f]\d{3})+|\d+)[.,]\d{2}(?:-)?)(?![\w.,])"
)


def bank_command(args, deadline):
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise BusinessError(
            "La lecture du PDF a dépassé le délai. Importez un relevé plus court."
        )
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            timeout=min(remaining, 30),
            env={**os.environ, "LC_ALL": "C", "OMP_THREAD_LIMIT": "1"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BusinessError(
            "Lecture du relevé impossible. Vérifiez les outils PDF et OCR de l’instance ou importez un relevé plus court."
        ) from exc
    if result.returncode:
        raise BusinessError(
            "Impossible de lire ce PDF. Vérifiez que le fichier est lisible et déverrouillé."
        )
    return result.stdout.decode("utf-8", errors="replace")


def proposed_rows(text, page):
    debit_col = credit_col = -1
    for line in text.splitlines():
        lowered = line.lower().replace("é", "e")
        if "debit" in lowered and "credit" in lowered and not DATE.match(line):
            debit_col, credit_col = lowered.find("debit"), lowered.find("credit")
            continue
        d = DATE.match(line)
        if not d:
            continue
        plain = header(line[d.end() :])
        if re.match(
            r"(?:nouveau_solde|ancien_solde|solde|balance|totaux|total_des|report_de_solde)(?:_|$)",
            plain,
        ):
            continue
        tail = line[d.end() :]
        second = DATE.match(tail)
        offset = d.end() + (second.end() if second else 0)
        amounts = list(AMOUNT.finditer(line, offset))
        if not amounts:
            continue
        first = amounts[0]
        label = line[offset : first.start()].strip(" \t|")
        if not label:
            continue
        try:
            day = bank_date(d[1])
        except BusinessError:
            day = ""
        value = first[1].strip()
        direction = ""
        if value.startswith(("-", "−", "–")) or value.endswith("-"):
            direction = "outgoing"
        elif value.startswith("+"):
            direction = "incoming"
        elif debit_col >= 0 and credit_col >= 0 and len(amounts) == 1:
            position = first.start() + len(first[1]) - len(first[1].lstrip())
            direction = (
                "outgoing"
                if abs(position - debit_col) < abs(position - credit_col)
                else "incoming"
            )
        amount = re.sub(r"\D", "", value)
        # Multiple amounts might be a movement and a running balance. Ask for the amount.
        amount = (
            f"{int(amount[:-2])}.{amount[-2:]}"
            if len(amounts) == 1 and 2 < len(amount) <= 12
            else ""
        )
        yield {
            "page": page,
            "original": line.strip(),
            "date": day,
            "label": label,
            "amount": amount,
            "direction": direction,
        }


def preview(raw):
    if not raw.startswith(b"%PDF-"):
        raise BusinessError("Le fichier choisi n’est pas un PDF lisible.")
    deadline = monotonic() + 50
    with TemporaryDirectory(prefix="zf-releve-") as folder:
        root = Path(folder)
        pdf = root / "statement.pdf"
        pdf.write_bytes(raw)
        info = bank_command(["pdfinfo", str(pdf)], deadline)
        pages = re.search(r"^Pages:\s+(\d+)", info, re.M)
        if not pages or not 1 <= int(pages[1]) <= MAX_PAGES:
            raise BusinessError("Importez au maximum 15 pages de relevé PDF à la fois.")
        text_path = root / "layout.txt"
        bank_command(
            ["pdftotext", "-layout", "-enc", "UTF-8", str(pdf), str(text_path)],
            deadline,
        )
        if text_path.stat().st_size > MAX_TEXT * 4:
            raise BusinessError("Le relevé contient trop de texte. Scindez le PDF.")
        pages_text = text_path.read_text().split("\f")
        rows, source = [], []
        for number in range(1, int(pages[1]) + 1):
            page_text = pages_text[number - 1] if number <= len(pages_text) else ""
            found = list(proposed_rows(page_text, number))
            if not found:
                image = root / "scan"
                bank_command(
                    [
                        "pdftoppm",
                        "-f",
                        str(number),
                        "-l",
                        str(number),
                        "-singlefile",
                        "-scale-to",
                        "2000",
                        "-gray",
                        "-png",
                        str(pdf),
                        str(image),
                    ],
                    deadline,
                )
                ocr = bank_command(
                    [
                        "tesseract",
                        str(image.with_suffix(".png")),
                        "stdout",
                        "-l",
                        "fra+eng",
                        "--psm",
                        "6",
                    ],
                    deadline,
                )
                if ocr.strip():
                    found = list(proposed_rows(ocr, number))
                    if found or len(ocr.strip()) > len(page_text.strip()):
                        page_text = ocr
            rows.extend(found)
            source.append(f"Page {number}\n{page_text.strip()}")
        transcript = "\n\n".join(source)
        if len(rows) > MAX_LINES or len(transcript) > MAX_TEXT:
            raise BusinessError(
                "Le PDF dépasse la capacité de relecture. Scindez le relevé en documents plus courts."
            )
        return rows, transcript
