import json
import os
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone


CONFIGMAP_NAME = "mo-chaos-maintenance"


def _utc_now():
    return datetime.now(timezone.utc)


def _parse_utc(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class MaintenanceGate:
    def __init__(
        self,
        namespace,
        runner_id=None,
        command_runner=subprocess.run,
        now=None,
        poll_seconds=2,
        heartbeat_seconds=10,
        logger=None,
    ):
        self.namespace = namespace
        self.runner_id = runner_id or os.environ.get("GITHUB_RUN_ID") or str(uuid.uuid4())
        self._run = command_runner
        self._now = now or _utc_now
        self.poll_seconds = poll_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.logger = logger
        self.active_tasks = 0
        self._active_lock = threading.Lock()
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread = None

    def _log(self, message, *args):
        if self.logger:
            self.logger.info(message, *args)

    def _kubectl(self, args, **kwargs):
        return self._run(
            ["kubectl", "-n", self.namespace] + args,
            capture_output=True,
            text=True,
            **kwargs,
        )

    def _read_data(self):
        result = self._kubectl(["get", "configmap", CONFIGMAP_NAME, "-o", "json"])
        if result.returncode == 0:
            return json.loads(result.stdout or "{}").get("data", {})
        if "NotFound" in (result.stderr or "") or "not found" in (result.stderr or "").lower():
            return None
        raise RuntimeError("failed to read maintenance ConfigMap: {}".format(result.stderr.strip()))

    def _ensure_configmap(self):
        if self._read_data() is not None:
            return
        manifest = self._kubectl(
            ["create", "configmap", CONFIGMAP_NAME, "--dry-run=client", "-o", "json"],
            check=True,
        ).stdout
        self._run(
            ["kubectl", "-n", self.namespace, "apply", "-f", "-"],
            input=manifest,
            capture_output=True,
            text=True,
            check=True,
        )

    def _publish(self, **fields):
        self._ensure_configmap()
        patch = json.dumps({"data": {key: str(value) for key, value in fields.items()}})
        self._kubectl(
            ["patch", "configmap", CONFIGMAP_NAME, "--type=merge", "-p", patch],
            check=True,
        )

    def _request_is_active(self, data):
        if not data or data.get("requested") != "true":
            return False
        if data.get("protect") == "true":
            return True
        expires_at = _parse_utc(data.get("expires_at", ""))
        return expires_at is None or expires_at > self._now()

    def _state_fields(self, data=None):
        data = data if data is not None else (self._read_data() or {})
        with self._active_lock:
            active = self.active_tasks
        fields = {
            "runner_id": self.runner_id,
            "heartbeat_at": self._now().isoformat().replace("+00:00", "Z"),
            "active_task": active,
        }
        if self._request_is_active(data) and active == 0:
            fields.update(state="paused", ack_request_id=data.get("request_id", ""))
        elif active:
            fields["state"] = "active"
        else:
            fields["state"] = "running"
        return fields

    def _heartbeat_once(self):
        data = self._read_data() or {}
        self._publish(**self._state_fields(data))

    def _heartbeat_loop(self):
        while not self._heartbeat_stop.is_set():
            try:
                self._heartbeat_once()
            except Exception as exc:
                self._log("Maintenance heartbeat failed: %s", exc)
            self._heartbeat_stop.wait(self.heartbeat_seconds)

    def start(self):
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            return
        self._ensure_configmap()
        self._heartbeat_stop.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="chaos-maintenance-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def stop(self):
        self._heartbeat_stop.set()
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=max(1, self.heartbeat_seconds + 1))
        try:
            self._publish(
                runner_id=self.runner_id,
                heartbeat_at=self._now().isoformat().replace("+00:00", "Z"),
                state="stopped",
                active_task=0,
            )
        except Exception as exc:
            self._log("Failed to publish stopped maintenance state: %s", exc)

    def enter_task(self, stop_event):
        while not stop_event.is_set():
            data = self._read_data() or {}
            if not self._request_is_active(data):
                with self._active_lock:
                    self.active_tasks += 1
                return True
            self._publish(**self._state_fields(data))
            stop_event.wait(self.poll_seconds)
        return False

    def leave_task(self):
        with self._active_lock:
            if self.active_tasks <= 0:
                raise RuntimeError("maintenance active task counter underflow")
            self.active_tasks -= 1
        self._heartbeat_once()

    def wait_interval(self, seconds, stop_event):
        deadline = time.monotonic() + seconds
        while not stop_event.is_set() and time.monotonic() < deadline:
            data = self._read_data() or {}
            if self._request_is_active(data):
                self._publish(**self._state_fields(data))
                stop_event.wait(self.poll_seconds)
                continue
            remaining = deadline - time.monotonic()
            stop_event.wait(min(self.poll_seconds, max(0, remaining)))
        return not stop_event.is_set()
