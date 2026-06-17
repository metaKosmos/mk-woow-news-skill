"""state_manager.py — estado durável da WooW News (queue + por-edição) + espelho Firebase.

Store abstrato: LocalStore (filesystem, p/ teste) e GcsStore (produção, bucket privado).
queue.json é derivado dos states por edição; o painel lê o espelho no Firebase.
"""
import json
import os
from datetime import datetime, timezone, timedelta

BRT = timezone(timedelta(hours=-3))
STAGE_RANK = {"empty": 0, "researched": 1, "generated": 2, "ready": 3, "sent": 4}


def _now_brt():
    return datetime.now(BRT).isoformat(timespec="seconds")


class LocalStore:
    """Store em filesystem (testes / dev offline)."""
    def __init__(self, root):
        self.root = str(root)
        os.makedirs(os.path.join(self.root, "editions"), exist_ok=True)

    def read(self, key):
        path = os.path.join(self.root, key)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return f.read()

    def write(self, key, data):
        path = os.path.join(self.root, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)

    def list_editions(self):
        d = os.path.join(self.root, "editions")
        if not os.path.isdir(d):
            return []
        return [f[:-len(".state.json")] for f in os.listdir(d) if f.endswith(".state.json")]


class GcsStore:
    """Store em bucket GCS privado (produção). Lazy-importa a lib só quando usado."""
    def __init__(self, bucket=None):
        from google.cloud import storage
        self.bucket_name = bucket or os.environ.get("STATE_BUCKET", "mk-woow-news-state")
        self._bucket = storage.Client().bucket(self.bucket_name)

    def read(self, key):
        blob = self._bucket.blob(key)
        if not blob.exists():
            return None
        return blob.download_as_text()

    def write(self, key, data):
        self._bucket.blob(key).upload_from_string(data, content_type="application/json")

    def list_editions(self):
        out = []
        for blob in self._bucket.list_blobs(prefix="editions/"):
            name = blob.name.split("/")[-1]
            if name.endswith(".state.json"):
                out.append(name[:-len(".state.json")])
        return out


class StateManager:
    def __init__(self, store):
        self.store = store

    # -- por edição --
    def get_state(self, edition):
        raw = self.store.read(f"editions/{edition}.state.json")
        return json.loads(raw) if raw else {"edition": edition, "stage": "empty"}

    def upsert_edition(self, edition, patch):
        """Merge raso do patch no state da edição (não rebaixa stage). Atualiza queue."""
        st = self.get_state(edition)
        new_stage = patch.get("stage")
        for k, v in patch.items():
            if k == "stage":
                continue
            st[k] = v
        if new_stage is not None and STAGE_RANK.get(new_stage, -1) >= STAGE_RANK.get(st.get("stage", "empty"), 0):
            st["stage"] = new_stage
            st.setdefault("timestamps", {})[f"{new_stage}_at"] = _now_brt()
        st["edition"] = edition
        self.store.write(f"editions/{edition}.state.json", json.dumps(st, ensure_ascii=False, indent=2))
        self._rebuild_queue()
        return st

    # -- queue derivado --
    def _rebuild_queue(self):
        rows = []
        for ed in self.store.list_editions():
            st = self.get_state(ed)
            rows.append({
                "edition": ed,
                "date": st.get("date", ""),
                "stage": st.get("stage", "empty"),
                "subject": st.get("subject", ""),
                "image_ready": st.get("image_ready", False),
                "open_rate": (st.get("metrics") or {}).get("open_rate"),
            })
        rows.sort(key=lambda r: r["edition"])
        queue = {"updated_at": _now_brt(), "editions": rows}
        self.store.write("queue.json", json.dumps(queue, ensure_ascii=False, indent=2))
        return queue

    def get_queue(self):
        raw = self.store.read("queue.json")
        return json.loads(raw) if raw else self._rebuild_queue()

    def coverage(self):
        cov = {k: 0 for k in STAGE_RANK}
        for e in self.get_queue()["editions"]:
            stage = e["stage"] if e["stage"] in STAGE_RANK else "empty"
            cov[stage] += 1
        return cov

    def reset_edition(self, edition):
        """Zera o state da edição p/ 'empty' e reconstrói a queue."""
        self.store.write(f"editions/{edition}.state.json",
                         json.dumps({"edition": edition, "stage": "empty"}))
        return self._rebuild_queue()

    # -- espelho Firebase (produção; devolve erro estruturado se firebase_admin ausente) --
    def sync_to_firebase(self):
        try:
            import firebase_admin
            from firebase_admin import db
            try:
                firebase_admin.get_app()
            except ValueError:
                firebase_admin.initialize_app(options={
                    "databaseURL": os.environ.get("FIREBASE_DB_URL", "")})
            payload = {"queue": self.get_queue(),
                       "editions": {e: self.get_state(e) for e in self.store.list_editions()}}
            db.reference("/woow_news").set(payload)
            return {"synced": len(payload["editions"]), "at": _now_brt()}
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}
