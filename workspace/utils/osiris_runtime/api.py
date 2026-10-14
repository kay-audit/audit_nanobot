"""One registered-service backend for skills, operator CLI and native Tool."""
from __future__ import annotations

from . import client, json_transport, lifecycle
from .protocol import identity, json_bytes, operation
from .registry import services


class OsirisRuntime:
    def __init__(self, registry=None, sdk=None):
        self.registry = services if registry is None else registry
        self.sdk = sdk

    def profile(self, service):
        return self.registry.get(service)

    def start(self, service):
        return lifecycle.ensure_ready(self.profile(service), self.sdk)

    def status(self, service):
        result = lifecycle.service_status(self.profile(service), self.sdk)
        return dict(result, service_name=service)

    def stop(self, service):
        return lifecycle.stop_service(self.profile(service), self.sdk)

    def invoke(self, service, operation_name, payload, *, session_id, consumer_id="python",
               query="", timeout_sec=1800, artifacts=None, file_root=None):
        profile = self.profile(service)
        operation(profile, operation_name)
        identity(consumer_id, "consumer ID")
        json_bytes(payload, profile.max_payload_bytes)
        json_transport.submit(profile, session_id, operation_name, payload,
                              consumer_id=consumer_id, query=query, timeout_sec=timeout_sec,
                              artifacts=artifacts, file_root=file_root, validate_only=True)
        self.start(service)
        rid = json_transport.submit(profile, session_id, operation_name, payload,
                                    consumer_id=consumer_id, query=query, timeout_sec=timeout_sec,
                                    artifacts=artifacts, file_root=file_root)
        return json_transport.wait(profile, session_id, rid, operation_name,
                                   consumer_id=consumer_id, timeout_sec=timeout_sec)

    def invoke_legacy(self, service, request_type, query, payload, *, session_id, timeout_sec=1800):
        """Trusted in-process compatibility API; never exposed to LLM payloads."""
        profile = self.profile(service)
        operation(profile, request_type)
        self.start(service)
        rid = client.submit_request(profile, session_id, request_type, query, payload, timeout_sec)
        return client.wait_result(profile, session_id, rid, request_type, timeout_sec, auto_recover=True)
