"""A project route bound to a transcript session must actually deliver.

Regression from 2026-08-04: `bind_route(allow_unroutable_source=True)` was
added so an operator could route a project at a webhook session a human had
adopted as a working conversation. Binding worked, resolution worked — and then
every delivery was silently dropped by `_resolve_delivery_target` as an
"unsupported source". The controller reported `last_status: ok`, the route
resolved on demand, and the operator's session received nothing for hours.
"""

import pytest

from cron import scheduler


class _Target:
    def __init__(self, source, session_id="sess-1", project_id="p_1"):
        self.source = source
        self.session_id = session_id
        self.project_id = project_id


class TestTranscriptDeliverySources:
    def test_webhook_is_a_transcript_delivery_source(self):
        # The property under test, independent of the resolution plumbing:
        # a webhook session delivers into its transcript, like api_server.
        assert "webhook" in scheduler._TRANSCRIPT_DELIVERY_SOURCES
        assert "api_server" in scheduler._TRANSCRIPT_DELIVERY_SOURCES

    def test_desktop_and_webui_stay_on_the_local_platform_path(self):
        # They have their own adapter handling and must not be folded into the
        # transcript path, or they would lose it.
        assert "desktop" in scheduler._LOCAL_SESSION_PLATFORMS
        assert "webui" in scheduler._LOCAL_SESSION_PLATFORMS
        assert not (
            scheduler._LOCAL_SESSION_PLATFORMS & scheduler._TRANSCRIPT_DELIVERY_SOURCES
        )

    def test_a_messaging_source_is_not_a_transcript_source(self):
        # telegram/discord have live adapters and their own origin routing;
        # folding them in would let a project route re-target a chat thread.
        for source in ("telegram", "discord", "cron", "subagent", "tui"):
            assert source not in scheduler._TRANSCRIPT_DELIVERY_SOURCES

    def test_both_branches_read_the_set_not_a_literal(self):
        # Guards against either branch drifting back to `== "api_server"`,
        # which is what dropped webhook targets in the first place.
        import inspect

        resolve = inspect.getsource(scheduler._resolve_project_route_target)
        assert "_TRANSCRIPT_DELIVERY_SOURCES" in resolve
        assert 'target.source == "api_server"' not in resolve

        deliver = inspect.getsource(scheduler._deliver_result)
        assert "_TRANSCRIPT_DELIVERY_SOURCES" in deliver
        assert 'platform_name).lower() == "api_server"' not in deliver

    def test_the_resolved_target_carries_the_sessions_real_source(self):
        # _deliver_to_local_session refuses when the session's source does not
        # equal the platform it was handed. Passing a stand-in name made every
        # webhook delivery fail that guard: "api_server session … has source
        # 'webhook'". The platform must stay truthful so the guard stays exact.
        import inspect

        resolve = inspect.getsource(scheduler._resolve_project_route_target)
        assert '"platform": target.source' in resolve
