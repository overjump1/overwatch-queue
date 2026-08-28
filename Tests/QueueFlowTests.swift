import XCTest
import SwiftUI

final class QueueFlowTests: XCTestCase {

    func testLegalTransitions() {
        let searching = QueuePhase.searching(SearchInfo(mode: .quickPlay, role: .damage, startedAt: .now))
        let found = QueuePhase.matchFound(MatchFoundInfo(mode: .quickPlay, role: .damage,
                                                         waited: 30, lockInAt: .now + 10))
        XCTAssertTrue(QueuePhase.idle.canTransition(to: searching))
        XCTAssertTrue(searching.canTransition(to: found))
        XCTAssertTrue(found.canTransition(to: searching), "a match that falls apart returns to searching")
        XCTAssertFalse(QueuePhase.idle.canTransition(to: found), "can't find a match you never queued for")
    }

    func testSnapshotOrdering() {
        let a = QueueSnapshot(sequence: 5)
        let older = QueueSnapshot(sessionID: a.sessionID, sequence: 4)
        let newer = QueueSnapshot(sessionID: a.sessionID, sequence: 6)
        XCTAssertFalse(a.supersededBy(older), "out-of-order delivery must not rewind state")
        XCTAssertTrue(a.supersededBy(newer))
        XCTAssertTrue(a.supersededBy(QueueSnapshot(sequence: 0)), "a new session always wins")
    }

    func testClockSyncCorrectsDrift() {
        var clock = ClockSync()
        let now = Date()
        clock.observe(serverTime: now.addingTimeInterval(30), receivedAt: now)
        XCTAssertEqual(clock.offset, 30, accuracy: 0.01)

        // A server timestamp 30s ahead should map back onto this device's clock.
        XCTAssertEqual(clock.toLocal(now.addingTimeInterval(30)).timeIntervalSince1970,
                       now.timeIntervalSince1970, accuracy: 0.01)
    }

    func testClockSyncIgnoresJitter() {
        var clock = ClockSync(offset: 10)
        let now = Date()
        clock.observe(serverTime: now.addingTimeInterval(10.4), receivedAt: now)
        XCTAssertEqual(clock.offset, 10, accuracy: 0.01,
                       "sub-deadband noise must not re-anchor timers")
    }

    func testWireRoundTrip() throws {
        let phase = QueuePhase.mapVote(MapVoteInfo(options: [MapOption(mapKey: "ilios", votes: 2)],
                                                   deadline: Date(timeIntervalSince1970: 1_700_000_000),
                                                   myVote: "ilios"))
        let snapshot = QueueSnapshot(sequence: 3, phase: phase,
                                     serverTime: Date(timeIntervalSince1970: 1_700_000_000))
        let data = try Wire.encode(snapshot)
        let decoded = try Wire.decode(QueueSnapshot.self, from: data)
        XCTAssertEqual(decoded, snapshot)
    }

    func testCommandRoundTrip() throws {
        let data = try Wire.encode(ClientCommand.selectHero(heroKey: "ana"))
        guard case .selectHero(let key) = try Wire.decode(ClientCommand.self, from: data) else {
            return XCTFail("wrong case decoded")
        }
        XCTAssertEqual(key, "ana")
    }

    func testBundledCatalogDecodes() throws {
        // Guards against an upstream schema change silently emptying every hero grid.
        let url = try XCTUnwrap(Bundle.main.url(forResource: "catalog-fallback", withExtension: "json"))
        struct Payload: Codable { var heroes: [Hero]; var maps: [OverwatchMap] }
        let payload = try Wire.decoder.decode(Payload.self, from: Data(contentsOf: url))

        XCTAssertGreaterThan(payload.heroes.count, 40)
        XCTAssertGreaterThan(payload.maps.count, 40)
        XCTAssertTrue(payload.heroes.allSatisfy { $0.portrait != nil })
        XCTAssertFalse(payload.maps.contains { $0.gamemodes.contains(.unknown) },
                       "an unmapped gamemode means MapType is out of date")
    }

    func testSearchProgressClampsAndFlagsOverdue() throws {
        let info = SearchInfo(mode: .competitive, role: .tank,
                              startedAt: Date().addingTimeInterval(-300), estimatedWait: 120)
        XCTAssertEqual(try XCTUnwrap(info.progress()), 1.0, accuracy: 0.001,
                       "progress must clamp rather than exceed a full bar")
        XCTAssertTrue(info.isOverdue())

        let noEstimate = SearchInfo(mode: .quickPlay, role: .damage, startedAt: Date())
        XCTAssertNil(noEstimate.progress(), "no estimate means no progress to show")
        XCTAssertFalse(noEstimate.isOverdue())
    }

    /// The hero-select crash. `Text(timerInterval:)` takes a `ClosedRange`, and forming
    /// one whose upper bound has already passed traps rather than yielding an empty
    /// range — so a countdown used to kill the process the moment its own deadline went
    /// by. Hero select reaches that state on any run where the player uses the whole
    /// window, and the Live Activity re-renders well past it.
    func testCountdownOutlivesItsDeadline() {
        _ = Text.countdown(to: Date().addingTimeInterval(-3600))
        _ = Text.countdown(to: Date().addingTimeInterval(-0.001))
        _ = Text.countdown(to: Date())
        _ = Text.countdown(to: Date().addingTimeInterval(40))
    }

    /// A scenario is compiled in one go but plays out over minutes, so a phase built up
    /// front carries a deadline that has been running down ever since — `fastQuickPlay`
    /// used to reach hero select a full six seconds after that phase's deadline expired.
    /// Each phase has to be built when its own step is applied.
    func testScenarioDeadlinesStartWhenTheStepDoes() throws {
        for scenario in MockTransport.Scenario.allCases {
            let steps = scenario.steps(mapKeys: [], heroKeys: [])
            for (index, step) in steps.enumerated() {
                guard let early = step.makePhase().deadline else { continue }
                Thread.sleep(forTimeInterval: 0.02)
                let late = try XCTUnwrap(step.makePhase().deadline)
                XCTAssertGreaterThan(late, early,
                                     "\(scenario.rawValue) step \(index) bakes its deadline in at compile time")
                XCTAssertGreaterThan(late, Date(),
                                     "\(scenario.rawValue) step \(index) starts a phase that has already expired")
            }
        }
    }
}
