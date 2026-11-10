"""On-disk storage for reviews and map templates. No HTTP in here.

Layout under data_dir:
    maps/<id>.map                   your own map templates (shown next to the bundled ones; same id overrides)
    reviews/<id>/review.json        answers, annotations, PDF position, which template it came from
    reviews/<id>/map.map            this review's own copy of its map
    reviews/<id>/paper.pdf          the paper (optional)
The bundled templates live in a read-only folder next to the code (bundled_dir).
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
KIND_ORDER = {"conference": 0, "journal": 1, "general": 2}


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


def _fields(node, inherited_private=False):
    """Yield (field_node, is_private) for every input widget under node."""
    private = inherited_private or bool(node["attrs"].get("private"))
    if node["type"] in mapparser.FIELD_TYPES:
        yield node, private
    if node["type"] != "Choice":
        for child in node["children"]:
            yield from _fields(child, private)


def _is_filled(value) -> bool:
    return value is not None and str(value).strip() != ""


class Store:
    MAX_PDF = 100 * 1024 * 1024
    MAX_MAP = 1024 * 1024
    MAX_JSON = 20 * 1024 * 1024
    DEFAULT_TEMPLATE = "blank"          # used when a review is created without choosing one
    LEGACY_TEMPLATE = "aamas-2027"      # what old single-file reviews and old exports were written against

    def __init__(self, data_dir, bundled_dir):
        self.data = Path(data_dir)
        self.bundled = Path(bundled_dir)
        self.reviews = self.data / "reviews"
        self.custom = self.data / "maps"
        self.reviews.mkdir(parents=True, exist_ok=True)
        self.custom.mkdir(parents=True, exist_ok=True)
        self._meta_cache = {}

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

    @staticmethod
    def _check_id(tid) -> str:
        if not isinstance(tid, str) or not ID_RE.match(tid):
            raise ValueError("invalid template id")
        return tid

    # ---------- templates ----------
    def _template_path(self, tid):
        """(path, is_custom) of the template file; your own file wins over a bundled one with the same id."""
        self._check_id(tid)
        c = self.custom / f"{tid}.map"
        if c.is_file():
            return c, True
        b = self.bundled / f"{tid}.map"
        if b.is_file():
            return b, False
        raise FileNotFoundError(tid)

    def template_text(self, tid) -> str:
        return self._template_path(tid)[0].read_text(encoding="utf-8")

    def _summary(self, path: Path) -> dict:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
        if key not in self._meta_cache:
            try:
                tree = mapparser.parse(path.read_text(encoding="utf-8"))
                a = tree["attrs"]
                fields = list(_fields(tree))
                kind = a.get("kind") if a.get("kind") in KIND_ORDER else "general"
                meta = {"label": str(a.get("label") or path.stem), "venue": str(a.get("venue") or ""), "kind": kind,
                        "year": a.get("year") if isinstance(a.get("year"), int) else None,
                        "fidelity": str(a.get("fidelity") or "generic"), "description": str(a.get("description") or ""),
                        "source": str(a.get("source") or ""),
                        "fields": sum(1 for _, private in fields if not private),
                        "required": sum(1 for n, _ in fields if n["attrs"].get("required"))}
            except (mapparser.MapError, UnicodeDecodeError) as e:
                meta = {"label": path.stem, "venue": "", "kind": "general", "year": None, "fidelity": "generic",
                        "description": "", "source": "", "fields": 0, "required": 0, "error": str(e)}
            self._meta_cache[key] = meta
        return dict(self._meta_cache[key])

    def templates(self) -> list:
        ids = {p.stem for p in self.bundled.glob("*.map")} | {p.stem for p in self.custom.glob("*.map")}
        out = []
        for tid in ids:
            if not ID_RE.match(tid):
                continue
            path, custom = self._template_path(tid)
            meta = self._summary(path)
            meta.update(id=tid, custom=custom, overrides=custom and (self.bundled / f"{tid}.map").is_file())
            out.append(meta)
        return sorted(out, key=lambda t: (t["custom"] is False, KIND_ORDER[t["kind"]], t["label"].lower()))

    def set_custom_template(self, tid, text: str) -> None:
        self._check_id(tid)
        self._check_map(text)
        _atomic_write(self.custom / f"{tid}.map", text.encode())

    def delete_custom_template(self, tid) -> None:
        self._check_id(tid)
        p = self.custom / f"{tid}.map"
        if not p.is_file():
            raise FileNotFoundError(tid)
        p.unlink()

    # ---------- reviews ----------
    def authorize(self, rid, owner) -> None:
        """Raise FileNotFoundError unless review `rid` belongs to `owner` (someone else's review looks like a missing one)."""
        try:
            d = self._dir(rid)
        except ValueError:
            raise FileNotFoundError(rid)
        try:
            actual = json.loads((d / "review.json").read_text(encoding="utf-8")).get("owner")
        except (OSError, ValueError):
            actual = None
        if owner is None or actual != owner:
            raise FileNotFoundError(rid)

    def claim_unowned(self, owner) -> int:
        """Give every review that has no owner (made before accounts existed) to `owner`."""
        n = 0
        for d in self.reviews.iterdir():
            try:
                r = json.loads((d / "review.json").read_text(encoding="utf-8"))
                if isinstance(r, dict) and not r.get("owner"):
                    r["owner"] = owner
                    self._write_json(d, r)
                    n += 1
            except (OSError, ValueError):
                continue
        return n

    def create(self, title: str = "", template=None, owner=None) -> dict:
        tid = template or self.DEFAULT_TEMPLATE
        text = self.template_text(tid)  # resolve (and validate) before any folder exists
        self._check_map(text)
        rid, d = self._new_dir()
        try:
            now = time.time()
            review = {"id": rid, "title": title or "Untitled review", "template": tid, "owner": owner, "created": now, "updated": now,
                      "paperFile": None, "values": {}, "annotations": []}
            _atomic_write(d / "map.map", text.encode())
            self._write_json(d, review)
            return review
        except BaseException:
            shutil.rmtree(d, ignore_errors=True)
            raise

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

    def _progress(self, d: Path, review: dict):
        try:
            tree = mapparser.parse((d / "map.map").read_text(encoding="utf-8"))
        except Exception:
            return None, None
        values = review.get("values") if isinstance(review.get("values"), dict) else {}
        required = [n["attrs"]["id"] for n, _ in _fields(tree) if n["attrs"].get("required")]
        filled = sum(1 for i in required if _is_filled(values.get(i)))
        return {"filled": filled, "required": len(required)}, str(tree["attrs"].get("label") or "")

    def list(self, owner=None) -> list:
        out = []
        for d in self.reviews.iterdir():
            try:
                r = json.loads((d / "review.json").read_text(encoding="utf-8"))
                if (r.get("owner") or None) != owner:
                    continue
                progress, label = self._progress(d, r)
                out.append({"id": d.name, "title": r.get("title"), "created": r.get("created"), "updated": r.get("updated"),
                            "paperFile": r.get("paperFile"), "template": r.get("template"), "mapLabel": label,
                            "progress": progress, "hasPdf": (d / "paper.pdf").is_file()})
            except Exception:
                continue
        return sorted(out, key=lambda r: r.get("updated") or 0, reverse=True)

    # ---------- map of one review ----------
    def map_text(self, rid) -> str:
        return (self._dir(rid) / "map.map").read_text(encoding="utf-8")

    def set_map(self, rid, text: str) -> None:
        d = self._dir(rid)
        self._check_map(text)
        _atomic_write(d / "map.map", text.encode())

    def set_template(self, rid, tid) -> None:
        """Replace the review's map with a template's. Answers are kept (fields are matched by id)."""
        d = self._dir(rid)
        text = self.template_text(tid)
        self._check_map(text)
        review = self.get(rid)
        _atomic_write(d / "map.map", text.encode())
        review["template"] = tid
        review["updated"] = time.time()
        self._write_json(d, review)

    def reset_map(self, rid) -> None:
        """Back to the template the review was created from (old reviews fall back to the legacy template)."""
        tid = self.get(rid).get("template")
        try:
            self.template_text(tid)
        except (ValueError, FileNotFoundError):
            tid = self.LEGACY_TEMPLATE
            try:
                self.template_text(tid)
            except FileNotFoundError:
                tid = self.DEFAULT_TEMPLATE
        self.set_template(rid, tid)

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

    def import_zip(self, data: bytes, owner=None) -> dict:
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
        map_text = read("map.map").decode("utf-8") if "map.map" in names else self._fallback_map(review)
        self._check_map(map_text)
        pdf = read("paper.pdf") if "paper.pdf" in names else None
        if pdf is not None:
            self._check_pdf(pdf)
        return self._materialise(review, map_text, pdf, drop_paper_name=pdf is None, owner=owner)

    def import_review_json(self, obj: dict, owner=None) -> dict:
        if not isinstance(obj, dict) or not isinstance(obj.get("values"), dict):
            raise ValueError("not a review file: missing 'values'")
        return self._materialise(obj, self._fallback_map(obj), None, owner=owner)

    def _fallback_map(self, review: dict) -> str:
        tid = review.get("template")
        try:
            return self.template_text(tid)
        except (ValueError, FileNotFoundError):
            return self.template_text(self.LEGACY_TEMPLATE)

    def _materialise(self, review: dict, map_text: str, pdf, drop_paper_name=False, owner=None) -> dict:
        rid, d = self._new_dir(review.get("id"))
        try:
            now = time.time()
            tid = review.get("template")
            out = {"id": rid, "title": review.get("title") or "Imported review",
                   "template": tid if isinstance(tid, str) and ID_RE.match(tid) else self.LEGACY_TEMPLATE,
                   "owner": owner,  # never taken from the file being imported
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
                r = dict(r, template=self.LEGACY_TEMPLATE)
                self._materialise(r, self.template_text(self.LEGACY_TEMPLATE), None)
                migrated.append(rid)
            except Exception:
                continue
        return migrated
