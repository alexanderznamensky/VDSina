"""Offline API tests; run with Python 3.11+ and aiohttp installed."""
import importlib
import sys
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path

# Import the API without executing HA's integration __init__.py.
package = types.ModuleType("vdsina_test")
package.__path__ = [str(Path(__file__).resolve().parents[1] / "custom_components/vdsina")]
sys.modules[package.__name__] = package
api = importlib.import_module("vdsina_test.api")
const = importlib.import_module("vdsina_test.const")


class Response:
    def __init__(self, status, data):
        self.status, self.data = status, data
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass
    async def json(self):
        if isinstance(self.data, Exception):
            raise self.data
        return self.data


def ok(data):
    return (200, {"status": "ok", "data": data})


class Session:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        path = url.split("/v1/")[1]
        value = self.routes[path]
        if isinstance(value, list):
            value = value.pop(0)
        return Response(*value)


def routes():
    return {
        "account": ok({"account": {"id": 7, "name": "a7"}, "forecast": "2026-10-28"}),
        "account.balance": ok({"real": "12.34", "bonus": "0", "currency": "USD"}),
        "server": ok([{"id": 20, "name": "Two", "status": "active"},
                       {"id": 10, "name": "One", "status": "notpaid"}]),
        "server/20": ok({"id": 20, "name": "Two", "status": "active", "ip": {"ip": "192.0.2.20"}, "password": "SECRET-DO-NOT-EXPOSE"}),
        "server/10": ok({"id": 10, "name": "One", "status": "notpaid", "ip": [{"ip": "192.0.2.10"}, {"ip": "2001:db8::1"}]}),
        "server.stat/20": ok([]),
        "server.stat/10": ok([]),
    }


class APITests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_preserves_ids_and_missing_amounts(self):
        session = Session(routes())
        result = await api.VDSinaClient(session, "test-token", const.DEFAULT_API_HOST).snapshot()
        self.assertEqual(set(result["servers"]), {"10", "20"})
        self.assertEqual(result["balance"], 12.34)
        self.assertEqual(result["bonus"], 0)
        self.assertIsNone(result["partner"])
        self.assertEqual(result["servers"]["10"]["ips"], ["192.0.2.10", "2001:db8::1"])
        self.assertNotIn("SECRET-DO-NOT-EXPOSE", repr(result))
        self.assertTrue(all(not kwargs["allow_redirects"] for _, kwargs in session.calls))

    async def test_auth_fallback_same_host_and_reuse(self):
        data = routes()
        data["account"] = [(401, None), data["account"], data["account"]]
        session = Session(data)
        client = api.VDSinaClient(session, "Bearer test-token", const.DEFAULT_API_HOST)
        await client.snapshot()
        await client.account()
        self.assertEqual(session.calls[0][1]["headers"]["Authorization"], "Bearer test-token")
        self.assertTrue(all(call[1]["headers"]["Authorization"] == "test-token" for call in session.calls[1:]))
        self.assertTrue(all(call[0].startswith("https://userapi.vdsina.com/") for call in session.calls))

    async def test_invalid_auth(self):
        session = Session({"account": [(401, None), (401, None)]})
        with self.assertRaises(api.InvalidAuth):
            await api.VDSinaClient(session, "test-token", const.DEFAULT_API_HOST).snapshot()
        self.assertEqual(len(session.calls), 2)

    async def test_empty_account_no_false_zero(self):
        data = routes()
        data["account"] = ok({"account": {"id": 7}, "forecast": None})
        data["account.balance"] = ok(None)
        data["server"] = ok(None)
        result = await api.VDSinaClient(Session(data), "test-token", const.DEFAULT_API_HOST).snapshot()
        self.assertIsNone(result["balance"])
        self.assertIsNone(result["forecast"])
        self.assertEqual(result["servers"], {})

    async def test_deleted_during_refresh(self):
        data = routes()
        data["server/10"] = (404, None)
        result = await api.VDSinaClient(Session(data), "test-token", const.DEFAULT_API_HOST).snapshot()
        self.assertEqual(set(result["servers"]), {"20"})

    async def test_partial_failure_does_not_return_false_success(self):
        data = routes()
        data["server/10"] = (403, None)
        with self.assertRaises(api.VDSinaError):
            await api.VDSinaClient(Session(data), "test-token", const.DEFAULT_API_HOST).snapshot()

    async def test_bad_payloads(self):
        for bad in [(200, ValueError("bad JSON")), (200, []), (302, None), (429, None),
                    (200, {"status": "error", "data": {}})]:
            with self.subTest(bad=bad):
                with self.assertRaises(api.VDSinaError):
                    await api.VDSinaClient(Session({"account": bad}), "test-token", const.DEFAULT_API_HOST).account()

    async def test_malformed_server_id(self):
        data = routes()
        data["server"] = ok([{"id": "not-an-id"}])
        with self.assertRaises(api.VDSinaError):
            await api.VDSinaClient(Session(data), "test-token", const.DEFAULT_API_HOST).snapshot()

    def test_dates_amounts_and_default_interval(self):
        self.assertEqual(const.DEFAULT_SCAN_INTERVAL, 60)
        self.assertEqual(api.days_left(datetime.now(api.MOSCOW).date().isoformat()), 0)
        yesterday = datetime.now(api.MOSCOW).date() - timedelta(days=1)
        self.assertEqual(api.days_left(yesterday.isoformat()), -1)
        self.assertIsNone(api.days_left(None))
        self.assertEqual(api.parse_timestamp("2026-10-04 12:00:00").utcoffset(), timedelta(hours=3))
        for amount in [None, "bad", "NaN", "Infinity", "1e1000000", True]:
            self.assertIsNone(api.number(amount))
        self.assertEqual(api.number("0.00"), 0)

    def test_monthly_traffic_conversion_and_missing_values(self):
        self.assertEqual(api.traffic_gb({"current_month": 12_500_000_000}), 12.5)
        self.assertEqual(api.traffic_gb({"current_month": 0}), 0)
        self.assertEqual(api.traffic_gb({"last_month": "2500000000"}, "last_month"), 2.5)
        for payload in [None, {}, {"current_month": None}, {"current_month": -1},
                        {"current_month": "NaN"}, {"current_month": "bad"}]:
            self.assertIsNone(api.traffic_gb(payload))

    def test_hourly_direction_sums_and_calendar_boundaries(self):
        start = datetime(2026, 10, 1, tzinfo=api.MOSCOW)
        end = datetime(2026, 10, 4, tzinfo=api.MOSCOW)
        rows = [
            {"dt": "2026-09-30 23:00:00", "stat": {"vnet_rx": 999, "vnet_tx": 999}},
            {"dt": "2026-10-01 00:00:00", "stat": {"vnet_rx": 10, "vnet_tx": 20, "lnet_rx": 999}},
            {"dt": "2026-10-01 01:00:00", "stat": {"vnet_rx": 30, "vnet_tx": 40}},
            {"dt": "2026-11-01 00:00:00", "stat": {"vnet_rx": 999, "vnet_tx": 999}},
        ]
        result = api.monthly_directions(rows, start, end)
        self.assertEqual(result, {"incoming_bytes": 40, "outgoing_bytes": 60, "samples": 2})
        rows[2]["stat"].pop("vnet_rx")
        self.assertIsNone(api.monthly_directions(rows, start, end)["incoming_bytes"])
        self.assertEqual(api.monthly_directions(rows, start, end)["outgoing_bytes"], 60)
        with self.assertRaises(api.VDSinaError):
            api.monthly_directions([rows[1], rows[1]], start, end)

    async def test_statistics_failure_does_not_hide_account(self):
        data = routes()
        data["server.stat/10"] = (403, None)
        session = Session(data)
        result = await api.VDSinaClient(session, "test-token", const.DEFAULT_API_HOST).snapshot()
        self.assertEqual(result["balance"], 12.34)
        self.assertIsNone(result["servers"]["10"]["traffic_statistics"]["incoming_bytes"])
        self.assertIn("403", result["servers"]["10"]["traffic_statistics_error"])
        stats_call = next(kwargs for url, kwargs in session.calls if url.endswith("server.stat/10"))
        self.assertTrue(stats_call["params"]["from"].endswith("-01 00:00:00"))


if __name__ == "__main__":
    unittest.main()
