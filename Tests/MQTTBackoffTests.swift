import XCTest
@testable import OverwatchQueue

/// `reconnectAttempts` climbs unboundedly over a long outage — a PC left off overnight
/// can run it into the hundreds — so the backoff math has to stay safe at every value,
/// not just the small ones exercised by a normal reconnect. Reproduced a real crash: an
/// unclamped exponent overflowed `Int(Double)`, which traps rather than saturating.
final class MQTTBackoffTests: XCTestCase {
    func testFloorsAtFiveSecondsForEarlyAttempts() {
        // A PC that's simply unreachable (broker down, wrong network) fails each attempt
        // near-instantly — without a floor, the first few retries would fire in a tight
        // loop rather than actually backing off, which is what tipped a real crash in
        // Transport's stream-task setup racing its own teardown.
        XCTAssertEqual(MQTT.backoffSeconds(forAttempt: 0), 5)
        XCTAssertEqual(MQTT.backoffSeconds(forAttempt: 1), 5)
        XCTAssertEqual(MQTT.backoffSeconds(forAttempt: 2), 5)
    }

    func testGrowsExponentiallyOnceItClearsTheFloor() {
        XCTAssertEqual(MQTT.backoffSeconds(forAttempt: 3), 8)
        XCTAssertEqual(MQTT.backoffSeconds(forAttempt: 4), 16)
    }

    func testCapsAtThirtySecondsOnceTheExponentClearsIt() {
        XCTAssertEqual(MQTT.backoffSeconds(forAttempt: 5), 30)
        XCTAssertEqual(MQTT.backoffSeconds(forAttempt: 6), 30)
    }

    func testNeverTrapsNoMatterHowManyAttemptsHaveAccumulated() {
        // Before the fix, `pow(2, Double(attempt))` overflowed Int64 well before this
        // point and `Int(Double)` trapped instead of saturating — crashing the app on
        // what should have been a routine retry after a long PC outage.
        for attempt in [50, 500, 5000, .max] as [Int] {
            XCTAssertEqual(MQTT.backoffSeconds(forAttempt: attempt), 30)
        }
    }
}
