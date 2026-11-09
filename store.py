"""On-disk storage for reviews. No HTTP in here.

Layout under data_dir:
    default.map                     the default map (seeded from the bundled copy if missing)
    reviews/<id>/review.json        answers, annotations, PDF position
    reviews/<id>/map.map            this review's own map
    reviews/<id>/paper.pdf          the paper (optional)
"""
import io
import json
import os
import re
import shutil
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

import mapparser

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
SAVABLE = ("title", "values", "paperFile", "annotations", "pdfState")
ZIP_MEMBERS = {"review.json", "map.map", "paper.pdf"}


def _atomic_write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o644)  # mkstemp makes 0600; keep backups readable by the host user
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class Store:
    MAX_PDF = 100 * 1024 * 1024
    MAX_MAP = 1024 * 1024
    MAX_JSON = 20 * 1024 * 1024

    def __init__(self, data_dir, default_map_src):
        self.data = Path(data_dir)
        self.reviews = self.data / "reviews"
        self.reviews.mkdir(parents=True, exist_ok=True)
        self.default_map = self.data / "default.map"
        if not self.default_map.exists():
            shutil.copyfile(default_map_src, self.default_map)

    # ---------- helpers ----------
    def _dir(self, rid) -> Path:
        if not isinstance(rid, str) or not ID_RE.match(rid):
            raise ValueError("invalid review id")
        d = self.reviews / rid
        if not d.is_dir():
            raise FileNotFoundError(rid)
        return d

    def _write_json(self, d: Path, review: dict) -> None:
        _atomic_write(d / "review.json", json.dumps(review, indent=2, ensure_ascii=False).encode())

    def _new_dir(self, wanted=None):
        rid = wanted if isinstance(wanted, str) and ID_RE.match(wanted) and not (self.reviews / wanted).exists() else uuid.uuid4().hex[:12]
        d = self.reviews / rid
        d.mkdir()
        return rid, d

    def _check_map(self, text: str) -> None:
        if len(text.encode()) > self.MAX_MAP:
            raise ValueError("map file too large")
        mapparser.parse(text)

    def _check_pdf(self, data: bytes) -> None:
        if len(data) > self.MAX_PDF:
            raise ValueError("PDF too large")
        if not data.startswith(b"%PDF-"):
            raise ValueError("not a PDF file")

    # ---------- reviews ----------
    def default_map_text(self) -> str:
        return self.default_map.read_text(encoding="utf-8")

    def create(self, title: str = "") -> dict:
        rid, d = self._new_dir()
        now = time.time()
        review = {"id": rid, "title": title or "Untitled review", "created": now, "updated": now,
                  "paperFile": None, "values": {}, "annotations": []}
        shutil.copyfile(self.default_map, d / "map.map")
        self._write_json(d, review)
        return review

    def get(self, rid) -> dict:
        d = self._dir(rid)
        review = json.loads((d / "review.json").read_text(encoding="utf-8"))
        review["id"] = rid
        return review

    def save(self, rid, fields: dict) -> dict:
        review = self.get(rid)
        for k in SAVABLE:
            if k in fields:
                review[k] = fields[k]
        patch = fields.get("valuesPatch")  # merge only the answers that changed (null deletes one)
        if patch is not None:
            if not isinstance(patch, dict):
                raise ValueError("valuesPatch must be an object")
            values = review.get("values") if isinstance(review.get("values"), dict) else {}
            for k, v in patch.items():
                if v is None:
                    values.pop(k, None)
                else:
                    values[k] = v
            review["values"] = values
        review["updated"] = time.time()
        self._write_json(self._dir(rid), review)
        return review

    def delete(self, rid) -> None:
        shutil.rmtree(self._dir(rid))

    def list(self) -> list:
        out = []
        for d in self.reviews.iterdir():
            try:
                r = json.loads((d / "review.json").read_text(encoding="utf-8"))
                out.append({"id": d.name, "title": r.get("title"), "created": r.get("created"),
                            "updated": r.get("updated"), "paperFile": r.get("paperFile")})
            except Exception:
                continue
        return sorted(out, key=lambda r: r.get("updated") or 0, reverse=True)

    # ---------- map ----------
    def map_text(self, rid) -> str:
        return (self._dir(rid) / "map.map").read_text(encoding="utf-8")

    def set_map(self, rid, text: str) -> None:
        d = self._dir(rid)
        self._check_map(text)
        _atomic_write(d / "map.map", text.encode())

    def reset_map(self, rid) -> None:
        d = self._dir(rid)
        _atomic_write(d / "map.map", self.default_map.read_bytes())

    # ---------- pdf ----------
    def pdf_path(self, rid):
        p = self._dir(rid) / "paper.pdf"
        return p if p.is_file() else None

    def set_pdf(self, rid, data: bytes) -> None:
        d = self._dir(rid)
        self._check_pdf(data)
        _atomic_write(d / "paper.pdf", data)

    def delete_pdf(self, rid) -> None:
        p = self._dir(rid) / "paper.pdf"
        if p.exists():
            p.unlink()

    # ---------- backup / restore ----------
    def export_zip(self, rid) -> bytes:
        d = self._dir(rid)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name in ("review.json", "map.map", "paper.pdf"):
                if (d / name).is_file():
                    z.write(d / name, name)
        return buf.getvalue()

    def import_zip(self, data: bytes) -> dict:
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise ValueError("not a valid zip file")
        limits = {"review.json": self.MAX_JSON, "map.map": self.MAX_MAP, "paper.pdf": self.MAX_PDF}
        names = [i.filename for i in z.infolist()]
        if "review.json" not in names:
            raise ValueError("backup has no review.json")
        for info in z.infolist():
            if info.filename not in ZIP_MEMBERS:
                raise ValueError(f"unexpected file in backup: {info.filename!r}")
            if info.file_size > limits[info.filename]:
                raise ValueError(f"{info.filename} is too large")
        if len(names) != len(set(names)):
            raise ValueError("duplicate entries in backup")

        def read(name):  # bounded read, trusting the real size not the header
            with z.open(name) as f:
                blob = f.read(limits[name] + 1)
            if len(blob) > limits[name]:
                raise ValueError(f"{name} is too large")
            return blob

        review = json.loads(read("review.json").decode("utf-8"))
        if not isinstance(review, dict) or not isinstance(review.get("values"), dict):
            raise ValueError("review.json is not a review")
        map_text = read("map.map").decode("utf-8") if "map.map" in names else self.default_map_text()
        self._check_map(map_text)
        pdf = read("paper.pdf") if "paper.pdf" in names else None
        if pdf is not None:
            self._check_pdf(pdf)
        return self._materialise(review, map_text, pdf, drop_paper_name=pdf is None)

    def import_review_json(self, obj: dict) -> dict:
        if not isinstance(obj, dict) or not isinstance(obj.get("values"), dict):
            raise ValueError("not a review file: missing 'values'")
        return self._materialise(obj, self.default_map_text(), None)

    def _materialise(self, review: dict, map_text: str, pdf, drop_paper_name=False) -> dict:
        rid, d = self._new_dir(review.get("id"))
        try:
            now = time.time()
            out = {"id": rid, "title": review.get("title") or "Imported review",
                   "created": review.get("created") or now, "updated": now,
                   "paperFile": None if drop_paper_name else review.get("paperFile"),
                   "values": review["values"], "annotations": review.get("annotations") or [],
                   "pdfState": review.get("pdfState")}
            _atomic_write(d / "map.map", map_text.encode())
            if pdf is not None:
                _atomic_write(d / "paper.pdf", pdf)
            self._write_json(d, out)
            return out
        except BaseException:
            shutil.rmtree(d, ignore_errors=True)
            raise

    # ---------- migration from the old single-file layout ----------
    def migrate_legacy(self, legacy_dir) -> list:
        legacy = Path(legacy_dir)
        migrated = []
        if not legacy.is_dir():
            return migrated
        for f in sorted(legacy.glob("*.json")):
            try:
                r = json.loads(f.read_text(encoding="utf-8"))
                rid = r.get("id") if isinstance(r, dict) else None
                if not isinstance(rid, str) or not ID_RE.match(rid) or (self.reviews / rid).exists():
                    continue
                if not isinstance(r.get("values"), dict):
                    continue
                self._materialise(r, self.default_map_text(), None)
                migrated.append(rid)
            except Exception:
                continue
        return migrated
