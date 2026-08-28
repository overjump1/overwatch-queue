import Foundation

/// A transport with no server behind it, driven by hand from the debug panel or by a
/// scripted `Scenario`. This is what makes the whole app testable before the PC side
/// exists — it emits exactly the same `QueueEvent`s a real server would.
@MainActor
public final class MockTransport: QueueTransport {
    public let name = "Mock"
    public private(set) var status: TransportStatus = .offline {
        didSet { onStatusChange?(status) }
    }

    public var onEvent: ((QueueEvent) -> Void)?
    public var onStatusChange: ((TransportStatus) -> Void)?

    /// Commands the app sent upstream. Surfaced in the debug panel so you can confirm a
    /// vote or hero pick actually produced the message the PC server will receive.
    public private(set) var sentCommands: [ClientCommand] = []

    private var snapshot = QueueSnapshot.idle
    private var scenarioTask: Task<Void, Never>?

    public init() {}

    public func connect() {
        status = .connected
        emit()
    }

    public func disconnect() {
        scenarioTask?.cancel()
        scenarioTask = nil
        status = .offline
    }

    public func send(_ command: ClientCommand) {
        sentCommands.append(command)
        // Reflect the local player's own choices back into state, the way a real server
        // would echo them in its next snapshot.
        switch command {
        case .voteMap(let key):
            if case .mapVote(var info) = snapshot.phase {
                info.myVote = key
                if let idx = info.options.firstIndex(where: { $0.mapKey == key }) {
                    info.options[idx].votes += 1
                }
                apply(.mapVote(info))
            }
        case .selectHero(let key):
            if case .heroSelect(var info) = snapshot.phase {
                info.myHeroKey = key
                apply(.heroSelect(info))
            }
        case .cancelQueue:
            // The mock always succeeds. A real server will sometimes have to report that
            // the cancel didn't land in time, and the UI is built to accept that.
            apply(.cancelled(CancelInfo(reason: .userLeft)))
        case .requestSnapshot:
            emit()
        case .hello:
            break
        }
    }

    // MARK: - Manual control

    /// Drives the state machine directly. Illegal transitions are refused and reported,
    /// which is how the debug panel greys out buttons that don't apply.
    @discardableResult
    public func apply(_ phase: QueuePhase) -> Bool {
        guard snapshot.phase.canTransition(to: phase) else { return false }
        snapshot = snapshot.advanced(to: phase)
        emit()
        return true
    }

    /// Starts a fresh session — new `sessionID`, sequence back to zero.
    public func reset() {
        cancelScenario()
        snapshot = QueueSnapshot()
        sentCommands.removeAll()
        emit()
    }

    public var currentPhase: QueuePhase { snapshot.phase }

    /// Back-dates the queue start so you can see a long wait without waiting for one.
    public func addElapsed(_ seconds: TimeInterval) {
        guard case .searching(var info) = snapshot.phase else { return }
        info.startedAt = info.startedAt.addingTimeInterval(-seconds)
        apply(.searching(info))
    }

    public func setEstimate(_ seconds: TimeInterval?) {
        guard case .searching(var info) = snapshot.phase else { return }
        info.estimatedWait = seconds
        apply(.searching(info))
    }

    private func emit() {
        onEvent?(.snapshot(snapshot))
    }

    // MARK: - Scenarios

    public func cancelScenario() {
        scenarioTask?.cancel()
        scenarioTask = nil
    }

    public var isRunningScenario: Bool { scenarioTask != nil }

    /// Plays a scripted timeline so the real transitions and their animations can be
    /// watched end to end, rather than jumped between.
    public func play(_ scenario: Scenario, catalog: [String] = [], heroes: [String] = []) {
        cancelScenario()
        reset()
        scenarioTask = Task { [weak self] in
            guard let self else { return }
            for step in scenario.steps(mapKeys: catalog, heroKeys: heroes) {
                if Task.isCancelled { return }
                self.apply(step.makePhase())
                try? await Task.sleep(nanoseconds: UInt64(step.hold * 1_000_000_000))
            }
            self.scenarioTask = nil
        }
    }

    public struct Step: Sendable {
        /// Built when the step is applied, not when the timeline is compiled. Phases carry
        /// absolute deadlines and the whole scenario is laid out in one go, so a deadline
        /// computed up front has already been running down — by the time a later step came
        /// up its own deadline had passed, and the phase started expired.
        public var makePhase: @Sendable () -> QueuePhase
        /// Seconds to hold before moving to the next step.
        public var hold: TimeInterval
    }

    public enum Scenario: String, CaseIterable, Identifiable, Sendable {
        case fastQuickPlay
        case longCompTank
        case matchFellApart
        case mysteryHeroes

        public var id: String { rawValue }

        public var displayName: String {
            switch self {
            case .fastQuickPlay: return "Fast Quick Play"
            case .longCompTank: return "Long Comp Tank"
            case .matchFellApart: return "Match Falls Apart"
            case .mysteryHeroes: return "Mystery Heroes"
            }
        }

        public var detail: String {
            switch self {
            case .fastQuickPlay: return "12s search → found → vote → heroes → game"
            case .longCompTank: return "Starts 6 minutes deep, past its estimate"
            case .matchFellApart: return "Match found, then cancelled — back to searching"
            case .mysteryHeroes: return "No role queue, no hero select"
            }
        }

        func steps(mapKeys: [String], heroKeys: [String]) -> [Step] {
            // Chosen once so every step of a run agrees on the map, but each phase is
            // built at the moment it is applied so its deadline starts from then.
            let maps = mapKeys.isEmpty
                ? ["kings-row", "circuit-royal", "ilios"]
                : Array(mapKeys.shuffled().prefix(3))

            switch self {
            case .fastQuickPlay:
                return [
                    Step(makePhase: { .searching(SearchInfo(mode: .quickPlay, role: .damage,
                                                            startedAt: .now, estimatedWait: 45)) }, hold: 12),
                    Step(makePhase: { .matchFound(MatchFoundInfo(mode: .quickPlay, role: .damage,
                                                                 waited: 12,
                                                                 lockInAt: .now + 10)) }, hold: 4),
                    Step(makePhase: { .mapVote(MapVoteInfo(options: maps.map { MapOption(mapKey: $0, votes: .random(in: 0...3)) },
                                                           deadline: .now + 20)) }, hold: 20),
                    Step(makePhase: { .heroSelect(HeroSelectInfo(mode: .quickPlay, role: .damage,
                                                                 mapKey: maps.first,
                                                                 deadline: .now + 30,
                                                                 takenHeroKeys: Array(heroKeys.shuffled().prefix(2)))) }, hold: 30),
                    Step(makePhase: { .inGame(InGameInfo(mode: .quickPlay, mapKey: maps.first,
                                                         heroKey: heroKeys.first, startedAt: .now)) }, hold: 60),
                ]

            case .longCompTank:
                return [
                    Step(makePhase: { .searching(SearchInfo(mode: .competitive, role: .tank,
                                                            startedAt: .now.addingTimeInterval(-360),
                                                            estimatedWait: 240)) }, hold: 25),
                    Step(makePhase: { .matchFound(MatchFoundInfo(mode: .competitive, role: .tank,
                                                                 waited: 385,
                                                                 lockInAt: .now + 10)) }, hold: 5),
                    Step(makePhase: { .heroSelect(HeroSelectInfo(mode: .competitive, role: .tank,
                                                                 mapKey: maps.first,
                                                                 deadline: .now + 40)) }, hold: 40),
                    Step(makePhase: { .inGame(InGameInfo(mode: .competitive, mapKey: maps.first,
                                                         startedAt: .now)) }, hold: 60),
                ]

            case .matchFellApart:
                return [
                    Step(makePhase: { .searching(SearchInfo(mode: .quickPlay, role: .support,
                                                            startedAt: .now, estimatedWait: 30)) }, hold: 8),
                    Step(makePhase: { .matchFound(MatchFoundInfo(mode: .quickPlay, role: .support,
                                                                 waited: 8,
                                                                 lockInAt: .now + 10)) }, hold: 6),
                    Step(makePhase: { .cancelled(CancelInfo(reason: .matchCancelled)) }, hold: 3),
                    Step(makePhase: { .searching(SearchInfo(mode: .quickPlay, role: .support,
                                                            startedAt: .now, estimatedWait: 40)) }, hold: 30),
                ]

            case .mysteryHeroes:
                return [
                    Step(makePhase: { .searching(SearchInfo(mode: .mysteryHeroes, role: .open,
                                                            startedAt: .now, estimatedWait: 20)) }, hold: 10),
                    Step(makePhase: { .matchFound(MatchFoundInfo(mode: .mysteryHeroes, role: .open,
                                                                 waited: 10,
                                                                 lockInAt: .now + 10)) }, hold: 4),
                    Step(makePhase: { .inGame(InGameInfo(mode: .mysteryHeroes, mapKey: maps.first,
                                                         heroKey: heroKeys.randomElement(),
                                                         startedAt: .now)) }, hold: 60),
                ]
            }
        }
    }
}
