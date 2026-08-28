import XCTest

/// Pins the exact JSON that crosses the wire.
///
/// The Windows server is written against `docs/PROTOCOL.md` by hand, in another language.
/// It can't be refactored in lockstep with this app, so these tests exist to make a
/// breaking change to the format fail here rather than in the field.
final class WireFormatTests: XCTestCase {

    private let fixedDate = Date(timeIntervalSince1970: 1_700_000_000)   // 2023-11-14T22:13:20Z

    private func json<T: Codable & Sendable>(_ value: T) throws -> String {
        String(decoding: try Wire.encode(value), as: UTF8.self)
    }

    func testPhaseIsTaggedNotCompilerSynthesized() throws {
        let phase = QueuePhase.searching(SearchInfo(mode: .competitive, role: .tank,
                                                    startedAt: fixedDate, estimatedWait: 240))
        let text = try json(phase)

        XCTAssertTrue(text.contains("\"type\":\"searching\""), text)
        XCTAssertTrue(text.contains("\"startedAt\":\"2023-11-14T22:13:20Z\""), text)
        XCTAssertFalse(text.contains("_0"),
                       "compiler-synthesized keys leaked into the wire format")
    }

    func testPayloadlessPhaseHasNoDataKey() throws {
        let text = try json(QueuePhase.idle)
        XCTAssertTrue(text.contains("\"type\":\"idle\""), text)
        XCTAssertFalse(text.contains("\"data\""), text)
    }

    func testEveryPhaseRoundTrips() throws {
        let phases: [QueuePhase] = [
            .idle,
            .searching(SearchInfo(mode: .quickPlay, role: .support, startedAt: fixedDate,
                                  estimatedWait: 45, groupSize: 3)),
            .matchFound(MatchFoundInfo(mode: .competitive, role: .tank, waited: 137,
                                       lockInAt: fixedDate)),
            .mapVote(MapVoteInfo(options: [MapOption(mapKey: "ilios", votes: 2)],
                                 deadline: fixedDate, myVote: "ilios")),
            .heroSelect(HeroSelectInfo(mode: .quickPlay, role: .damage, mapKey: "havana",
                                       deadline: fixedDate, takenHeroKeys: ["ana"],
                                       myHeroKey: "tracer")),
            .inGame(InGameInfo(mode: .stadium, mapKey: "havana", heroKey: "orisa",
                               startedAt: fixedDate)),
            .cancelled(CancelInfo(reason: .matchCancelled)),
        ]
        for phase in phases {
            let decoded = try Wire.decode(QueuePhase.self, from: try Wire.encode(phase))
            XCTAssertEqual(decoded, phase, "round trip lost data for \(phase.kind.rawValue)")
        }
    }

    func testEveryCommandRoundTrips() throws {
        let commands: [ClientCommand] = [
            .hello(client: ClientIdentity(kind: .phone, name: "iPhone", appVersion: "1.0"),
                   token: "3F2504E0-4F89-41D3-9A0C-0305E82C3301"),
            .voteMap(mapKey: "ilios"),
            .selectHero(heroKey: "ana"),
            .cancelQueue,
            .requestSnapshot,
            .registerPushToken(token: "5fceb98...", environment: .sandbox),
            .registerActivityPushToken(sessionID: UUID(uuidString: "3F2504E0-4F89-41D3-9A0C-0305E82C3301")!,
                                       token: "activity-token", environment: .sandbox),
            .registerActivityStartToken(token: "start-token", environment: .production),
        ]
        for command in commands {
            let text = try json(command)
            XCTAssertTrue(text.contains("\"type\""), text)
            XCTAssertFalse(text.contains("_0"), text)
            // Decoding must not throw; the payloads are checked case-by-case elsewhere.
            _ = try Wire.decode(ClientCommand.self, from: try Wire.encode(command))
        }
    }

    func testUnknownTypeFailsWithAUsefulMessage() {
        let data = Data(#"{"v":1,"body":{"type":"teleporting"}}"#.utf8)
        XCTAssertThrowsError(try Wire.decode(QueuePhase.self, from: data)) { error in
            XCTAssertTrue("\(error)".contains("teleporting"),
                          "the error should name the offending type: \(error)")
        }
    }

    func testVersionMismatchIsRejected() {
        let data = Data(#"{"v":99,"body":{"type":"idle"}}"#.utf8)
        XCTAssertThrowsError(try Wire.decode(QueuePhase.self, from: data))
    }

    /// Not an assertion — prints the canonical samples that `docs/PROTOCOL.md` documents,
    /// so the docs can be regenerated from the code rather than drifting from it.
    func testPrintProtocolSamples() throws {
        let snapshot = QueueSnapshot(
            sessionID: UUID(uuidString: "3F2504E0-4F89-41D3-9A0C-0305E82C3301")!,
            sequence: 7,
            phase: .searching(SearchInfo(mode: .competitive, role: .tank,
                                         startedAt: fixedDate, estimatedWait: 240)),
            serverTime: fixedDate.addingTimeInterval(137))

        print("SAMPLE snapshot: \(try json(QueueEvent.snapshot(snapshot)))")
        print("SAMPLE heartbeat: \(try json(QueueEvent.heartbeat(serverTime: fixedDate)))")
        print("SAMPLE error: \(try json(QueueEvent.error(code: "no_game", message: "Overwatch isn't running")))")
        print("SAMPLE matchFound: \(try json(QueuePhase.matchFound(MatchFoundInfo(mode: .competitive, role: .tank, waited: 137, lockInAt: fixedDate))))")
        print("SAMPLE mapVote: \(try json(QueuePhase.mapVote(MapVoteInfo(options: [MapOption(mapKey: "ilios", votes: 2), MapOption(mapKey: "havana", votes: 1)], deadline: fixedDate, myVote: "ilios"))))")
        print("SAMPLE heroSelect: \(try json(QueuePhase.heroSelect(HeroSelectInfo(mode: .competitive, role: .tank, mapKey: "havana", deadline: fixedDate, takenHeroKeys: ["orisa"], myHeroKey: "reinhardt"))))")
        print("SAMPLE inGame: \(try json(QueuePhase.inGame(InGameInfo(mode: .competitive, mapKey: "havana", heroKey: "reinhardt", startedAt: fixedDate))))")
        print("SAMPLE cancelled: \(try json(QueuePhase.cancelled(CancelInfo(reason: .matchCancelled))))")
        print("SAMPLE hello: \(try json(ClientCommand.hello(client: ClientIdentity(kind: .phone, name: "Tomer's iPhone", appVersion: "1.0"), token: "3F2504E0-4F89-41D3-9A0C-0305E82C3301")))")
        print("SAMPLE voteMap: \(try json(ClientCommand.voteMap(mapKey: "ilios")))")
        print("SAMPLE selectHero: \(try json(ClientCommand.selectHero(heroKey: "reinhardt")))")
        print("SAMPLE cancelQueue: \(try json(ClientCommand.cancelQueue))")
        print("SAMPLE registerPushToken: \(try json(ClientCommand.registerPushToken(token: "5fceb98...", environment: .sandbox)))")
    }
}
