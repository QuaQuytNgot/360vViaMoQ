import unittest

from moq_360_protocol_lab.protocol.timeout import classify_timeout_event
from moq_360_protocol_lab.protocol.updates import UpdateTiming, update_timing_metrics


class ProtocolObservationTests(unittest.TestCase):
    def test_request_update_metrics_keep_ack_and_effect_separate(self):
        metrics = update_timing_metrics(UpdateTiming(
            update_generated_ts_ns=100,
            request_update_sent_ts_ns=110,
            update_response_ts_ns=120,
            first_schedule_effect_ts_ns=160,
            first_new_priority_object_ts_ns=170,
        ))
        self.assertEqual(metrics["control_response_latency_ns"], 10)
        self.assertEqual(metrics["effective_scheduling_reaction_latency_ns"], 50)
        self.assertEqual(metrics["new_priority_object_latency_ns"], 60)

    def test_missing_native_events_are_not_zero_latency(self):
        metrics = update_timing_metrics(UpdateTiming(update_generated_ts_ns=100))
        self.assertIsNone(metrics["control_response_latency_ns"])
        self.assertIsNone(metrics["effective_scheduling_reaction_latency_ns"])

    def test_timeout_classifier_requires_native_delivery_timeout_evidence(self):
        self.assertEqual(
            classify_timeout_event({"source": "native_moqt", "reason": "delivery_timeout", "event_type": "reset"}),
            "native_delivery_timeout_reset",
        )
        self.assertEqual(
            classify_timeout_event({"source": "application", "reason": "delivery_timeout", "event_type": "expired"}),
            "unclassified",
        )
        self.assertEqual(
            classify_timeout_event({"source": "native_relay", "reason": "application_deadline", "event_type": "dropped"}),
            "unclassified",
        )


if __name__ == "__main__":
    unittest.main()
